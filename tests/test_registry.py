import sqlite3

from httpx import ASGITransport, AsyncClient

from asha_shahayak.api import app
from asha_shahayak.config import Settings, get_settings
from asha_shahayak.registry import AshaRegistry
from asha_shahayak.status import StatusStore
from asha_shahayak.whatsapp import reply_for_text


def _settings(path):
    return Settings(
        database_path=str(path),
        session_salt="registry-test-salt",
        officer_token="officer-token",
        _env_file=None,
    )


def _status(store, asha_id, stage, amount):
    store.import_csv(
        (
            "asha_id,month,head,claimed_amount,approved_amount,stage,stage_date\n"
            f"{asha_id},march,vaccination,250,{amount},{stage},2026-03-28\n"
        ).encode()
    )


def test_new_sender_matching_registration_binds(tmp_path):
    settings = _settings(tmp_path / "registry.sqlite3")
    registry = AshaRegistry(settings.database_path, settings.session_salt)
    registry.import_csv(b"asha_id,phone_number\nASHA-1,15550000001\n")

    reply = reply_for_text("ASHA-1", "ASHA-registration", settings, "15550000001")

    assert "registration हो गया" in reply
    assert registry.resolve_sender("15550000001") == "ASHA-1"
    with sqlite3.connect(settings.database_path) as db:
        values = db.execute(
            "SELECT expected_phone_hash, bound_phone_hash FROM asha_registry"
        ).fetchone()
    assert "15550000001" not in values


def test_registration_mismatch_and_unknown_id_do_not_bind(tmp_path):
    settings = _settings(tmp_path / "registry-mismatch.sqlite3")
    registry = AshaRegistry(settings.database_path, settings.session_salt)
    registry.import_csv(b"asha_id,phone_number\nASHA-1,15550000001\n")

    mismatch = reply_for_text("ASHA-1", "ASHA-registration", settings, "15550000002")
    unknown = reply_for_text("ASHA-404", "ASHA-registration", settings, "15550000002")

    assert "match नहीं हुई" in mismatch
    assert "match नहीं हुई" in unknown
    assert registry.resolve_sender("15550000002") is None


def test_bound_sender_reads_only_its_own_asha_record(tmp_path):
    settings = _settings(tmp_path / "isolation.sqlite3")
    registry = AshaRegistry(settings.database_path, settings.session_salt)
    registry.import_csv(
        b"asha_id,phone_number\nASHA-1,15550000001\nASHA-2,15550000002\n"
    )
    store = StatusStore(settings.database_path)
    _status(store, "ASHA-1", "paid", 300)
    _status(store, "ASHA-2", "moic_pending", 400)
    reply_for_text("ASHA-1", "r1", settings, "15550000001")
    reply_for_text("ASHA-2", "r2", settings, "15550000002")

    first = reply_for_text(
        "vaccination march count 3 amount 250", "q1", settings, "15550000001"
    )
    second = reply_for_text(
        "vaccination march count 3 amount 250", "q2", settings, "15550000002"
    )

    assert "भुगतान हो चुका है" in first
    assert "MOIC approval pending" in second
    assert "MOIC approval pending" not in first
    assert "भुगतान हो चुका है" not in second


def test_bound_sender_with_only_global_row_gets_no_record(tmp_path):
    settings = _settings(tmp_path / "global.sqlite3")
    registry = AshaRegistry(settings.database_path, settings.session_salt)
    registry.import_csv(b"asha_id,phone_number\nASHA-1,15550000001\n")
    store = StatusStore(settings.database_path)
    store.import_csv(
        b"activity,month,status,amount\nvaccination,march,paid,300\n"
    )
    reply_for_text("ASHA-1", "r1", settings, "15550000001")

    reply = reply_for_text(
        "vaccination march count 3 amount 250", "q1", settings, "15550000001"
    )

    assert "इस मद की प्रविष्टि ब्लॉक के रिकॉर्ड में नहीं मिली" in reply
    assert "भुगतान हो चुका है" not in reply


def test_officer_reset_requires_registration_again(tmp_path):
    settings = _settings(tmp_path / "reset.sqlite3")
    registry = AshaRegistry(settings.database_path, settings.session_salt)
    registry.import_csv(b"asha_id,phone_number\nASHA-1,15550000001\n")
    assert "registration हो गया" in reply_for_text(
        "ASHA-1", "r1", settings, "15550000001"
    )
    registry.reset("ASHA-1")

    assert reply_for_text("vaccination march count 3 amount 250", "q1", settings, "15550000001") == (
        "पहली बार register करने के लिए अपना ASHA ID भेजें।"
    )


async def test_dashboard_registry_upload_and_reset(tmp_path):
    settings = _settings(tmp_path / "dashboard-registry.sqlite3")
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        async with AsyncClient(
            transport=ASGITransport(app), base_url="http://test"
        ) as client:
            upload = await client.post(
                "/dashboard/registry?token=officer-token",
                files={
                    "file": (
                        "registry.csv",
                        b"asha_id,phone_number\nASHA-7,15550000007\n",
                        "text/csv",
                    )
                },
            )
            reset = await client.post(
                "/dashboard/registry/reset?token=officer-token",
                data={"asha_id": "ASHA-7"},
            )
    finally:
        app.dependency_overrides.clear()

    assert upload.status_code == 200
    assert reset.status_code == 303
    with sqlite3.connect(settings.database_path) as db:
        row = db.execute(
            "SELECT expected_phone_hash, bound_phone_hash FROM asha_registry "
            "WHERE asha_id = 'ASHA-7'"
        ).fetchone()
    assert row[0]
    assert row[1] is None
