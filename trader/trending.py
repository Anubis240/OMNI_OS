"""GeckoTerminal trending pools — discovery signal only, port of
trending.js. Every candidate still goes through analyze() and the Seraph
gate like anything else."""

from __future__ import annotations

import re

from . import chains as chains_mod
from .marketdata import get_json, API

MIN_LIQUIDITY_USD = 100000

MAJOR_SYMBOLS = {
    "WETH", "ETH", "WBTC", "BTC", "WMATIC", "MATIC", "POL", "WPOL",
    "USDC", "USDC.E", "USDT", "DAI",
}
STABLE_SYMBOLS = {
    "USDC", "USDC.E", "USDT", "DAI", "USDS", "FRAX", "TUSD", "USDP", "GUSD",
    "LUSD", "USDE", "PYUSD", "FDUSD", "CRVUSD", "SUSDE",
}
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_FEE_TIER_RE = re.compile(r"\s*\d+(\.\d+)?%$")


def _parse_symbol(name: str | None, side: str) -> str:
    parts = [s.strip() for s in (name or "").split("/")]
    raw = parts[0] if side == "base" and parts else (parts[1] if side != "base" and len(parts) > 1 else "")
    return _FEE_TIER_RE.sub("", raw or "").strip().upper()


def discover(chain_key: str = chains_mod.DEFAULT_CHAIN, limit: int = 5, min_liquidity_usd: float = MIN_LIQUIDITY_USD) -> list[dict]:
    network = chains_mod.CHAINS.get(chain_key, chains_mod.CHAINS[chains_mod.DEFAULT_CHAIN])["geckoNetwork"]
    data = get_json(f"{API}/networks/{network}/trending_pools?page=1")
    pools = data.get("data", [])
    seen = set()
    candidates = []

    for p in pools:
        at = p.get("attributes", {})
        rel = p.get("relationships", {})
        base_addr = ((rel.get("base_token") or {}).get("data") or {}).get("id", "").replace(f"{network}_", "")
        quote_addr = ((rel.get("quote_token") or {}).get("data") or {}).get("id", "").replace(f"{network}_", "")

        base_symbol = _parse_symbol(at.get("name"), "base")
        quote_symbol = _parse_symbol(at.get("name"), "quote")
        base_is_major = base_symbol in MAJOR_SYMBOLS
        quote_is_major = quote_symbol in MAJOR_SYMBOLS
        if base_is_major == quote_is_major:
            continue

        address = quote_addr if base_is_major else base_addr
        symbol = quote_symbol if base_is_major else base_symbol
        if not _ADDR_RE.match(address or ""):
            continue
        addr_lower = address.lower()
        if addr_lower == ZERO_ADDRESS or addr_lower in seen:
            continue

        liquidity_usd = float(at.get("reserve_in_usd") or 0)
        pct = at.get("price_change_percentage") or {}
        chg1h = float(pct.get("h1") or 0)
        chg30m = float(pct.get("m30") or 0)
        if liquidity_usd < min_liquidity_usd:
            continue
        if chg1h <= 0 and chg30m <= 0:
            continue
        if symbol in STABLE_SYMBOLS:
            continue

        seen.add(addr_lower)
        candidates.append({"symbol": symbol, "address": address, "chain": chain_key, "chg1h": chg1h, "liquidityUsd": liquidity_usd})

    candidates.sort(key=lambda c: c["chg1h"], reverse=True)
    return candidates[:limit]
