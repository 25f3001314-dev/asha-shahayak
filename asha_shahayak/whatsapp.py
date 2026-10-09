import hashlib
import hmac
import json
import logging
import tempfile
import asyncio
import re
import inspect
import unicodedata
from datetime import date
from typing import Annotated, Any, Protocol
from pathlib import Path

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from .asr import AsrEngine, AsrStub, SarvamAsr
from .audio import AudioTooShortError, prepare_for_asr, to_ogg_opus, to_wav_16k
from .config import Settings, get_settings
from .complaints import ComplaintStore
from .dates import format_date
from .extraction import ExtractedClaim, extract_claim
from .factset import FactSet, factset_from_result
from .intent import classify_intent, intent_reply
from .session import QueryMemory, sender_hash
from .reconciliation import reconcile_saved_text
from .registry import AshaRegistry
from .storage import IntakeStore
from .status import StatusStore
from .tts import synthesize_hindi
from .validator import safe_fallback, validate_reply

router = APIRouter()
logger = logging.getLogger(__name__)
_empty_salt_warning_logged = False


class MediaClient(Protocol):
    async def download(self, media_id: str) -> bytes:
        ...


class WhatsAppMediaClient:
    def __init__(self, access_token: str, graph_version: str) -> None:
        self.access_token = access_token
        self.graph_version = graph_version

    async def download(self, media_id: str) -> bytes:
        headers = {"Authorization": f"Bearer {self.access_token}"}
        async with httpx.AsyncClient(timeout=30) as client:
            media = await client.get(
                f"https://graph.facebook.com/{self.graph_version}/{media_id}",
                headers=headers,
            )
            media.raise_for_status()
            audio = await client.get(media.json()["url"], headers=headers)
            audio.raise_for_status()
            return audio.content


def whatsapp_is_configured(settings: Settings) -> bool:
    return all(
        (
            settings.meta_access_token,
            settings.meta_phone_number_id,
            settings.meta_verify_token,
            settings.meta_app_secret,
            settings.meta_graph_version,
        )
    )


def get_media_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> WhatsAppMediaClient:
    return WhatsAppMediaClient(settings.meta_access_token, settings.meta_graph_version)


def get_asr(settings: Annotated[Settings, Depends(get_settings)]) -> AsrEngine:
    if not settings.sarvam_api_key:
        return AsrStub()
    return SarvamAsr(settings.sarvam_api_key, settings.sarvam_api_url)


async def voice_text(asr: AsrEngine, audio: bytes) -> tuple[str, float]:
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as file:
            file.write(audio)
            temp_name = file.name
        raw_audio = Path(temp_name).read_bytes()
        if isinstance(asr, SarvamAsr):
            prepared_audio = prepare_for_asr(raw_audio)
        else:
            prepared_audio = to_wav_16k(raw_audio)
        return await asr.transcribe(prepared_audio)
    finally:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)


def verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.removeprefix("sha256="), expected)


def message_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            events.extend(change.get("value", {}).get("messages", []))
    return events


async def send_reply(
    settings: Settings,
    to: str,
    text: str,
    factset: FactSet | None = None,
    receipt_id: str = "",
) -> bool | None:
    import os
    if not to or os.environ.get("WA_REPLY") != "1":
        return
    ok, reasons = validate_reply(text, factset or FactSet())
    if not ok:
        logger.warning("WhatsApp reply blocked: %s", ",".join(reasons))
        text = safe_fallback(receipt_id)
    from .meta import MetaWhatsApp
    try:
        client = MetaWhatsApp(
            settings.meta_access_token,
            settings.meta_phone_number_id,
            settings.meta_graph_version,
        )
        await client.send_text(to, text)
    except AudioTooShortError:
        reply = "आवाज़ बहुत छोटी थी। कृपया कम से कम 2 सेकंड बोलकर दोबारा भेजें।"
        facts = FactSet()
        failed = False
    except Exception as error:
        response = getattr(error, "response", None)
        detail = response.text[:300] if response is not None else ""
        logger.warning("WhatsApp reply failed: %s %s", type(error).__name__, detail)
        return
    if ok:
        await _send_voice(client, to, text)


