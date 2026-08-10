def launch_trader(parameters: dict = None, player=None) -> str:
    """Opens the native trader panel in place of the HUD — same mechanic as
    the old theme switcher (in-place swap, no separate window). Seraph
    itself never places trades: this only opens the panel, whose own typed
    command bar is the sole path that can move a position (see
    trader/engine.py's command() and core/prompt.txt's CRITICAL SAFETY
    RULE)."""
    if player is None:
        msg = "Sir, I don't have a window to open the trader panel in."
        _log(msg, player)
        return msg

    try:
        player.open_trader_panel()
    except Exception as e:
        msg = f"Sir, I couldn't open the trader panel: {e}"
        _log(msg, player)
        return msg

    msg = "Opening the trader panel, sir."
    _log(msg, player)
    return msg


def _log(message: str, player=None) -> None:
    print(f"[Trader] {message}")
    if player:
        try:
            player.write_log(f"SYS: {message}")
        except Exception:
            pass
