import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class Ledger:
    def __init__(self, database_path: str, prefix: str = "ASHA") -> None:
        self.database_path = database_path
        self.prefix = prefix
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS ledger_entries (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    entry_hash TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS gap_traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    rupees INTEGER NOT NULL,
                    trace_json TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    def append(self, payload: dict) -> dict:
        created_at = datetime.now(timezone.utc).isoformat()
        receipt_id = f"{self.prefix}-{uuid4().hex[:12].upper()}"
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._connect() as db:
            row = db.execute(
                "SELECT entry_hash FROM ledger_entries ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous_hash = row[0] if row else "GENESIS"
            entry_hash = hashlib.sha256(
                f"{previous_hash}|{receipt_id}|{created_at}|{body}".encode()
            ).hexdigest()
            db.execute(
                """
                INSERT INTO ledger_entries
                (receipt_id, created_at, payload_json, previous_hash, entry_hash)
                VALUES (?, ?, ?, ?, ?)
                """,
                (receipt_id, created_at, body, previous_hash, entry_hash),
            )
        return {"receipt_id": receipt_id, "created_at": created_at, "entry_hash": entry_hash}

    def entries(self) -> list[tuple]:
        with self._connect() as db:
            return db.execute(
                "SELECT sequence, receipt_id, previous_hash, entry_hash FROM ledger_entries ORDER BY sequence"
            ).fetchall()

    def record_gap(self, receipt_id: str, rupees: int, trace: tuple[str, ...]) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO gap_traces (receipt_id, stage, rupees, trace_json) "
                "VALUES (?, ?, ?, ?)",
                (receipt_id, "rules_engine", rupees, json.dumps(trace)),
            )

    def gaps(self) -> list[tuple]:
        with self._connect() as db:
            return db.execute(
                "SELECT receipt_id, stage, rupees, trace_json FROM gap_traces ORDER BY id"
            ).fetchall()
