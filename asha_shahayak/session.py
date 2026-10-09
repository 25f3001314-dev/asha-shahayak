"""Short-lived memory for a sender's last confirmed structured query."""

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


class QueryMemory:
    def __init__(
        self,
        database_path: str,
        salt: str,
        *,
        clock: Callable[[], datetime] | None = None,
        ttl: timedelta = timedelta(minutes=30),
    ) -> None:
        self.database_path = database_path
        self.salt = salt
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.ttl = ttl
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as database:
            database.execute(
                """
                CREATE TABLE IF NOT EXISTS confirmed_queries (
                    sender_hash TEXT PRIMARY KEY,
                    activity TEXT NOT NULL,
                    query_date TEXT NOT NULL,
                    receipt_id TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    def _sender_hash(self, sender: str) -> str:
        return sender_hash(sender, self.salt)

    def save(
        self, sender: str, activity: str, query_date: str, receipt_id: str
    ) -> None:
        now = self.clock().astimezone(timezone.utc).isoformat()
        with self._connect() as database:
            database.execute(
                """
                INSERT INTO confirmed_queries
                    (sender_hash, activity, query_date, receipt_id, confirmed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(sender_hash) DO UPDATE SET
                    activity = excluded.activity,
                    query_date = excluded.query_date,
                    receipt_id = excluded.receipt_id,
                    confirmed_at = excluded.confirmed_at
                """,
                (self._sender_hash(sender), activity, query_date, receipt_id, now),
            )

    def get(self, sender: str) -> dict[str, str] | None:
        sender_hash = self._sender_hash(sender)
        with self._connect() as database:
            row = database.execute(
                """
                SELECT activity, query_date, receipt_id, confirmed_at
                FROM confirmed_queries
                WHERE sender_hash = ?
                """,
                (sender_hash,),
            ).fetchone()
            if row is None:
                return None
            confirmed_at = datetime.fromisoformat(row[3])
            if self.clock().astimezone(timezone.utc) - confirmed_at > self.ttl:
                database.execute(
                    "DELETE FROM confirmed_queries WHERE sender_hash = ?",
                    (sender_hash,),
                )
                return None
        return {
            "activity": row[0],
            "date": row[1],
            "receipt_id": row[2],
        }

    def clear(self, sender: str) -> None:
        with self._connect() as database:
            database.execute(
                "DELETE FROM confirmed_queries WHERE sender_hash = ?",
                (self._sender_hash(sender),),
            )


def sender_hash(sender: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{sender}".encode("utf-8")).hexdigest()
