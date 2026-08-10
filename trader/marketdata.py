"""Market data via GeckoTerminal's public API. Port of marketdata.js — see
that file's header comment for the base/quote pool-side ambiguity note
(why base_token_price_usd can silently be the WRONG token's price)."""

from __future__ import annotations

import time

import requests

from . import chains as chains_mod

API = "https://api.geckoterminal.com/api/v2"


def get_json(url: str, attempt: int = 1) -> dict:
    res = requests.get(url, headers={"accept": "application/json"}, timeout=30)
    if res.status_code == 429 and attempt <= 3:
        time.sleep(15 * attempt)
        return get_json(url, attempt + 1)
    res.raise_for_status()
    return res.json()


def ranked_pools(address: str, network: str | None = None) -> list[dict]:
    network = network or chains_mod.CHAINS[chains_mod.DEFAULT_CHAIN]["geckoNetwork"]
    data = get_json(f"{API}/networks/{network}/tokens/{address}/pools?page=1")
    wanted = f"{network}_{address.lower()}"
    pools = []
    for p in data.get("data", []):
        at = p.get("attributes", {})
        base_id = ((p.get("relationships") or {}).get("base_token") or {}).get("data", {}).get("id")
        side = "base" if base_id == wanted else "quote"
        price = at.get("base_token_price_usd") if side == "base" else at.get("quote_token_price_usd")
        pools.append({
            "poolAddress": at.get("address"),
            "priceUsd": float(price or 0),
            "priceSide": side,
            "liquidityUsd": float(at.get("reserve_in_usd") or 0),
            "change24hPct": float((at.get("price_change_percentage") or {}).get("h24") or 0),
            "volume24hUsd": float((at.get("volume_usd") or {}).get("h24") or 0),
        })
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
            return {**token, **pool, "candles": candles}
        last_count = max(last_count, len(candles))
    raise RuntimeError(f"no pool with enough history for {token.get('symbol')} (best: {last_count} candles)")


def current_price(address: str, chain: str | None) -> float:
    chain_info = chains_mod.CHAINS.get(chain or chains_mod.DEFAULT_CHAIN)
    network = (chain_info or chains_mod.CHAINS[chains_mod.DEFAULT_CHAIN])["geckoNetwork"]
    pools = ranked_pools(address, network)
    return pools[0]["priceUsd"]
