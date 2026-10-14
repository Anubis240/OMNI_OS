"""Market data via GeckoTerminal's public API. Port of marketdata.js — see
that file's header comment for the base/quote pool-side ambiguity note
(why base_token_price_usd can silently be the WRONG token's price)."""

from __future__ import annotations

import logging
import time

import requests

from . import chains as chains_mod

API = "https://api.geckoterminal.com/api/v2"

log = logging.getLogger(__name__)


def get_json(url: str, attempt: int = 1) -> dict:
    res = requests.get(url, headers={"accept": "application/json"}, timeout=30)
    if res.status_code == 429 and attempt <= 3:
        time.sleep(15 * attempt)
        return get_json(url, attempt + 1)
    res.raise_for_status()
    return res.json()


# Stablecoins per GeckoTerminal network (EIP-55 form; tests pin every entry
# and check the checksums). Only addresses known for certain are listed.
_STABLECOINS = {
    "eth": (
        "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC
        "0xdAC17F958D2ee523a2206206994597C13D831ec7",  # USDT
        "0x6B175474E89094C44Da98b954EedeAC495271d0F",  # DAI
    ),
    "base": (
        "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # USDC
        "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA",  # USDbC
        "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",  # DAI
    ),
    "arbitrum": (
        "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC
        "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8",  # USDC.e
        "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",  # USDT
    ),
    "optimism": (
        "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",  # USDC
    ),
}


def _build_quote_assets() -> dict[str, frozenset[str]]:
    """Quote assets per GeckoTerminal network: every configured chain's
    wrapped native (chains.py "live"."weth") plus _STABLECOINS. When a pool
    address is passed where a token belongs, a side listed here is what the
    token is priced AGAINST, not the token the caller meant. A network with
    no entry never auto-resolves a pool (see _resolve_pool)."""
    table: dict[str, set[str]] = {}
    for chain in chains_mod.CHAINS.values():
        wrapped = (chain.get("live") or {}).get("weth")
        if wrapped:
            table.setdefault(chain["geckoNetwork"], set()).add(wrapped.lower())
    for network, addresses in _STABLECOINS.items():
        table.setdefault(network, set()).update(a.lower() for a in addresses)
    return {network: frozenset(addresses) for network, addresses in table.items()}


QUOTE_ASSETS = _build_quote_assets()

# A pool side whose GeckoTerminal symbol is one of these also counts as a
# quote asset, covering stablecoins not in _STABLECOINS (for example USDC on
# Unichain). This can only turn a resolution into a refusal, never pick one
# of these tokens.
_QUOTE_SYMBOLS = frozenset({"ETH", "WETH", "USDC", "USDC.E", "USDBC", "USDT", "USDT0", "DAI"})


class PoolAddressError(RuntimeError):
    """A pool/pair address was given where a token address belongs, and it
    can't (or, with allow_pool=False, mustn't) be used as that token."""


def short_address(address: str | None) -> str:
    if not address or len(address) <= 12:
        return address or "?"
    return f"{address[:6]}…{address[-4:]}"


def network_name(network: str) -> str:
    """Human chain name for a GeckoTerminal network id ("eth" -> "Ethereum")."""
    return next((c["name"] for c in chains_mod.CHAINS.values() if c["geckoNetwork"] == network), network)


def resolution_note(resolution: dict) -> str:
    """"(pool 0x79dd…fe72 → token KNDX 0x7ca5…efc1)" for replies and feed lines."""
    symbol = f"{resolution['symbol']} " if resolution.get("symbol") else ""
    return f"(pool {short_address(resolution['pool'])} → token {symbol}{short_address(resolution['token'])})"


def _http_status(err: requests.HTTPError) -> int | None:
    return getattr(err.response, "status_code", None)


def _strip_id(raw, network: str) -> str | None:
    if not isinstance(raw, str) or not raw:
        return None
    prefix = f"{network}_"
    return (raw[len(prefix):] if raw.startswith(prefix) else raw).lower()


def _token_id(rel: dict, key: str, network: str) -> str | None:
    return _strip_id(((rel.get(key) or {}).get("data") or {}).get("id"), network)


