"""Sarvam Bulbul Hindi text-to-speech integration."""

import base64
import logging
import os

import httpx

from .config import get_settings

logger = logging.getLogger(__name__)

TTS_URL = "https://api.sarvam.ai/text-to-speech"
DEFAULT_MODEL = "bulbul:v3"
# Speaker names are tied to the model version: "anushka" is a bulbul:v2 voice and
# is rejected by bulbul:v3, which made every voice reply fail silently.
DEFAULT_SPEAKERS = {"bulbul:v2": "anushka", "bulbul:v3": "ritu"}
FALLBACK_SPEAKER = "ritu"
MAX_CHARS = 2400  # bulbul:v3 accepts at most 2500 characters per request
_preferred_schema = 0  # remembers which request shape Sarvam accepted last


def _payloads(text: str, model: str, speaker: str) -> list[dict]:
    legacy = {
        "inputs": [text],
        "target_language_code": "hi-IN",
        "speaker": speaker,
        "model": model,
    }
    current = {
        "text": text,
        "language_code": "hi-IN",
        "speaker": speaker,
        "model": model,
    }
    return [legacy, current]


async def synthesize_hindi(text: str) -> bytes | None:
    """Return WAV bytes for the Hindi text, or None if TTS is unavailable."""
    global _preferred_schema
    settings = get_settings()
    text = (text or "").strip()
    if not settings.sarvam_api_key or not text:
        return None
    model = os.environ.get("ASHA_TTS_MODEL", "").strip() or DEFAULT_MODEL
    speaker = (
        os.environ.get("ASHA_TTS_SPEAKER", "").strip()
        or DEFAULT_SPEAKERS.get(model, FALLBACK_SPEAKER)
    )
    payloads = _payloads(text[:MAX_CHARS], model, speaker)
    order = [_preferred_schema] + [i for i in range(len(payloads)) if i != _preferred_schema]
    headers = {"api-subscription-key": settings.sarvam_api_key}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            for index in order:
                try:
                    response = await client.post(TTS_URL, headers=headers, json=payloads[index])
                    response.raise_for_status()
                except httpx.HTTPStatusError as error:
                    status = error.response.status_code
                    logger.warning(
                        "Sarvam TTS HTTP %s (model=%s speaker=%s): %s",
                        status, model, speaker, error.response.text[:200],
                    )
                    if status in (400, 422):
                        continue  # schema or speaker mismatch: try the other request shape
                    return None
                audios = response.json().get("audios") or [None]
                if not audios[0]:
                    logger.warning("Sarvam TTS returned no audio")
                    return None
                _preferred_schema = index
                return base64.b64decode(audios[0])
    except Exception as error:
        logger.warning("Sarvam TTS failed: %s", type(error).__name__)
    return None
