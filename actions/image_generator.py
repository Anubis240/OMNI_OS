import json
import os
import random
import ssl
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from core.app_paths import get_data_dir
from urllib.parse import quote

import requests

# Switched 2026-09-06 from pollinations.ai's free, unauthenticated "flux"
# model — used alone — to Gemini's own native image generation, tried
# FIRST, with pollinations kept as a fallback. Root cause for the switch:
# a tester's targeted retest of the Sep 4 rollback (see the 2026-09-06
# report) showed the rollback's own risk assessment was wrong — the
# premise was that unintended nudity would be confined to rare, stylized/
# artistic prompts; the data showed the opposite. A plain, unstylized
# prompt ("a person standing in a garden") produced full nudity 4/6 times
# (67%), while every stylized prompt (Picasso, Renaissance oil painting)
# and the one non-human subject came back clean 100% of the time.
# pollinations.ai has no safety filtering of its own to catch that
# (confirmed: partial face-only blurring was observed on later outputs,
# suggesting *something* tries and fails, not that nothing exists at all).
#
# Why a fallback, not a straight swap: confirmed by live testing against
# this project's own real API key (2026-09-06) that Gemini's native image
# models — both gemini-3.1-flash-image and gemini-2.5-flash-image — return
# a hard 429 "limit: 0" on a bare, no-billing-enabled free-tier key, even
# though 2.5-flash-image is documented as free-tier-eligible in principle
# (500 images/day). The exact same key generates plain text fine. This app
# is BYOK against a bare free key by design — every other feature assumes
# that — so requiring billing for images alone, silently, would trade one
# regression (occasional unsafe output) for a worse one (the feature is
# dead for most real users). Gemini is tried first for the real safety
# filtering when billing happens to be available; pollinations.ai is the
# fallback for everyone else, exactly as before this change.
IMAGE_MODEL = "gemini-2.5-flash-image"  # NOT gemini-3.1-flash-image — confirmed
# billing-only (limit: 0 on the free tier) by live testing. If this model
# ever also moves to paid-only, every BYOK user loses the Gemini path and
# silently falls back to pollinations — check ai.google.dev/gemini-api/docs
# /image-generation and the free-tier docs before assuming this is broken.

# Cheap, always-on stopgap for the pollinations.ai fallback path specifically
# — a free, unauthenticated image model has no safety guardrails of its own.
_SAFETY_SUFFIX = ", safe for work, no nudity, no explicit or graphic content"

IMAGE_TIMEOUT_S = 60

# Generated images are shown from a temp folder, not saved to Downloads —
# opening the file lets the user view it and choose to "Save As" themselves
# rather than Omni deciding every generation is a keeper.
OUTPUT_DIR = Path(tempfile.gettempdir()) / "Omni Images"


def _get_base_dir() -> Path:
    return get_data_dir()


_API_CONFIG_PATH = _get_base_dir() / "config" / "api_keys.json"


