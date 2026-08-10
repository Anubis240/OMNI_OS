"""Live execution — real swaps built directly against Uniswap V3
(Quoter + SwapRouter02), signed and broadcast by the app-managed local
wallet (trader/wallet/local_wallet.py — WalletConnect was dropped for
this Python version, see the project plan), with Seraph's REAL pre-trade
firewall (guardian_pretrade_check) as the gate on the actual calldata
before it's ever signed. Port of live.js — see that file's header comment
for why Uniswap directly (not an aggregator): guardian_pretrade_check
only decodes plain Uniswap V3/V2 calldata, not 0x-aggregator or
multicall-wrapped calls.

Uses web3.py (the Python equivalent of ethers.js used here) for all
RPC/contract interaction. Gas pricing uses plain legacy gasPrice
(w3.eth.gas_price) rather than replicating ethers' EIP-1559 fee
estimation — a minor simplification, not a safety-relevant one; legacy
gasPrice transactions are valid on every chain this app trades on.
"""

from __future__ import annotations

import json
import logging
import re
import time

import requests
from web3 import Web3

from . import chains as chains_mod
from .wallet import local_wallet as wallet

logger = logging.getLogger(__name__)

FEE_TIERS = [500, 3000, 10000, 100]
SLIPPAGE_BPS = 100  # 1%
V2_GAS_ESTIMATE = 170000
V2_DEADLINE_SECONDS = 600

