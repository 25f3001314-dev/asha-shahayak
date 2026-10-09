import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4


class IntakeStore:
    """Small durable intake store; the table is an append-only evidence boundary."""

    def __init__(self, database_path: str, receipt_prefix: str = "ASHA") -> None:
        self._database_path = database_path
        self._receipt_prefix = receipt_prefix
        self._lock = Lock()
        self._initialise()

    def _connect(self) -> sqlite3.Connection:
        if self._database_path != ":memory:":
            Path(self._database_path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialise(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS intake_requests (
                    receipt_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    external_message_id TEXT NOT NULL UNIQUE,
                    received_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'received'
                )
                """
            )
            columns = connection.execute("PRAGMA table_info(intake_requests)").fetchall()
            if not any(column[1] == "status" for column in columns):
                connection.execute(
                    "ALTER TABLE intake_requests ADD COLUMN status TEXT NOT NULL DEFAULT 'received'"
                )
                connection.execute(
                    "UPDATE intake_requests SET status = 'processed'"
                )

    def save_or_get(
        self, *, source: str, external_message_id: str, payload_json: str
    ) -> tuple[str, bool, datetime]:
        received_at = datetime.now(timezone.utc)
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                """
                SELECT receipt_id, received_at
                FROM intake_requests
                WHERE external_message_id = ?
                """,
                (external_message_id,),
            ).fetchone()
            if existing:
                return (
                    existing["receipt_id"],
                    True,
                    datetime.fromisoformat(existing["received_at"]),
                )

            receipt_id = f"{self._receipt_prefix}-{uuid4().hex[:12].upper()}"
            connection.execute(
                """
                INSERT INTO intake_requests
                    (receipt_id, source, external_message_id, received_at, payload_json, status)
                VALUES (?, ?, ?, ?, ?, 'received')
                """,
                (receipt_id, source, external_message_id, received_at.isoformat(), payload_json),
            )
            return receipt_id, False, received_at

    def status_for(self, external_message_id: str) -> str:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM intake_requests WHERE external_message_id = ?",
                (external_message_id,),
            ).fetchone()
        return row["status"] if row else "received"

    def mark_status(self, receipt_id: str, status: str) -> None:
        if status not in {"received", "processed", "failed"}:
            raise ValueError("invalid intake status")
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE intake_requests SET status = ? WHERE receipt_id = ?",
                (status, receipt_id),
            )
