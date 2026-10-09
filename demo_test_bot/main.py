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
from asha_shahayak.factset import FactSet
from asha_shahayak.registry import AshaRegistry
from asha_shahayak.status import StatusStore
from asha_shahayak.storage import IntakeStore
from asha_shahayak.whatsapp import (
    ASHA_ID_PATTERN,
    reply_context_for_text,
)


ROOT = Path(__file__).resolve().parent
settings = Settings()
app = FastAPI(title="ASHA Shahayak Local Demo Bot")
logger = logging.getLogger(__name__)
VOICE_ERROR = "आवाज़ समझ नहीं आई, दोबारा बोलें या text भेजें।"
selected_asha_id: str | None = None
PAYMENT_QUERY_MARKERS = (
    "payment",
    "paisa",
    "paise",
    "eske payment",
    "iske payment",
    "bhugtan",
    "भुगतान",
    "पैसा",
    "पैसे",
    "iske payment",
    "इसका भुगतान",
    "इसके भुगतान",
)
STAGE_LABELS = {
    "anm_pending": "ANM वाउचर लंबित",
    "moic_pending": "MOIC स्वीकृति लंबित",
    "bam_pending": "BAM भुगतान लंबित",
    "paid": "भुगतान पूरा हो चुका है",
}


class TextMessage(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


def save_demo_message(payload: dict[str, Any]) -> tuple[str, bool]:
    return IntakeStore(settings.database_path, settings.receipt_prefix).save_or_get(
        source="ivr",
        external_message_id=f"demo-{uuid4().hex}",
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )[:2]


def payment_status_reply(asha_id: str) -> tuple[str, FactSet]:
    rows = StatusStore(settings.database_path).for_asha(asha_id)
    if not rows:
        return f"{asha_id} के लिए भुगतान की स्थिति अपलोड नहीं हुई है।", FactSet()
    lines = [f"{asha_id} के भुगतान की स्थिति:"]
    for row in rows:
        stage = STAGE_LABELS.get(row["stage"], row["stage"])
        amount_text = (
            f" स्वीकृत राशि ₹{row['approved_amount']}।"
            if row["approved_amount"] is not None
            else ""
        )
        date_text = f" तारीख: {row['stage_date']}।" if row["stage_date"] else ""
        lines.append(
            f"{row['month'].title()} महीने की {row['head']} मद: {stage}।"
            f"{amount_text}{date_text}"
        )
    return "\n".join(lines), FactSet()


def safe_reply_context(text: str, receipt_id: str) -> tuple[str, FactSet]:
    try:
        return reply_context_for_text(
            text,
            receipt_id,
            settings,
            "local-demo",
            selected_asha_id,
        )
    except ValueError as error:
        if "extraction_errors" not in str(error):
            raise
        if text.strip().isdigit():
            return (
                "कृपया पूरी ASHA ID भेजें, जैसे ASHA-106। "
                "भुगतान पूछने के लिए लिखें: इसके payment के बारे में बताओ।",
                FactSet(),
            )
        return (
            "मैं इस संदेश से भुगतान की जानकारी नहीं समझ पाई। "
            "पहले ASHA ID भेजें, जैसे ASHA-106। फिर लिखें: "
            "इसके payment के बारे में बताओ। "
            "किसी खास काम की जानकारी चाहिए तो काम, महीना, गिनती और मिली राशि भी लिखें।",
            FactSet(),
        )


def bot_response(text: str, receipt_id: str) -> dict[str, Any]:
    global selected_asha_id
    id_match = ASHA_ID_PATTERN.search(text)
    if (
        not id_match
        and selected_asha_id
        and any(marker in text.casefold() for marker in PAYMENT_QUERY_MARKERS)
    ):
        reply, facts = payment_status_reply(selected_asha_id)
    elif id_match:
        requested_id = id_match.group(1).upper()
        registry = AshaRegistry(settings.database_path, settings.session_salt)
        if requested_id not in registry.ids():
            reply = "This ASHA ID is not present in the uploaded registry."
            facts = FactSet()
        else:
            selected_asha_id = requested_id
            text = ASHA_ID_PATTERN.sub("", text).strip()
            if not text:
                reply = (
                    f"ASHA ID {selected_asha_id} चुन ली गई है। "
                    "अब भुगतान के बारे में पूछ सकते हैं।"
                )
                facts = FactSet()
            else:
                normalized = text.casefold()
                if selected_asha_id and any(
                    marker in normalized for marker in PAYMENT_QUERY_MARKERS
                ):
                    reply, facts = payment_status_reply(selected_asha_id)
                else:
                    normalized = text.casefold()
                    if selected_asha_id and any(
                        marker in normalized for marker in PAYMENT_QUERY_MARKERS
                    ):
                        reply, facts = payment_status_reply(selected_asha_id)
                    else:
                        reply, facts = safe_reply_context(text, receipt_id)
    else:
        reply, facts = safe_reply_context(text, receipt_id)
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
