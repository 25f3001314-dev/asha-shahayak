import csv
import io
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .session import sender_hash


class AshaRegistry:
    columns = {"asha_id", "phone_number"}

    def __init__(self, database_path: str, salt: str) -> None:
        self.database_path = database_path
        self.salt = salt
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS asha_registry (
                    asha_id TEXT PRIMARY KEY,
                    expected_phone_hash TEXT NOT NULL,
                    bound_phone_hash TEXT,
                    bound_at TEXT
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def import_csv(self, content: bytes, *, replace: bool = False) -> int:
        if not self.salt:
            raise ValueError("session salt is required for ASHA registration")
        try:
            reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        except UnicodeDecodeError as error:
            raise ValueError("CSV must be UTF-8") from error
        if set(reader.fieldnames or ()) != self.columns:
            raise ValueError("CSV columns must be: asha_id, phone_number")
        rows = []
        for line_number, row in enumerate(reader, 2):
            asha_id = (row.get("asha_id") or "").strip().upper()
            phone = (row.get("phone_number") or "").strip()
            if not asha_id or not phone:
                raise ValueError(f"invalid values on row {line_number}")
            rows.append((asha_id, sender_hash(phone, self.salt)))
        with self._connect() as db:
            if replace:
                db.execute("DELETE FROM asha_registry")
            db.executemany(
                """
                INSERT INTO asha_registry (asha_id, expected_phone_hash)
                VALUES (?, ?)
                ON CONFLICT(asha_id) DO UPDATE SET
                    expected_phone_hash = excluded.expected_phone_hash
                """,
                rows,
            )
        return len(rows)

    def has_entries(self) -> bool:
        with self._connect() as db:
            return db.execute("SELECT 1 FROM asha_registry LIMIT 1").fetchone() is not None

    def ids(self) -> set[str]:
        with self._connect() as db:
            rows = db.execute("SELECT asha_id FROM asha_registry").fetchall()
        return {row["asha_id"] for row in rows}

    def bind_sender(self, asha_id: str, sender: str) -> bool:
        phone_hash = sender_hash(sender, self.salt)
        with self._connect() as db:
            row = db.execute(
                "SELECT expected_phone_hash, bound_phone_hash FROM asha_registry WHERE asha_id = ?",
                (asha_id.upper(),),
            ).fetchone()
            if not row or not secrets.compare_digest(
                phone_hash.encode("utf-8"), row["expected_phone_hash"].encode("utf-8")
            ):
                return False
            if row["bound_phone_hash"] is not None:
                return secrets.compare_digest(
                    phone_hash.encode("utf-8"),
                    row["bound_phone_hash"].encode("utf-8"),
                )
            if row["bound_phone_hash"] is None:
                db.execute(
                    "UPDATE asha_registry SET bound_phone_hash = ?, bound_at = ? "
                    "WHERE asha_id = ?",
                    (phone_hash, datetime.now(timezone.utc).isoformat(), asha_id.upper()),
                )
            return True

    def resolve_sender(self, sender: str) -> str | None:
        phone_hash = sender_hash(sender, self.salt)
        with self._connect() as db:
            row = db.execute(
                "SELECT asha_id FROM asha_registry WHERE bound_phone_hash = ?",
                (phone_hash,),
            ).fetchone()
        return row["asha_id"] if row else None

    def reset(self, asha_id: str) -> None:
        with self._connect() as db:
            changed = db.execute(
                "UPDATE asha_registry SET bound_phone_hash = NULL, bound_at = NULL "
                "WHERE asha_id = ?",
                (asha_id.upper(),),
            ).rowcount
        if not changed:
            raise KeyError("ASHA ID not found")
