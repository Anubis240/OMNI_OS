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
        f"https://image.pollinations.ai/prompt/{quote(prompt)}"
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
