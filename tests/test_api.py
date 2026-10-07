import pytest
from httpx import ASGITransport, AsyncClient

from asha_shahayak.api import app
from asha_shahayak.config import get_settings


@pytest.fixture
def isolated_database(tmp_path):
    get_settings.cache_clear()
    from asha_shahayak.config import Settings

    app.dependency_overrides[get_settings] = lambda: Settings(
        database_path=str(tmp_path / "intake.sqlite3")
    )
    yield
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_intake_returns_receipt_and_is_idempotent(isolated_database):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "source": "whatsapp",
            "external_message_id": "wamid-123",
            "payload": {"text": "pachaas"},
        }
        first = await client.post("/v1/intake", json=payload)
        second = await client.post("/v1/intake", json=payload)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["receipt_id"] == second.json()["receipt_id"]
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True
