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

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from trader import chains as chains_mod
from trader import engine as engine_mod
from trader import live as live_mod
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


POOL = "0x79dd15ad871b0fe18040a52f951d757ef88cfe72"
KNDX = "0x7ca5af5ba3472af6049f63c1abc324475d44efc1"
OTHER = "0x1111111111111111111111111111111111111111"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
DAI = "0x6b175474e89094c44da98b954eedeac495271d0f"
OWNER = "0x2222222222222222222222222222222222222222"
NOTE = "(pool 0x79dd…fe72 → token KNDX 0x7ca5…efc1)"

# Every quote asset, written out independently of trader/marketdata.py.
EXPECTED_QUOTE_ASSETS = {
    "eth": {
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",  # WETH
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC
        "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT
        "0x6b175474e89094c44da98b954eedeac495271d0f",  # DAI
    },
    "optimism": {
        "0x4200000000000000000000000000000000000006",  # WETH
        "0x0b2c639c533813f4aa9d7837caf62653d097ff85",  # USDC
    },
    "unichain": {"0x4200000000000000000000000000000000000006"},  # WETH
    "world-chain": {"0x4200000000000000000000000000000000000006"},  # WETH
    "robinhood": {"0x0bd7d308f8e1639fab988df18a8011f41eacad73"},  # WETH (chains.py)
    "base": {
        "0x4200000000000000000000000000000000000006",  # WETH
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC
        "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca",  # USDbC
        "0x50c5725949a6f0c72e6c4a641f24049a917db0cb",  # DAI
    },
    "arbitrum": {
        "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",  # WETH
        "0xaf88d065e77c8cc2239327c5edb3a432268e5831",  # USDC
        "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8",  # USDC.e
        "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",  # USDT
    },
}


def _http_error(status):
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(f"{status} Client Error", response=resp)


def _pool_detail(base, quote, network=NETWORK, symbols=None):
    """GeckoTerminal pools/{addr}?include=base_token,quote_token shape."""
    included = [{"id": f"{network}_{addr}", "type": "token", "attributes": {"address": addr, "symbol": sym}}
                for addr, sym in (symbols or {}).items()]
    return {"data": {"attributes": {"address": POOL}, "relationships": {
        "base_token": {"data": {"id": f"{network}_{base}"}},
        "quote_token": {"data": {"id": f"{network}_{quote}"}},
    }}, "included": included}


def _token_pools(token, quote, network=NETWORK, price=0.02):
    return {"data": [_pool(POOL, base_id=f"{network}_{token}", quote_id=f"{network}_{quote}",
                           base_price=price, quote_price=3000.0, reserve=250_000)]}


def _kndx_pools():
    return _token_pools(KNDX, WETH)


def _kndx_routes(detail=None):
    return {
        f"/tokens/{POOL}/pools": _http_error(404),
        f"/pools/{POOL}": detail or _pool_detail(KNDX, WETH, symbols={KNDX: "KNDX", WETH: "WETH"}),
        f"/tokens/{KNDX}/pools": _kndx_pools(),
    }