def _included_symbols(data: dict, network: str) -> dict[str, str]:
    """token address -> symbol, from the `included` tokens of a pool response."""
    symbols = {}
    for item in data.get("included") or []:
        if not isinstance(item, dict):
            continue
        attrs = item.get("attributes") or {}
        address = _strip_id(item.get("id"), network) or _strip_id(attrs.get("address"), network)
        if address and isinstance(attrs.get("symbol"), str) and attrs["symbol"]:
            symbols[address] = attrs["symbol"]
    return symbols


def _resolve_pool(address: str, network: str) -> dict:
    """`address` 404'd as a token. Check (once) whether it's a pool/pair
    address and, if exactly one side is a non-quote token, return
    {"pool", "token", "symbol", "side", "pair"} for that side. Raises
    PoolAddressError for a pool that can't be resolved safely, and
    RuntimeError when GeckoTerminal knows neither a token nor a pool."""
    name = network_name(network)
    pool = address.lower()
    try:
        data = get_json(f"{API}/networks/{network}/pools/{address}?include=base_token,quote_token")
    except requests.HTTPError as err:
        if _http_status(err) == 404:
            raise RuntimeError(f"{pool} was not found on GeckoTerminal ({name}) as a token or a pool — check the "
                               f"address and chain (a new token may not be indexed yet)") from None
        raise
    rel = ((data.get("data") or {}).get("relationships")) or {}
    base = _token_id(rel, "base_token", network)
    quote = _token_id(rel, "quote_token", network)
    symbols = _included_symbols(data, network)
    pair = f"{symbols.get(base) or '?'}/{symbols.get(quote) or '?'}"
    is_pool = f"{pool} is a pool, not a token, on {name}"
    quote_assets = QUOTE_ASSETS.get(network)
    if not quote_assets or not base or not quote:
        raise PoolAddressError(f"{is_pool}; use the token address (pool pair: {pair})")

    def is_quote(token: str) -> bool:
        return token in quote_assets or (symbols.get(token) or "").upper() in _QUOTE_SYMBOLS

    candidates = [(side, token) for side, token in (("base", base), ("quote", quote)) if not is_quote(token)]
    if len(candidates) == 2:
        raise PoolAddressError(f"{is_pool}, and neither side is a known quote asset (pool pair: {pair}), so the "
                               f"token you meant is ambiguous; use the token address")
    if not candidates:
        raise PoolAddressError(f"{is_pool}; use the token address (pool pair: {pair})")
    side, token = candidates[0]
    return {"pool": pool, "token": token, "symbol": symbols.get(token), "side": side, "pair": pair}


