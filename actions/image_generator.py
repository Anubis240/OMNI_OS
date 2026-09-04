import json
import os
import random
import ssl
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests

# Generated images are shown from a temp folder, not saved to Downloads —
# opening the file lets the user view it and choose to "Save As" themselves
# rather than Omni deciding every generation is a keeper.
OUTPUT_DIR  = Path(tempfile.gettempdir()) / "Omni Images"
IMAGE_MODEL = "flux"  # pollinations.ai model — no API key required

# Cheap, immediate stopgap (defense-in-depth, not a substitute for the real
# check below) — a free, unauthenticated image model has no safety
# guardrails comparable to a first-party one. Confirmed in testing: a
# completely neutral prompt ("NFT in the style of Picasso") produced
# unsolicited nudity, most likely from the model's own stylistic
# association with Picasso's nude-figure-heavy body of work — something a
# prompt-text filter can't catch, since the input itself was benign.
_SAFETY_SUFFIX = ", safe for work, no nudity, no explicit or graphic content"


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_API_CONFIG_PATH = _get_base_dir() / "config" / "api_keys.json"


def _get_api_key() -> str:
    with open(_API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


# main.py already solves this exact problem for its own Gemini Live
# connection (see _write_merged_ca_bundle() there, same logic verbatim) —
# on some Windows machines, certifi's bundled root list alone isn't enough
# to verify Google's TLS chain (a corporate/personal security product
# injecting its own root CA into Windows' store, not into certifi's static
# list, is the common cause), so httpx/ssl reject the connection outright.
# Confirmed empirically: this tool's new output-moderation check (added to
# fix a real content-safety gap) was the first code in this file to ever
# call Gemini directly rather than the unauthenticated pollinations.ai
# endpoint — on an affected machine that meant EVERY moderation call failed
# closed with a raw SSL error, before any image content was even
# evaluated, which is exactly why testing saw 100% blocking with zero
# correlation to actual content. main.py's own fix sets this up
# process-wide (os.environ["SSL_CERT_FILE"]) before this module ever runs,
# so in principle it's already inherited — but that's exactly the
# assumption that broke down for real, so this module now guarantees it
# for its own Gemini calls independently rather than relying on it.
def _ensure_ca_bundle() -> None:
    if os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import certifi
        parts = [Path(certifi.where()).read_bytes()]
        if sys.platform == "win32":
            try:
                seen = set()
                for der, _encoding, _trust in ssl.enum_certificates("ROOT"):
                    if der not in seen:
                        seen.add(der)
                        parts.append(ssl.DER_cert_to_PEM_cert(der).encode("ascii"))
            except Exception:
                pass
        bundle_path = _get_base_dir() / "config" / "ca_bundle.pem"
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        bundle_path.write_bytes(b"\n".join(parts))
        os.environ["SSL_CERT_FILE"] = str(bundle_path)
    except Exception:
        pass  # best-effort — _check_image_safety() still fails closed if this doesn't help


_ensure_ca_bundle()


def _check_image_safety(image_bytes: bytes, mime_type: str) -> tuple[str, str]:
    """Output-side moderation — the real fix, not just the prompt suffix
    above. This tool previously had NO check at all on the image it was
    about to show/deliver to the user; the only "moderation" was whichever
    judgment the live conversational model happened to apply before ever
    calling this tool, which a stylistic prompt can slip past even when the
    prompt itself is entirely innocuous (confirmed in testing). Runs a
    second, independent Gemini call purely to classify the actual pixels
    that came back.

    Returns (status, detail): status is "safe", "unsafe", or "error" — kept
    distinct so the caller can tell a genuine content flag apart from the
    check itself failing. A real point of confusion in testing: a TLS
    trust-store issue on the test machine made this call fail closed on
    every single attempt regardless of content, and with no way to
    distinguish "correctly blocked" from "broken and blocking everything",
    it read as the moderation feature being fundamentally non-functional
    rather than a fixable technical fault. Both "unsafe" and "error" still
    fail CLOSED (the image is never delivered either way) — only the
    user-facing message differs, so this failure mode is never invisible
    or ambiguous again. 60s request timeout: an earlier version had none,
    and a stuck/black-holed connection (e.g. an intercepting security
    product on the network path) left a request pending indefinitely with
    no verdict ever returned, rather than failing fast."""
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=_get_api_key(), http_options={"timeout": 60_000})
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                "Does this image contain nudity, sexual content, or graphic "
                "violence/gore? Answer with exactly one word: YES or NO.",
            ],
        )
        answer = (resp.text or "").strip().upper()
        if not answer:
            return "error", "empty response from moderation check"
        return ("unsafe" if answer.startswith("Y") else "safe"), answer
    except Exception as e:
        return "error", str(e)


def generate_image(
    parameters: dict,
    player=None,
    speak=None,
    notify_image=None,
) -> str:
    params = parameters or {}
    prompt = (params.get("prompt") or "").strip()

    if not prompt:
        msg = "Sir, I need a description of the image you want."
        _log(msg, player)
        return msg

    if speak:
        speak("Generating that image now, sir — one moment.")

    url = (
        f"https://image.pollinations.ai/prompt/{quote(prompt + _SAFETY_SUFFIX)}"
        f"?width=1024&height=1024&nologo=true&model={IMAGE_MODEL}"
        f"&seed={random.randint(0, 2_000_000_000)}"
    )

    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        image_bytes = resp.content
        mime_type = resp.headers.get("content-type", "image/jpeg")
    except Exception as e:
        msg = f"Sir, image generation failed: {e}"
        _log(msg, player)
        return msg

    if not image_bytes or "image" not in mime_type:
        msg = "Sir, the image service didn't return an image — please try again."
        _log(msg, player)
        return msg

    status, detail = _check_image_safety(image_bytes, mime_type)
    if status != "safe":
        print(f"[ImageGen] not delivered ({status}): {detail}")
        if status == "unsafe":
            msg = (
                "Sir, that image didn't pass a safety check, so I'm not showing it — "
                "try rephrasing the request."
            )
        else:
            # "error", not "unsafe" — the check itself failed (network/API
            # issue), not a genuine content flag. Distinct wording so this
            # never reads as "content was rejected" when it wasn't actually
            # evaluated at all.
            msg = (
                "Sir, I couldn't verify that image was safe to show — a technical "
                "issue with the safety check itself, not a content flag. Please try again."
            )
        _log(msg, player)
        return msg

    ext = "png" if "png" in mime_type else "jpg"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"seraph_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
    dest = OUTPUT_DIR / filename

    try:
        dest.write_bytes(image_bytes)
    except Exception as e:
        msg = f"Sir, I generated the image but couldn't display it: {e}"
        _log(msg, player)
        return msg

    try:
        import os
        os.startfile(dest)
    except Exception:
        pass

    if notify_image:
        try:
            notify_image(image_bytes, mime_type)
        except Exception:
            pass

    msg = "Here's your image, sir — let me know if you'd like to save it."
    _log(msg, player)
    return msg


def _log(message: str, player=None) -> None:
    print(f"[ImageGen] {message}")
    if player:
        try:
            player.write_log(f"SYS: {message}")
        except Exception:
            pass