ERC20_ABI = [
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}],
     "stateMutability": "view", "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"constant": False, "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"},
    {"constant": True, "inputs": [{"name": "account", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
]
# WETH9's own withdraw() — burns WETH, sends native ETH to msg.sender.
# Not a swap and not a multicall, so it's outside the Seraph-decoding
# limitation that forces sell proceeds to land as WETH in the first
# place (see live_sell()'s comment) — this is the deliberate, correct way
# to get from WETH back to native ETH afterward, on demand.
WETH_ABI = ERC20_ABI + [
    {"constant": False, "inputs": [{"name": "wad", "type": "uint256"}], "name": "withdraw",
     "outputs": [], "stateMutability": "nonpayable", "type": "function"},
]
QUOTER_ABI = [{
    "inputs": [{"components": [
        {"name": "tokenIn", "type": "address"}, {"name": "tokenOut", "type": "address"},
        {"name": "amountIn", "type": "uint256"}, {"name": "fee", "type": "uint24"},
        {"name": "sqrtPriceLimitX96", "type": "uint160"},
    ], "name": "params", "type": "tuple"}],
    "name": "quoteExactInputSingle",
    "outputs": [
        {"name": "amountOut", "type": "uint256"}, {"name": "sqrtPriceX96After", "type": "uint160"},
        {"name": "initializedTicksCrossed", "type": "uint32"}, {"name": "gasEstimate", "type": "uint256"},
    ],
    "stateMutability": "nonpayable", "type": "function",
}]
# Deliberately just exactInputSingle — see live_sell()'s comment on why
# unwrapWETH9/multicall are not used despite Uniswap supporting them.
ROUTER_ABI = [{
    "inputs": [{"components": [
        {"name": "tokenIn", "type": "address"}, {"name": "tokenOut", "type": "address"},
        {"name": "fee", "type": "uint24"}, {"name": "recipient", "type": "address"},
        {"name": "amountIn", "type": "uint256"}, {"name": "amountOutMinimum", "type": "uint256"},
        {"name": "sqrtPriceLimitX96", "type": "uint160"},
    ], "name": "params", "type": "tuple"}],
    "name": "exactInputSingle", "outputs": [{"name": "amountOut", "type": "uint256"}],
    "stateMutability": "payable", "type": "function",
}]
V2_FACTORY_ABI = [{"inputs": [{"name": "tokenA", "type": "address"}, {"name": "tokenB", "type": "address"}],
                    "name": "getPair", "outputs": [{"name": "pair", "type": "address"}],
                    "stateMutability": "view", "type": "function"}]
V2_ROUTER_ABI = [
    {"inputs": [{"name": "amountIn", "type": "uint256"}, {"name": "path", "type": "address[]"}],
     "name": "getAmountsOut", "outputs": [{"name": "amounts", "type": "uint256[]"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "amountOutMin", "type": "uint256"}, {"name": "path", "type": "address[]"},
                {"name": "to", "type": "address"}, {"name": "deadline", "type": "uint256"}],
     "name": "swapExactETHForTokens", "outputs": [{"name": "amounts", "type": "uint256[]"}],
     "stateMutability": "payable", "type": "function"},
    {"inputs": [{"name": "amountIn", "type": "uint256"}, {"name": "amountOutMin", "type": "uint256"},
                {"name": "path", "type": "address[]"}, {"name": "to", "type": "address"},
                {"name": "deadline", "type": "uint256"}],
     "name": "swapExactTokensForETH", "outputs": [{"name": "amounts", "type": "uint256[]"}],
     "stateMutability": "nonpayable", "type": "function"},
]

_mcp_call = None  # (name, args) -> {ok, text|error} — bound to the Seraph server by the caller (engine.py)


def init(mcp_call=None, server_id=None, zero_ex_api_key=None):
    global _mcp_call
    _mcp_call = mcp_call


def clean_rpc_error(err) -> str:
    raw = str(err)
    m = re.search(r"insufficient funds for gas \* price \+ value: address (\S+) have (\d+) want (\d+)", raw, re.I)
    if m:
        address, have_wei, want_wei = m.groups()
        short = address[:6] + "…" + address[-4:]
        return f"insufficient ETH for gas — wallet {short} has {Web3.from_wei(int(have_wei), 'ether')} ETH, needs ~{Web3.from_wei(int(want_wei), 'ether')} ETH"
    if re.search(r"insufficient funds", raw, re.I):
        return "insufficient funds for gas — wallet balance too low"
    return re.sub(r"0x[0-9a-fA-F]{20,}", "0x…", raw)[:300]


def _live_config(chain: str | None) -> dict:
    c = chains_mod.resolve(chain)
    if not c.get("live"):
        raise RuntimeError(f"live execution is not supported on {c['name']}")
    return c


def _with_rpc(chain, fn):
    rpc_urls = _live_config(chain)["live"]["rpcUrls"]
    last_err = None
    for url in rpc_urls:
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 15}))
            return fn(w3)
        except Exception as err:
            last_err = err
    raise RuntimeError(f"all RPC endpoints failed for {chains_mod.resolve(chain)['name']}: {clean_rpc_error(last_err)}")


def _eth_usd_price_from_coingecko() -> float:
    res = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd", timeout=10)
    res.raise_for_status()
    price = res.json().get("ethereum", {}).get("usd")
    if not isinstance(price, (int, float)):
        raise RuntimeError("ETH price missing from CoinGecko response")
    return price


def eth_usd_price() -> float:
    res = _mcp_call("crypto_get_price", {"asset": "eth"}) if _mcp_call else {"ok": False, "error": "no mcp_call configured"}
    if res.get("ok"):
        try:
            parsed = json.loads(res.get("text") or "")
        except Exception:
            parsed = None
        if parsed:
            price = parsed.get("price_usd", parsed.get("priceUsd", parsed.get("price", parsed.get("usd"))))
            if isinstance(price, (int, float)):
                return price
    try:
        return _eth_usd_price_from_coingecko()
    except Exception as fallback_err:
        raise RuntimeError(f"could not fetch ETH price (Seraph: {'bad response' if res.get('ok') else res.get('error')}; CoinGecko fallback: {fallback_err})")


def wallet_eth_balance_usd(chain: str | None, address: str, eth_price_usd: float) -> float:
    wei = _with_rpc(chain, lambda w3: w3.eth.get_balance(Web3.to_checksum_address(address)))
    return float(Web3.from_wei(wei, "ether")) * eth_price_usd


