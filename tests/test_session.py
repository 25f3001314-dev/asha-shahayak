from datetime import datetime, timedelta, timezone
import sqlite3

from asha_shahayak.config import Settings
from asha_shahayak.session import QueryMemory
from asha_shahayak.whatsapp import reply_for_text


def test_query_memory_saves_and_reads_confirmed_query(tmp_path):
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    memory = QueryMemory(
        str(tmp_path / "session.sqlite3"),
        "test-salt",
        clock=lambda: now,
    )

    memory.save("15550000001", "vaccination", "2026-10-07", "ASHA-1")

    assert memory.get("15550000001") == {
        "activity": "vaccination",
        "date": "2026-10-07",
        "receipt_id": "ASHA-1",
    }


def test_query_memory_expires_after_ttl(tmp_path):
    current = [datetime(2026, 10, 8, tzinfo=timezone.utc)]
    memory = QueryMemory(
        str(tmp_path / "session.sqlite3"),
        "test-salt",
        clock=lambda: current[0],
        ttl=timedelta(minutes=30),
    )
    memory.save("15550000001", "vaccination", "2026-10-07", "ASHA-1")

    current[0] += timedelta(minutes=31)

    assert memory.get("15550000001") is None


def test_query_memory_is_isolated_by_sender(tmp_path):
    memory = QueryMemory(str(tmp_path / "session.sqlite3"), "test-salt")
    memory.save("15550000001", "vaccination", "2026-10-07", "ASHA-1")

    assert memory.get("15550000002") is None


def test_query_memory_does_not_store_plain_sender(tmp_path):
    database_path = tmp_path / "session.sqlite3"
    memory = QueryMemory(str(database_path), "test-salt")
    memory.save("15550000001", "vaccination", "2026-10-07", "ASHA-1")

    with sqlite3.connect(database_path) as database:
        values = database.execute(
            "SELECT sender_hash FROM confirmed_queries"
        ).fetchone()
    assert "15550000001" not in values[0]


def test_whatsapp_saves_and_resolves_confirmed_query(tmp_path, monkeypatch):
    settings = Settings(
        database_path=str(tmp_path / "session.sqlite3"),
        session_salt="test-salt",
    )

    monkeypatch.setattr(
        "asha_shahayak.whatsapp.reconcile_saved_text",
        lambda text, receipt_id, settings: {
            "status": "reconciled",
            "receipt_id": "ASHA-2",
            "expected_amount": 750,
            "gap": {"found": False},
        },
    )

    reply_for_text(
        "vaccination budhwar count 3 amount 250",
        "intake-1",
        settings,
        "15550000001",
    )
    followup = reply_for_text(
        "uska status batao",
        "intake-2",
        settings,
        "15550000001",
    )

    assert "ASHA-2" in followup