async def _send_voice(client: Any, to: str, text: str) -> None:
    """Best-effort voice note of the validated reply."""
    try:
        audio = await synthesize_hindi(text)
        if not audio:
            return
        ogg = await asyncio.to_thread(to_ogg_opus, audio)
        if not ogg:
            logger.warning("Voice reply skipped: audio conversion failed")
            return
        media_id = await client.upload_media(ogg)
        await client.send_audio(to, media_id)
    except Exception as error:
        logger.warning("Voice reply failed: %s", type(error).__name__)


FORMAT_HINT = "Samajh nahi aaya. Aise bhejein: Vaccination march count 3 amount 250"
ASHA_ID_PATTERN = re.compile(r"\b(ASHA-[A-Z0-9][A-Z0-9_-]*)\b", re.IGNORECASE)
REGISTRATION_PROMPT = "पहली बार register करने के लिए अपना ASHA ID भेजें।"
REGISTRATION_REJECTED = (
    "ये ASHA ID इस नंबर से match नहीं हुई, कृपया अपने block/ANM से संपर्क करें।"
)


def build_reply(result: dict, receipt_id: str) -> str:
    if result.get("status") == "medical_emergency":
        return "यह भुगतान का दावा नहीं बना। आपातकालीन मदद के लिए 108 या 112 पर कॉल करें।"
    gap = result.get("gap") or {}
    alias_missing = result.get("alias_missing")
    if alias_missing and alias_missing.get("heads"):
        heads = " ".join(
            f"{index}. {head}" for index, head in enumerate(alias_missing["heads"], 1)
        )
        return (
            f"इस नाम का रिकॉर्ड वाला मद नहीं मिला। कृपया {heads} में से "
            "सही मद का नंबर बताएं।"
        )
    expected = result.get("expected_amount")
    rid = result.get("receipt_id", receipt_id)
    lines = [f"प्रविष्टि मिल गई। रसीद ID: {rid}।"]
    if expected is not None:
        lines.append(f"रेट कार्ड के अनुसार अपेक्षित राशि: ₹{expected}।")
    rupees = gap.get("rupees")
    if gap.get("found") and rupees is not None and expected is not None:
        reported = expected - rupees
        if rupees > 0:
            lines.append(f"आपने ₹{reported} बताए, जो रेट कार्ड से ₹{rupees} कम है।")
            lines.append("भुगतान का सत्यापित रिकॉर्ड अभी नहीं देखा गया।")
            lines.append("शिकायत का मसौदा बन गया है, आपकी हाँ के बिना दर्ज नहीं होगा।")
        elif rupees == 0:
            lines.append("आपके बताए अनुसार राशि रेट कार्ड के बराबर है।")
        else:
            lines.append(f"आपने ₹{reported} बताए, जो रेट कार्ड से ₹{abs(rupees)} ज़्यादा है।")
    else:
        lines.append("रिकॉर्ड से अंतर तय नहीं हो सका।")
    if "SAMPLE_ONLY" in str(gap.get("trace", "")):
        lines.append("(रेट अभी नमूना है, सत्यापित नहीं।)")
    status = result.get("officer_status")
    if status:
        stage = status["stage"]
        if stage == "paid":
            lines.append(
                f"ब्लॉक रिकॉर्ड में भुगतान हो चुका है: ₹{status['approved_amount']}। "
                f"स्थिति की तारीख: {status.get('stage_date') or 'तारीख नहीं दी गई'}।"
            )
        else:
            labels = {
                "anm_pending": "ANM voucher pending",
                "moic_pending": "MOIC approval pending",
                "bam_pending": "BAM payment pending",
            }
            lines.append(f"ब्लॉक रिकॉर्ड में स्थिति: {labels[stage]}।")
            lines.append(
                "सामान्य चक्र के अनुसार, पक्का नहीं: 20 तारीख ANM voucher, "
                "28 तारीख MOIC approval, अगले महीने की 5 तारीख BAM payment।"
            )
    elif result.get("officer_status_checked"):
        lines.append("इस मद की प्रविष्टि ब्लॉक के रिकॉर्ड में नहीं मिली।")
    return " ".join(lines)


