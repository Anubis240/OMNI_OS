def launch_trader(parameters: dict = None, player=None) -> str:
    """Opens the native trader panel in place of the HUD — same mechanic as
    the old theme switcher (in-place swap, no separate window). Omni
    itself never places trades: this only opens the panel, whose own typed
    command bar is the sole path that can move a position (see
    trader/engine.py's command() and core/prompt.txt's CRITICAL SAFETY
    RULE)."""
    from core import settings_store
    if not settings_store.load_settings()["trader"]["enabled"]:
        msg = "The trader panel isn't enabled. You can turn it on in the Settings panel."
        _log(msg, player)
        return msg

    if player is None:
        msg = "There's no window to open the trader panel in."
        _log(msg, player)
        return msg

    try:
        player.open_trader_panel()
    except Exception as e:
        msg = f"The trader panel didn't open: {e}"
        _log(msg, player)
        return msg

    msg = "Opening the trader panel."
    _log(msg, player)
    return msg


def _log(message: str, player=None) -> None:
    print(f"[Trader] {message}")
    if player:
        try:
            player.post(f"SYS: {message}")
        except Exception:
            pass
