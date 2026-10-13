import asyncio
import base64
import json
import logging
import re
import shutil
import time
from datetime import date
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
from asha_shahayak.tts import synthesize_hindi
from asha_shahayak.whatsapp import (
    ASHA_ID_PATTERN,
    reply_context_for_text,
)


ROOT = Path(__file__).resolve().parent
settings = Settings()
app = FastAPI(title="ASHA Shahayak Local Demo Bot")
logger = logging.getLogger(__name__)

STT_URL = "https://api.sarvam.ai/speech-to-text"
# saaras:v3 is Sarvam's current model; saarika:v2.5 is legacy and kept as a fallback.
STT_MODELS = (("saaras:v3", {"mode": "transcribe"}), ("saarika:v2.5", {}))
MAX_AUDIO_BYTES = 5 * 1024 * 1024
MIN_AUDIO_BYTES = 1500

VOICE_ERROR = "आवाज़ समझ नहीं आई, दोबारा बोलें या text भेजें।"
VOICE_MESSAGES = {
    "empty": "आवाज़ साफ़ नहीं आई, थोड़ा पास से और धीरे बोलकर दोबारा भेजें।",
    "too_short": "आवाज़ बहुत छोटी थी, कम से कम 2 सेकंड बोलें।",
    "too_long": "आवाज़ बहुत लंबी है, 30 सेकंड से छोटा message भेजें।",
    "no_key": "Voice अभी चालू नहीं है, कृपया text भेजें।",
    "auth": "Voice service की key सही नहीं है, कृपया text भेजें।",
    "quota": "Voice service अभी व्यस्त है, थोड़ी देर बाद कोशिश करें या text भेजें।",
    "service": "Voice service अभी जवाब नहीं दे रही, थोड़ी देर बाद कोशिश करें या text भेजें।",
}

# --- Conversation state -------------------------------------------------------
# The browser sends an `x-session-id` header so every visitor has their own selected
# ASHA ID. The module-level `selected_asha_id` is only the fallback for callers that
# send no session id (older clients and tests).
selected_asha_id: str | None = None
SESSION_TTL_SECONDS = 6 * 60 * 60
MAX_SESSIONS = 2000
_sessions: dict[str, tuple[str, float]] = {}
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

PAYMENT_QUERY_MARKERS = (
    "payment",
    "paisa",
    "paise",
    "eske payment",
    "iske payment",
    "bhugtan",
    "kab milega",
    "kab aayega",
    "kab ayega",
    "status",
    "भुगतान",
    "पैसा",
    "पैसे",
    "पेमेंट",
    "पेमेन्ट",
    "पेमेण्ट",
    "स्टेटस",
    "स्थिति",
    "कब मिलेगा",
    "कब आएगा",
    "कब आयेगा",
    "इसका भुगतान",
    "इसके भुगतान",
)
STAGE_LABELS = {
    "anm_pending": "ANM वाउचर लंबित",
    "moic_pending": "MOIC स्वीकृति लंबित",
    "bam_pending": "BAM भुगतान लंबित",
    "paid": "भुगतान पूरा हो चुका है",
}

# Plain spoken-Hindi names used only to make the payment reply sound human.
HEAD_HI = {
    "institutional_delivery": "संस्थागत प्रसव",
    "home_visit": "घर-घर विज़िट",
    "vaccination": "टीकाकरण",
    "family_planning": "परिवार नियोजन",
}
MONTH_HI = {
    "January": "जनवरी",
    "February": "फ़रवरी",
    "March": "मार्च",
    "April": "अप्रैल",
    "May": "मई",
    "June": "जून",
    "July": "जुलाई",
    "August": "अगस्त",
    "September": "सितंबर",
    "October": "अक्टूबर",
    "November": "नवंबर",
    "December": "दिसंबर",
}
MONTH_NAMES_HI = list(MONTH_HI.values())

# Voice transcripts say "ASHA 106" or "आशा १०६" instead of "ASHA-106".
SPOKEN_ID_PATTERN = re.compile(
    r"(?:asha|आशा)[\s\-_]*([A-Za-z]{0,3}[0-9०-९][A-Za-z0-9०-९_-]*)",
    re.IGNORECASE,
)
DIGIT_MAP = str.maketrans("०१२३४५६७८९", "0123456789")