class _FakeGecko:
    """Routes get_json by URL; records every call. No network."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, attempt=1):
        self.calls.append(url)
        for fragment, result in self.routes.items():
            if fragment in url:
                if isinstance(result, Exception):
                    raise result
                return result
        raise AssertionError(f"unexpected URL {url}")


class QuoteAssetTableTests(unittest.TestCase):
    """the table that decides which pool side is the token."""

    def test_table_matches_the_known_good_list(self):
        self.assertEqual({k: set(v) for k, v in marketdata.QUOTE_ASSETS.items()}, EXPECTED_QUOTE_ASSETS)

    def test_eth_dai_is_mainnet_dai(self):
        self.assertIn(DAI, marketdata.QUOTE_ASSETS["eth"])
        self.assertNotIn("0x6b175474e89094c44da98ecd5ee7a0cf9ffd4b50", marketdata.QUOTE_ASSETS["eth"])

    def test_every_wrapped_native_in_chains_is_a_quote_asset(self):
        for key, chain in chains_mod.CHAINS.items():
            wrapped = (chain.get("live") or {}).get("weth")
            if wrapped:
                with self.subTest(chain=key):
                    self.assertIn(wrapped.lower(), marketdata.QUOTE_ASSETS[chain["geckoNetwork"]])

    def test_hardcoded_stablecoins_are_valid_eip55_checksums(self):
        from web3 import Web3
        for network, addresses in marketdata._STABLECOINS.items():
            for address in addresses:
                with self.subTest(network=network, address=address):
                    self.assertTrue(Web3.is_checksum_address(address))

    def test_networks_without_a_known_wrapped_native_have_no_table(self):
        for network in ("polygon_pos", "soneium", "ink"):
            with self.subTest(network=network):
                self.assertNotIn(network, marketdata.QUOTE_ASSETS)


class PoolAddressFallbackTests(unittest.TestCase):
    def test_token_address_makes_no_extra_request(self):
        fake = _FakeGecko({f"/tokens/{KNDX}/pools": _kndx_pools()})
        with patch.object(marketdata, "get_json", fake):
            pools = marketdata.ranked_pools(KNDX, NETWORK)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(pools[0]["tokenAddress"], KNDX)
        self.assertAlmostEqual(pools[0]["priceUsd"], 0.02)
        self.assertNotIn("resolution", pools[0])

    def test_mixed_case_token_input_returns_lowercase_token_address(self):
        mixed = "0x7CA5af5ba3472af6049f63c1abc324475d44EFC1"
        fake = _FakeGecko({f"/tokens/{mixed}/pools": _kndx_pools()})
        with patch.object(marketdata, "get_json", fake):
            pools = marketdata.ranked_pools(mixed, NETWORK)
        self.assertEqual(pools[0]["tokenAddress"], KNDX)

    def test_pool_address_resolves_to_base_token_with_metadata(self):
        fake = _FakeGecko(_kndx_routes())
        with patch.object(marketdata, "get_json", fake), patch("builtins.print") as printed:
            pools = marketdata.ranked_pools(POOL, NETWORK)
        self.assertEqual(len(fake.calls), 3)
        self.assertIn(f"/networks/{NETWORK}/pools/{POOL}?include=base_token,quote_token", fake.calls[1])
        self.assertIn(f"/tokens/{KNDX}/pools", fake.calls[2])
        self.assertEqual(pools[0]["tokenAddress"], KNDX)
        self.assertEqual(pools[0]["priceSide"], "base")
        self.assertAlmostEqual(pools[0]["priceUsd"], 0.02)
        self.assertEqual(pools[0]["resolution"], {"pool": POOL, "token": KNDX, "symbol": "KNDX",
                                                  "side": "base", "pair": "KNDX/WETH"})
        self.assertEqual(marketdata.resolution_note(pools[0]["resolution"]), NOTE)
        printed.assert_not_called()

    def test_checksum_case_relationship_ids_resolve(self):
        mixed = "0x7CA5af5ba3472af6049f63c1abc324475d44EFC1"
        fake = _FakeGecko(_kndx_routes(_pool_detail(mixed, "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")))
        with patch.object(marketdata, "get_json", fake):
            pools = marketdata.ranked_pools(POOL, NETWORK)
        self.assertEqual(pools[0]["tokenAddress"], KNDX)
        self.assertIn(f"/tokens/{KNDX}/pools", fake.calls[2])

    def test_pool_with_weth_base_resolves_to_other_side(self):
        fake = _FakeGecko(_kndx_routes(_pool_detail(WETH, KNDX)))
        with patch.object(marketdata, "get_json", fake):
            pools = marketdata.ranked_pools(POOL, NETWORK)
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(pools[0]["tokenAddress"], KNDX)
        self.assertEqual(pools[0]["resolution"]["side"], "quote")

    def test_pool_with_dai_base_resolves_to_other_side(self):
        fake = _FakeGecko(_kndx_routes(_pool_detail(DAI, KNDX)))
        with patch.object(marketdata, "get_json", fake):
            pools = marketdata.ranked_pools(POOL, NETWORK)
        self.assertEqual(pools[0]["tokenAddress"], KNDX)

    def test_no_network_ever_resolves_to_its_wrapped_native_or_a_stablecoin(self):
        # Every configured chain. Symbols are left out so only the
        # address table decides. A network with no table must refuse.
        fallback_wrapped = {"polygon_pos": "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270"}
        for key, chain in chains_mod.CHAINS.items():
            network = chain["geckoNetwork"]
            table = marketdata.QUOTE_ASSETS.get(network, frozenset())
            wrapped = ((chain.get("live") or {}).get("weth")
                       or fallback_wrapped.get(network, "0x4200000000000000000000000000000000000006")).lower()
            for quote_asset in sorted(table) or [wrapped]:
                for base, quote in ((quote_asset, OTHER), (OTHER, quote_asset)):
                    with self.subTest(chain=key, base=base, quote=quote):
                        fake = _FakeGecko({
                            f"/tokens/{POOL}/pools": _http_error(404),
                            f"/pools/{POOL}": _pool_detail(base, quote, network=network),
                            f"/tokens/{OTHER}/pools": _token_pools(OTHER, quote_asset, network=network),
                        })
                        with patch.object(marketdata, "get_json", fake):
                            if table:
                                self.assertEqual(marketdata.ranked_pools(POOL, network)[0]["tokenAddress"], OTHER)
                            else:
                                with self.assertRaisesRegex(
                                        marketdata.PoolAddressError,
                                        re.escape(f"{POOL} is a pool, not a token, on {chain['name']}; use the token address")):
                                    marketdata.ranked_pools(POOL, network)
                                self.assertEqual(len(fake.calls), 2)

    def test_quote_symbol_counts_as_quote_where_the_address_is_unknown(self):
        # Unichain's table only knows WETH; a USDC there is caught by symbol.
        usdc_uni = "0x3333333333333333333333333333333333333333"
        fake = _FakeGecko({
            f"/tokens/{POOL}/pools": _http_error(404),
            f"/pools/{POOL}": _pool_detail(usdc_uni, OTHER, network="unichain",
                                           symbols={usdc_uni: "USDC", OTHER: "FOO"}),
            f"/tokens/{OTHER}/pools": _token_pools(OTHER, usdc_uni, network="unichain"),
        })
        with patch.object(marketdata, "get_json", fake):
            pools = marketdata.ranked_pools(POOL, "unichain")
        self.assertEqual(pools[0]["tokenAddress"], OTHER)

    def test_pool_of_two_quote_assets_is_refused(self):
        fake = _FakeGecko({
            f"/tokens/{POOL}/pools": _http_error(404),
            f"/pools/{POOL}": _pool_detail(WETH, USDC, symbols={WETH: "WETH", USDC: "USDC"}),
        })
        with patch.object(marketdata, "get_json", fake):
            with self.assertRaises(marketdata.PoolAddressError) as ctx:
                marketdata.ranked_pools(POOL, NETWORK)
        self.assertEqual(str(ctx.exception),
                         f"{POOL} is a pool, not a token, on Ethereum; use the token address (pool pair: WETH/USDC)")
        self.assertEqual(len(fake.calls), 2)

    def test_pool_of_two_non_quote_tokens_is_ambiguous(self):
        fake = _FakeGecko({
            f"/tokens/{POOL}/pools": _http_error(404),
            f"/pools/{POOL}": _pool_detail(KNDX, OTHER, symbols={KNDX: "KNDX", OTHER: "OTHER"}),
        })
        with patch.object(marketdata, "get_json", fake):
            with self.assertRaises(marketdata.PoolAddressError) as ctx:
                marketdata.ranked_pools(POOL, NETWORK)
        self.assertIn(f"{POOL} is a pool, not a token, on Ethereum", str(ctx.exception))
        self.assertIn("ambiguous", str(ctx.exception))
        self.assertIn("KNDX/OTHER", str(ctx.exception))
        self.assertEqual(len(fake.calls), 2)

    def test_address_unknown_as_token_and_pool_says_not_found(self):
        fake = _FakeGecko({
            f"/tokens/{POOL}/pools": _http_error(404),
            f"/pools/{POOL}": _http_error(404),
        })
        with patch.object(marketdata, "get_json", fake):
            with self.assertRaises(RuntimeError) as ctx:
                marketdata.ranked_pools(POOL, NETWORK)
        self.assertNotIsInstance(ctx.exception, marketdata.PoolAddressError)
        self.assertEqual(str(ctx.exception), f"{POOL} was not found on GeckoTerminal (Ethereum) as a token or a pool — "
                                             f"check the address and chain (a new token may not be indexed yet)")
        self.assertNotIn("Client Error", str(ctx.exception))
        self.assertEqual(len(fake.calls), 2)

    def test_resolved_token_without_pools_gets_a_clear_error(self):
        routes = _kndx_routes()
        routes[f"/tokens/{KNDX}/pools"] = _http_error(404)
        fake = _FakeGecko(routes)
        with patch.object(marketdata, "get_json", fake):
            with self.assertRaises(RuntimeError) as ctx:
                marketdata.ranked_pools(POOL, NETWORK)
        self.assertEqual(str(ctx.exception),
                         f"pool {POOL} on Ethereum resolves to token KNDX {KNDX}, which has no GeckoTerminal pools")
        self.assertEqual(len(fake.calls), 3)

    def test_pool_detail_429_or_5xx_propagates(self):
        for status in (429, 500):
            with self.subTest(status=status):
                fake = _FakeGecko({
                    f"/tokens/{POOL}/pools": _http_error(404),
                    f"/pools/{POOL}": _http_error(status),
                })
                with patch.object(marketdata, "get_json", fake):
                    with self.assertRaises(requests.HTTPError) as ctx:
                        marketdata.ranked_pools(POOL, NETWORK)
                self.assertEqual(ctx.exception.response.status_code, status)
                self.assertEqual(len(fake.calls), 2)

    def test_429_on_token_endpoint_does_not_fall_back(self):
        fake = _FakeGecko({f"/tokens/{POOL}/pools": _http_error(429)})
        with patch.object(marketdata, "get_json", fake):
            with self.assertRaises(requests.HTTPError):
                marketdata.ranked_pools(POOL, NETWORK)
        self.assertEqual(len(fake.calls), 1)

    def test_allow_pool_false_refuses_and_names_the_token(self):
        fake = _FakeGecko(_kndx_routes())
        with patch.object(marketdata, "get_json", fake):
            with self.assertRaises(marketdata.PoolAddressError) as ctx:
                marketdata.current_price(POOL, "ethereum", allow_pool=False)
        self.assertEqual(str(ctx.exception),
                         f"{POOL} is a pool, not a token, on Ethereum; use the token address (KNDX {KNDX})")
        self.assertEqual(len(fake.calls), 2)

    def test_snapshot_and_current_quote_use_resolved_token(self):
        fake = _FakeGecko(_kndx_routes())
        with patch.object(marketdata, "get_json", fake):
            quote = marketdata.current_quote(POOL, "ethereum")
        self.assertEqual(quote["address"], KNDX)
        self.assertAlmostEqual(quote["priceUsd"], 0.02)
        self.assertEqual(quote["resolution"]["token"], KNDX)
        self.assertEqual(quote["resolution"]["pool"], POOL)

        fake.calls.clear()
        candles = [{"ts": i, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for i in range(30)]
        with patch.object(marketdata, "get_json", fake), \
                patch.object(marketdata, "ohlcv", return_value=candles), patch.object(marketdata.time, "sleep"):
            snap = marketdata.snapshot({"symbol": "KNDX", "chain": "ethereum", "address": POOL}, "hour", 100)
        self.assertEqual(snap["address"], KNDX)
        self.assertEqual(snap["resolution"]["token"], KNDX)

    def test_direct_token_quote_has_no_resolution(self):
        fake = _FakeGecko({f"/tokens/{KNDX}/pools": _kndx_pools()})
        with patch.object(marketdata, "get_json", fake):
            quote = marketdata.current_quote(KNDX, "ethereum")
        self.assertIsNone(quote["resolution"])


class _Mcp:
    """guardian_check_token stub: records the args, approves low risk."""

    def __init__(self):
        self.calls = []

    def tools(self):
        return {"ok": True, "tools": [{"name": "guardian_check_token", "schema": {}}]}

    def call(self, name, args):
        self.calls.append((name, args))
        return {"ok": True, "text": json.dumps({"risk_level": "low", "risk_score": 10})}


class EnginePoolResolutionTests(unittest.TestCase):
    """buy/adopt/scan with a pool address, through the real marketdata
    resolution (only get_json, the MCP gate and live.py are faked)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.enterContext(patch.object(engine_mod, "_base_dir", return_value=Path(tmp.name)))
        self.events = []
        self.mcp = _Mcp()
        self.engine = engine_mod.TraderEngine(
            emit=self.events.append, mcp_tools=self.mcp.tools, mcp_call=self.mcp.call,
            wallet_status=lambda: {"connected": True, "address": OWNER, "signerGranted": True})
        self.fake = _FakeGecko(_kndx_routes())
        self.enterContext(patch.object(marketdata, "get_json", self.fake))

    def _events(self, etype):
        return [e for e in self.events if e.get("type") == etype]

    def _assert_resolution_logged(self):
        logs = [e["text"] for e in self._events("log") if NOTE in e["text"]]
        self.assertEqual(len(logs), 1, self.events)
        self.assertIn(f"{POOL} is a pool", logs[0])
        self.assertIn(KNDX, logs[0])

    def test_force_buy_reports_the_resolution_and_records_the_token(self):
        res = self.engine.buy_one(f"KONDUX:{POOL} force")
        self.assertTrue(res["ok"], res)
        self.assertIn(f"bought KONDUX @ $0.02 {NOTE}", res["message"])
        self.assertIn("GeckoTerminal lists this token as KNDX", res["message"])
        self.assertEqual(self.engine.state["positions"][0]["address"], KNDX)
        gate = self._events("gate")
        self.assertEqual(len(gate), 1)
        self.assertEqual(gate[0]["address"], KNDX)
        self.assertEqual(gate[0]["resolution"], NOTE)
        self._assert_resolution_logged()

    def test_matching_symbol_gets_no_warning(self):
        res = self.engine.buy_one(f"KNDX:{POOL} force")
        self.assertTrue(res["ok"], res)
        self.assertIn(NOTE, res["message"])
        self.assertNotIn("⚠", res["message"])

    def test_token_address_buy_has_no_resolution_note(self):
        res = self.engine.buy_one(f"KNDX:{KNDX} force")
        self.assertTrue(res["ok"], res)
        self.assertNotIn("pool", res["message"])
        self.assertNotIn("resolution", self._events("gate")[0])
        self.assertEqual(self._events("log"), [])

    def test_gated_paper_buy_checks_and_reports_the_token(self):
        res = self.engine.buy_one(f"KONDUX:{POOL}")
        self.assertTrue(res["ok"], res)
        self.assertIn(NOTE, res["message"])
        self.assertEqual(len(self.mcp.calls), 1)
        name, args = self.mcp.calls[0]
        self.assertEqual(name, "guardian_check_token")
        self.assertEqual(args["address"], KNDX)
        gate = self._events("gate")
        self.assertEqual([g["address"] for g in gate], [KNDX])
        self.assertTrue(gate[0]["approved"])
        self.assertEqual(gate[0]["resolution"], NOTE)
        self.assertEqual(self.engine.state["positions"][0]["address"], KNDX)
        self._assert_resolution_logged()

    def test_gated_live_buy_swaps_the_token(self):
        self.engine.armed_live = True
        self.engine.config["chains"] = ["ethereum"]
        fill = {"qty": 250.0, "priceUsd": 0.02, "costUsd": 5.0, "txHash": "0x" + "cd" * 32, "wallet": OWNER}
        with patch.object(live_mod, "live_buy", return_value=fill) as live_buy:
            res = self.engine.buy_one(f"KONDUX:{POOL}")
        self.assertTrue(res["ok"], res)
        live_buy.assert_called_once()
        self.assertEqual(live_buy.call_args.kwargs["token"]["address"], KNDX)
        self.assertEqual(self.mcp.calls[0][1]["address"], KNDX)
        self.assertEqual(self._events("gate")[0]["address"], KNDX)
        self.assertEqual(self.engine.state["livePositions"][0]["address"], KNDX)

    def test_pool_on_network_without_quote_table_is_refused(self):
        self.fake.routes = {
            f"/tokens/{POOL}/pools": _http_error(404),
            f"/pools/{POOL}": _pool_detail(KNDX, OTHER, network="polygon_pos"),
        }
        res = self.engine.buy_one(f"KONDUX:polygon:{POOL} force")
        self.assertFalse(res["ok"])
        self.assertIn(f"{POOL} is a pool, not a token, on Polygon; use the token address", res["message"])
        self.assertEqual(self.engine.state["positions"], [])
        self.assertEqual(self._events("gate"), [])

    def test_ambiguous_pool_is_refused(self):
        self.fake.routes[f"/pools/{POOL}"] = _pool_detail(KNDX, OTHER, symbols={KNDX: "KNDX", OTHER: "OTHER"})
        res = self.engine.buy_one(f"KONDUX:{POOL} force")
        self.assertFalse(res["ok"])
        self.assertIn("ambiguous", res["message"])
        self.assertEqual(self.engine.state["positions"], [])

    def test_adopt_refuses_a_pool_address_without_tx_hash(self):
        self.engine.armed_live = True
        with patch.object(live_mod, "token_balance", return_value=5.0):
            res = self.engine.adopt_one(f"KONDUX:{POOL}")
        self.assertFalse(res["ok"])
        self.assertEqual(res["message"], f"adopt KONDUX failed: {POOL} is a pool, not a token, on Ethereum; "
                                         f"use the token address (KNDX {KNDX})")
        self.assertEqual(self.engine.state["livePositions"], [])

    def test_adopt_refuses_a_pool_address_with_tx_hash(self):
        self.engine.armed_live = True
        info = {"qty": 5.0, "priceUsd": 0.02, "costUsd": 0.1, "txHash": "0x" + "ab" * 32}
        with patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
                patch.object(live_mod, "adopt_from_tx", return_value=info):
            res = self.engine.adopt_one(f"KONDUX:{POOL}:{'0x' + 'ab' * 32}")
        self.assertFalse(res["ok"])
        self.assertIn("is a pool, not a token", res["message"])
        self.assertEqual(self.engine.state["livePositions"], [])

    def test_adopt_of_a_token_address_still_works(self):
        self.engine.armed_live = True
        with patch.object(live_mod, "token_balance", return_value=5.0):
            res = self.engine.adopt_one(f"KNDX:{KNDX}")
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.engine.state["livePositions"][0]["address"], KNDX)
        self.assertAlmostEqual(self.engine.state["livePositions"][0]["entryPriceUsd"], 0.02)

    def test_scan_saves_the_token_address_for_a_pool_watchlist_entry(self):
        # The first scan that resolves the pool stores the token.
        self.engine.config.update({"watchlist": [{"symbol": "KONDUX", "chain": "ethereum", "address": POOL}],
                                   "autoDiscoverTrending": False, "autoDiscoverVolumeSpikes": False,
                                   "autoDiscoverMemeCoins": False})
        self.engine.running = True
        candles = [{"ts": i, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for i in range(30)]
        with patch.object(marketdata, "ohlcv", return_value=candles), \
                patch.object(marketdata.time, "sleep"), patch.object(engine_mod.time, "sleep"):
            self.engine._cycle()
        self.assertEqual(self.engine.config["watchlist"], [{"symbol": "KONDUX", "chain": "ethereum", "address": KNDX}])
        self.assertTrue(any("saved token address" in e["text"] for e in self._events("log")), self.events)
        self._assert_resolution_logged()
        self.assertEqual(self._events("watchlist")[-1]["watchlist"][0]["address"], KNDX)
        saved = json.loads(self.engine._config_file().read_text(encoding="utf-8"))
        self.assertEqual(saved["watchlist"][0]["address"], KNDX)

        # The next scan asks for the token directly: one request, no fallback.
        self.fake.calls.clear()
        with patch.object(marketdata, "ohlcv", return_value=candles), \
                patch.object(marketdata.time, "sleep"), patch.object(engine_mod.time, "sleep"):
            self.engine._cycle()
        self.assertEqual(len(self.fake.calls), 1, self.fake.calls)
        self.assertIn(f"/tokens/{KNDX}/pools", self.fake.calls[0])


if __name__ == "__main__":
    unittest.main()
