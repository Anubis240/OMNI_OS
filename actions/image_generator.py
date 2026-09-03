import json
import random
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


def _check_image_safety(image_bytes: bytes, mime_type: str) -> tuple[bool, str]:
    """Output-side moderation — the real fix, not just the prompt suffix
    above. This tool previously had NO check at all on the image it was
    about to show/deliver to the user; the only "moderation" was whichever
    judgment the live conversational model happened to apply before ever
    calling this tool, which a stylistic prompt can slip past even when the
    prompt itself is entirely innocuous (confirmed in testing). Runs a
    second, independent Gemini call purely to classify the actual pixels
    that came back. Fails CLOSED: if the check itself can't complete for
    any reason, the image is treated as unsafe rather than delivered
    unverified — the one place in this app where "assume the worst on
    error" is the correct default, not paranoia."""
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=_get_api_key())
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                "Does this image contain nudity, sexual content, or graphic "
                "violence/gore? Answer with exactly one word: YES or NO.",
            ],
        )
        answer = (resp.text or "").strip().upper()
        return (not answer.startswith("Y")), answer
    except Exception as e:
        return False, f"moderation check failed: {e}"


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

    is_safe, _reason = _check_image_safety(image_bytes, mime_type)
    if not is_safe:
        print(f"[ImageGen] blocked by output moderation: {_reason}")
        msg = (
            "Sir, that image didn't pass a safety check, so I'm not showing it — "
            "try rephrasing the request."
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
