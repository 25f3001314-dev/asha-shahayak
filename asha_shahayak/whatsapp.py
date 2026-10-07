import json
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from .asr import SarvamAsr
from .config import Settings, get_settings
from .extraction import amount_words, extract_claim
from .meta import MetaWhatsApp
from .reconciliation import reconcile_saved_text
from .storage import IntakeStore

router = APIRouter()


def get_meta(settings: Annotated[Settings, Depends(get_settings)]) -> MetaWhatsApp:
    return MetaWhatsApp(settings.meta_access_token, settings.meta_phone_number_id)


def get_asr(settings: Annotated[Settings, Depends(get_settings)]) -> SarvamAsr:
    return SarvamAsr(settings.sarvam_api_key, settings.sarvam_api_url)


@router.get("/webhooks/whatsapp", response_class=PlainTextResponse)
async def verify(
    mode: str = Query(alias="hub.mode"),
    token: str = Query(alias="hub.verify_token"),
    challenge: str = Query(alias="hub.challenge"),
    settings: Settings = Depends(get_settings),
) -> str:
    if mode != "subscribe" or not settings.meta_verify_token or token != settings.meta_verify_token:
        raise HTTPException(status_code=403, detail="webhook verification failed")
    return challenge


def message_from_payload(payload: dict) -> tuple[str, str, str, str | None] | None:
    try:
        value = payload["entry"][0]["changes"][0]["value"]
        message = value["messages"][0]
        sender = message["from"]
        message_id = message["id"]
        kind = message["type"]
        if kind == "text":
            return sender, message_id, message["text"]["body"], None
        if kind == "audio":
            return sender, message_id, "", message["audio"]["id"]
    except (KeyError, IndexError, TypeError):
        return None
    return None


async def voice_text(asr: SarvamAsr, audio: bytes) -> tuple[str, float]:
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as file:
            file.write(audio)
            temp_name = file.name
        return await asr.transcribe(Path(temp_name).read_bytes())
    finally:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)


@router.post("/webhooks/whatsapp", status_code=200)
async def receive(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    meta: Annotated[MetaWhatsApp, Depends(get_meta)],
    asr: Annotated[SarvamAsr, Depends(get_asr)],
) -> dict:
    payload = await request.json()
    parsed = message_from_payload(payload)
    if not parsed:
        return {"received": True}
    sender, message_id, text, media_id = parsed
    store = IntakeStore(settings.database_path, settings.receipt_prefix)
    receipt_id, duplicate, _ = store.save_or_get(
        source="whatsapp",
        external_message_id=message_id,
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )
    if duplicate:
        return {"received": True, "duplicate": True}
    await meta.send_text(sender, f"Received. Receipt ID {receipt_id}")
    if media_id:
        try:
            text, score = await voice_text(asr, await meta.media_bytes(media_id))
        except Exception:
            await meta.send_text(sender, "Voice samajh nahi aayi. Human callback arrange kiya jayega.")
            return {"received": True, "receipt_id": receipt_id, "status": "human_callback"}
    else:
        score = 1.0
    claim = extract_claim(text)
    if claim.emergency:
        await meta.send_text(sender, "Emergency hai. Payment claim nahi bana. 108/112 par call karein.")
        return {"received": True, "receipt_id": receipt_id, "status": "medical_emergency"}
    if score < settings.confidence_medium_threshold or claim.errors:
        await meta.send_text(sender, "Details clear nahi hain. Human callback arrange kiya jayega.")
        return {"received": True, "receipt_id": receipt_id, "status": "human_callback"}
    if score < settings.confidence_high_threshold:
        await meta.send_text(sender, f"Main samjha: {text}. Kripya confirm karein: haan ya nahi.")
        return {"received": True, "receipt_id": receipt_id, "status": "confirmation_required"}
    try:
        result = reconcile_saved_text(text, receipt_id, settings)
    except ValueError:
        await meta.send_text(sender, "Details clear nahi hain. Human callback arrange kiya jayega.")
        return {"received": True, "receipt_id": receipt_id, "status": "human_callback"}
    if result["status"] == "medical_emergency":
        await meta.send_text(sender, "Emergency hai. Payment claim nahi bana. 108/112 par call karein.")
        return {"received": True, "receipt_id": receipt_id, "status": "medical_emergency"}
    amount = result["expected_amount"]
    await meta.send_text(
        sender,
        f"Read-back: expected amount ₹{amount} ({amount_words(amount)} rupees). "
        "Kripya confirm karein: haan ya nahi.",
    )
    return {
        "received": True,
        "receipt_id": receipt_id,
        "status": "confirmation_required",
        "reconciliation": result,
    }
