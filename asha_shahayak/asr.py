from typing import Protocol

import httpx


class AsrEngine(Protocol):
    async def transcribe(self, audio: bytes) -> tuple[str, float]:
        ...


class AsrStub:
    """Text-first placeholder; a real provider can implement AsrEngine later."""

    async def transcribe(self, audio: bytes) -> tuple[str, float]:
        raise NotImplementedError("ASR provider is not configured")


class SarvamAsr:
    def __init__(self, api_key: str, url: str) -> None:
        if not api_key:
            raise ValueError("SARVAM_API_KEY is not configured")
        self.api_key = api_key
        self.url = url

    async def transcribe(self, audio: bytes) -> tuple[str, float]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                self.url,
                headers={"api-subscription-key": self.api_key},
                files={"file": ("voice.ogg", audio, "audio/ogg")},
            )
        response.raise_for_status()
        data = response.json()
        text = data.get("transcript") or data.get("text")
        if not text:
            raise ValueError("Sarvam returned no transcript")
        return text, float(data.get("confidence", 0.0))
