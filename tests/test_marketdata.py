"""trader/marketdata.py's ranked_pools — GEMZ4US, 2026-09-18 (Part 3/B):
a PAPER FORCE BUY entered at roughly double the real market price
(Dexscreener showed a flat market the whole window) — exactly the
failure mode this module's own header comment already warns about
(base/quote pool-side ambiguity can silently return the wrong token's
price). The old logic assumed "quote" whenever base_id didn't match,
with no check that quote_id matched anything either. Fixture shapes
below are grounded in a real GeckoTerminal response (WebFetch against
the exact LIT token address from the report), not invented structure.
No real network calls — get_json is mocked."""

import unittest
from unittest.mock import patch

from trader import marketdata

NETWORK = "eth"
TOKEN = "0x232ce3bd40fcd6f80f3d55a522d03f25df784ee2"
WANTED = f"{NETWORK}_{TOKEN}"


def _pool(address, base_id=None, quote_id=None, base_price=0, quote_price=0, reserve=0):
    return {
        "attributes": {
            "address": address,
            "base_token_price_usd": base_price,
            "quote_token_price_usd": quote_price,
            "reserve_in_usd": reserve,
            "price_change_percentage": {"h24": 0},
            "volume_usd": {"h24": 0},
        },
        "relationships": {
            "base_token": {"data": {"id": base_id}} if base_id else {},
            "quote_token": {"data": {"id": quote_id}} if quote_id else {},
        },
    }


class RankedPoolsTests(unittest.TestCase):
    def test_base_side_match_uses_base_price(self):
        # Grounded in the real LIT pool response (WebFetch, 2026-09-18).
        data = {"data": [_pool("0x8366...", base_id=WANTED, quote_id="eth_0xa0b8...usdc",
                                base_price=5.0539920477, quote_price=1.00049844036363, reserve=2142916.1161)]}
        with patch.object(marketdata, "get_json", return_value=data):
            pools = marketdata.ranked_pools(TOKEN, NETWORK)
        self.assertEqual(len(pools), 1)
        self.assertEqual(pools[0]["priceSide"], "base")
        self.assertAlmostEqual(pools[0]["priceUsd"], 5.0539920477)

    def test_quote_side_match_uses_quote_price(self):
        # Our token is listed as the QUOTE side of this pool — must read
        # quote_token_price_usd, not base_token_price_usd.
        data = {"data": [_pool("0xabc...", base_id="eth_0xsomeotherbasetoken",
                                quote_id=WANTED, base_price=999.0, quote_price=4.82, reserve=500000)]}
        with patch.object(marketdata, "get_json", return_value=data):
            pools = marketdata.ranked_pools(TOKEN, NETWORK)
        self.assertEqual(len(pools), 1)
        self.assertEqual(pools[0]["priceSide"], "quote")
        self.assertAlmostEqual(pools[0]["priceUsd"], 4.82)

    def test_neither_side_matches_pool_is_skipped_not_guessed(self):
        # Before the fix: base_id != wanted defaulted straight to "quote"
        # and returned quote_token_price_usd (999.0) with total
        # confidence, even though this pool has nothing to do with our
        # token at all. Now it must be skipped entirely.
        unrelated = _pool("0xdead...", base_id="eth_0xsomeotherbasetoken",
                           quote_id="eth_0xsomeotherquotetoken", base_price=1.0, quote_price=999.0, reserve=1_000_000)
        real = _pool("0x8366...", base_id=WANTED, quote_id="eth_0xa0b8...usdc",
                      base_price=5.05, quote_price=1.0, reserve=2142916.1161)
        data = {"data": [unrelated, real]}
        with patch.object(marketdata, "get_json", return_value=data):
            pools = marketdata.ranked_pools(TOKEN, NETWORK)
        self.assertEqual(len(pools), 1)
        self.assertAlmostEqual(pools[0]["priceUsd"], 5.05)

    def test_sorted_by_liquidity_descending_across_mixed_sides(self):
        low_liq_base = _pool("0x1...", base_id=WANTED, quote_id="eth_0xq1", base_price=5.0, quote_price=1.0, reserve=100_000)
        high_liq_quote = _pool("0x2...", base_id="eth_0xother", quote_id=WANTED, base_price=1.0, quote_price=5.1, reserve=2_000_000)
        data = {"data": [low_liq_base, high_liq_quote]}
        with patch.object(marketdata, "get_json", return_value=data):
            pools = marketdata.ranked_pools(TOKEN, NETWORK)
        self.assertEqual(len(pools), 2)
        self.assertAlmostEqual(pools[0]["liquidityUsd"], 2_000_000)
        self.assertAlmostEqual(pools[0]["priceUsd"], 5.1)

    def test_raises_when_every_pool_is_unrelated(self):
        data = {"data": [_pool("0xdead...", base_id="eth_0xsomeotherbasetoken",
                                quote_id="eth_0xsomeotherquotetoken", base_price=1.0, quote_price=999.0, reserve=1_000_000)]}
        with patch.object(marketdata, "get_json", return_value=data):
            with self.assertRaises(RuntimeError):
                marketdata.ranked_pools(TOKEN, NETWORK)

    def test_no_pools_at_all_raises(self):
        with patch.object(marketdata, "get_json", return_value={"data": []}):
            with self.assertRaises(RuntimeError):
                marketdata.ranked_pools(TOKEN, NETWORK)


if __name__ == "__main__":
    unittest.main()