def _get_api_key() -> str:
    with open(_API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


# main.py already solves this exact Windows TLS trust-store gap for its own
# Gemini Live connection (see _write_merged_ca_bundle() there, same logic
# verbatim) — process-wide, at import time, before this module runs. That
# "should already be inherited" assumption is exactly what broke down for
# real once before (this file's Sep 2 output-moderation call, on the real
# test machine, failed closed with a raw SSL error on every attempt) — so
# this module guarantees its own Gemini calls independently rather than
# relying on it a second time, now that this IS the primary generation
# path rather than a secondary check.
def _ensure_ca_bundle() -> None:
    # google-genai's httpx transport respects SSL_CERT_FILE (a standard
    # OpenSSL/ssl-module variable); the pollinations.ai fallback below uses
    # `requests`, which does NOT — it defaults to certifi's own bundle
    # unless REQUESTS_CA_BUNDLE is set explicitly. Confirmed by testing:
    # with only SSL_CERT_FILE set, the Gemini call succeeded past the TLS
    # gap but the pollinations.ai fallback still failed with the exact same
    # [SSL: CERTIFICATE_VERIFY_FAILED] error — both env vars need to point
    # at the same merged bundle for both HTTP libraries in this file to
    # actually benefit.
    bundle_path = _get_base_dir() / "config" / "ca_bundle.pem"
    if os.environ.get("SSL_CERT_FILE") and os.environ.get("REQUESTS_CA_BUNDLE"):
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
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        bundle_path.write_bytes(b"\n".join(parts))
        os.environ.setdefault("SSL_CERT_FILE", str(bundle_path))
        os.environ["REQUESTS_CA_BUNDLE"] = str(bundle_path)
    except Exception:
        pass  # best-effort — the caller still reports a clear error if this doesn't help


_ensure_ca_bundle()

_SAFETY_FINISH_REASONS = {"SAFETY", "IMAGE_SAFETY", "FinishReason.SAFETY", "FinishReason.IMAGE_SAFETY"}

# Local, offline safety net — added 2026-09-06 after research confirmed
# every generation-side option (Gemini's own image models, OpenAI's GPT
# Image, the DALL-E option the original Open-Jarvis fork this project
# started from once had — deprecated/removed from OpenAI's API entirely as
# of May 2026 anyway) gates its real safety filtering behind billing, so it
# only protects users who pay. This runs the same way for every user
# regardless of which backend answered or whether they have billing at
# all: nudenet ships its own ONNX model inside the pip package itself (no
# download, confirmed by building and running an actual frozen executable
# before this shipped), so it needs no API key, no network call, and can't
# ever be "unavailable" the way an external service can.
_NSFW_BLOCK_CLASSES = {
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "FEMALE_BREAST_EXPOSED",
    "BUTTOCKS_EXPOSED",
    "ANUS_EXPOSED",
}
# Deliberately NOT blocking on FEET_EXPOSED/BELLY_EXPOSED/ARMPITS_EXPOSED —
# the Sep 6 retest's real failures were full nudity, not bare feet or a
# midriff; blocking on those would just make ordinary portraits/swimwear
# feel broken for no safety benefit.
_NSFW_CONFIDENCE_THRESHOLD = 0.6  # calibrated against two real generations
# from this app's own history: a benign photo scored 0.325 on an unrelated,
# non-blocked class (false-positive-prone but harmless); a confirmed
# explicit generation scored 0.626-0.874 on classes in the block set above.
# 0.6 sits cleanly between the two with real margin on both sides.
_LOCAL_SAFETY_MAX_ATTEMPTS = 3  # applies only to the pollinations.ai retry
# loop below — each attempt already gets a fresh random seed (see
# _generate_via_pollinations), and the Sep 6 retest showed the same prompt
# doesn't reliably repeat the same result (4/6 unsafe, not 6/6), so a few
# extra rolls meaningfully raise the odds of a safe image instead of just
# refusing outright.

_nsfw_detector = None  # lazy singleton — loading the model has a real, if
# small, cost, and most sessions never generate an image at all.


def _get_nsfw_detector():
    global _nsfw_detector
    if _nsfw_detector is None:
        from nudenet import NudeDetector
        _nsfw_detector = NudeDetector()
    return _nsfw_detector


def _local_safety_check(image_bytes: bytes) -> tuple[bool, list[str]]:
    """Returns (is_safe, flagged_labels).

    Fails OPEN, not closed — the opposite of this file's network-based
    checks. A broken *local* classifier (missing model file, a bad
    onnxruntime build, a packaging gap) is a structural bug that would fail
    identically on every single call forever, not a transient hiccup worth
    being cautious about — blocking every image forever due to a packaging
    bug is a worse outcome than shipping with one fewer safety layer until
    it's fixed. The failure is still logged loudly so it surfaces in the
    next bug report rather than being silently swallowed.
    """
    try:
        detector = _get_nsfw_detector()
        detections = detector.detect(image_bytes)
    except Exception as e:
        print(f"[ImageGen] Local safety check failed to run ({e}) — allowing this image through unfiltered by this layer.")
        return True, []

    flagged = [
        d["class"] for d in detections
        if d.get("class") in _NSFW_BLOCK_CLASSES and d.get("score", 0) >= _NSFW_CONFIDENCE_THRESHOLD
    ]
    return not flagged, flagged


def _generate_via_gemini(prompt: str) -> tuple[str, bytes | None, str | None]:
    """Returns (status, image_bytes, mime_type_or_message):
      - status="ok": image_bytes/mime_type are the real result.
      - status="safety_blocked": Gemini's own filter made an actual content
        decision — the caller must NOT fall back to pollinations for this
        same prompt, since that would just launder the request through the
        one backend with no safety filtering at all, defeating the point.
      - status="unavailable": no content decision was made at all (a raised
        exception — network, auth, quota — or a response with no image and
        no positive safety signal). Safe, and intended, to fall back."""
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=_get_api_key(), http_options={"timeout": IMAGE_TIMEOUT_S * 1000})
        resp = client.models.generate_content(
            model=IMAGE_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
        )
    except Exception as e:
        return "unavailable", None, str(e)

    if getattr(resp, "prompt_feedback", None) and resp.prompt_feedback.block_reason:
        return "safety_blocked", None, "prompt blocked before generation"

    for cand in (resp.candidates or []):
        content = getattr(cand, "content", None)
        if not content or not content.parts:
            continue
        for part in content.parts:
            blob = getattr(part, "inline_data", None)
            if blob and blob.data:
                return "ok", blob.data, (blob.mime_type or "image/png")

    reasons = {str(getattr(cand, "finish_reason", "")) for cand in (resp.candidates or [])}
    if reasons & _SAFETY_FINISH_REASONS:
        return "safety_blocked", None, "generated content failed Gemini's safety filter"
    return "unavailable", None, "no image returned"


