"""Meme-coin discovery — opt-in, off by default. Port of memecoins.js. See
that file's header comment for why this is a separate, explicitly-enabled
category rather than folded into trending.py's general discovery."""

from __future__ import annotations

import re
import time

import requests

CG_API = "https://api.coingecko.com/api/v3"
GT_API = "https://api.geckoterminal.com/api/v2"
NETWORK = "eth"

NEW_POOL_MIN_LIQUIDITY_USD = 15000
CG_REQUEST_DELAY_SECONDS = 1.2

MAJORS = {
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "0xdac17f958d2ee523a2206206994597c13d831ec7",
    "0x6b175474e89094c44da98b954eedeac495271d0f",
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",
}
STABLE_SYMBOLS = {
    "USDC", "USDT", "DAI", "USDS", "FRAX", "TUSD", "USDP", "GUSD", "LUSD",
    "USDE", "PYUSD", "FDUSD", "CRVUSD", "SUSDE",
}
_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_FEE_TIER_RE = re.compile(r"\s*\d+(\.\d+)?%$")


def get_json(url: str, headers: dict, attempt: int = 1) -> dict:
    res = requests.get(url, headers=headers, timeout=30)
    if res.status_code == 429 and attempt <= 2:
        time.sleep(3 * attempt)
        return get_json(url, headers, attempt + 1)
    res.raise_for_status()
    return res.json()


def discover_trending(limit: int = 5) -> list[dict]:
    headers = {"User-Agent": "Mozilla/5.0"}
    coins = get_json(
        f"{CG_API}/coins/markets?vs_currency=usd&category=meme-token&order=volume_desc&per_page=20&price_change_percentage=1h,24h",
        headers,
    )
    results = []
    for c in coins:
        if len(results) >= limit:
            break
        try:
            info = get_json(
                f"{CG_API}/coins/{c['id']}?localization=false&tickers=false&market_data=false&community_data=false&developer_data=false&sparkline=false",
                headers,
            )
            address = (info.get("platforms") or {}).get("ethereum")
            if not address or not _ADDR_RE.match(address):
                continue
            results.append({
                "category": "trending",
                "symbol": (c.get("symbol") or "").upper(),
                "address": address,
                "chg1h": c.get("price_change_percentage_1h_in_currency"),
                "chg24h": c.get("price_change_percentage_24h"),
                "volume24hUsd": c.get("total_volume") or 0,
            })
        except Exception:
            pass
        time.sleep(CG_REQUEST_DELAY_SECONDS)
    return results


def _parse_symbol(name: str | None, side: str) -> str:
    parts = [s.strip() for s in (name or "").split("/")]
    raw = parts[0] if side == "base" and parts else (parts[1] if side != "base" and len(parts) > 1 else "")
    return _FEE_TIER_RE.sub("", raw or "").strip().upper()


def discover_new(limit: int = 5) -> list[dict]:
    data = get_json(f"{GT_API}/networks/{NETWORK}/new_pools?page=1", {"accept": "application/json"})
    pools = data.get("data", [])
    seen = set()
    results = []

    for p in pools:
        at = p.get("attributes", {})
        rel = p.get("relationships", {})
        base_addr = ((rel.get("base_token") or {}).get("data") or {}).get("id", "")
        quote_addr = ((rel.get("quote_token") or {}).get("data") or {}).get("id", "")
        base_addr = re.sub(r"^eth_", "", base_addr)
        quote_addr = re.sub(r"^eth_", "", quote_addr)
        base_is_major = base_addr.lower() in MAJORS
        quote_is_major = quote_addr.lower() in MAJORS
        if base_is_major == quote_is_major:
            continue

        side = "quote" if base_is_major else "base"
        address = quote_addr if base_is_major else base_addr
        if not _ADDR_RE.match(address or "") or address.lower() in seen:
            continue

        liquidity_usd = float(at.get("reserve_in_usd") or 0)
        if liquidity_usd < NEW_POOL_MIN_LIQUIDITY_USD:
            continue

        symbol = _parse_symbol(at.get("name"), side)
        if symbol in STABLE_SYMBOLS:
            continue

        seen.add(address.lower())
        pct = at.get("price_change_percentage") or {}
        results.append({
            "category": "new", "symbol": symbol, "address": address,
            "chg1h": float(pct.get("h1") or 0), "chg24h": float(pct.get("h24") or 0),
            "liquidityUsd": liquidity_usd, "createdAt": at.get("pool_created_at"),
        })

    results.sort(key=lambda r: r.get("createdAt") or "", reverse=True)
    return results[:limit]


def discover(limit: int = 5) -> dict:
    try:
        trending = discover_trending(limit)
    except Exception:
        trending = []
    try:
        fresh = discover_new(limit)
    except Exception:
        fresh = []
    return {"trending": trending, "new": fresh}
