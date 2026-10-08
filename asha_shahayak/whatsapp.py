import hashlib
import hmac
import json
import logging
import tempfile
from datetime import date
from typing import Annotated, Any, Protocol
from pathlib import Path

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from .asr import AsrEngine, AsrStub, SarvamAsr
from .audio import to_wav_16k
from .config import Settings, get_settings
from .dates import format_date
from .extraction import ExtractedClaim, extract_claim
from .reconciliation import reconcile_saved_text
from .storage import IntakeStore

router = APIRouter()
logger = logging.getLogger(__name__)


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
        return await asr.transcribe(to_wav_16k(Path(temp_name).read_bytes()))
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


async def send_reply(settings: Settings, to: str, text: str) -> None:
    import os
    if not to or os.environ.get("WA_REPLY") != "1":
        return
    from .meta import MetaWhatsApp
    try:
        client = MetaWhatsApp(
            settings.meta_access_token,
            settings.meta_phone_number_id,
            settings.meta_graph_version,
        )
        await client.send_text(to, text)
    except Exception as error:
        response = getattr(error, "response", None)
        detail = response.text[:300] if response is not None else ""
        logger.warning("WhatsApp reply failed: %s %s", type(error).__name__, detail)


FORMAT_HINT = "Samajh nahi aaya. Aise bhejein: Vaccination march count 3 amount 250"


def build_reply(result: dict, receipt_id: str) -> str:
    if result.get("status") == "medical_emergency":
        return "यह भुगतान का दावा नहीं बना। आपातकालीन मदद के लिए 108 या 112 पर कॉल करें।"
    gap = result.get("gap") or {}
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


def reply_for_text(text: str, receipt_id: str, settings: Settings) -> str:
    claim = extract_claim(text)
    gated = date_gate_reply(claim)
    if gated:
        return gated
    result = reconcile_saved_text(text, receipt_id, settings)
    return add_clear_date(build_reply(result, receipt_id), claim)


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
    if duplicate:
        return
    if message.get("type") == "text":
        text = message.get("text", {}).get("body", "")
        to = message.get("from", "")
        try:
            reply = reply_for_text(text, receipt_id, settings)
        except Exception as error:
            logger.warning("WhatsApp reconciliation failed: %s", type(error).__name__)
            reply = FORMAT_HINT
        await send_reply(settings, to, reply)
        return
    if message.get("type") == "audio":
        to = message.get("from", "")
        try:
            audio = await media_client.download(message["audio"]["id"])
            text, _score = await voice_text(asr, audio)
            if not text.strip():
                raise ValueError("empty transcript")
            reply = f'मैंने सुना: "{text.strip()}"\n' + reply_for_text(
                text, receipt_id, settings
            )
        except Exception as error:
            logger.warning("voice failed: %s", type(error).__name__)
            reply = f"आपकी आवाज़ मिल गई। रसीद ID: {receipt_id}. आवाज़ साफ़ नहीं आई, कृपया दोबारा बोलें या लिखकर भेजें।"
        await send_reply(settings, to, reply)
        return


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
