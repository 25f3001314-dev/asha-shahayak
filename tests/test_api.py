import pytest
from httpx import ASGITransport, AsyncClient

from asha_shahayak.api import app
from asha_shahayak.config import Settings, get_settings


@pytest.fixture
def isolated_database(tmp_path):
    get_settings.cache_clear()
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_path=str(tmp_path / "intake.sqlite3"),
        api_key="test-api-key",
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
        headers = {"x-api-key": "test-api-key"}
        first = await client.post("/v1/intake", json=payload, headers=headers)
        second = await client.post("/v1/intake", json=payload, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["receipt_id"] == second.json()["receipt_id"]
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured_key", "header_key"),
    [
        ("test-api-key", None),
        ("test-api-key", "wrong-api-key"),
        ("", "test-api-key"),
        ("test-api-key", b"cl\xc3\xa9"),
    ],
)
async def test_intake_rejects_missing_wrong_empty_or_non_ascii_key(
    tmp_path, configured_key, header_key
):
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_path=str(tmp_path / "auth.sqlite3"),
        api_key=configured_key,
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = {"x-api-key": header_key} if header_key is not None else {}
            response = await client.post(
                "/v1/intake",
                json={
                    "source": "sms",
                    "external_message_id": f"auth-{configured_key}-{header_key}",
                    "payload": {},
                },
                headers=headers,
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_all_machine_endpoints_require_api_key(tmp_path):
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_path=str(tmp_path / "all-auth.sqlite3"),
        api_key="test-api-key",
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            intake = await client.post(
                "/v1/intake",
                json={"source": "sms", "external_message_id": "auth-1", "payload": {}},
            )
            reconcile = await client.post(
                "/v1/reconcile",
                json={"external_message_id": "auth-2", "text": "hello"},
            )
            decision = await client.post(
                "/v1/complaints/missing/decision",
                json={"confirm": True},
            )
    finally:
        app.dependency_overrides.clear()

    assert [response.status_code for response in (intake, reconcile, decision)] == [
        401,
        401,
        401,
    ]


@pytest.mark.asyncio
async def test_reconcile_failed_once_is_retryable_and_idempotent(tmp_path, monkeypatch):
    import asha_shahayak.api as api_module
    import sqlite3

    settings = Settings(
        database_path=str(tmp_path / "retry.sqlite3"),
        api_key="test-api-key",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    original = api_module.reconcile_saved_text
    attempts = 0

    def fail_once(text, receipt_id, current_settings):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("temporary failure")
        return original(text, receipt_id, current_settings)

    monkeypatch.setattr(api_module, "reconcile_saved_text", fail_once)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            request = {
                "external_message_id": "retry-1",
                "text": "vaccination march count 3 amount 250",
            }
            first = await client.post("/v1/reconcile", json=request, headers={"x-api-key": "test-api-key"})
            second = await client.post("/v1/reconcile", json=request, headers={"x-api-key": "test-api-key"})
            third = await client.post("/v1/reconcile", json=request, headers={"x-api-key": "test-api-key"})
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 422
    assert second.status_code == 202
    assert third.status_code == 202
    assert third.json()["status"] == "already_received"
    assert second.json()["receipt_id"] == second.json()["intake_receipt_id"]
    with sqlite3.connect(settings.database_path) as database:
        assert database.execute("SELECT status FROM intake_requests").fetchone()[0] == "processed"
        assert database.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0] == 1
        assert database.execute("SELECT COUNT(*) FROM complaints").fetchone()[0] == 1
