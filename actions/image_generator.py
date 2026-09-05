import random
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

# A real second Gemini call to classify each generated image (added
# 2026-09-02, after a neutral "Picasso-style NFT" prompt produced
# unsolicited nudity) was tried and reverted 2026-09-05: independently
# verified as completely non-functional across three consecutive releases
# (v1.4.0, v1.5.0, v1.6.0) on the actual test machine — 100% "check failed"
# regardless of subject, language, or local firewall state, even after a
# TLS trust-store fix that tested successfully on the dev machine. A tool
# that never works is worse than one with an occasional content surprise,
# and — per product direction — a stylized/painterly nude is a different
# category of "unsafe" than explicit photorealistic content in the first
# place. Kept: this prompt-side suffix, which costs nothing, can't fail or
# hang, and reduces the odds of a repeat without a network dependency.
_SAFETY_SUFFIX = ", safe for work, no nudity, no explicit or graphic content"


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
