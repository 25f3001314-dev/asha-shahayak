import pytest
from httpx import ASGITransport, AsyncClient

from asha_shahayak.api import app
from asha_shahayak.config import Settings, get_settings
from asha_shahayak.whatsapp import voice_text
from asha_shahayak.asr import SarvamAsr


class FakeMeta:
    def __init__(self):
        self.sent = []

    async def send_text(self, to, text):
        self.sent.append((to, text))

    async def media_bytes(self, media_id):
        return b"audio"


class FakeAsr:
    async def transcribe(self, audio):
        assert audio == b"audio"
        return "vaccination march count 3 amount 300", 0.95


@pytest.fixture
def webhook_settings(tmp_path):
    settings = Settings(
        database_path=str(tmp_path / "webhook.sqlite3"),
        meta_verify_token="verify-me",
        meta_access_token="test-access",
        meta_phone_number_id="test-number",
        sarvam_api_key="test-sarvam",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_meta_verify_and_text_reply(webhook_settings):
    app.dependency_overrides[
        __import__("asha_shahayak.whatsapp", fromlist=["get_meta"]).get_meta
    ] = lambda: FakeMeta()
    app.dependency_overrides[
        __import__("asha_shahayak.whatsapp", fromlist=["get_asr"]).get_asr
    ] = lambda: FakeAsr()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            verified = await client.get(
                "/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=abc"
            )
            payload = {
                "entry": [{"changes": [{"value": {"messages": [{
                    "from": "919999999999", "id": "wamid-1", "type": "text",
                    "text": {"body": "vaccination march count 3 amount 300"},
                }]}}]}],
            }
            received = await client.post("/webhooks/whatsapp", json=payload)
    finally:
        app.dependency_overrides.clear()

    assert verified.status_code == 200
    assert verified.text == "abc"
    assert received.status_code == 200
    assert received.json()["status"] == "confirmation_required"


@pytest.mark.asyncio
async def test_voice_fallback_and_duplicate(webhook_settings):
    meta = FakeMeta()
    app.dependency_overrides[
        __import__("asha_shahayak.whatsapp", fromlist=["get_meta"]).get_meta
    ] = lambda: meta
    class BrokenAsr:
        async def transcribe(self, audio):
            raise RuntimeError("provider down")
    app.dependency_overrides[
        __import__("asha_shahayak.whatsapp", fromlist=["get_asr"]).get_asr
    ] = lambda: BrokenAsr()
    payload = {
        "entry": [{"changes": [{"value": {"messages": [{
            "from": "919999999999", "id": "wamid-voice", "type": "audio",
            "audio": {"id": "media-1"},
        }]}}]}],
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post("/webhooks/whatsapp", json=payload)
            second = await client.post("/webhooks/whatsapp", json=payload)
    finally:
        app.dependency_overrides.clear()

    assert first.json()["status"] == "human_callback"
    assert second.json()["duplicate"] is True
    assert len(meta.sent) == 2


@pytest.mark.asyncio
async def test_raw_audio_file_is_removed_after_processing(tmp_path):
    result = await voice_text(FakeAsr(), b"audio")

    assert result[0].startswith("vaccination")


@pytest.mark.asyncio
async def test_sarvam_request_uses_api_key(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"transcript": "vaccination", "confidence": 0.91}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, headers, files):
            assert headers["api-subscription-key"] == "only-in-memory"
            assert files["file"][1] == b"audio"
            return Response()

    monkeypatch.setattr("asha_shahayak.asr.httpx.AsyncClient", lambda timeout: Client())
    result = await SarvamAsr("only-in-memory", "https://sarvam.test").transcribe(b"audio")

    assert result == ("vaccination", 0.91)
