import sqlite3

from asha_shahayak.storage import IntakeStore


def test_existing_intake_rows_are_processed_when_status_is_migrated(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE intake_requests ("
            "receipt_id TEXT PRIMARY KEY, source TEXT NOT NULL, "
            "external_message_id TEXT NOT NULL UNIQUE, received_at TEXT NOT NULL, "
            "payload_json TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO intake_requests VALUES (?, ?, ?, ?, ?)",
            ("ASHA-old", "api", "old-1", "2024-01-01T00:00:00+00:00", "{}"),
        )

    store = IntakeStore(str(path))

    assert store.status_for("old-1") == "processed"
    receipt, duplicate, _ = store.save_or_get(
        source="api", external_message_id="new-1", payload_json="{}"
    )
    assert receipt.startswith("ASHA-")
    assert duplicate is False
    assert store.status_for("new-1") == "received"
