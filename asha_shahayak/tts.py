"""Sarvam Bulbul Hindi text-to-speech integration."""

import base64
import logging

import httpx

from .config import get_settings

logger = logging.getLogger(__name__)


async def synthesize_hindi(text: str) -> bytes | None:
    settings = get_settings()
    if not settings.sarvam_api_key or not text.strip():
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.sarvam.ai/text-to-speech",
                headers={"api-subscription-key": settings.sarvam_api_key},
                json={
                    "inputs": [text],
                    "target_language_code": "hi-IN",
                    "speaker": "anushka",
                    "model": "bulbul:v3",
                },
            )
        response.raise_for_status()
        audio = response.json().get("audios", [None])[0]
        return base64.b64decode(audio) if audio else None
    except Exception as error:
        logger.warning("Sarvam TTS failed: %s", type(error).__name__)
        return None
