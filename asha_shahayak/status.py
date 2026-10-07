import csv
import io
import sqlite3
from pathlib import Path

from .extraction import MONTHS


class StatusStore:
    columns = {"activity", "month", "status", "amount"}

    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS officer_status (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    activity TEXT NOT NULL,
                    month TEXT NOT NULL,
                    status TEXT NOT NULL,
                    amount INTEGER NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def import_csv(self, content: bytes) -> int:
        try:
            text = content.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
        except UnicodeDecodeError as error:
            raise ValueError("CSV must be UTF-8") from error
        if not reader.fieldnames or set(reader.fieldnames) != self.columns:
            raise ValueError("CSV columns must be: activity, month, status, amount")
        rows = []
        for line_number, row in enumerate(reader, 2):
            month = (row.get("month") or "").strip().lower()
            if month not in MONTHS:
                raise ValueError(f"invalid month on row {line_number}: {month}")
            try:
                amount = int((row.get("amount") or "").strip())
            except ValueError as error:
                raise ValueError(f"amount must be a number on row {line_number}") from error
            if amount < 0 or not row.get("activity") or not row.get("status"):
                raise ValueError(f"invalid values on row {line_number}")
            rows.append((row["activity"].strip(), month, row["status"].strip(), amount))
        with self._connect() as db:
            db.executemany(
                "INSERT INTO officer_status (activity, month, status, amount) VALUES (?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def all(self) -> list[dict]:
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT activity, month, status, amount FROM officer_status ORDER BY id"
            ).fetchall()]

    def find(self, activity: str, month: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT activity, month, status, amount FROM officer_status "
                "WHERE activity = ? AND month = ? ORDER BY id DESC LIMIT 1",
                (activity, month),
            ).fetchone()
        return dict(row) if row else None
