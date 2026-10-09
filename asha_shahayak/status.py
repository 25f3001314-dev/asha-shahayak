import csv
import io
import sqlite3
from pathlib import Path

from .extraction import MONTHS


class StatusStore:
    columns = {
        "asha_id", "month", "head", "claimed_amount", "approved_amount",
        "stage", "stage_date",
    }
    stages = {"anm_pending", "moic_pending", "bam_pending", "paid"}

    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS officer_status (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asha_id TEXT,
                    month TEXT NOT NULL,
                    head TEXT NOT NULL,
                    claimed_amount INTEGER,
                    approved_amount INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    stage_date TEXT
                )
                """
            )
            columns = {
                row[1] for row in db.execute("PRAGMA table_info(officer_status)")
            }
            migrations = {
                "asha_id": "TEXT",
                "head": "TEXT",
                "claimed_amount": "INTEGER",
                "approved_amount": "INTEGER",
                "stage": "TEXT",
                "stage_date": "TEXT",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE officer_status ADD COLUMN {name} {definition}")
            db.execute(
                "CREATE TABLE IF NOT EXISTS head_aliases "
                "(spoken_name TEXT PRIMARY KEY, head TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def import_csv(self, content: bytes, *, replace: bool = False) -> int:
        try:
            text = content.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
        except UnicodeDecodeError as error:
            raise ValueError("CSV must be UTF-8") from error
        fields = set(reader.fieldnames or ())
        old_columns = {"activity", "month", "status", "amount"}
        if fields == old_columns:
            rows = []
            for line_number, row in enumerate(reader, 2):
                stage = (row.get("status") or "").strip().lower()
                if stage not in self.stages:
                    raise ValueError(
                        f"unknown stage on row {line_number}: {stage}"
                    )
                month = (row.get("month") or "").strip().lower()
                if month not in MONTHS:
                    raise ValueError(f"invalid month on row {line_number}: {month}")
                try:
                    amount = int((row.get("amount") or "").strip())
                except ValueError as error:
                    raise ValueError(f"amount must be a number on row {line_number}") from error
                rows.append((None, month, row["activity"].strip(), None, amount, stage, None))
                self._pending_aliases = getattr(self, "_pending_aliases", [])
                self._pending_aliases.append((row["activity"].strip(), row["activity"].strip()))
        elif fields == self.columns:
            rows = []
            for line_number, row in enumerate(reader, 2):
                month = (row.get("month") or "").strip().lower()
                if month not in MONTHS:
                    raise ValueError(f"invalid month on row {line_number}: {month}")
                stage = (row.get("stage") or "").strip().lower()
                if stage not in self.stages:
                    raise ValueError(f"unknown stage on row {line_number}: {stage}")
                try:
                    approved = int((row.get("approved_amount") or "").strip())
                    claimed = (row.get("claimed_amount") or "").strip()
                    claimed_value = int(claimed) if claimed else None
                except ValueError as error:
                    raise ValueError(f"amount must be a number on row {line_number}") from error
                if approved < 0 or (claimed_value is not None and claimed_value < 0):
                    raise ValueError(f"invalid values on row {line_number}")
                rows.append((
                    (row.get("asha_id") or "").strip() or None,
                    month,
                    row["head"].strip(),
                    claimed_value,
                    approved,
                    stage,
                    (row.get("stage_date") or "").strip() or None,
                ))
                self._pending_aliases = getattr(self, "_pending_aliases", [])
                self._pending_aliases.append((row["head"].strip(), row["head"].strip()))
        else:
            raise ValueError(
                (
                    "CSV columns must be: activity, month, status, amount; or "
                    "asha_id, month, head, claimed_amount, approved_amount, "
                    "stage, stage_date"
                )
            )
        with self._connect() as db:
            if replace:
                db.execute("DELETE FROM officer_status")
                db.execute("DELETE FROM head_aliases")
            db.executemany(
                "INSERT INTO officer_status "
                "(asha_id, month, head, claimed_amount, approved_amount, stage, stage_date) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            db.executemany(
                "INSERT OR IGNORE INTO head_aliases (spoken_name, head) VALUES (?, ?)",
                getattr(self, "_pending_aliases", []),
            )
            self._pending_aliases = []
        return len(rows)

    def all(self) -> list[dict]:
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT asha_id, month, head, claimed_amount, approved_amount, "
                "stage, stage_date FROM officer_status ORDER BY id"
            ).fetchall()]

    def for_asha(self, asha_id: str) -> list[dict]:
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT asha_id, month, head, claimed_amount, approved_amount, "
                "stage, stage_date FROM officer_status WHERE asha_id = ? "
                "ORDER BY id DESC",
                (asha_id.upper(),),
            ).fetchall()]

    def add_alias(self, spoken_name: str, head: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO head_aliases (spoken_name, head) VALUES (?, ?)",
                (spoken_name.casefold().strip(), head.strip()),
            )

    def resolve_head(self, spoken_name: str) -> str | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT head FROM head_aliases WHERE spoken_name = ?",
                (spoken_name.casefold().strip(),),
            ).fetchone()
        return row["head"] if row else None

    def aliases(self) -> list[tuple[str, str]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT spoken_name, head FROM head_aliases ORDER BY length(spoken_name) DESC"
            ).fetchall()
        return [(row["spoken_name"], row["head"]) for row in rows]

    def heads_for_month(self, month: str, asha_id: str | None = None) -> list[str]:
        with self._connect() as db:
            if asha_id:
                rows = db.execute(
                    "SELECT DISTINCT head FROM officer_status WHERE month = ? "
                    "AND asha_id = ? ORDER BY head",
                    (month, asha_id),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT DISTINCT head FROM officer_status WHERE month = ? ORDER BY head",
                    (month,),
                ).fetchall()
        return [row["head"] for row in rows]

    def find(self, head: str, month: str, asha_id: str | None = None) -> dict | None:
        with self._connect() as db:
            query = (
                "SELECT asha_id, month, head, claimed_amount, approved_amount, "
                "stage, stage_date FROM officer_status WHERE head = ? AND month = ? "
            )
            params: tuple = (head, month)
            if asha_id:
                query += "AND asha_id = ? "
                params += (asha_id,)
            row = db.execute(query + "ORDER BY id DESC LIMIT 1", params).fetchone()
        return dict(row) if row else None