def _generate_via_pollinations(prompt: str) -> tuple[bytes | None, str | None, str | None]:
    """Returns (image_bytes, mime_type, error_message)."""
    url = (
        f"https://image.pollinations.ai/prompt/{quote(prompt + _SAFETY_SUFFIX)}"
        f"?width=1024&height=1024&nologo=true&model=flux&seed={random.randint(0, 2_000_000_000)}"
    )
    try:
        resp = requests.get(url, timeout=IMAGE_TIMEOUT_S)
        resp.raise_for_status()
    except Exception as e:
        return None, None, f"image generation failed: {e}"
    mime_type = resp.headers.get("content-type", "image/jpeg")
    if not resp.content or "image" not in mime_type:
        return None, None, "the image service didn't return an image — please try again"
    return resp.content, mime_type, None


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

    status, image_bytes, detail = _generate_via_gemini(prompt)

    if status == "safety_blocked":
        print(f"[ImageGen] Gemini blocked this generation: {detail}")
        msg = (
            "Sir, that image didn't pass Gemini's own safety filter, so I'm not "
            "showing it — try rephrasing the request."
        )
        _log(msg, player)
        return msg

    mime_type = "image/png"
    used_gemini = status == "ok"
    if used_gemini:
        mime_type = detail  # detail carries the mime type on the "ok" path
    else:
        print(f"[ImageGen] Gemini unavailable ({detail}) — falling back to pollinations.ai")
        image_bytes, mime_type, err = _generate_via_pollinations(prompt)
        if err:
            msg = f"Sir, {err}"
            _log(msg, player)
            return msg

    is_safe, flagged = _local_safety_check(image_bytes)
    if not is_safe and not used_gemini:
        # pollinations has no safety filtering of its own — worth a few
        # fresh rolls (new random seed each time) before giving up.
        for attempt in range(2, _LOCAL_SAFETY_MAX_ATTEMPTS + 1):
            print(f"[ImageGen] Local safety check flagged {flagged} — retrying pollinations.ai (attempt {attempt}/{_LOCAL_SAFETY_MAX_ATTEMPTS})")
            image_bytes, mime_type, err = _generate_via_pollinations(prompt)
            if err:
                msg = f"Sir, {err}"
                _log(msg, player)
                return msg
            is_safe, flagged = _local_safety_check(image_bytes)
            if is_safe:
                break
    # Note: a Gemini "ok" image flagged here is NOT retried — that would
    # silently re-bill the user's Gemini quota for what should be a rare,
    # defense-in-depth-only case (Gemini's own filter already passed it).

    if not is_safe:
        print(f"[ImageGen] Local safety check blocked this image: {flagged}")
        msg = (
            "Sir, the image that came back didn't pass a local safety check, "
            "so I'm not showing it — try rephrasing the request."
        )
        _log(msg, player)
        return msg

    ext = "png" if "png" in mime_type else "jpg"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"omni_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
    dest = OUTPUT_DIR / filename

    try:
        dest.write_bytes(image_bytes)
    except Exception as e:
        msg = f"Sir, I generated the image but couldn't display it: {e}"
        _log(msg, player)
        return msg

    try:
        if sys.platform == "win32":
            os.startfile(dest)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(dest)])
        else:
            subprocess.Popen(["xdg-open", str(dest)])
    except Exception as e:
        print(f"[ImageGen] Could not open image viewer: {e}")

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
