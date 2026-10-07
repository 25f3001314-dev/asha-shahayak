import json
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from .complaints import ComplaintStore
from .config import Settings
from .extraction import extract_claim
from .gaps import find_gap
from .ledger import Ledger
from .rules import calculate_amount, load_rate_cards
from .status import StatusStore


def reconcile_saved_text(
    text: str, receipt_id: str, settings: Settings
) -> dict[str, Any]:
    claim = extract_claim(text)
    if claim.emergency:
        return {"status": "medical_emergency", "receipt_id": receipt_id}
    if "amount_out_of_range" in claim.errors:
        raise ValueError("reported amount is out of range")
    if claim.errors:
        raise ValueError(json.dumps({"extraction_errors": claim.errors}))

    sample_card = Path(__file__).resolve().parent.parent / "data" / "sample_rate_cards.csv"
    cards = load_rate_cards(sample_card)
    expected = calculate_amount(claim, cards, date.today())
    gap = find_gap(expected, claim.reported_amount)
    ledger = Ledger(settings.database_path, settings.receipt_prefix)
    entry = ledger.append(
        {"intake_receipt_id": receipt_id, "claim": claim.__dict__, "expected": expected}
    )
    if gap.found:
        ledger.record_gap(entry["receipt_id"], gap.rupees, gap.trace)
    result: dict[str, Any] = {
        "status": "reconciled",
        "receipt_id": entry["receipt_id"],
        "intake_receipt_id": receipt_id,
        "expected_amount": expected,
        "gap": gap.__dict__,
        "officer_status": StatusStore(settings.database_path).find(
            claim.activity, claim.month
        ),
    }
    if gap.found:
        complaint_id = f"complaint-{uuid4().hex[:12]}"
        draft = (
            f"Sample reconciliation: expected ₹{expected}, reported "
            f"₹{claim.reported_amount if claim.reported_amount is not None else 'missing'}. "
            "ASHA confirmation required before filing."
        )
        result["complaint"] = ComplaintStore(settings.database_path).create(
            complaint_id, entry["receipt_id"], draft
        )
    return result
