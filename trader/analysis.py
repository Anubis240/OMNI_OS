"""Deterministic chart analysis — port of analysis.js. Produces a 0-100
signal score per token from trend (EMA cross), momentum (RSI), volume, and
24h structure. The score is a candidate filter — every candidate still has
to pass the Seraph risk gate."""

from __future__ import annotations


def ema(values: list[float], period: int) -> list[float | None]:
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    out: list[float | None] = [None] * (period - 1)
    out.append(e)
    for i in range(period, len(values)):
        e = values[i] * k + e * (1 - k)
        out.append(e)
    return out


def rsi(closes: list[float], period: int = 14) -> float:
    gain = 0.0
    loss = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gain += d
        else:
            loss -= d
    avg_gain = gain / period
    avg_loss = loss / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def analyze(snap: dict, min_liquidity_usd: float = 100000) -> dict:
    closes = [c["close"] for c in snap["candles"]]
    vols = [c["volume"] for c in snap["candles"]]
    last = closes[-1]

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    e9, e21 = ema9[-1], ema21[-1]
    e9prev, e21prev = ema9[-4], ema21[-4]

    r = rsi(closes)
    tail20 = vols[-21:-1] if len(vols) >= 21 else vols[:-1]
    avg_vol = (sum(tail20) / 20) if tail20 else 0
    vol_ratio = (vols[-1] / avg_vol) if avg_vol > 0 else 0

    reasons: list[str] = []
    score = 0

    if e9 is not None and e21 is not None and e9 > e21:
        score += 30
        reasons.append("uptrend (EMA9 > EMA21)")
        if e9prev is not None and e21prev is not None and (e9 - e21) > (e9prev - e21prev):
            score += 10
            reasons.append("trend strengthening")
    else:
        reasons.append("downtrend (EMA9 < EMA21)")

    if 50 <= r <= 68:
        score += 25
        reasons.append(f"RSI {r:.0f} healthy")
    elif 68 < r <= 75:
        score += 10
        reasons.append(f"RSI {r:.0f} hot")
    elif r > 75:
        score -= 15
        reasons.append(f"RSI {r:.0f} overbought")
    elif r >= 40:
        score += 10
        reasons.append(f"RSI {r:.0f} neutral")
    else:
        reasons.append(f"RSI {r:.0f} weak")

    if vol_ratio >= 1.5:
        score += 20
        reasons.append(f"volume surge x{vol_ratio:.1f}")
    elif vol_ratio >= 1.1:
        score += 10
        reasons.append(f"volume above avg x{vol_ratio:.1f}")

    if last > (e9 or last):
        score += 15
        reasons.append("price above EMA9")

    if snap.get("liquidityUsd", 0) < min_liquidity_usd:
        score = 0
        reasons.append(f"VETO: liquidity < ${min_liquidity_usd:,.0f}")

    return {
        "symbol": snap["symbol"],
        "score": max(0, min(100, score)),
        "direction": "buy" if score >= 50 else "none",
        "priceUsd": snap["priceUsd"],
        "rsi": r,
        "volRatio": vol_ratio,
        "reasons": reasons,
    }
