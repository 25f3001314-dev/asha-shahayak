import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


class ComplaintStore:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS complaints (
                    complaint_id TEXT PRIMARY KEY,
                    receipt_id TEXT NOT NULL,
                    draft TEXT NOT NULL,
                    state TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    sender_hash TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            columns = db.execute("PRAGMA table_info(complaints)").fetchall()
            if not any(column[1] == "reason_code" for column in columns):
                db.execute("ALTER TABLE complaints ADD COLUMN reason_code TEXT")
            if not any(column[1] == "sender_hash" for column in columns):
                db.execute("ALTER TABLE complaints ADD COLUMN sender_hash TEXT")
            if not any(column[1] == "created_at" for column in columns):
                db.execute(
                    "ALTER TABLE complaints ADD COLUMN created_at TEXT NOT NULL DEFAULT ''"
                )
                db.execute(
                    "UPDATE complaints SET created_at = updated_at WHERE created_at = ''"
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def create(
        self,
        complaint_id: str,
        receipt_id: str,
        draft: str,
        *,
        sender_hash: str | None = None,
    ) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute(
                "INSERT INTO complaints "
                "(complaint_id, receipt_id, draft, state, updated_at, reason_code, sender_hash, created_at) "
                "VALUES (?, ?, ?, 'draft', ?, NULL, ?, ?)",
                (complaint_id, receipt_id, draft, now, sender_hash, now),
            )
        return {"complaint_id": complaint_id, "state": "draft", "filed": False}

    def pending(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT complaint_id, receipt_id, draft, state, updated_at, reason_code, "
                "sender_hash, created_at FROM complaints WHERE state = 'draft' ORDER BY updated_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def confirmed(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT complaint_id, receipt_id, draft, state, updated_at, reason_code, "
                "sender_hash, created_at FROM complaints WHERE state = 'filed' ORDER BY updated_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def for_receipt(self, receipt_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT complaint_id, receipt_id, draft, state, updated_at, reason_code, "
                "sender_hash, created_at "
                "FROM complaints WHERE receipt_id = ? ORDER BY updated_at LIMIT 1",
                (receipt_id,),
            ).fetchone()
        return dict(row) if row else None

    def latest_pending_for_sender(
        self, sender_hash: str, *, now: datetime | None = None
    ) -> dict | None:
        now = now or datetime.now(timezone.utc)
        with self._connect() as db:
            rows = db.execute(
                "SELECT complaint_id, receipt_id, draft, state, updated_at, reason_code, "
                "sender_hash, created_at FROM complaints "
                "WHERE state = 'draft' AND sender_hash = ? ORDER BY created_at DESC",
                (sender_hash,),
            ).fetchall()
        if not rows:
            return None
        created_at = datetime.fromisoformat(rows[0]["created_at"])
        return dict(rows[0]) if now - created_at <= timedelta(hours=24) else None

    def latest_decided_for_sender(
        self, sender_hash: str, *, now: datetime | None = None
    ) -> dict | None:
        now = now or datetime.now(timezone.utc)
        with self._connect() as db:
            row = db.execute(
                "SELECT complaint_id, state, updated_at FROM complaints "
                "WHERE sender_hash = ? AND state != 'draft' ORDER BY updated_at DESC LIMIT 1",
                (sender_hash,),
            ).fetchone()
        if row and now - datetime.fromisoformat(row["updated_at"]) <= timedelta(hours=24):
            return dict(row)
        return None

    def set_reason(self, complaint_id: str, reason_code: str) -> None:
        with self._connect() as db:
            changed = db.execute(
                "UPDATE complaints SET reason_code = ?, updated_at = ? "
                "WHERE complaint_id = ? AND state = 'filed'",
                (reason_code, datetime.now(timezone.utc).isoformat(), complaint_id),
            ).rowcount
        if not changed:
            raise KeyError("pending complaint not found")

    def decide(self, complaint_id: str, confirm: bool) -> dict:
        state = "filed" if confirm else "rejected"
        with self._connect() as db:
            row = db.execute(
                "SELECT complaint_id, state FROM complaints WHERE complaint_id = ?",
                (complaint_id,),
            ).fetchone()
            if not row:
                raise KeyError("complaint not found")
            if row["state"] != "draft":
                return {
                    "complaint_id": complaint_id,
                    "state": row["state"],
                    "filed": row["state"] == "filed",
                    "already_recorded": True,
                }
            db.execute(
                "UPDATE complaints SET state = ?, updated_at = ? WHERE complaint_id = ?",
                (state, datetime.now(timezone.utc).isoformat(), complaint_id),
            )
        return {"complaint_id": complaint_id, "state": state, "filed": confirm}