def token_balance(chain: str | None, token_address: str, owner_address: str) -> float:
    """Real on-chain ERC20 balance in human units — used to reconcile the
    local position ledger against tokens actually sold/moved outside the app."""
    contract_fn = lambda w3: w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
    decimals = _with_rpc(chain, lambda w3: contract_fn(w3).functions.decimals().call())
    balance_wei = _with_rpc(chain, lambda w3: contract_fn(w3).functions.balanceOf(Web3.to_checksum_address(owner_address)).call())
    return balance_wei / (10 ** decimals)


def wallet_equity_usd_across_chains(chain_keys: list[str], address: str, eth_price_usd: float) -> float:
    total = 0.0
    for c in chain_keys:
        try:
            total += wallet_eth_balance_usd(c, address, eth_price_usd)
        except Exception:
            pass
    return total


def best_quote(chain: str | None, token_in: str, token_out: str, amount_in_wei: int) -> dict:
    live_cfg = _live_config(chain)["live"]
    quoter_addr = live_cfg["uniswapV3Quoter"]
    best = None
    for fee in FEE_TIERS:
        try:
            def _call(w3, fee=fee):
                quoter = w3.eth.contract(address=Web3.to_checksum_address(quoter_addr), abi=QUOTER_ABI)
                return quoter.functions.quoteExactInputSingle((
                    Web3.to_checksum_address(token_in), Web3.to_checksum_address(token_out),
                    amount_in_wei, fee, 0,
                )).call()
            result = _with_rpc(chain, _call)
            amount_out = result[0]
            if not best or amount_out > best["amountOut"]:
                best = {"dex": "v3", "fee": fee, "amountOut": amount_out, "gasEstimate": result[3]}
        except Exception:
            pass
    if best:
        return best

    v2_router = live_cfg.get("uniswapV2Router")
    v2_factory = live_cfg.get("uniswapV2Factory")
    if v2_router and v2_factory:
        try:
            pair = _with_rpc(chain, lambda w3: w3.eth.contract(address=Web3.to_checksum_address(v2_factory), abi=V2_FACTORY_ABI)
                              .functions.getPair(Web3.to_checksum_address(token_in), Web3.to_checksum_address(token_out)).call())
            if pair and int(pair, 16) != 0:
                amounts = _with_rpc(chain, lambda w3: w3.eth.contract(address=Web3.to_checksum_address(v2_router), abi=V2_ROUTER_ABI)
                                     .functions.getAmountsOut(amount_in_wei, [Web3.to_checksum_address(token_in), Web3.to_checksum_address(token_out)]).call())
                return {"dex": "v2", "amountOut": amounts[-1], "gasEstimate": V2_GAS_ESTIMATE}
        except Exception:
            pass
    raise RuntimeError(f"no Uniswap V3{' or V2' if v2_router else ''} pool found for this pair on {chains_mod.resolve(chain)['name']}")


PRETRADE_IN_PROGRESS_STATUSES = {"pending", "running"}


def _short(text: str, limit: int = 160) -> str:
    """One-line, length-capped summary for user-facing feed messages.
    Full detail always goes to the log instead of the feed."""
    single_line = re.sub(r"\s+", " ", str(text)).strip()
    return single_line if len(single_line) <= limit else single_line[:limit].rstrip() + "…"


