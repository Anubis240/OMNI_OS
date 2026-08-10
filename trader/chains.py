"""Registry of blockchains Seraph's risk-gate API covers.

Port of the JS trader's chains.js — see that file's header comment for the
full rationale (why Polygon/Soneium/Ink are paper-only, why some chains
have only one RPC fallback, etc). Kept as a 1:1 data port, not re-derived.
"""

CHAINS = {
    "ethereum": {
        "name": "Ethereum", "chainId": 1, "geckoNetwork": "eth", "dexscreenerId": "ethereum",
        "live": {
            "weth": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            "uniswapV3Router": "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
            "uniswapV3Quoter": "0x61fFE014bA17989E743c5F6cB21bF9697530B21e",
            "uniswapV2Router": "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
            "uniswapV2Factory": "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
            "rpcUrls": [
                "https://ethereum-rpc.publicnode.com",
                "https://rpc.mevblocker.io",
            ],
        },
    },
    "optimism": {
        "name": "Optimism", "chainId": 10, "geckoNetwork": "optimism", "dexscreenerId": "optimism",
        "live": {
            "weth": "0x4200000000000000000000000000000000000006",
            "uniswapV3Router": "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
            "uniswapV3Quoter": "0x61fFE014bA17989E743c5F6cB21bF9697530B21e",
            "rpcUrls": ["https://mainnet.optimism.io", "https://optimism-rpc.publicnode.com"],
        },
    },
    "unichain": {
        "name": "Unichain", "chainId": 130, "geckoNetwork": "unichain", "dexscreenerId": "unichain",
        "live": {
            "weth": "0x4200000000000000000000000000000000000006",
            "uniswapV3Router": "0x73855d06de49d0fe4a9c42636ba96c62da12ff9c",
            "uniswapV3Quoter": "0x385a5cf5f83e99f7bb2852b6a19c3538b9fa7658",
            "rpcUrls": ["https://mainnet.unichain.org", "https://unichain-rpc.publicnode.com"],
        },
    },
    "polygon": {"name": "Polygon", "chainId": 137, "geckoNetwork": "polygon_pos", "dexscreenerId": "polygon"},
    "worldchain": {
        "name": "World Chain", "chainId": 480, "geckoNetwork": "world-chain", "dexscreenerId": "worldchain",
        "live": {
            "weth": "0x4200000000000000000000000000000000000006",
            "uniswapV3Router": "0x091AD9e2e6e5eD44c1c66dB50e49A601F9f36cF6",
            "uniswapV3Quoter": "0x10158D43e6cc414deE1Bd1eB0EfC6a5cBCfF244c",
            "rpcUrls": ["https://worldchain-mainnet.g.alchemy.com/public"],
        },
    },
    "soneium": {"name": "Soneium", "chainId": 1868, "geckoNetwork": "soneium", "dexscreenerId": "soneium"},
    "robinhood": {
        "name": "Robinhood Chain", "chainId": 4663, "geckoNetwork": "robinhood", "dexscreenerId": "robinhood",
        "live": {
            "weth": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
            "uniswapV3Router": "0xcaf681a66d020601342297493863e78c959e5cb2",
            "uniswapV3Quoter": "0x33e885ed0ec9bf04ecfb19341582aadcb4c8a9e7",
            "rpcUrls": ["https://rpc.mainnet.chain.robinhood.com"],
        },
    },
    "base": {
        "name": "Base", "chainId": 8453, "geckoNetwork": "base", "dexscreenerId": "base",
        "live": {
            "weth": "0x4200000000000000000000000000000000000006",
            "uniswapV3Router": "0x2626664c2603336E57B271c5C0b26F421741e481",
            "uniswapV3Quoter": "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a",
            "rpcUrls": ["https://mainnet.base.org", "https://base-rpc.publicnode.com"],
        },
    },
    "arbitrum": {
        "name": "Arbitrum One", "chainId": 42161, "geckoNetwork": "arbitrum", "dexscreenerId": "arbitrum",
        "live": {
            "weth": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
            "uniswapV3Router": "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
            "uniswapV3Quoter": "0x61fFE014bA17989E743c5F6cB21bF9697530B21e",
            "rpcUrls": ["https://arb1.arbitrum.io/rpc", "https://arbitrum-one-rpc.publicnode.com"],
        },
    },
    "ink": {"name": "Ink", "chainId": 57073, "geckoNetwork": "ink", "dexscreenerId": "ink"},
}

DEFAULT_CHAIN = "ethereum"


def is_supported(key: str) -> bool:
    return key in CHAINS


def is_live_supported(key: str) -> bool:
    return bool(CHAINS.get(key, {}).get("live"))


def by_dexscreener_id(id_: str):
    for k, v in CHAINS.items():
        if v.get("dexscreenerId") == id_:
            return k
    return None


def dexscreener_url(key: str, address: str) -> str | None:
    """Token chart/info page — DexScreener resolves a bare token address to
    its most-liquid pair automatically, so no separate pair lookup is
    needed. None if the chain or address is missing/unrecognized."""
    slug = CHAINS.get(key, {}).get("dexscreenerId")
    if not slug or not address:
        return None
    return f"https://dexscreener.com/{slug}/{address}"


def resolve(key: str) -> dict:
    return CHAINS.get(key, CHAINS[DEFAULT_CHAIN])


def live_chain_keys():
    return [k for k, v in CHAINS.items() if v.get("live")]
