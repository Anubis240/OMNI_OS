#!/usr/bin/env python3
"""W4.P2 — real-money smoke test of the Seraph server-side signer on Base.

    THIS SCRIPT SPENDS REAL MONEY. Three real transactions, ~0.001 ETH
    of notional plus gas. There is no dry-run that proves what W4.P2
    needs to prove: the point is precisely that the Seraph wallet signs
    and broadcasts for real, and that the custody gate is in the path.

Deliverable W4.P2.T3 of docs/plans/2026-09-21-seraph-privy-login-oauth-desktop.md.

WHAT IT PROVES
  a) guardian_wallet_status returns the embedded-wallet address, the signer
     grant and the linked external address.
  b) Probe 18 — guardian_execute with a device key whose signer has NOT been
     authorized is refused with signer_not_granted. This phase can only be
     run BEFORE authorizing the signer in the console; see PROBE 18 below.
  c) A real buy: 0.001 ETH -> USDC through Uniswap V3 on Base, kind=swap,
     one transaction, receipt status == 1.
  d) A real sell of that USDC back to ETH: approve (kind=approve) plus swap
     (kind=swap), i.e. TWO separate gate decisions and two receipts.
  e) Real idempotency: replaying guardian_execute with the buy's own
     requestId returns the SAME txHash and broadcasts nothing new.

WHAT IT DELIBERATELY DOES NOT DO
  * It never signs anything locally. There is no private key anywhere in
    this process; trader.live cannot sign since 1.12.0.
  * It never passes bypass_gate=True. Every transaction goes through
    guardian_pretrade_check -> guardian_execute.
  * It never writes to your real Omni-OS config and never mints a key.
  * It never prints or persists the API key, and never writes a file
    inside this repository.

BEFORE YOU RUN IT
  1. Use a THROWAWAY wallet. Fund the Seraph embedded wallet with at least
     0.005 ETH on Base (0.001 trades, the rest is gas headroom for three
     transactions).
  2. Mint a device API key for that account and put it in a settings.json
     OUTSIDE this repository. Any of these shapes is accepted:
         {"mcpServers": [{"id": "seraph-kondux", "apiKey": "mcp_..."}]}
         {"seraph_mcp_api_key": "mcp_..."}
         {"apiKey": "mcp_..."}
     NEVER commit that file. It is a live credential.
  3. Run phase probe18 FIRST, while the signer is still unauthorized.
  4. Then authorize the signer in the console and run the rest.

USAGE
    python docs/plans/scripts/smoke-execute-base.py --settings C:\\tmp\\settings.json --phase probe18
    python docs/plans/scripts/smoke-execute-base.py --settings C:\\tmp\\settings.json --phase all --i-understand-this-spends-real-money

EMERGENCY ROLLBACK
    Revoke the test wallet's session signer in the console
    (removeSessionSigners), or disable the policy with the offline admin key.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from trader import chains as chains_mod  # noqa: E402
from trader import live as live_mod  # noqa: E402
from trader.mcp_client import SERAPH_MCP_URL, SERAPH_SERVER_ID, McpClient  # noqa: E402

# --------------------------------------------------------------------------
# Hard safety rails. These are not configuration; they are the blast radius.
# --------------------------------------------------------------------------
CHAIN = "base"                     # chainId 8453. Any other chain is refused.
ETH_IN = 0.001                     # notional of the buy, per the plan
ETH_IN_HARD_CAP = 0.002            # refuse any override above this
MIN_WALLET_ETH = 0.0025            # buy + three lots of gas
# Circle-issued native USDC on Base. Printed for you to verify before any
# money moves; override with --token only if you know exactly why.
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
HASH_RE = re.compile(r"0x[0-9a-fA-F]{64}")


class SmokeError(RuntimeError):
    """A smoke assertion failed. Never swallowed, never retried."""


# --------------------------------------------------------------------------
# Credential loading. The key is read into memory and never echoed.
# --------------------------------------------------------------------------
def load_api_key(path: Path) -> str:
    if not path.is_file():
        raise SmokeError(f"settings file not found: {path}")
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    if resolved.is_relative_to(REPO_ROOT):
        raise SmokeError(
            f"refusing to read a credential from inside the repository ({resolved}). "
            "Keep settings.json outside the working tree so it can never be committed."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    for server in data.get("mcpServers", []) or []:
        if server.get("id") == SERAPH_SERVER_ID and server.get("apiKey"):
            return str(server["apiKey"])
    for field in ("seraph_mcp_api_key", "apiKey", "api_key"):
        if data.get(field):
            return str(data[field])
    raise SmokeError(
        "no API key in the settings file. Expected mcpServers[] with "
        f"id={SERAPH_SERVER_ID!r}, or a seraph_mcp_api_key/apiKey field."
    )


# --------------------------------------------------------------------------
# MCP wiring. Every call is recorded so the idempotency phase can replay the
# buy's own requestId, and so the report carries an auditable trail.
# --------------------------------------------------------------------------
class RecordingMcp:
    def __init__(self, client: McpClient) -> None:
        self._client = client
        self.calls: list[dict] = []

    def __call__(self, name: str, args: dict | None = None) -> dict:
        result = self._client.call(name, args or {})
        self.calls.append({"tool": name, "args": args or {}, "result": result})
        return result

    def executes(self) -> list[dict]:
        return [c for c in self.calls if c["tool"] == "guardian_execute"]

    def last_request_id(self) -> str:
        for call in reversed(self.executes()):
            request_id = call["args"].get("requestId")
            if request_id:
                return str(request_id)
        raise SmokeError("no guardian_execute requestId was recorded")


def response_text(result: dict) -> str:
    if not isinstance(result, dict):
        return str(result)
    return str(result.get("text") or result.get("error") or json.dumps(result))


def tx_hash_from(result: dict) -> str:
    match = HASH_RE.search(response_text(result))
    if not match:
        raise SmokeError(f"no transaction hash in guardian_execute response: {response_text(result)[:200]}")
    return match.group(0)


# --------------------------------------------------------------------------
# Phases
# --------------------------------------------------------------------------
def phase_status(mcp: RecordingMcp) -> dict:
    result = mcp("guardian_wallet_status", {})
    text = response_text(result)
    try:
        status = json.loads(text)
    except Exception:
        raise SmokeError(f"guardian_wallet_status did not return JSON: {text[:200]}")
    address = status.get("address")
    if not address:
        raise SmokeError(f"guardian_wallet_status returned no wallet address: {text[:200]}")
    print(f"  address              : {address}")
    print(f"  signerGranted        : {status.get('signerGranted')}")
    print(f"  linkedExternalAddress: {status.get('linkedExternalAddress')}")
    return status


def phase_probe18(mcp: RecordingMcp, status: dict) -> dict:
    """Probe 18 — an unauthorized signer must be refused.

    This cannot be faked after the fact: once the signer is granted the
    refusal no longer happens, so the phase reports NOT_RUN rather than
    pretending to have passed. Run it before authorizing.
    """
    if status.get("signerGranted"):
        print("  NOT_RUN — the signer is already authorized for this wallet.")
        print("  Probe 18 only has meaning before authorization. To run it, either")
        print("  use a wallet whose signer was never granted, or revoke the signer")
        print("  in the console (removeSessionSigners) and re-run this phase first.")
        return {"outcome": "NOT_RUN", "reason": "signer already granted"}

    # A pretrade check is needed to obtain a requestId to execute against.
    # The swap is never broadcast: guardian_execute is expected to refuse.
    address = status["address"]
    token = {"chain": CHAIN, "address": USDC_BASE}
    try:
        live_mod.live_buy(token, ETH_IN * live_mod.eth_usd_price(), max_price_impact_bps=300)
    except Exception as err:
        message = str(err)
        if "signer_not_granted" in message:
            print(f"  PASS — refused with signer_not_granted (wallet {address}).")
            return {"outcome": "PASS", "error": message[:300]}
        raise SmokeError(
            "expected signer_not_granted while the signer is unauthorized, got: " + message[:300]
        )
    raise SmokeError("guardian_execute was NOT refused although signerGranted is false — custody gate is broken")


def phase_buy(mcp: RecordingMcp, status: dict, eth_in: float, token_address: str) -> dict:
    eth_price = live_mod.eth_usd_price()
    trade_size_usd = eth_in * eth_price
    print(f"  ETH/USD              : {eth_price:.2f}")
    print(f"  buying               : {eth_in} ETH (~${trade_size_usd:.2f}) of {token_address}")

    balance_usd = live_mod.wallet_eth_balance_usd(CHAIN, status["address"], eth_price)
    balance_eth = balance_usd / eth_price if eth_price else 0.0
    print(f"  wallet ETH on Base   : {balance_eth:.6f} ETH (~${balance_usd:.2f})")
    if balance_eth < MIN_WALLET_ETH:
        raise SmokeError(
            f"wallet holds {balance_eth:.6f} ETH but needs at least {MIN_WALLET_ETH} ETH "
            "to cover the trade plus gas for three transactions"
        )

    before = len(mcp.executes())
    result = live_mod.live_buy({"chain": CHAIN, "address": token_address}, trade_size_usd,
                              max_price_impact_bps=300)
    executed = mcp.executes()[before:]
    if len(executed) != 1:
        raise SmokeError(f"expected exactly 1 guardian_execute for the buy, saw {len(executed)}")

    tx_hash = result["txHash"]
    request_id = executed[0]["args"].get("requestId")
    print(f"  dex                  : {result.get('dex')}")
    print(f"  priceImpactBps       : {result.get('priceImpactBps')}")
    print(f"  txHash               : {tx_hash}")
    print(f"  requestId            : {request_id}")

    receipt = live_mod._wait_for_receipt(CHAIN, tx_hash)
    if int(receipt.get("status", 0)) != 1:
        raise SmokeError(f"buy receipt status is {receipt.get('status')}, expected 1 — tx {tx_hash}")
    print(f"  receipt.status       : 1  (block {receipt.get('blockNumber')})")
    print(f"  qty received         : {result['qty']}")
    return {"outcome": "PASS", "txHash": tx_hash, "requestId": request_id,
            "qty": result["qty"], "costUsd": result["costUsd"], "dex": result.get("dex"),
            "priceImpactBps": result.get("priceImpactBps")}


def phase_sell(mcp: RecordingMcp, buy: dict, token_address: str) -> dict:
    position = {"chain": CHAIN, "address": token_address, "symbol": "USDC",
                "qty": buy["qty"], "costUsd": buy["costUsd"]}
    before = len(mcp.executes())
    # min_net_profit_usd=None disables ONLY the local pre-flight profit floor
    # (live.py:661 guards it with `is not None`). An immediate round trip is a
    # small, expected loss, so a floor of 0 would refuse. This does NOT touch
    # the transaction gate: require_allow still runs for the approve and for
    # the swap. bypass_gate stays False, deliberately.
    result = live_mod.live_sell(position, min_net_profit_usd=None)
    executed = mcp.executes()[before:]
    kinds = [c["args"].get("kind") for c in executed]
    if len(executed) != 2:
        raise SmokeError(
            f"expected 2 guardian_execute calls for the sell (approve + swap), saw {len(executed)}: {kinds}"
        )

    hashes = []
    for index, call in enumerate(executed):
        tx_hash = tx_hash_from(call["result"])
        receipt = live_mod._wait_for_receipt(CHAIN, tx_hash)
        if int(receipt.get("status", 0)) != 1:
            raise SmokeError(f"sell leg {index + 1} receipt status is {receipt.get('status')} — tx {tx_hash}")
        print(f"  leg {index + 1} txHash         : {tx_hash}  (status 1, block {receipt.get('blockNumber')})")
        hashes.append(tx_hash)

    print(f"  proceeds             : {result.get('proceedsUsd', result.get('netUsd', 'n/a'))}")
    return {"outcome": "PASS", "txHashes": hashes, "result": {k: v for k, v in result.items()
                                                             if isinstance(v, (str, int, float, bool, type(None)))}}


def phase_idempotency(mcp: RecordingMcp, buy: dict) -> dict:
    """Replaying the buy's requestId must return the same hash, not a new tx."""
    request_id = buy.get("requestId") or mcp.last_request_id()
    print(f"  replaying requestId  : {request_id}")
    result = mcp("guardian_execute", {"requestId": request_id})
    replayed = tx_hash_from(result)
    if replayed.lower() != str(buy["txHash"]).lower():
        raise SmokeError(
            f"idempotency broken: replay returned {replayed} but the buy was {buy['txHash']}"
        )
    print(f"  replayed txHash      : {replayed}  == buy txHash  (no new transaction)")
    return {"outcome": "PASS", "requestId": request_id, "txHash": replayed}


# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--settings", required=True, type=Path,
                        help="path to a settings.json OUTSIDE this repo holding the test device API key")
    parser.add_argument("--phase", default="status",
                        choices=("status", "probe18", "buy", "sell", "idempotency", "trade", "all"),
                        help="'trade' = buy+sell+idempotency; 'all' = every phase")
    parser.add_argument("--eth", type=float, default=ETH_IN, help=f"ETH notional of the buy (cap {ETH_IN_HARD_CAP})")
    parser.add_argument("--token", default=USDC_BASE, help="token to round-trip into (default: native USDC on Base)")
    parser.add_argument("--url", default=SERAPH_MCP_URL, help="MCP endpoint override")
    parser.add_argument("--report", type=Path, help="write the JSON report here (must be outside the repo)")
    parser.add_argument("--i-understand-this-spends-real-money", action="store_true", dest="confirmed",
                        help="required for any phase that broadcasts a transaction")
    args = parser.parse_args(argv)

    if args.eth > ETH_IN_HARD_CAP:
        print(f"refusing --eth {args.eth}: the hard cap is {ETH_IN_HARD_CAP} ETH", file=sys.stderr)
        return 2
    if args.eth <= 0:
        print("refusing a non-positive --eth", file=sys.stderr)
        return 2
    if args.report is not None:
        try:
            resolved_report = args.report.resolve()
        except OSError:
            resolved_report = args.report
        if resolved_report.is_relative_to(REPO_ROOT):
            print(f"refusing to write the report inside the repository ({resolved_report})", file=sys.stderr)
            return 2

    spends = args.phase in ("buy", "sell", "idempotency", "trade", "all")
    if spends and not args.confirmed:
        print("refusing to broadcast: pass --i-understand-this-spends-real-money", file=sys.stderr)
        return 2

    if chains_mod.resolve(CHAIN).get("chainId") != 8453:
        print(f"refusing: chain {CHAIN!r} does not resolve to Base (8453)", file=sys.stderr)
        return 2

    try:
        api_key = load_api_key(args.settings)
    except SmokeError as err:
        print(f"credential error: {err}", file=sys.stderr)
        return 2

    client = McpClient(url=args.url, api_key=api_key)
    mcp = RecordingMcp(client)
    live_mod.init(mcp_call=mcp, server_id=SERAPH_SERVER_ID)

    report: dict = {"chain": CHAIN, "chainId": 8453, "token": args.token, "ethIn": args.eth,
                    "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "phases": {}}
    wanted = {"all": ("status", "probe18", "buy", "sell", "idempotency"),
              "trade": ("status", "buy", "sell", "idempotency")}.get(args.phase, ("status", args.phase))
    wanted = tuple(dict.fromkeys(wanted))

    try:
        print("\n== status ==")
        status = phase_status(mcp)
        report["phases"]["status"] = {"outcome": "PASS", **{k: status.get(k) for k in
                                                            ("address", "signerGranted", "linkedExternalAddress")}}
        # trader.live reads the wallet through this provider; point it at the
        # status we just fetched so nothing re-derives custody state locally.
        live_mod.set_wallet_status_provider(lambda: {
            "connected": bool(status.get("address")),
            "address": status.get("address"),
            "signerGranted": bool(status.get("signerGranted")),
        })

        if "probe18" in wanted:
            print("\n== probe 18: unauthorized signer is refused ==")
            report["phases"]["probe18"] = phase_probe18(mcp, status)

        if {"buy", "sell", "idempotency"} & set(wanted) and not status.get("signerGranted"):
            raise SmokeError("signerGranted is false — authorize the signer in the console before trading")

        buy = None
        if "buy" in wanted:
            print(f"\n== buy {args.eth} ETH -> {args.token} ==")
            buy = phase_buy(mcp, status, args.eth, args.token)
            report["phases"]["buy"] = buy

        if "sell" in wanted:
            if buy is None:
                raise SmokeError("the sell phase needs the buy's qty/costUsd — run --phase trade or all")
            print("\n== sell back to ETH (approve + swap = two gates) ==")
            report["phases"]["sell"] = phase_sell(mcp, buy, args.token)

        if "idempotency" in wanted:
            if buy is None:
                raise SmokeError("the idempotency phase needs the buy's requestId — run --phase trade or all")
            print("\n== idempotency: replay the buy's requestId ==")
            report["phases"]["idempotency"] = phase_idempotency(mcp, buy)

        report["outcome"] = "PASS"
    except SmokeError as err:
        report["outcome"] = "FAIL"
        report["error"] = str(err)
        print(f"\nSMOKE FAILED: {err}", file=sys.stderr)
    except Exception as err:  # noqa: BLE001 — a smoke run must always report
        report["outcome"] = "ERROR"
        report["error"] = f"{type(err).__name__}: {err}"
        print(f"\nSMOKE ERROR: {type(err).__name__}: {err}", file=sys.stderr)

    report["toolCalls"] = [{"tool": c["tool"], "args": c["args"]} for c in mcp.calls]
    hashes = sorted({h for c in mcp.calls for h in HASH_RE.findall(response_text(c["result"]))})
    report["transactionHashes"] = hashes

    print("\n== summary ==")
    print(f"  outcome              : {report['outcome']}")
    for name, phase in report["phases"].items():
        print(f"  {name:20s}: {phase.get('outcome')}")
    print(f"  public tx hashes     : {len(hashes)}")
    for tx_hash in hashes:
        print(f"    https://basescan.org/tx/{tx_hash}")
    print("\n  Now verify in the console that the test key is still listed and")
    print("  that wallet_daily_spend accumulated for this wallet.")

    if args.report is not None:
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"  report written       : {args.report}")

    return 0 if report["outcome"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