class TextMessage(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    speak: bool = False


class VoiceError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def session_id_from(request: Request | None) -> str | None:
    if request is None:
        return None
    value = (request.headers.get("x-session-id") or "").strip()
    return value if SESSION_ID_PATTERN.match(value) else None


def _purge_sessions() -> None:
    now = time.time()
    for key in [k for k, (_, seen) in _sessions.items() if now - seen > SESSION_TTL_SECONDS]:
        _sessions.pop(key, None)
    while len(_sessions) > MAX_SESSIONS:
        _sessions.pop(next(iter(_sessions)))


def get_selected_asha_id(session_id: str | None) -> str | None:
    if not session_id:
        return selected_asha_id
    _purge_sessions()
    entry = _sessions.get(session_id)
    return entry[0] if entry else None


def set_selected_asha_id(session_id: str | None, asha_id: str) -> None:
    global selected_asha_id
    if not session_id:
        selected_asha_id = asha_id
        return
    _sessions.pop(session_id, None)
    _sessions[session_id] = (asha_id, time.time())
    _purge_sessions()


def save_demo_message(payload: dict[str, Any]) -> tuple[str, bool]:
    return IntakeStore(settings.database_path, settings.receipt_prefix).save_or_get(
        source="ivr",
        external_message_id=f"demo-{uuid4().hex}",
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )[:2]


def is_payment_query(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    return any(marker in normalized for marker in PAYMENT_QUERY_MARKERS)


def _spoken_date(iso: Any) -> str:
    """'2026-03-28' -> '28 मार्च'; empty string if the date cannot be read."""
    try:
        parsed = date.fromisoformat(str(iso))
        return f"{parsed.day} {MONTH_NAMES_HI[parsed.month - 1]}"
    except (TypeError, ValueError):
        return ""


def _whole_rupees(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def payment_status_reply(asha_id: str) -> tuple[str, FactSet]:
    rows = StatusStore(settings.database_path).for_asha(asha_id)
    if not rows:
        return f"{asha_id} के लिए भुगतान की स्थिति अपलोड नहीं हुई है।", FactSet()
    lines = [f"नमस्ते दीदी, {asha_id} के भुगतान की स्थिति ये है:"]
    paid_total = 0
    pending_total = 0
    for row in rows:
        stage = str(row["stage"] or "")
        head = HEAD_HI.get(row["head"], str(row["head"]).replace("_", " "))
        month_raw = str(row["month"]).title()
        month = MONTH_HI.get(month_raw, month_raw)
        amount = _whole_rupees(row["approved_amount"])
        when = _spoken_date(row["stage_date"])

        if stage == "paid":
            paid_total += amount
            on_date = f" {when} को" if when else ""
            lines.append(
                f"{month} में {head} के {amount} रुपये{on_date} आपको मिल चुके हैं।"
            )
        elif stage == "bam_pending":
            pending_total += amount
            lines.append(
                f"{month} में {head} के {amount} रुपये पास हो गए हैं, "
                "बस ब्लॉक से भुगतान आना बाकी है।"
            )
        elif stage == "moic_pending":
            pending_total += amount
            lines.append(
                f"{month} में {head} की स्वीकृति अभी MOIC के पास बाकी है।"
            )
        elif stage == "anm_pending":
            pending_total += amount
            lines.append(
                f"{month} में {head} का वाउचर अभी ANM के पास अटका है, "
                "इसलिए अभी भुगतान आगे नहीं बढ़ा है।"
            )
        else:
            pending_total += amount
            label = STAGE_LABELS.get(stage, stage)
            lines.append(f"{month} में {head} का काम अभी इस चरण में है: {label}।")
    if paid_total or pending_total:
        lines.append(
            f"कुल मिलाकर {paid_total} रुपये मिल चुके हैं "
            f"और {pending_total} रुपये आने बाकी हैं।"
        )
    return "\n".join(lines), FactSet()


def safe_reply_context(
    text: str,
    receipt_id: str,
    asha_id: str | None = None,
    sender: str = "local-demo",
) -> tuple[str, FactSet]:
    try:
        return reply_context_for_text(text, receipt_id, settings, sender, asha_id)
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


def extract_asha_id(text: str, known_ids: set[str]) -> tuple[str | None, str]:
    """Find an ASHA ID in typed or spoken text; return (id, text without the id)."""
    strict = ASHA_ID_PATTERN.search(text)
    if strict:
        return strict.group(1).upper(), ASHA_ID_PATTERN.sub("", text).strip()
    for match in SPOKEN_ID_PATTERN.finditer(text):
        candidate = "ASHA-" + match.group(1).translate(DIGIT_MAP).upper()
        if candidate in known_ids:  # only trust spoken IDs that really exist
            return candidate, f"{text[:match.start()]} {text[match.end():]}".strip()
    return None, text


def bot_response(
    text: str, receipt_id: str, session_id: str | None = None
) -> dict[str, Any]:
    original_text = text
    sender = f"demo-{session_id}" if session_id else "local-demo"
    known_ids = AshaRegistry(settings.database_path, settings.session_salt).ids()
    requested_id, remaining = extract_asha_id(text, known_ids)
    selected = get_selected_asha_id(session_id)

    if requested_id is None:
        if selected and is_payment_query(text):
            reply, facts = payment_status_reply(selected)
        else:
            reply, facts = safe_reply_context(text, receipt_id, selected, sender)
    elif requested_id not in known_ids:
        reply = (
            f"{requested_id} अपलोड की गई registry में नहीं मिली। "
            "ID जाँचकर दोबारा भेजें।"
        )
        facts = FactSet()
    else:
        selected = requested_id
        set_selected_asha_id(session_id, requested_id)
        if not remaining:
            reply = (
                f"ASHA ID {selected} चुन ली गई है। "
                "अब भुगतान के बारे में पूछ सकते हैं।"
            )
            facts = FactSet()
        elif is_payment_query(remaining):
            reply, facts = payment_status_reply(selected)
        else:
            reply, facts = safe_reply_context(remaining, receipt_id, selected, sender)

    return {
        "ok": True,
        "kind": "text",
        "transcript": original_text,
        "reply": reply,
        "receipt_id": receipt_id,
        "asha_id": selected,
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


# --- Voice in (speech to text) ---------------------------------------------------
def sniff_audio(audio: bytes, content_type: str = "") -> tuple[str, str]:
    """Return (filename, mime) for the audio, trusting the bytes over the header."""
    head = audio[:16]
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "voice.wav", "audio/wav"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "voice.webm", "audio/webm"
    if head[:4] == b"OggS":
        return "voice.ogg", "audio/ogg"
    if head[4:8] == b"ftyp":
        return "voice.m4a", "audio/mp4"
    if head[:4] == b"fLaC":
        return "voice.flac", "audio/flac"
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "voice.mp3", "audio/mpeg"
    declared = content_type.split(";")[0].strip().lower()
    known = {
        "audio/mpeg": ("voice.mp3", "audio/mpeg"),
        "audio/aac": ("voice.aac", "audio/aac"),
        "audio/x-m4a": ("voice.m4a", "audio/mp4"),
        "audio/mp4": ("voice.m4a", "audio/mp4"),
    }
    return known.get(declared, ("voice.webm", "audio/webm"))


def prepare_audio(audio: bytes, content_type: str = "") -> tuple[str, bytes, str]:
    """Convert to 16 kHz WAV when ffmpeg exists; otherwise send the original with its
    real type (Sarvam accepts webm/ogg/mp4). Sending webm labelled as .wav is what
    used to make voice fail on hosts without ffmpeg."""
    if shutil.which("ffmpeg"):
        converted = to_wav_16k(audio)
        if converted is not audio:
            return "voice.wav", converted, "audio/wav"
    name, mime = sniff_audio(audio, content_type)
    return name, audio, mime


async def transcribe_voice(audio: bytes, content_type: str = "") -> tuple[str, str]:
    """Transcribe browser audio with Sarvam's HTTP API."""
    if not settings.sarvam_api_key:
        raise VoiceError("no_key")
    filename, payload, mime = await asyncio.to_thread(prepare_audio, audio, content_type)
    headers = {"api-subscription-key": settings.sarvam_api_key}
    url = getattr(settings, "sarvam_api_url", "") or STT_URL
    failure = VoiceError("service")
    async with httpx.AsyncClient(timeout=45) as client:
        for model, extra in STT_MODELS:
            data = {"model": model, "language_code": "hi-IN", **extra}
            try:
                response = await client.post(
                    url,
                    headers=headers,
                    files={"file": (filename, payload, mime)},
                    data=data,
                )
            except httpx.HTTPError as error:
                logger.warning("Sarvam STT network error (%s): %s", model, type(error).__name__)
                failure = VoiceError("service", type(error).__name__)
                continue
            if response.status_code in (401, 403):
                raise VoiceError("auth", str(response.status_code))
            if response.status_code == 429:
                raise VoiceError("quota")
            if response.status_code >= 400:
                logger.warning(
                    "Sarvam STT HTTP %s (%s, %s): %s",
                    response.status_code, model, mime, response.text[:200],
                )
                failure = VoiceError("service", f"http_{response.status_code}")
                continue
            body = response.json()
            transcript = str(body.get("transcript") or "").strip()
            if transcript:
                logger.info("Voice transcribed (%s, %d chars)", model, len(transcript))
                return transcript, str(body.get("language_code") or "hi-IN")
            failure = VoiceError("empty")
    raise failure


# --- Voice out (text to speech) ---------------------------------------------------
def speech_text(reply: str) -> str:
    """Make a reply easier to read aloud."""
    text = re.sub(r"₹\s*([0-9][0-9,]*)", r"\1 रुपये", reply)
    text = re.sub(r"\s*\n\s*", "। ", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) > 2400:
        text = text[:2400].rsplit("।", 1)[0] + "।"
    return text


async def attach_audio(result: dict[str, Any]) -> None:
    """Add a spoken version of the reply; text reply is always kept as the fallback."""
    try:
        audio = await synthesize_hindi(speech_text(result.get("reply", "")))
    except Exception as error:
        logger.warning("voice reply failed: %s", type(error).__name__)
        audio = None
    if audio:
        result["audio_base64"] = base64.b64encode(audio).decode("ascii")
        result["audio_mime"] = "audio/wav"
    else:
        result["audio_error"] = "voice reply अभी उपलब्ध नहीं है, text जवाब ऊपर है।"


def _database_counts() -> dict[str, int | None]:
    try:
        registry = len(AshaRegistry(settings.database_path, settings.session_salt).ids())
        statuses = len(StatusStore(settings.database_path).all())
        return {"registry_count": registry, "status_rows": statuses}
    except Exception as error:
        logger.warning("health db check failed: %s", type(error).__name__)
        return {"registry_count": None, "status_rows": None}


@app.get("/", response_class=FileResponse)
async def home() -> FileResponse:
    return FileResponse(ROOT / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/health")
async def health() -> dict[str, Any]:
    has_key = bool(settings.sarvam_api_key)
    return {
        "ok": True,
        "voice_transcription": has_key,
        "voice_reply": has_key,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "session_salt_set": bool(settings.session_salt),
        "database": settings.database_path,
        **_database_counts(),
    }


@app.post("/api/text")
async def text_message(message: TextMessage, request: Request) -> dict[str, Any]:
    session_id = session_id_from(request)
    receipt_id, _duplicate = save_demo_message({"type": "text", "text": message.text})
    try:
        result = bot_response(message.text, receipt_id, session_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        logger.exception("text reply failed")
        raise HTTPException(
            status_code=500, detail="सर्वर में दिक्कत आई, कृपया दोबारा कोशिश करें।"
        ) from error
    if message.speak:
        await attach_audio(result)
    return result


def _voice_failure(receipt_id: str, error: VoiceError) -> dict[str, Any]:
    logger.warning("voice failed: %s", error)
    return {
        "ok": False,
        "kind": "voice",
        "receipt_id": receipt_id,
        "error_code": error.code,
        "error": VOICE_MESSAGES.get(error.code, VOICE_ERROR),
    }


@app.post("/api/voice")
async def voice_message(request: Request) -> dict[str, Any]:
    audio = await request.body()
    if not audio:
        raise HTTPException(status_code=400, detail="Voice recording is empty")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail=VOICE_MESSAGES["too_long"])
    content_type = request.headers.get("content-type", "")
    session_id = session_id_from(request)
    receipt_id, _duplicate = save_demo_message(
        {"type": "audio", "content_type": content_type}
    )
    if len(audio) < MIN_AUDIO_BYTES:
        return _voice_failure(receipt_id, VoiceError("too_short"))
    try:
        transcript, language = await transcribe_voice(audio, content_type)
    except VoiceError as error:
        return _voice_failure(receipt_id, error)
    except Exception as error:
        logger.exception("voice transcription crashed")
        return _voice_failure(receipt_id, VoiceError("service", type(error).__name__))
    try:
        result = bot_response(transcript, receipt_id, session_id)
    except Exception:
        logger.exception("voice reply crashed")
        return _voice_failure(receipt_id, VoiceError("service", "reply"))
    result.update({"kind": "voice", "language": language})
    await attach_audio(result)
    return result


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("demo_test_bot.main:app", host="0.0.0.0", port=8010, reload=True)