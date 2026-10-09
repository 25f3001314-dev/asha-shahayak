"""In-memory registry and incentive status data loaded from CSV files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


class CsvDataHandler:
    def __init__(self, registry_path: str | Path, status_path: str | Path) -> None:
        self.registry_path = Path(registry_path)
        self.status_path = Path(status_path)
        self.registry = pd.DataFrame()
        self.status = pd.DataFrame()

    def load(self) -> None:
        try:
            registry = pd.read_csv(self.registry_path, dtype=str).fillna("")
            status = pd.read_csv(self.status_path).fillna("")
        except (OSError, pd.errors.ParserError, UnicodeDecodeError) as error:
            logger.exception("CSV data load failed")
            raise ValueError("registry.csv and status.csv could not be loaded") from error

        required_registry = {"asha_id", "phone_number"}
        required_status = {
            "asha_id", "month", "head", "claimed_amount",
            "approved_amount", "stage", "stage_date",
        }
        if set(registry.columns) != required_registry:
            raise ValueError("registry.csv columns must be asha_id, phone_number")
        if not required_status.issubset(status.columns):
            raise ValueError("status.csv is missing required columns")

        registry["asha_id"] = registry["asha_id"].str.strip().str.upper()
        registry["phone_number"] = registry["phone_number"].str.strip()
        status["asha_id"] = status["asha_id"].astype(str).str.strip().str.upper()
        status["stage"] = status["stage"].astype(str).str.strip().str.lower()
        self.registry = registry
        self.status = status
        logger.info("Loaded %d registry and %d status rows", len(registry), len(status))

    def get_asha_status(self, phone_number: str) -> dict[str, Any] | None:
        match = self.registry[self.registry["phone_number"] == phone_number.strip()]
        if match.empty:
            return None
        asha_id = str(match.iloc[0]["asha_id"])
        rows = self.status[self.status["asha_id"] == asha_id].copy()
        for column in ("claimed_amount", "approved_amount"):
            rows[column] = pd.to_numeric(rows[column], errors="coerce").fillna(0)
        pending = rows[rows["stage"].isin(["anm_pending", "moic_pending"])]
        return {
            "asha_id": asha_id,
            "total_claimed_amount": float(rows["claimed_amount"].sum()),
            "total_approved_amount": float(rows["approved_amount"].sum()),
            "pending_records": pending.fillna("").to_dict(orient="records"),
        }
