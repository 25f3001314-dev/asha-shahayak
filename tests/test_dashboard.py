import pytest
from httpx import ASGITransport, AsyncClient

from asha_shahayak.api import app
from asha_shahayak.complaints import ComplaintStore
from asha_shahayak.config import Settings, get_settings
from asha_shahayak.ledger import Ledger
from seed_demo import seed


@pytest.fixture
def dashboard_settings(tmp_path):
    settings = Settings(database_path=str(tmp_path / "dashboard.sqlite3"), officer_token="test-token")
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_dashboard_empty_state_and_token(dashboard_settings):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.get("/dashboard")
        page = await client.get("/dashboard?token=test-token")

    assert denied.status_code == 401
    assert page.status_code == 200
    assert page.text.count("No entries yet") == 4


@pytest.mark.asyncio
async def test_dashboard_reads_ledger_and_rejects_bad_csv(dashboard_settings):
    Ledger(dashboard_settings.database_path).append({"label": "DEMO", "amount": 100})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        page = await client.get("/dashboard", headers={"x-officer-token": "test-token"})
        upload = await client.post(
            "/dashboard/status?token=test-token",
            files={"file": ("bad.csv", b"activity,month\nvaccination,march", "text/csv")},
        )

    assert "ASHA-" in page.text
    assert "CSV error: CSV columns must be" in upload.text


@pytest.mark.asyncio
async def test_dashboard_rejects_bad_month_csv(dashboard_settings):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/dashboard/status?token=test-token",
            files={
                "file": (
                    "bad-month.csv",
                    b"activity,month,status,amount\nvaccination,monsoon,paid,100",
                    "text/csv",
                )
            },
        )

    assert "CSV error: invalid month on row 2" in upload.text


def test_seeded_demo_dashboard_loads(tmp_path):
    database = tmp_path / "demo.sqlite3"
    seed(str(database))
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_path=str(database), officer_token="my-local-token"
    )
    try:
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            response = client.get("/dashboard?token=my-local-token")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "DEMO-complaint-1" in response.text
    assert "DEMO" in response.text


def test_pending_returns_a_non_empty_complaint(tmp_path):
    store = ComplaintStore(str(tmp_path / "complaints.sqlite3"))
    store.create("complaint-1", "ASHA-1", "amount mismatch")

    complaints = store.pending()

    assert len(complaints) == 1
    assert complaints[0]["complaint_id"] == "complaint-1"
    assert complaints[0]["draft"] == "amount mismatch"