def _parse_pretrade_response(text: str) -> dict:
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if parsed and not parsed.get("decision") and parsed.get("status") in PRETRADE_IN_PROGRESS_STATUSES and parsed.get("requestId"):
        return {"pending": True, "requestId": parsed["requestId"], "retryAfterMs": parsed.get("retryAfterMs") or 4000, "raw": text[:1500]}
    decision = str(parsed["decision"]).lower() if parsed and parsed.get("decision") else "unknown"
    if not parsed:
        if re.search(r"\ballow\b", text, re.I):
            decision = "allow"
        elif re.search(r"\bblock\b", text, re.I):
            decision = "block"
    return {
        "pending": False, "decision": decision, "raw": text[:1500],
        "explain": parsed.get("explain") if parsed and isinstance(parsed.get("explain"), str) else None,
        "expectedAmountOut": int(parsed["expectedAmountOut"]) if parsed and parsed.get("expectedAmountOut") else None,
        "priceImpactBps": parsed.get("priceImpactBps") if parsed and isinstance(parsed.get("priceImpactBps"), (int, float)) else None,
        "maxFeePerGasWei": int(parsed["gas"]["maxFeePerGasWei"]) if parsed and parsed.get("gas", {}).get("maxFeePerGasWei") else None,
    }


def pretrade_check(chain: str | None, to: str, data: str, value, from_addr: str) -> dict:
    chain_id = chains_mod.resolve(chain)["chainId"]
    res = _mcp_call("guardian_pretrade_check", {"chainId": chain_id, "to": to, "callData": data, "value": str(value or "0"), "from": from_addr})
    if not res.get("ok"):
        logger.warning("pre-trade firewall unavailable (guardian_pretrade_check): %s", res.get("error"))
        raise RuntimeError(f"Seraph pre-trade firewall unavailable: {_short(res.get('error'))} (fail-closed)")
    result = _parse_pretrade_response(res.get("text") or "")

    poll_budget_s = 120
    start = time.monotonic()
    while result["pending"]:
        if time.monotonic() - start > poll_budget_s:
            raise RuntimeError("Seraph pre-trade firewall still pending after 2 minutes — refusing to sign (fail-closed)")
        time.sleep(result["retryAfterMs"] / 1000)
        poll_res = _mcp_call("guardian_pretrade_result", {"requestId": result["requestId"]})
        if not poll_res.get("ok"):
            logger.warning("pre-trade firewall unavailable (guardian_pretrade_result): %s", poll_res.get("error"))
            raise RuntimeError(f"Seraph pre-trade firewall unavailable: {_short(poll_res.get('error'))} (fail-closed)")
        result = _parse_pretrade_response(poll_res.get("text") or "")
    return result


def require_allow(chain: str | None, tx: dict, from_address: str) -> dict:
    gate = pretrade_check(chain, tx["to"], tx.get("data"), tx.get("value"), from_address)
    if gate["decision"] != "allow":
        logger.warning("pre-trade firewall %s — raw response: %s", gate["decision"], gate["raw"])
        reason = gate["explain"] or gate["raw"]
        raise RuntimeError(f"Seraph pre-trade firewall {gate['decision'].upper()} — refusing to sign. {_short(reason)}")
    return gate


def _wait_for_receipt(chain: str | None, tx_hash: str, timeout_s: float = 180) -> dict:
    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        try:
            receipt = _with_rpc(chain, lambda w3: w3.eth.get_transaction_receipt(tx_hash))
        except Exception:
            receipt = None
        if receipt:
            if receipt.get("status") == 0:
                raise RuntimeError(f"transaction reverted on-chain: {tx_hash}")
            return receipt
        time.sleep(5)
    raise RuntimeError(f"timed out waiting for confirmation (still pending): {tx_hash}")


def _gas_cost_usd(receipt: dict, eth_price_usd: float) -> float:
    price = receipt.get("effectiveGasPrice") or receipt.get("gasPrice") or 0
    wei = int(receipt["gasUsed"]) * int(price)
    return float(Web3.from_wei(wei, "ether")) * eth_price_usd


def _ensure_allowance(chain: str | None, token_address: str, owner: str, spender: str, amount_wei: int):
    current = _with_rpc(chain, lambda w3: w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
                         .functions.allowance(Web3.to_checksum_address(owner), Web3.to_checksum_address(spender)).call())
    if current >= amount_wei:
        return
    contract = Web3().eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
    data = contract.encode_abi("approve", args=[Web3.to_checksum_address(spender), amount_wei])
    tx_hash = wallet.send_transaction(token_address, data, "0x0", chain)
    _wait_for_receipt(chain, tx_hash)


