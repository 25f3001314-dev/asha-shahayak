import json
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from asha_shahayak.config import Settings
from asha_shahayak.audio import to_wav_16k
from asha_shahayak.storage import IntakeStore
from asha_shahayak.whatsapp import (
    reply_context_for_text,
)


ROOT = Path(__file__).resolve().parent
settings = Settings(database_path=str(ROOT / "demo.sqlite3"))
app = FastAPI(title="ASHA Shahayak Local Demo Bot")
logger = logging.getLogger(__name__)
VOICE_ERROR = "आवाज़ समझ नहीं आई, दोबारा बोलें या text भेजें।"


class TextMessage(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


def save_demo_message(payload: dict[str, Any]) -> tuple[str, bool]:
    return IntakeStore(settings.database_path, settings.receipt_prefix).save_or_get(
        source="ivr",
        external_message_id=f"demo-{uuid4().hex}",
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )[:2]


def bot_response(text: str, receipt_id: str) -> dict[str, Any]:
    reply, facts = reply_context_for_text(text, receipt_id, settings, "local-demo")
    return {
        "ok": True,
        "kind": "text",
        "transcript": text,
        "reply": reply,
        "receipt_id": receipt_id,
        "facts": [
            {
                "name": fact.name,
                "value": fact.value,
                "state": fact.state.value,
                "source": fact.source,
            }
            for fact in facts.all()
        ],
    }


async def transcribe_voice(audio: bytes) -> tuple[str, str]:
    """Convert browser audio and transcribe it with Sarvam's HTTP API."""
    if not settings.sarvam_api_key:
        raise ValueError("missing Sarvam API key")
    wav_audio = to_wav_16k(audio)
    files = {"file": ("voice.wav", wav_audio, "audio/wav")}
    data = {"model": "saarika:v2.5", "language_code": "hi-IN"}
    headers = {"api-subscription-key": settings.sarvam_api_key}
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(
            "https://api.sarvam.ai/speech-to-text",
            headers=headers,
            files=files,
            data=data,
        )
    response.raise_for_status()
    payload = response.json()
    transcript = str(payload.get("transcript") or "").strip()
    print(f"LOCAL_DEMO_TRANSCRIPT: {transcript}", flush=True)
    if not transcript:
        raise ValueError("empty transcript")
    return transcript, str(payload.get("language_code") or "hi-IN")


@app.get("/", response_class=FileResponse)
async def home() -> FileResponse:
    return FileResponse(ROOT / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "voice_transcription": bool(settings.sarvam_api_key),
        "database": settings.database_path,
    }


@app.post("/api/text")
async def text_message(message: TextMessage) -> dict[str, Any]:
    receipt_id, _duplicate = save_demo_message({"type": "text", "text": message.text})
    try:
        return bot_response(message.text, receipt_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/voice")
async def voice_message(request: Request) -> dict[str, Any]:
    audio = await request.body()
    open("/tmp/last_audio.bin", "wb").write(audio)
    if not audio:
        raise HTTPException(status_code=400, detail="Voice recording is empty")
    receipt_id, _duplicate = save_demo_message(
        {"type": "audio", "content_type": request.headers.get("content-type", "")}
    )
    if not settings.sarvam_api_key:
        return {
            "ok": False,
            "kind": "voice",
            "receipt_id": receipt_id,
            "error": VOICE_ERROR,
        }
    try:
        transcript, language = await transcribe_voice(audio)
        result = bot_response(transcript, receipt_id)
        result.update({"kind": "voice", "language": language})
        return result
    except (httpx.HTTPError, ValueError, OSError) as error:
        print("VOICE_FAIL:", repr(error), flush=True)
        return {
            "ok": False,
            "kind": "voice",
            "receipt_id": receipt_id,
            "error": VOICE_ERROR,
        }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("demo_test_bot.main:app", host="0.0.0.0", port=8010, reload=True)
