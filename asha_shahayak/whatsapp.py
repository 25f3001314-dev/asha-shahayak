import hashlib
import hmac
import json
import logging
import tempfile
from typing import Annotated, Any, Protocol
from pathlib import Path

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from .asr import AsrEngine, AsrStub, SarvamAsr
from .config import Settings, get_settings
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
        return await asr.transcribe(Path(temp_name).read_bytes())
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
        try:
            reconcile_saved_text(text, receipt_id, settings)
        except Exception as error:
            logger.warning("WhatsApp reconciliation failed: %s", type(error).__name__)
        return
    if message.get("type") == "audio":
        audio = await media_client.download(message["audio"]["id"])
        await asr.transcribe(audio)


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
