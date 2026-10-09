import pytest
from httpx import ASGITransport, AsyncClient

from asha_shahayak.api import app
from asha_shahayak.complaints import ComplaintStore
from asha_shahayak.config import Settings, get_settings
from asha_shahayak.ledger import Ledger
from asha_shahayak.registry import AshaRegistry
from asha_shahayak.status import StatusStore
from asha_shahayak.whatsapp import reply_for_text
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


@pytest.mark.asyncio
async def test_uploaded_block_csv_is_used_by_whatsapp_reply(dashboard_settings):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/dashboard/status?token=test-token",
            files={
                "file": (
                    "block.csv",
                    (
                        b"asha_id,month,head,claimed_amount,approved_amount,stage,stage_date\n"
                        b"ASHA-42,march,vaccination,250,300,moic_pending,2026-03-28\n"
                    ),
                    "text/csv",
                )
            },
        )

    assert upload.status_code == 200
    settings = dashboard_settings.model_copy(update={"session_salt": "test-salt"})
    reply = reply_for_text(
        "vaccination march count 3 amount 250",
        "ASHA-input",
        settings,
        "ASHA-42",
    )

    assert "MOIC approval pending" in reply
    assert "भुगतान हो चुका है" not in reply


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


def test_officer_token_default_is_empty(monkeypatch):
    monkeypatch.delenv("ASHA_OFFICER_TOKEN", raising=False)
    from asha_shahayak.config import Settings

    assert Settings(_env_file=None).officer_token == ""


@pytest.mark.asyncio
async def test_local_demo_reads_dashboard_uploaded_status(dashboard_settings, monkeypatch):
    from demo_test_bot import main as demo_main

    monkeypatch.setattr(demo_main, "settings", dashboard_settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/dashboard/status?token=test-token",
            files={
                "file": (
                    "block.csv",
                    (
                        b"asha_id,month,head,claimed_amount,approved_amount,stage,stage_date\n"
                        b"ASHA-42,march,vaccination,250,300,paid,2026-03-05\n"
                    ),
                    "text/csv",
                )
            },
        )

    assert upload.status_code == 200
    result = demo_main.bot_response(
        "vaccination march count 3 amount 250",
        "ASHA-demo-test",
    )
    assert "भुगतान हो चुका है" in result["reply"]


@pytest.mark.asyncio
async def test_combined_dashboard_upload_replaces_both_datasets(dashboard_settings):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/dashboard/data?token=test-token",
            files={
                "registry_file": (
                    "registry.csv",
                    b"asha_id,phone_number\nASHA-1,15550000001\n",
                    "text/csv",
                ),
                "status_file": (
                    "status.csv",
                    (
                        b"asha_id,month,head,claimed_amount,approved_amount,stage,stage_date\n"
                        b"ASHA-1,march,vaccination,250,300,paid,2026-03-28\n"
                    ),
                    "text/csv",
                ),
            },
        )

        replacement = await client.post(
            "/dashboard/data?token=test-token",
            files={
                "registry_file": (
                    "registry.csv",
                    b"asha_id,phone_number\nASHA-2,15550000002\n",
                    "text/csv",
                ),
                "status_file": (
                    "status.csv",
                    (
                        b"asha_id,month,head,claimed_amount,approved_amount,stage,stage_date\n"
                        b"ASHA-2,april,home_visit,450,300,moic_pending,2026-04-02\n"
                    ),
                    "text/csv",
                ),
            },
        )

    assert upload.status_code == 200
    assert "Uploaded 1 ASHA registrations and 1 officer status rows" in upload.text
    assert replacement.status_code == 200
    assert "ASHA-2" in replacement.text
    assert "ASHA-1,march" not in replacement.text


def test_local_demo_reads_uploaded_status_for_selected_asha(tmp_path, monkeypatch):
    from demo_test_bot import main as demo_main

    settings = Settings(
        database_path=str(tmp_path / "local-demo.sqlite3"),
        session_salt="test-salt",
        _env_file=None,
    )
    AshaRegistry(settings.database_path, settings.session_salt).import_csv(
        b"asha_id,phone_number\nASHA-101,15550000001\n"
    )
    StatusStore(settings.database_path).import_csv(
        (
            b"asha_id,month,head,claimed_amount,approved_amount,stage,stage_date\n"
            b"ASHA-101,march,vaccination,250,300,paid,2026-03-28\n"
        )
    )
    monkeypatch.setattr(demo_main, "settings", settings)
    monkeypatch.setattr(demo_main, "selected_asha_id", None)

    selected = demo_main.bot_response("ASHA-101", "demo-1")
    result = demo_main.bot_response(
        "vaccination march count 3 amount 250", "demo-2"
    )

    assert "ASHA ID ASHA-101 चुन ली गई है" in selected["reply"]
    assert "ब्लॉक रिकॉर्ड में भुगतान हो चुका है: ₹300" in result["reply"]


def test_local_demo_answers_payment_followup_for_selected_asha(tmp_path, monkeypatch):
    from demo_test_bot import main as demo_main

    settings = Settings(
        database_path=str(tmp_path / "payment-followup.sqlite3"),
        session_salt="test-salt",
        _env_file=None,
    )
    AshaRegistry(settings.database_path, settings.session_salt).import_csv(
        b"asha_id,phone_number\nASHA-106,15550000006\n"
    )
    StatusStore(settings.database_path).import_csv(
        (
            b"asha_id,month,head,claimed_amount,approved_amount,stage,stage_date\n"
            b"ASHA-106,march,vaccination,250,300,paid,2026-03-28\n"
        )
    )
    monkeypatch.setattr(demo_main, "settings", settings)
    monkeypatch.setattr(demo_main, "selected_asha_id", None)

    demo_main.bot_response("ASHA-106", "demo-1")
    result = demo_main.bot_response("eske payment ke bare me btao", "demo-2")

    assert "ASHA-106 के भुगतान की स्थिति" in result["reply"]
    assert "भुगतान पूरा हो चुका है" in result["reply"]
    assert "स्वीकृत राशि ₹300" in result["reply"]


def test_local_demo_uses_hindi_fallback_for_incomplete_text(tmp_path, monkeypatch):
    from demo_test_bot import main as demo_main

    settings = Settings(
        database_path=str(tmp_path / "fallback.sqlite3"),
        session_salt="test-salt",
        _env_file=None,
    )
    monkeypatch.setattr(demo_main, "settings", settings)
    monkeypatch.setattr(demo_main, "selected_asha_id", None)

    vague = demo_main.bot_response("kuch nhi", "demo-vague")
    numeric = demo_main.bot_response("106", "demo-numeric")

    assert "extraction_errors" not in vague["reply"]
    assert "पहले ASHA ID भेजें" in vague["reply"]
    assert "पूरी ASHA ID" in numeric["reply"]
