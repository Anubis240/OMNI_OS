from core import settings_store


def get_credentials(catalog_id: str) -> dict | None:
    """Returns the saved field values for a connected integration, or None
    if the user hasn't filled them in yet."""
    entry = settings_store.load_settings()["integrations"].get(catalog_id)
    if not entry or not entry.get("enabled"):
        return None
    return entry


def log(service: str, message: str, player=None) -> None:
    print(f"[{service}] {message}")
    if player:
        try:
            player.write_log(f"SYS: [{service}] {message}")
        except Exception:
            pass
