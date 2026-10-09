import pytest
from httpx import ASGITransport, AsyncClient

from asha_shahayak.api import app
from asha_shahayak.config import Settings, get_settings


@pytest.mark.asyncio
async def test_complaint_is_not_filed_until_asha_confirms(tmp_path):
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_path=str(tmp_path / "app.sqlite3"),
        api_key="test-api-key",
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/reconcile",
                json={
                    "external_message_id": "text-1",
                    "text": "vaccination march count 3 amount 250",
                },
                headers={"x-api-key": "test-api-key"},
            )
            complaint = response.json()["complaint"]["complaint_id"]
            draft = await client.post(
                f"/v1/complaints/{complaint}/decision",
                json={"confirm": False},
                headers={"x-api-key": "test-api-key"},
            )
            confirmed = await client.post(
                f"/v1/complaints/{complaint}/decision",
                json={"confirm": True},
                headers={"x-api-key": "test-api-key"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    assert draft.json() == {
        "complaint_id": complaint,
        "state": "rejected",
        "filed": False,
    }
    assert confirmed.json()["state"] == "filed"