def live_buy(token: dict, trade_size_usd: float, max_price_impact_bps: float = 300, bypass_gate: bool = False) -> dict:
    chain = token.get("chain") or chains_mod.DEFAULT_CHAIN
    live_cfg = _live_config(chain)["live"]
    weth, v3_router, v2_router = live_cfg["weth"], live_cfg["uniswapV3Router"], live_cfg.get("uniswapV2Router")
    status = wallet.status()
    if not status.get("connected"):
        raise RuntimeError("wallet not connected")

    eth_price_usd = eth_usd_price()
    amount_in_wei = Web3.to_wei(round(trade_size_usd / eth_price_usd, 18), "ether")

    quote = best_quote(chain, weth, token["address"], amount_in_wei)
    amount_out = quote["amountOut"]
    amount_out_minimum = amount_out - (amount_out * SLIPPAGE_BPS) // 10000

    contract = Web3().eth.contract(abi=V2_ROUTER_ABI if quote["dex"] == "v2" else ROUTER_ABI)
    if quote["dex"] == "v2":
        data = contract.encode_abi("swapExactETHForTokens", args=[
            amount_out_minimum, [Web3.to_checksum_address(weth), Web3.to_checksum_address(token["address"])],
            Web3.to_checksum_address(status["address"]), int(time.time()) + V2_DEADLINE_SECONDS,
        ])
        tx = {"to": v2_router, "data": data, "value": amount_in_wei}
    else:
        data = contract.encode_abi("exactInputSingle", args=[(
            Web3.to_checksum_address(weth), Web3.to_checksum_address(token["address"]), quote["fee"],
            Web3.to_checksum_address(status["address"]), amount_in_wei, amount_out_minimum, 0,
        )])
        tx = {"to": v3_router, "data": data, "value": amount_in_wei}

    if not bypass_gate:
        gate = require_allow(chain, tx, status["address"])
        if max_price_impact_bps is not None and gate.get("priceImpactBps") is not None and gate["priceImpactBps"] > max_price_impact_bps:
            raise RuntimeError(
                f"Seraph simulation shows {gate['priceImpactBps'] / 100:.2f}% price impact "
                f"(max allowed {max_price_impact_bps / 100:.2f}%) — thin liquidity, skipping"
            )

    tx_hash = wallet.send_transaction(tx["to"], tx["data"], hex(amount_in_wei), chain)
    receipt = _wait_for_receipt(chain, tx_hash)
    try:
        gas_usd = _gas_cost_usd(receipt, eth_price_usd)
    except Exception:
        gas_usd = 0

    try:
        decimals = _with_rpc(chain, lambda w3: w3.eth.contract(address=Web3.to_checksum_address(token["address"]), abi=ERC20_ABI).functions.decimals().call())
    except Exception:
        decimals = token.get("decimals", 18)
    qty = amount_out / (10 ** decimals)
    cost_usd = trade_size_usd + gas_usd

    return {"txHash": tx_hash, "qty": qty, "priceUsd": cost_usd / qty, "costUsd": cost_usd, "ethPriceUsd": eth_price_usd}


