import json
from pathlib import Path
from typing import Any

from .complaints import ComplaintStore
from .config import Settings
from .extraction import extract_claim
from .gaps import find_gap
from .ledger import Ledger
from .rules import find_rate_card, load_rate_cards, month_start
from .session import sender_hash
from .status import StatusStore


def reconcile_saved_text(
    text: str, receipt_id: str, settings: Settings, sender: str = ""
) -> dict[str, Any]:
    claim = extract_claim(text)
    if claim.emergency:
        return {"status": "medical_emergency", "receipt_id": receipt_id}
    if "amount_out_of_range" in claim.errors:
        raise ValueError("reported amount is out of range")
    extraction_errors = tuple(
        error for error in claim.errors if error != "month_not_found"
    )
    if extraction_errors:
        raise ValueError(json.dumps({"extraction_errors": extraction_errors}))

    sample_card = Path(__file__).resolve().parent.parent / "data" / "sample_rate_cards.csv"
    cards = load_rate_cards(sample_card)
    on_date = month_start(claim.month)
    card = find_rate_card(claim, cards, on_date)
    expected = card.rate * claim.count
    context = (
        f"rate_{card.rate}_x_count_{claim.count}",
        f"rate_source_{card.source}",
        f"rate_effective_{card.effective_date.isoformat()}",
        f"activity_{claim.activity}",
        f"month_{claim.month or 'missing'}",
    )
    if claim.month is None:
        context = context + ("month_missing_used_today",)
    gap = find_gap(expected, claim.reported_amount, context)
    ledger = Ledger(settings.database_path, settings.receipt_prefix)
    entry = ledger.append(
        {"intake_receipt_id": receipt_id, "claim": claim.__dict__, "expected": expected},
        receipt_id=receipt_id,
    )
    if gap.found and not ledger.has_gap(entry["receipt_id"]):
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
    if len(claim.day_candidates) == 1:
        result["query_date"] = claim.day_candidates[0]
    if gap.found and gap.rupees >= 0:
        complaint_store = ComplaintStore(settings.database_path)
        existing_complaint = complaint_store.for_receipt(entry["receipt_id"])
        if existing_complaint:
            result["complaint"] = {
                "complaint_id": existing_complaint["complaint_id"],
                "state": existing_complaint["state"],
                "filed": existing_complaint["state"] == "filed",
            }
            return result
        complaint_id = f"complaint-{entry['receipt_id']}"
        month = claim.month or "missing (used today's date)"
        reported = (
            str(claim.reported_amount)
            if claim.reported_amount is not None
            else "missing"
        )
        unverified = " (rate unverified)" if card.source == "SAMPLE_ONLY" else ""
        draft = (
            f"Receipt ID: {entry['receipt_id']}; activity: {claim.activity}; "
            f"month: {month}; count: {claim.count}; rate: ₹{card.rate}; "
            f"expected amount: ₹{expected}; reported amount: ₹{reported}; "
            f"difference: ₹{gap.rupees}; rate card source: {card.source}{unverified}. "
            "ASHA confirmation required before filing."
        )
        result["complaint"] = complaint_store.create(
            complaint_id,
            entry["receipt_id"],
            draft,
            sender_hash=(
                sender_hash(sender, settings.session_salt)
                if sender and settings.session_salt
                else None
            ),
        )
    return result
