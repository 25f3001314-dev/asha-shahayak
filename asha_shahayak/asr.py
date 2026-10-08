from typing import Protocol

from sarvamai import AsyncSarvamAI


class AsrEngine(Protocol):
    async def transcribe(self, audio: bytes) -> tuple[str, str | None]:
        ...


class AsrStub:
    """Text-first placeholder; a real provider can implement AsrEngine later."""

    async def transcribe(self, audio: bytes) -> tuple[str, str | None]:
        raise NotImplementedError("ASR provider is not configured")


class SarvamAsr:
    def __init__(self, api_key: str, url: str = "") -> None:
        if not api_key:
            raise ValueError("SARVAM_API_KEY is not configured")
        self.api_key = api_key
        self.client = AsyncSarvamAI(api_subscription_key=api_key)

    async def transcribe(self, audio: bytes) -> tuple[str, str | None]:
        response = await self.client.speech_to_text.transcribe(
            file=("voice.wav", audio, "audio/wav"),
            model="saaras:v3",
            mode="transcribe",
            language_code="hi-IN",
            input_audio_codec="wav",
        )
        if not response.transcript:
            raise ValueError("Sarvam returned no transcript")
        return response.transcript, response.language_code