def live_sell(position: dict, min_net_profit_usd: float = 0, qty: float | None = None, cost_basis_usd: float | None = None, bypass_gate: bool = False) -> dict:
    chain = position.get("chain") or chains_mod.DEFAULT_CHAIN
    qty = position["qty"] if qty is None else qty
    cost_basis_usd = position["costUsd"] if cost_basis_usd is None else cost_basis_usd

    live_cfg = _live_config(chain)["live"]
    weth, v3_router, v2_router = live_cfg["weth"], live_cfg["uniswapV3Router"], live_cfg.get("uniswapV2Router")
    status = wallet.status()
    if not status.get("connected"):
        raise RuntimeError("wallet not connected")

    eth_price_usd = eth_usd_price()
    decimals = _with_rpc(chain, lambda w3: w3.eth.contract(address=Web3.to_checksum_address(position["address"]), abi=ERC20_ABI).functions.decimals().call())
    amount_in_wei = int(round(qty * (10 ** decimals)))

    # Clamp to the REAL on-chain balance right before building the swap —
    # the ledger's recorded qty can drift from what's actually received
    # (execution-time slippage at buy time), and even a dust-level
    # shortfall makes the router's exact-amount transferFrom revert with
    # "STF" (see live.js's identical comment — this is the fix for a real
    # bug hit in production).
    real_balance_wei = _with_rpc(chain, lambda w3: w3.eth.contract(address=Web3.to_checksum_address(position["address"]), abi=ERC20_ABI)
                                  .functions.balanceOf(Web3.to_checksum_address(status["address"])).call())
    if amount_in_wei > real_balance_wei:
        amount_in_wei = real_balance_wei
    if amount_in_wei <= 0:
        raise RuntimeError(f"no {position['symbol']} balance in wallet to sell (on-chain balance is 0)")

    quote = best_quote(chain, position["address"], weth, amount_in_wei)
    amount_out, gas_estimate = quote["amountOut"], quote.get("gasEstimate")
    amount_out_minimum = amount_out - (amount_out * SLIPPAGE_BPS) // 10000

    spender = v2_router if quote["dex"] == "v2" else v3_router
    contract = Web3().eth.contract(abi=V2_ROUTER_ABI if quote["dex"] == "v2" else ROUTER_ABI)
    if quote["dex"] == "v2":
        data = contract.encode_abi("swapExactTokensForETH", args=[
            amount_in_wei, amount_out_minimum, [Web3.to_checksum_address(position["address"]), Web3.to_checksum_address(weth)],
            Web3.to_checksum_address(status["address"]), int(time.time()) + V2_DEADLINE_SECONDS,
        ])
        tx = {"to": v2_router, "data": data, "value": "0"}
    else:
        data = contract.encode_abi("exactInputSingle", args=[(
            Web3.to_checksum_address(position["address"]), Web3.to_checksum_address(weth), quote["fee"],
            Web3.to_checksum_address(status["address"]), amount_in_wei, amount_out_minimum, 0,
        )])
        tx = {"to": v3_router, "data": data, "value": "0"}

    # Gate + profit check BEFORE the approval tx below — approving costs
    # real gas/signature, so it must not fire unless the swap itself is
    # already known-good.
    if not bypass_gate:
        gate = require_allow(chain, tx, status["address"])
        if gate.get("expectedAmountOut") is not None and min_net_profit_usd is not None:
            simulated_proceeds_usd = float(Web3.from_wei(gate["expectedAmountOut"], "ether")) * eth_price_usd
            gas_units = gas_estimate if gas_estimate is not None else 200000
            gas_price_wei = gate.get("maxFeePerGasWei") or 0
            est_gas_usd = float(Web3.from_wei(gas_units * gas_price_wei, "ether")) * eth_price_usd if gas_price_wei else 0
            net_profit_usd = simulated_proceeds_usd - est_gas_usd - cost_basis_usd
            if net_profit_usd < min_net_profit_usd:
                raise RuntimeError(
                    f"Seraph simulation shows only ${net_profit_usd:.2f} net after gas "
                    f"(need >= ${min_net_profit_usd}) — skipping, will re-check next cycle. "
                    f"To sell anyway at a loss, use: sell {position['symbol']} force"
                )

    _ensure_allowance(chain, position["address"], status["address"], spender, amount_in_wei)

    tx_hash = wallet.send_transaction(tx["to"], tx["data"], "0x0", chain)
    receipt = _wait_for_receipt(chain, tx_hash)
    try:
        gas_usd = _gas_cost_usd(receipt, eth_price_usd)
    except Exception:
        gas_usd = 0

    proceeds_usd = float(Web3.from_wei(amount_out, "ether")) * eth_price_usd - gas_usd
    return {"txHash": tx_hash, "proceedsUsd": proceeds_usd, "ethPriceUsd": eth_price_usd}