def ranked_pools(address: str, network: str | None = None, *, allow_pool: bool = True) -> list[dict]:
    """The token's pools, most liquid first. A pool/pair address is resolved
    to its token (every entry then carries "resolution"), unless allow_pool
    is False, in which case it raises PoolAddressError."""
    network = network or chains_mod.CHAINS[chains_mod.DEFAULT_CHAIN]["geckoNetwork"]
    resolution = None
    try:
        data = get_json(f"{API}/networks/{network}/tokens/{address}/pools?page=1")
    except requests.HTTPError as err:
        # Only a 404 means "not a token here"; 429/5xx/network errors keep
        # their existing behavior (no fallback, no extra requests).
        if _http_status(err) != 404:
            raise
        resolution = _resolve_pool(address, network)
        symbol = f"{resolution['symbol']} " if resolution["symbol"] else ""
        if not allow_pool:
            raise PoolAddressError(f"{resolution['pool']} is a pool, not a token, on {network_name(network)}; "
                                   f"use the token address ({symbol}{resolution['token']})")
        log.info("%s is a pool on %s; using its %s token %s%s", resolution["pool"], network,
                 resolution["side"], symbol, resolution["token"])
        try:
            data = get_json(f"{API}/networks/{network}/tokens/{resolution['token']}/pools?page=1")
        except requests.HTTPError as err2:
            if _http_status(err2) == 404:
                raise RuntimeError(f"pool {resolution['pool']} on {network_name(network)} resolves to token "
                                   f"{symbol}{resolution['token']}, which has no GeckoTerminal pools") from None
            raise
        address = resolution["token"]
    # One casing for every token address this module hands out (the ledger,
    # risk check and swap all take it from here; live.py checksums it).
    token_address = address.lower()
    wanted = f"{network}_{address.lower()}"
    pools = []
    for p in data.get("data", []):
        at = p.get("attributes", {})
        rel = p.get("relationships") or {}
        base_id = ((rel.get("base_token") or {}).get("data") or {}).get("id")
        quote_id = ((rel.get("quote_token") or {}).get("data") or {}).get("id")
        # GEMZ4US, 2026-09-18 (Part 3/B): a PAPER FORCE BUY entered at
        # roughly double the real market price, exactly the failure mode
        # this module's own header comment already warns about — using
        # the wrong token's price from a pool. The old logic assumed
        # "quote" whenever base_id didn't match `wanted`, with no check
        # that quote_id matched anything either — any mismatch (a
        # different address-casing convention, a relationship field
        # GeckoTerminal omits for a given pool, an unrelated listing)
        # silently returned a confidently-wrong price instead of an
        # error. Now requires an actual match on one side or the other,
        # skipping the pool rather than guessing when neither matches.
        if base_id == wanted:
            side = "base"
        elif quote_id == wanted:
            side = "quote"
        else:
            continue
        price = at.get("base_token_price_usd") if side == "base" else at.get("quote_token_price_usd")
        entry = {
            "poolAddress": at.get("address"),
            "tokenAddress": token_address,
            "priceUsd": float(price or 0),
            "priceSide": side,
            "liquidityUsd": float(at.get("reserve_in_usd") or 0),
            "change24hPct": float((at.get("price_change_percentage") or {}).get("h24") or 0),
            "volume24hUsd": float((at.get("volume_usd") or {}).get("h24") or 0),
        }
        if resolution:
            entry["resolution"] = resolution
        pools.append(entry)
    pools.sort(key=lambda p: p["liquidityUsd"], reverse=True)
    if not pools:
        raise RuntimeError(f"no pools for {address}")
    return pools


def ohlcv(pool_address: str, network: str, side: str, timeframe: str = "hour", limit: int = 100) -> list[dict]:
    data = get_json(f"{API}/networks/{network}/pools/{pool_address}/ohlcv/{timeframe}?limit={limit}&token={side}")
    raw = ((data.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    candles = [
        {"ts": ts, "open": float(o), "high": float(h), "low": float(lo), "close": float(c), "volume": float(v)}
        for ts, o, h, lo, c, v in raw
    ]
    candles.sort(key=lambda c: c["ts"])
    return candles


def snapshot(token: dict, candle_timeframe: str, candle_limit: int) -> dict:
    chain_info = chains_mod.CHAINS.get(token.get("chain") or chains_mod.DEFAULT_CHAIN)
    network = (chain_info or chains_mod.CHAINS[chains_mod.DEFAULT_CHAIN])["geckoNetwork"]
    pools = ranked_pools(token["address"], network)
    last_count = 0
    for pool in pools[:3]:
        time.sleep(2.5)
        candles = ohlcv(pool["poolAddress"], network, pool["priceSide"], candle_timeframe, candle_limit)
        if len(candles) >= 30:
            return {**token, **pool, "address": pool["tokenAddress"], "candles": candles}
        last_count = max(last_count, len(candles))
    raise RuntimeError(f"no pool with enough history for {token.get('symbol')} (best: {last_count} candles)")


def current_quote(address: str, chain: str | None, *, allow_pool: bool = True) -> dict:
    """{"priceUsd", "address", "resolution"}: `address` is the TOKEN address
    (lowercase). When a pool/pair address was passed it differs from the
    input and `resolution` describes the pool → token step (else None)."""
    chain_info = chains_mod.CHAINS.get(chain or chains_mod.DEFAULT_CHAIN)
    network = (chain_info or chains_mod.CHAINS[chains_mod.DEFAULT_CHAIN])["geckoNetwork"]
    top = ranked_pools(address, network, allow_pool=allow_pool)[0]
    return {"priceUsd": top["priceUsd"], "address": top["tokenAddress"], "resolution": top.get("resolution")}


def current_price(address: str, chain: str | None, *, allow_pool: bool = True) -> float:
    return current_quote(address, chain, allow_pool=allow_pool)["priceUsd"]
