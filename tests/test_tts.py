import base64

import pytest

from asha_shahayak.config import Settings
from asha_shahayak.tts import synthesize_hindi


@pytest.mark.asyncio
async def test_synthesize_hindi_uses_bulbul_v3(monkeypatch):
    settings = Settings(sarvam_api_key="test-key")
    monkeypatch.setattr("asha_shahayak.tts.get_settings", lambda: settings)

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"audios": [base64.b64encode(b"wav").decode()]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, headers, json):
            assert url.endswith("/text-to-speech")
            assert headers["api-subscription-key"] == "test-key"
            assert json["target_language_code"] == "hi-IN"
            assert json["model"] == "bulbul:v3"
            return Response()

    monkeypatch.setattr("asha_shahayak.tts.httpx.AsyncClient", lambda timeout: Client())

    assert await synthesize_hindi("भुगतान की जानकारी") == b"wav"


@pytest.mark.asyncio
async def test_synthesize_hindi_failure_returns_none(monkeypatch):
    settings = Settings(sarvam_api_key="test-key")
    monkeypatch.setattr("asha_shahayak.tts.get_settings", lambda: settings)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr("asha_shahayak.tts.httpx.AsyncClient", lambda timeout: Client())

    assert await synthesize_hindi("भुगतान की जानकारी") is None