# Conservative gas estimate for WETH9's withdraw() — a single storage
# write plus a native ETH transfer, consistently well under this on every
# chain it's been observed on. Used only to decide whether auto-unwrap is
# worth it; the real send_transaction() still does its own live estimate_gas.
UNWRAP_GAS_ESTIMATE = 45000

# Auto-unwrap requires the WETH being unwrapped to be worth more than this
# multiple of its own estimated gas cost, not just barely more — gas price
# and ETH's own price can each move between this check and the tx actually
# landing, so breaking exactly even here could easily net negative in
# practice.
AUTO_UNWRAP_MARGIN = 1.3


def estimate_auto_unwrap(chain: str | None = None) -> dict:
    """Read-only (sends nothing): is the wallet's current WETH balance on
    `chain` worth unwrapping right now, i.e. worth clearly more than its own
    gas cost? Used to decide whether to auto-unwrap after a live sell."""
    chain = chain or chains_mod.DEFAULT_CHAIN
    live_cfg = _live_config(chain)["live"]
    weth = live_cfg["weth"]
    status = wallet.status()
    if not status.get("connected"):
        return {"worthIt": False, "reason": "wallet not connected"}
    owner = Web3.to_checksum_address(status["address"])
    amount_wei = _with_rpc(chain, lambda w3: w3.eth.contract(address=Web3.to_checksum_address(weth), abi=WETH_ABI)
                            .functions.balanceOf(owner).call())
    if amount_wei <= 0:
        return {"worthIt": False, "reason": "no WETH balance", "amountWei": 0}
    gas_price_wei = _with_rpc(chain, lambda w3: w3.eth.gas_price)
    gas_cost_wei = UNWRAP_GAS_ESTIMATE * gas_price_wei
    # WETH:ETH is always exactly 1:1 (that's the entire point of the wrapper
    # contract), so comparing wei amounts directly needs no price lookup.
    worth_it = amount_wei > gas_cost_wei * AUTO_UNWRAP_MARGIN
    return {
        "worthIt": worth_it, "amountWei": amount_wei,
        "amountEth": float(Web3.from_wei(amount_wei, "ether")),
        "gasCostEth": float(Web3.from_wei(gas_cost_wei, "ether")),
    }


def unwrap_weth(chain: str | None = None, amount_wei: int | None = None) -> dict:
    """Converts WETH back to native ETH via WETH9's own withdraw() — a
    bare, non-swap, non-multicall call, so it needs no Seraph gate at all
    (nothing here is a trade). Defaults to the wallet's entire WETH
    balance on `chain` if amount_wei isn't given."""
    chain = chain or chains_mod.DEFAULT_CHAIN
    live_cfg = _live_config(chain)["live"]
    weth = live_cfg["weth"]
    status = wallet.status()
    if not status.get("connected"):
        raise RuntimeError("wallet not connected")
    owner = Web3.to_checksum_address(status["address"])

    if amount_wei is None:
        amount_wei = _with_rpc(chain, lambda w3: w3.eth.contract(address=Web3.to_checksum_address(weth), abi=WETH_ABI)
                                .functions.balanceOf(owner).call())
    if amount_wei <= 0:
        raise RuntimeError(f"no WETH balance to unwrap on {chains_mod.resolve(chain)['name']}")

    contract = Web3().eth.contract(abi=WETH_ABI)
    data = contract.encode_abi("withdraw", args=[amount_wei])
    tx_hash = wallet.send_transaction(weth, data, "0x0", chain)
    _wait_for_receipt(chain, tx_hash)
    return {"txHash": tx_hash, "amountEth": float(Web3.from_wei(amount_wei, "ether"))}
