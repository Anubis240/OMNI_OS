"""Omni-OS entry point: prepare the process, then start the window and the
voice assistant (voice/session.py) on a background thread."""

import os
import sys

# Packaged builds carry their own Playwright browsers next to the exe.
if getattr(sys, "frozen", False):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"

# The release pipeline runs the bundle once with --smoke-test before anything
# else (log files, TLS setup, audio) is touched.
if __name__ == "__main__" and "--smoke-test" in sys.argv:
    from bundle_smoke import run as _smoke
    raise SystemExit(_smoke())

from core.app_paths import get_data_dir  # noqa: E402


def _route_output() -> None:
    """A windowed build has no stdout/stderr at all, and a redirected one may
    default to a legacy code page; either way the first emoji print() would
    kill its thread silently. Send output to a UTF-8 log in the data folder."""
    if getattr(sys, "frozen", False) and (sys.stdout is None or sys.stderr is None):
        log = get_data_dir() / "seraph.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        stream = open(log, "a", encoding="utf-8", errors="replace", buffering=1)
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _hide_console() -> None:
    """python.exe always opens a console window; hide it straight away."""
    if sys.platform == "win32":
        import ctypes
        window = ctypes.windll.kernel32.GetConsoleWindow()
        if window:
            ctypes.windll.user32.ShowWindow(window, 0)


def _trust_system_roots() -> None:
    """Point SSL_CERT_FILE at certifi's bundle plus Windows' own root store.

    Security suites and corporate proxies re-sign HTTPS with a local root
    that Windows trusts but certifi can't contain, so the Gemini client
    (which reads SSL_CERT_FILE, else certifi) could never connect. A merged
    file only affects code that reads the variable — unlike patching
    ssl.SSLContext globally, which broke the dashboard's own HTTPS server.
    """
    if os.environ.get("SSL_CERT_FILE"):
        return
    import ssl
    import certifi
    pems = [open(certifi.where(), "rb").read()]
    if sys.platform == "win32":
        try:
            seen = set()
            for der, _enc, _trust in ssl.enum_certificates("ROOT"):
                if der not in seen:
                    seen.add(der)
                    pems.append(ssl.DER_cert_to_PEM_cert(der).encode("ascii"))
        except Exception:
            pass
    target = get_data_dir() / "config" / "ca_bundle.pem"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\n".join(pems))
    os.environ["SSL_CERT_FILE"] = str(target)


def main() -> None:
    _route_output()
    _hide_console()
    (get_data_dir() / "config").mkdir(parents=True, exist_ok=True)
    _trust_system_roots()

    import asyncio
    import threading
    from gui.facade import OmniUI
    from gui.theme import load_saved_voice
    from voice.session import Assistant

    window = OmniUI()

    def assistant_thread():
        window.wait_for_api_key()
        try:
            asyncio.run(Assistant(window, load_saved_voice).run())
        except KeyboardInterrupt:
            pass

    threading.Thread(target=assistant_thread, name="assistant", daemon=True).start()
    window.root.mainloop()


if __name__ == "__main__":
    main()
