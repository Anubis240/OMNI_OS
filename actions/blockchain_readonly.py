"""Read-only, no-credentials-needed EVM chain queries: native balance, ERC20
token balance, and current gas price, across every chain trader/chains.py
already has real RPC endpoints for. Distinct from trader/engine.py's paper-
trading loop and trader/live.py's real swap execution — this module never
signs or sends a transaction, only reads public chain state, so it needs no
wallet, no Seraph pre-trade gate, and no "trader enabled" flag. It exists so
the user can ask "what's my ETH balance on Base" or "what's gas like on
Arbitrum" without opening the trader panel at all.

Reuses trader/live.py's already-tested wallet_eth_balance_usd/token_balance/
eth_usd_price rather than re-deriving them. Deliberately does NOT import
trader/live.py's underscore-prefixed _with_rpc/_live_config — those are that
module's own private helpers, not a public cross-module API — so gas price
gets its own tiny local RPC-fallback helper instead.

Every "live" chain in trader/chains.py uses ETH (or an L2's 1:1 ETH-pegged
gas token) as its native currency, so multiplying a native balance by
eth_usd_price() is valid for all of them — there's no Polygon/Soneium/Ink
here because those chains only have paper-trading config (no "live" key,
see chains.py's own header comment), i.e. no RPC endpoints to query.

Solana is NOT covered — nothing in this codebase (trader/ or elsewhere) has
a Solana RPC client, key format, or dependency; the Hermes Agent skill
catalog's `solana` entry that inspired this module would need its own
research spike (likely `solders`/`solana-py`) as a separate follow-up, not
folded in here as a guess. See the 2026-09-09 conversation with the user.

trader/chains.py is imported at module level below — it's pure data, no
external dependencies. trader/live.py (and the web3 import it needs) is
deliberately imported lazily inside each function instead, matching
actions/launch_trader.py's own discipline of never pulling web3/wallet
code into main.py's always-loaded import graph just because the optional
trader feature exists somewhere in the app — this module has nothing to do
with the "trader enabled" setting and shouldn't cost anything at startup
for someone who never touches either feature.
"""

from trader import chains as chains_mod

_LIVE_CHAINS = chains_mod.live_chain_keys()

TOOL_DECLARATIONS = [
    {
        "name": "check_wallet_balance",
        "description": (
            "Checks a wallet's native token balance (ETH, or an L2's ETH-pegged gas "
            "token) on a supported EVM chain, in both native units and USD. "
            "Read-only — never signs or sends anything, no wallet setup required. Use "
            "this for 'what's my balance' / 'how much ETH do I have on Base' type "
            "questions. Not the same as the trader panel's own wallet — use "
            "launch_trader for that."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "address": {"type": "STRING", "description": "0x wallet address to check"},
                "chain": {
                    "type": "STRING",
                    "description": f"One of: {', '.join(_LIVE_CHAINS)}. Defaults to ethereum.",
                },
            },
            "required": ["address"],
        },
    },
    {
        "name": "check_token_balance",
        "description": (
            "Checks how many units of a specific ERC20 token a wallet holds on a "
            "supported EVM chain. Read-only."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "token_address": {"type": "STRING", "description": "0x contract address of the ERC20 token"},
                "owner_address": {"type": "STRING", "description": "0x wallet address to check"},
                "chain": {
                    "type": "STRING",
                    "description": f"One of: {', '.join(_LIVE_CHAINS)}. Defaults to ethereum.",
                },
            },
            "required": ["token_address", "owner_address"],
        },
    },
    {
        "name": "check_gas_price",
        "description": "Reports the current gas price on a supported EVM chain, in gwei.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "chain": {
                    "type": "STRING",
                    "description": f"One of: {', '.join(_LIVE_CHAINS)}. Defaults to ethereum.",
                },
            },
        },
    },
]


def _resolve_chain(raw: str | None) -> str:
    key = (raw or chains_mod.DEFAULT_CHAIN).strip().lower().replace(" ", "")
    if not chains_mod.is_live_supported(key):
        key = chains_mod.DEFAULT_CHAIN
    return key


def _gas_price_gwei(chain: str) -> float:
    from web3 import Web3

    rpc_urls = chains_mod.CHAINS[chain]["live"]["rpcUrls"]
    last_err: Exception | None = None
    for url in rpc_urls:
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 10}))
            return float(Web3.from_wei(w3.eth.gas_price, "gwei"))
        except Exception as e:
            last_err = e
    raise RuntimeError(f"all RPC endpoints failed: {last_err}")


def check_wallet_balance(parameters: dict, response=None, player=None, session_memory=None) -> str:
    from trader import live as live_mod

    params = parameters or {}
    address = (params.get("address") or "").strip()
    if not address:
        return "No wallet address given."
    chain = _resolve_chain(params.get("chain"))
    chain_name = chains_mod.resolve(chain)["name"]
    try:
        eth_price = live_mod.eth_usd_price()
        usd = live_mod.wallet_eth_balance_usd(chain, address, eth_price)
        native = usd / eth_price if eth_price else 0.0
        return f"{address} on {chain_name}: {native:.5f} native ≈ ${usd:,.2f}"
    except Exception as e:
        return f"Could not check balance on {chain_name}: {e}"


def check_token_balance(parameters: dict, response=None, player=None, session_memory=None) -> str:
    from trader import live as live_mod

    params = parameters or {}
    token = (params.get("token_address") or "").strip()
    owner = (params.get("owner_address") or "").strip()
    if not token or not owner:
        return "Need both a token contract address and an owner wallet address."
    chain = _resolve_chain(params.get("chain"))
    chain_name = chains_mod.resolve(chain)["name"]
    try:
        balance = live_mod.token_balance(chain, token, owner)
        return f"{owner} holds {balance:,.4f} of token {token} on {chain_name}."
    except Exception as e:
        return f"Could not check token balance on {chain_name}: {e}"


def check_gas_price(parameters: dict, response=None, player=None, session_memory=None) -> str:
    params = parameters or {}
    chain = _resolve_chain(params.get("chain"))
    chain_name = chains_mod.resolve(chain)["name"]
    try:
        gwei = _gas_price_gwei(chain)
        return f"Current gas price on {chain_name}: {gwei:.2f} gwei"
    except Exception as e:
        return f"Could not fetch gas price on {chain_name}: {e}"