def date_gate_reply(claim: ExtractedClaim) -> str | None:
    if "date_needs_confirmation" in claim.errors:
        candidates = ", ".join(
            format_date(date.fromisoformat(item))
            for item in claim.day_candidates
        )
        return f"आप किस तारीख की बात कर रही हैं? विकल्प: {candidates}।"
    return None


def add_clear_date(reply: str, claim: ExtractedClaim) -> str:
    if len(claim.day_candidates) == 1:
        understood = format_date(date.fromisoformat(claim.day_candidates[0]))
        return f"मैंने समझा: {understood}। {reply}"
    return reply


_YES_TOKENS = {"haan", "han", "haa", "ha", "हाँ", "हां", "जी", "ji", "yes"}
_NO_TOKENS = {"nahin", "nahi", "na", "ना", "नहीं", "no"}
_YES_PHRASES = {"theek hai", "ठीक है"}
_NO_PHRASES = {"mat karo", "मत करो"}
_ESCAPE_PHRASES = (
    "रद्द करो", "रद्द", "cancel", "शुरू से", "shuru se", "main menu",
    "मुख्य मेनू", "reset",
)


def decided_reply(state: str) -> str:
    if state == "filed":
        return "यह शिकायत पहले ही दर्ज की जा चुकी है।"
    if state == "rejected":
        return "यह शिकायत आपके कहने पर दर्ज नहीं की गई थी।"
    return "यह शिकायत पहले ही तय हो चुकी है।"


def confirmation_value(text: str) -> bool | None:
    cleaned = text.strip()
    while cleaned and unicodedata.category(cleaned[0]).startswith(("P", "S")):
        cleaned = cleaned[1:]
    while cleaned and unicodedata.category(cleaned[-1]).startswith(("P", "S")):
        cleaned = cleaned[:-1]
    cleaned = re.sub(r"\s+", " ", cleaned.casefold())
    if cleaned in _YES_PHRASES:
        return True
    if cleaned in _NO_PHRASES:
        return False
    words = set(cleaned.split())
    has_yes = any(word in _YES_TOKENS for word in words)
    has_no = any(word in _NO_TOKENS for word in words)
    if has_yes and has_no:
        return None
    confirmation_fillers = {"bilkul", "theek", "ठीक", "है", "hai", "जी"}
    if cleaned and has_yes and words <= (_YES_TOKENS | confirmation_fillers):
        return True
    if cleaned and has_no and words <= (_NO_TOKENS | confirmation_fillers):
        return False
    return None


