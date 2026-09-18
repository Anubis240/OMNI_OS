"""App-managed wallet — port of localwallet.js.

Only signing backend built for this Python version (see the project plan:
WalletConnect was deliberately dropped for v1, no mature Python v2 SDK).
Holds real key material in the app and signs/broadcasts with NO per-trade
human approval once live mode is armed — a materially different risk model
than WalletConnect, off by default, gated behind its own explicit "I OWN
THIS RISK" confirmation in the UI (trader_panel.py), separate from the
"type LIVE" arm confirmation.

Key material is encrypted at rest via Windows DPAPI (`win32crypt`, tied to
the OS user account — not portable to another machine/user by design,
same property as Electron's `safeStorage` on Windows). Held decrypted in
memory only while unlocked; every app restart starts locked, mirroring
engine.py's armed_live in-memory-only philosophy. Fails closed if DPAPI is
unavailable — refuses to hold key material rather than falling back to
plaintext.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from eth_account import Account
from web3 import Web3

from core.app_paths import get_data_dir

from .. import chains as chains_mod

Account.enable_unaudited_hdwallet_features()

_wallet = None  # in-memory eth_account.LocalAccount — only set while unlocked


def _base_dir() -> Path:
    return get_data_dir()


def _file_path() -> Path:
    return _base_dir() / "config" / "trader" / "wallet" / "local-wallet.enc"


def _assert_secure_storage():
    try:
        import win32crypt  # noqa: F401
    except Exception as err:
        raise RuntimeError("secure storage is unavailable on this system — app-managed wallet is disabled for safety") from err


def _encrypt(plaintext: str) -> bytes:
    import win32crypt
    return win32crypt.CryptProtectData(plaintext.encode("utf-8"), None, None, None, None, 0)


def _decrypt(blob: bytes) -> str:
    import win32crypt
    _, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    return data.decode("utf-8")


def _persist(secret: dict):
    _assert_secure_storage()
    _file_path().parent.mkdir(parents=True, exist_ok=True)
    _file_path().write_bytes(_encrypt(json.dumps(secret)))


def _read_secret() -> dict:
    _assert_secure_storage()
    if not _file_path().exists():
        raise RuntimeError("no app-managed wallet stored")
    return json.loads(_decrypt(_file_path().read_bytes()))


def exists() -> bool:
    return _file_path().exists()


def status() -> dict:
    return {"connected": _wallet is not None, "address": _wallet.address if _wallet else None, "exists": exists()}


def create() -> dict:
    _assert_secure_storage()
    global _wallet
    acct, mnemonic = Account.create_with_mnemonic()
    _persist({"type": "mnemonic", "value": mnemonic})
    _wallet = acct
    return {"address": acct.address, "mnemonic": mnemonic}


def import_mnemonic(phrase: str) -> dict:
    global _wallet
    clean = (phrase or "").strip()
    if not clean:
        raise RuntimeError("recovery phrase is required")
    acct = Account.from_mnemonic(clean)
    _persist({"type": "mnemonic", "value": clean})
    _wallet = acct
    return {"address": acct.address}


def import_private_key(hex_key: str) -> dict:
    global _wallet
    clean = (hex_key or "").strip()
    if not clean:
        raise RuntimeError("private key is required")
    pk = clean if clean.startswith("0x") else "0x" + clean
    acct = Account.from_key(pk)
    _persist({"type": "pk", "value": pk})
    _wallet = acct
    return {"address": acct.address}


def unlock() -> dict:
    global _wallet
    secret = _read_secret()
    _wallet = Account.from_key(secret["value"]) if secret["type"] == "pk" else Account.from_mnemonic(secret["value"])
    return {"address": _wallet.address}


def lock() -> dict:
    global _wallet
    _wallet = None
    return {"ok": True}


def remove() -> dict:
    global _wallet
    _wallet = None
    try:
        _file_path().unlink()
    except Exception:
        pass
    return {"ok": True}


def export_secret() -> dict:
    if not _wallet:
        raise RuntimeError("wallet is locked")
    return _read_secret()


def _with_rpc(chain: str | None, fn):
    c = chains_mod.resolve(chain)
    if not c.get("live"):
        raise RuntimeError(f"live execution is not supported on {c['name']}")
    last_err = None
    for url in c["live"]["rpcUrls"]:
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 15}))
            return fn(w3)
        except Exception as err:
            last_err = err
    raise RuntimeError(f"all RPC endpoints failed for {c['name']}: {last_err}")


# A real LIVE FORCE BUY submitted at the RPC's bare eth_gasPrice quote
# (0.236 Gwei) confirmed on-chain, but slowly enough to blow past
# live.py's _wait_for_receipt timeout and get reported to the user as a
# failed/fabricated transaction when it had actually gone through — see
# that module's BuyPendingError for the other half of this fix. A flat
# buffer on top of the node's own quote meaningfully lowers the odds of
# landing under-priced if the network gets busier between quoting and
# inclusion.
GAS_PRICE_BUFFER_PCT = 30


def send_transaction(to: str, data: str | None, value, chain: str | None, *, on_gas_quote=None) -> str:
    if not _wallet:
        raise RuntimeError("app-managed wallet is locked")
    chain_id = chains_mod.resolve(chain)["chainId"]
    value_wei = int(value, 16) if isinstance(value, str) and value.startswith("0x") else int(value or 0)

    def _send(w3: Web3) -> str:
        quoted_gas_price = w3.eth.gas_price
        buffered_gas_price = quoted_gas_price * (100 + GAS_PRICE_BUFFER_PCT) // 100
        # GEMZ4US, Section F (2026-09-17): after locally verifying the gas
        # margin fix, the exact 30% figure wasn't independently checkable
        # from anything the app exposed — the Event Feed only ever showed
        # the token price, never the RPC's own quote. on_gas_quote lets a
        # caller (live.py's live_buy/live_sell) surface both numbers for
        # engine.py to log, without changing this function's return type
        # for callers that don't care (approve/unwrap).
        if on_gas_quote:
            on_gas_quote(quoted_gas_price, buffered_gas_price)
        tx = {
            "to": Web3.to_checksum_address(to),
            "value": value_wei,
            "data": data or "0x",
            "nonce": w3.eth.get_transaction_count(_wallet.address),
            "chainId": chain_id,
            "gasPrice": buffered_gas_price,
        }
        tx["gas"] = w3.eth.estimate_gas({**tx, "from": _wallet.address})
        signed = Account.sign_transaction(tx, _wallet.key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        # HexBytes.hex() (like plain bytes.hex()) omits the "0x" prefix —
        # ethers.js's tx.hash always includes it, so match that for any
        # caller/display code that assumes a "0x..." string (block
        # explorer links, etc).
        h = tx_hash.hex()
        return h if h.startswith("0x") else "0x" + h

    return _with_rpc(chain, _send)
