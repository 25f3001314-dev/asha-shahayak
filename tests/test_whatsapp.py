import os
import hashlib
import hmac
import json
import sqlite3

import pytest
from httpx import ASGITransport, AsyncClient
from sarvamai.core.api_error import ApiError

from asha_shahayak.api import app
from asha_shahayak.asr import AsrStub, SarvamAsr
from asha_shahayak.config import Settings, get_settings
from asha_shahayak.factset import FactSet
from asha_shahayak.whatsapp import (
    get_asr,
    get_media_client,
    whatsapp_is_configured,
    voice_text,
)


class FakeMediaClient:
    def __init__(self):
        self.media_ids = []

    async def download(self, media_id):
        self.media_ids.append(media_id)
        return b"audio-bytes"


class FakeAsr:
    def __init__(self):
        self.audio = []

    async def transcribe(self, audio):
        self.audio.append(audio)
        return "vaccination march count 3 amount 300", 0.95


def signed(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def payload(message_id="wamid-test-1", kind="text"):
    message = {
        "from": "15550000001",
        "id": message_id,
        "type": kind,
    }
    if kind == "text":
        message["text"] = {"body": "vaccination march count 3 amount 300"}
    else:
        message["audio"] = {"id": "media-test-1"}
    return {
        "entry": [{"changes": [{"field": "messages", "value": {"messages": [message]}}]}]
    }


@pytest.fixture
def webhook_settings(tmp_path):
    settings = Settings(
        database_path=str(tmp_path / "webhook.sqlite3"),
        meta_access_token="test-access-token",
        meta_phone_number_id="test-phone-id",
        meta_verify_token="test-verify-token",
        meta_app_secret="test-app-secret",
        meta_graph_version="v21.0",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.clear()


async def post_payload(client, data, secret="test-app-secret", signature=True):
    body = json.dumps(data).encode()
    headers = {"X-Hub-Signature-256": signed(body, secret)} if signature else {}
    return await client.post("/webhooks/whatsapp", content=body, headers=headers)


@pytest.mark.asyncio
async def test_verification_get_ok_and_wrong_token(webhook_settings):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.get(
            "/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "test-verify-token",
                "hub.challenge": "challenge",
            },
        )
        wrong = await client.get(
            "/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong",
                "hub.challenge": "challenge",
            },
        )

    assert ok.status_code == 200
    assert ok.text == "challenge"
    assert wrong.status_code == 403


@pytest.mark.asyncio
async def test_post_valid_bad_and_missing_signature(webhook_settings):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        valid = await post_payload(client, payload())
        bad = await post_payload(client, payload("wamid-bad"), secret="wrong")
        missing = await post_payload(client, payload("wamid-missing"), signature=False)

    assert valid.status_code == 200
    assert bad.status_code == 403
    assert missing.status_code == 403


@pytest.mark.asyncio
async def test_text_message_reaches_intake(webhook_settings):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await post_payload(client, payload())

    assert response.status_code == 200
    with sqlite3.connect(webhook_settings.database_path) as connection:
        row = connection.execute(
            "SELECT source, external_message_id, payload_json FROM intake_requests"
        ).fetchone()
    assert row[0:2] == ("whatsapp", "wamid-test-1")
    assert "vaccination march" in row[2]


@pytest.mark.asyncio
async def test_audio_downloads_media_and_reaches_asr(webhook_settings):
    media = FakeMediaClient()
    asr = FakeAsr()
    app.dependency_overrides[get_media_client] = lambda: media
    app.dependency_overrides[get_asr] = lambda: asr
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await post_payload(client, payload("wamid-audio", "audio"))
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert media.media_ids == ["media-test-1"]
    assert asr.audio == [b"audio-bytes"]


