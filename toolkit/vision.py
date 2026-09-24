"""screen_process — look at the screen or the webcam and answer a question.

Captures one frame, asks Gemini about it, and returns the answer as the
tool result, so the live session speaks it in its own voice.
"""

from __future__ import annotations

import io
import sys

from toolkit import llm
from toolkit.base import ToolContext, register, S

_MAX_SIDE = 1280
_camera_index: int | None = None


def _as_jpeg(image) -> bytes:
    image = image.convert("RGB")
    image.thumbnail((_MAX_SIDE, _MAX_SIDE))
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def grab_screen() -> bytes:
    import mss
    from PIL import Image
    with mss.mss() as grabber:
        monitor = grabber.monitors[1] if len(grabber.monitors) > 1 else grabber.monitors[0]
        shot = grabber.grab(monitor)
    return _as_jpeg(Image.frombytes("RGB", shot.size, shot.rgb))


def _open_camera(index: int):
    import cv2
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else (
        cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY)
    cam = cv2.VideoCapture(index, backend)
    return cam if cam.isOpened() else None


def grab_camera() -> bytes:
    """First camera that gives a non-black frame; remembered for the session."""
    global _camera_index
    import cv2
    from PIL import Image
    candidates = [_camera_index] if _camera_index is not None else list(range(4))
    for index in candidates:
        cam = _open_camera(index)
        if cam is None:
            continue
        try:
            frame = None
            for _ in range(8):          # let exposure settle
                ok, frame = cam.read()
            if frame is not None and frame.mean() > 8:
                _camera_index = index
                return _as_jpeg(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
        finally:
            cam.release()
    _camera_index = None
    raise RuntimeError("no working camera found")


@register(
    "screen_process",
    "Looks at the user's screen (or webcam) and answers a question about what's "
    "visible. You have no sight without this tool — call it whenever the user asks "
    "what's on screen, to read or check something visible, or to look through the "
    "camera. Speak its answer to the user.",
    {
        "angle": S("'screen' (default) or 'camera'"),
        "text": S("The question or instruction about the image"),
    },
    ["text"],
)
def screen_process(args: dict, ctx: ToolContext) -> str:
    question = (args.get("text") or "").strip() or "Describe what you see."
    use_camera = (args.get("angle") or "screen").strip().lower() == "camera"
    ctx.log(f"[Vision] {'camera' if use_camera else 'screen'}: {question[:60]}")
    try:
        image = grab_camera() if use_camera else grab_screen()
    except Exception as err:
        return f"I couldn't capture the {'camera' if use_camera else 'screen'}: {err}"
    try:
        return llm.ask(
            [llm.image_part(image, "image/jpeg"), question],
            system="You are looking at the user's " + ("webcam image" if use_camera else "computer screen")
                   + ". Answer the question directly and concisely, reading any text exactly when asked.",
        )
    except Exception as err:
        return f"I captured the image but couldn't analyse it: {err}"