def is_escape_command(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    return any(phrase in normalized for phrase in _ESCAPE_PHRASES)


def _confirmation_context(
    text: str, settings: Settings, sender: str
) -> tuple[str, FactSet] | None:
    global _empty_salt_warning_logged
    if not settings.session_salt:
        if not _empty_salt_warning_logged:
            logger.warning("WhatsApp complaint confirmation disabled: session salt is empty")
            _empty_salt_warning_logged = True
        return None
    if is_escape_command(text):
        sender_key = sender_hash(sender, settings.session_salt)
        ComplaintStore(settings.database_path).reset_sender_state(sender_key)
        QueryMemory(settings.database_path, settings.session_salt).clear(sender)
        return "ठीक है। पिछली प्रक्रिया रद्द कर दी गई है। आप शुरू से बताइए।", FactSet()
    complaint = ComplaintStore(settings.database_path).latest_pending_for_sender(
        sender_hash(sender, settings.session_salt)
    )
    if complaint is None:
        if confirmation_value(text) is not None:
            decided = ComplaintStore(settings.database_path).latest_decided_for_sender(
                sender_hash(sender, settings.session_salt)
            )
            if decided:
                return decided_reply(decided["state"]), FactSet()
        return None
    value = confirmation_value(text)
    if value is None:
        retries = ComplaintStore(settings.database_path).increment_confirmation_retry(
            complaint["complaint_id"]
        )
        if retries >= 3:
            ComplaintStore(settings.database_path).reset_sender_state(
                sender_hash(sender, settings.session_salt)
            )
            return (
                "उत्तर स्पष्ट नहीं मिला। प्रक्रिया रीसेट कर दी गई है। "
                "कृपया अपना सवाल फिर से भेजें।",
                FactSet(),
            )
        return "कृपया केवल हाँ या नहीं लिखें।", FactSet()
    decision = ComplaintStore(settings.database_path).decide(
        complaint["complaint_id"], value
    )
    if decision.get("already_recorded"):
        return decided_reply(decision.get("state", "")), FactSet()
    if value:
        return (
            "आपकी शिकायत सहेज दी गई है और ब्लॉक अधिकारी के डैशबोर्ड पर दिखाई देगी।",
            FactSet(),
        )
    return "ठीक है, शिकायत दर्ज नहीं की गई।", FactSet()


def reply_context_for_text(
    text: str,
    receipt_id: str,
    settings: Settings,
    sender: str = "",
    demo_asha_id: str | None = None,
) -> tuple[str, FactSet]:
    registry = AshaRegistry(settings.database_path, settings.session_salt)
    resolved_asha_id: str | None = None
    registration_active = bool(settings.session_salt and registry.has_entries())
    if demo_asha_id is not None:
        resolved_asha_id = demo_asha_id
    elif registration_active:
        resolved_asha_id = registry.resolve_sender(sender)
        if resolved_asha_id is None:
            id_match = ASHA_ID_PATTERN.search(text)
            if id_match is None:
                return REGISTRATION_PROMPT, FactSet()
            requested_id = id_match.group(1).upper()
            if not registry.bind_sender(requested_id, sender):
                return REGISTRATION_REJECTED, FactSet()
            return f"आपका registration हो गया, ASHA ID: {requested_id}।", FactSet()
    if sender:
        confirmation = _confirmation_context(text, settings, sender)
        if confirmation is not None:
            return confirmation
    status_store = StatusStore(settings.database_path)
    normalized_text = text
    for spoken_name, head in status_store.aliases():
        normalized_text = re.sub(
            re.escape(spoken_name), head, normalized_text, flags=re.IGNORECASE
        )
    claim = extract_claim(normalized_text)
    if claim.activity is None and claim.month:
        heads = status_store.heads_for_month(claim.month)
        if heads:
            options = " ".join(
                f"{index}. {head}" for index, head in enumerate(heads, 1)
            )
            return (
                f"इस नाम का रिकॉर्ड वाला मद नहीं मिला। कृपया {options} में से "
                "सही मद का नंबर बताएं।",
                FactSet(),
            )
    gated = date_gate_reply(claim)
    if gated:
        return gated, FactSet()
    intent = classify_intent(text)
    has_structured_claim = (
        claim.activity is not None
        and claim.count is not None
        and "activity_not_found" not in claim.errors
        and "count_invalid" not in claim.errors
    )
    if intent != "unknown" and not has_structured_claim:
        if intent == "followup_status" and settings.session_salt:
            previous = QueryMemory(
                settings.database_path, settings.session_salt
            ).get(sender)
            if previous:
                return (
                    f"पिछली पुष्टि की प्रविष्टि की रसीद ID: {previous['receipt_id']}। "
                    "रिकॉर्ड से भुगतान की तारीख पक्की नहीं है।"
                ), FactSet()
        return intent_reply(intent), FactSet()
    if confirmation_value(text) is not None and not has_structured_claim:
        return FORMAT_HINT, FactSet()
    parameters = inspect.signature(reconcile_saved_text).parameters
    kwargs = {}
    if "sender" in parameters:
        kwargs["sender"] = sender
    if "asha_id" in parameters:
        kwargs["asha_id"] = resolved_asha_id
    result = reconcile_saved_text(normalized_text, receipt_id, settings, **kwargs)
    if (
        settings.session_salt
        and has_structured_claim
        and len(claim.day_candidates) == 1
        and result.get("status") == "reconciled"
    ):
        QueryMemory(settings.database_path, settings.session_salt).save(
            sender,
            claim.activity,
            claim.day_candidates[0],
            result.get("receipt_id", receipt_id),
        )
    reply = add_clear_date(build_reply(result, receipt_id), claim)
    if result.get("complaint") and settings.session_salt and sender:
        reply = (
            f"दावा: गतिविधि {claim.activity}, महीना {claim.month or 'नहीं मिला'}। "
            + reply
        )
        reply += " कृपया शिकायत की पुष्टि के लिए केवल हाँ या नहीं लिखें।"
    return reply, factset_from_result(result)


def reply_for_text(
    text: str,
    receipt_id: str,
    settings: Settings,
    sender: str = "",
    demo_asha_id: str | None = None,
) -> str:
    return reply_context_for_text(
        text, receipt_id, settings, sender, demo_asha_id
    )[0]


async def _process_message(
    message: dict[str, Any],
    payload: dict[str, Any],
    settings: Settings,
    media_client: MediaClient,
    asr: AsrEngine,
    receipt_id: str,
) -> None:
    message_id = message.get("id")
    if not message_id:
        return
    if message.get("type") == "text":
        text = message.get("text", {}).get("body", "")
        to = message.get("from", "")
        failed = False
        try:
            reply, facts = reply_context_for_text(text, receipt_id, settings, to)
        except Exception as error:
            logger.warning("WhatsApp reconciliation failed: %s", type(error).__name__)
            reply = FORMAT_HINT
            facts = FactSet()
            failed = True
        await send_reply(settings, to, reply, facts, receipt_id)
        if failed:
            return False
        return
    if message.get("type") == "audio":
        to = message.get("from", "")
        failed = False
        try:
            audio = await media_client.download(message["audio"]["id"])
            text, _score = await voice_text(asr, audio)
            if not text.strip():
                raise ValueError("empty transcript")
            voice_reply, facts = reply_context_for_text(
                text, receipt_id, settings, to
            )
            reply = f'मैंने सुना: "{text.strip()}"\n' + voice_reply
        except Exception as error:
            logger.warning("voice failed: %s", type(error).__name__)
            reply = f"आपकी आवाज़ मिल गई। रसीद ID: {receipt_id}. आवाज़ साफ़ नहीं आई, कृपया दोबारा बोलें या लिखकर भेजें।"
            facts = FactSet()
            failed = True
        await send_reply(settings, to, reply, facts, receipt_id)
        if failed:
            return False
        return


async def process_message(
    message: dict[str, Any],
    payload: dict[str, Any],
    settings: Settings,
    media_client: MediaClient,
    asr: AsrEngine,
) -> None:
    message_id = message.get("id")
    if not message_id:
        return
    store = IntakeStore(settings.database_path, settings.receipt_prefix)
    receipt_id, duplicate, _ = store.save_or_get(
        source="whatsapp",
        external_message_id=message_id,
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )
    if duplicate and store.status_for(message_id) == "processed":
        return
    try:
        processed = await _process_message(
            message, payload, settings, media_client, asr, receipt_id
        )
    except Exception:
        store.mark_status(receipt_id, "failed")
        raise
    store.mark_status(receipt_id, "processed" if processed is not False else "failed")


async def process_payload(
    payload: dict[str, Any],
    settings: Settings,
    media_client: MediaClient,
    asr: AsrEngine,
) -> None:
    for message in message_events(payload):
        await process_message(message, payload, settings, media_client, asr)


@router.get("/webhooks/whatsapp", response_class=PlainTextResponse)
async def verify(
    settings: Annotated[Settings, Depends(get_settings)],
    mode: str = Query(alias="hub.mode"),
    token: str = Query(alias="hub.verify_token"),
    challenge: str = Query(alias="hub.challenge"),
) -> str:
    if not settings or not whatsapp_is_configured(settings):
        raise HTTPException(status_code=503, detail="WhatsApp is not configured")
    if mode != "subscribe" or not hmac.compare_digest(token, settings.meta_verify_token):
        raise HTTPException(status_code=403, detail="webhook verification failed")
    return challenge


@router.post("/webhooks/whatsapp", status_code=200)
async def receive(
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
    media_client: Annotated[MediaClient, Depends(get_media_client)],
    asr: Annotated[AsrEngine, Depends(get_asr)],
    signature: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> dict[str, bool]:
    if not whatsapp_is_configured(settings):
        raise HTTPException(status_code=503, detail="WhatsApp is not configured")
    body = await request.body()
    if not verify_signature(body, signature, settings.meta_app_secret):
        raise HTTPException(status_code=403, detail="invalid webhook signature")
    payload = json.loads(body)
    background_tasks.add_task(process_payload, payload, settings, media_client, asr)
    return {"received": True}