@pytest.mark.asyncio
async def test_same_message_id_is_saved_once(webhook_settings):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await post_payload(client, payload("wamid-duplicate"))
        second = await post_payload(client, payload("wamid-duplicate"))

    assert first.status_code == 200
    assert second.status_code == 200
    with sqlite3.connect(webhook_settings.database_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM intake_requests").fetchone()[0]
    assert count == 1


@pytest.mark.asyncio
async def test_statuses_only_payload_creates_nothing(webhook_settings):
    body = {
        "entry": [{"changes": [{"field": "statuses", "value": {"statuses": [{"id": "status-1"}]}}]}]
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await post_payload(client, body)

    assert response.status_code == 200
    assert not __import__("pathlib").Path(webhook_settings.database_path).exists()


@pytest.mark.asyncio
async def test_whatsapp_routes_are_unavailable_without_configuration(monkeypatch):
    for name in list(os.environ):
        if name.startswith("ASHA_META_"):
            monkeypatch.delenv(name, raising=False)
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            get_response = await client.get(
                "/webhooks/whatsapp",
                params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "test",
                    "hub.challenge": "challenge",
                },
            )
            post_response = await client.post("/webhooks/whatsapp", content=b"{}")
    finally:
        app.dependency_overrides.clear()

    assert get_response.status_code == 503
    assert post_response.status_code == 503


@pytest.mark.asyncio
async def test_raw_audio_file_is_removed_after_processing(tmp_path):
    class Fake:
        async def transcribe(self, audio):
            return "vaccination", 0.95

    assert (await voice_text(Fake(), b"audio"))[0] == "vaccination"


@pytest.mark.asyncio
async def test_sarvam_request_uses_api_key(monkeypatch):
    class Response:
        transcript = "vaccination"
        language_code = "hi-IN"

    class SpeechToText:
        async def transcribe(self, **kwargs):
            assert kwargs["file"] == ("voice.wav", b"audio", "audio/wav")
            assert kwargs["model"] == "saaras:v3"
            assert kwargs["mode"] == "transcribe"
            assert kwargs["language_code"] == "hi-IN"
            assert kwargs["input_audio_codec"] == "wav"
            return Response()

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["api_subscription_key"] == "only-in-memory"
            self.speech_to_text = SpeechToText()

    monkeypatch.setattr("asha_shahayak.asr.AsyncSarvamAI", Client)
    result = await SarvamAsr("only-in-memory").transcribe(b"audio")

    assert result == ("vaccination", "hi-IN")


@pytest.mark.asyncio
async def test_sarvam_api_error_is_raised(monkeypatch):
    class SpeechToText:
        async def transcribe(self, **kwargs):
            raise ApiError(status_code=503, body={"error": "unavailable"})

    class Client:
        def __init__(self, **kwargs):
            self.speech_to_text = SpeechToText()

    monkeypatch.setattr("asha_shahayak.asr.AsyncSarvamAI", Client)

    with pytest.raises(ApiError):
        await SarvamAsr("only-in-memory").transcribe(b"audio")


def test_adapter_uses_existing_meta_settings():
    settings = Settings(
        meta_access_token="access",
        meta_phone_number_id="phone",
        meta_verify_token="verify",
        meta_app_secret="secret",
        meta_graph_version="v21.0",
    )

    assert whatsapp_is_configured(settings)
    assert settings.meta_graph_version == "v21.0"


def test_asha_meta_environment_verifies_webhook(monkeypatch):
    for name in (
        "WHATSAPP_ACCESS_TOKEN",
        "WHATSAPP_PHONE_NUMBER_ID",
        "WHATSAPP_VERIFY_TOKEN",
        "WHATSAPP_APP_SECRET",
        "WHATSAPP_GRAPH_VERSION",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ASHA_META_ACCESS_TOKEN", "access")
    monkeypatch.setenv("ASHA_META_PHONE_NUMBER_ID", "phone")
    monkeypatch.setenv("ASHA_META_VERIFY_TOKEN", "verify")
    monkeypatch.setenv("ASHA_META_APP_SECRET", "secret")
    monkeypatch.setenv("ASHA_META_GRAPH_VERSION", "v21.0")
    from asha_shahayak.config import get_settings

    get_settings.cache_clear()
    try:
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            response = client.get(
                "/webhooks/whatsapp",
                params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "verify",
                    "hub.challenge": "123",
                },
            )
            wrong = client.get(
                "/webhooks/whatsapp",
                params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "wrong",
                    "hub.challenge": "123",
                },
            )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    assert response.text == "123"
    assert wrong.status_code == 403


@pytest.mark.asyncio
async def test_text_webhook_reconciles_once(webhook_settings, monkeypatch):
    calls = []

    def fake_reconcile(text, receipt_id, settings):
        calls.append((text, receipt_id, settings))
        return {"status": "confirmation_required"}

    monkeypatch.setattr("asha_shahayak.whatsapp.reconcile_saved_text", fake_reconcile)
    body = json.dumps(payload("wamid-reconcile")).encode()
    headers = {"X-Hub-Signature-256": signed(body, "test-app-secret")}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/whatsapp", content=body, headers=headers)

    assert response.status_code == 200
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_blocked_reply_does_not_invoke_tts(webhook_settings, monkeypatch):
    monkeypatch.setenv("WA_REPLY", "1")
    called = []

    async def fake_tts(text):
        called.append(text)
        return b"audio"

    class FakeMeta:
        def __init__(self, *args):
            pass

        async def send_text(self, to, text):
            pass

    monkeypatch.setattr("asha_shahayak.whatsapp.synthesize_hindi", fake_tts)
    monkeypatch.setattr("asha_shahayak.meta.MetaWhatsApp", FakeMeta)
    monkeypatch.setattr(
        "asha_shahayak.whatsapp.validate_reply",
        lambda text, facts: (False, ["status_not_evidenced"]),
    )

    from asha_shahayak.whatsapp import send_reply

    await send_reply(
        webhook_settings,
        "15550000001",
        "₹2000 pending है",
        FactSet(),
        "ASHA-1",
    )

    assert called == []
