"""DexScreener volume-spike alerts — port of dexscreener.js. See that
file's header comment for the honest limitation: this only catches spikes
on already-boosted/promoted tokens, not the whole market."""

from __future__ import annotations

import time

import requests

API = "https://api.dexscreener.com"
MIN_LIQUIDITY_USD = 100000
BOOST_CANDIDATES_TO_CHECK = 20


def get_json(url: str, attempt: int = 1) -> dict:
    res = requests.get(url, headers={"accept": "application/json"}, timeout=30)
    if res.status_code == 429 and attempt <= 3:
        time.sleep(3 * attempt)
        return get_json(url, attempt + 1)
    res.raise_for_status()
    return res.json()


def check_spike(chain_id: str, token_address: str, min_ratio: float = 3, min_liquidity_usd: float = MIN_LIQUIDITY_USD) -> dict | None:
    pairs = get_json(f"{API}/token-pairs/v1/{chain_id}/{token_address}")
    if not isinstance(pairs, list) or not pairs:
        return None
    best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)

    liquidity_usd = (best.get("liquidity") or {}).get("usd") or 0
    if liquidity_usd < min_liquidity_usd:
        return None

    vol = best.get("volume") or {}
    avg_hourly_from_day = (vol.get("h24") or 0) / 24
    if avg_hourly_from_day <= 0:
        return None
    m5_rate = (vol.get("m5") or 0) * 12
    h1 = vol.get("h1") or 0
    ratio = max(m5_rate, h1) / avg_hourly_from_day
    if ratio < min_ratio:
        return None

    is_base = ((best.get("baseToken") or {}).get("address") or "").lower() == token_address.lower()
    symbol = (best.get("baseToken") if is_base else best.get("quoteToken") or {}).get("symbol", "?")

    return {
        "chainId": chain_id, "address": token_address, "symbol": symbol, "ratio": ratio,
        "volume1hUsd": h1, "liquidityUsd": liquidity_usd,
        "priceChange1hPct": (best.get("priceChange") or {}).get("h1"),
    }


def discover_spikes(limit: int = 5, min_ratio: float = 3, min_liquidity_usd: float = MIN_LIQUIDITY_USD) -> list[dict]:
    boosts = get_json(f"{API}/token-boosts/top/v1")
    seen = set()
    results = []
    for b in boosts[:BOOST_CANDIDATES_TO_CHECK]:
        token_address = b.get("tokenAddress")
        key = f"{b.get('chainId')}:{(token_address or '').lower()}"
        if not token_address or key in seen:
            continue
        seen.add(key)
        try:
            spike = check_spike(b.get("chainId"), token_address, min_ratio, min_liquidity_usd)
            if spike:
                results.append(spike)
        except Exception:
            pass
        time.sleep(1.1)
    results.sort(key=lambda s: s["ratio"], reverse=True)
    return results[:limit]
