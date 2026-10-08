import pytest

from asha_shahayak.meta import MetaWhatsApp


class FakeResponse:
    def raise_for_status(self):
        pass


class FakeClient:
    def __init__(self):
        self.headers = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def post(self, url, headers, json):
        self.headers.append(headers)
        return FakeResponse()


@pytest.mark.asyncio
async def test_send_text_uses_bearer_token(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(
        "asha_shahayak.meta.httpx.AsyncClient",
        lambda timeout: client,
    )

    await MetaWhatsApp("test-token", "phone-id", "v21.0").send_text("919999999999", "hello")

    assert client.headers == [{"Authorization": "Bearer test-token"}]
