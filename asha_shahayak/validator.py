"""Validate reply facts before sending them to an ASHA."""

import re
from typing import Iterable

from .factset import EvidenceState, FactSet

_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_AMOUNT_PATTERN = re.compile(r"₹\s*([0-9०-९][0-9०-९,]*)")
_DATE_PATTERN = re.compile(r"\b(\d{2}[-/]\d{2}[-/]\d{4})\b")
_STATUS_WORDS = (
    "लंबित",
    "pending",
    "मिल गया",
    "नहीं मिला",
    "आया",
    "नहीं आया",
)


def _fact_values(factset: FactSet, names: Iterable[str]) -> set[int]:
    values: set[int] = set()
    for name in names:
        fact = factset.get(name)
        if fact is not None and isinstance(fact.value, int):
            values.add(fact.value)
    return values


def validate_reply(reply_text: str, factset: FactSet) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    amounts = {
        int(match.group(1).replace(",", "").translate(_DIGITS))
        for match in _AMOUNT_PATTERN.finditer(reply_text)
    }
    allowed_amounts = _fact_values(
        factset, ("amount", "expected_amount", "reported_amount", "gap_amount")
    )
    for amount in sorted(amounts):
        if amount not in allowed_amounts:
            reasons.append("amount_mismatch")

    dates = {match.group(1).replace("/", "-") for match in _DATE_PATTERN.finditer(reply_text)}
    allowed_dates = {
        _normalize_date(str(fact.value))
        for fact in factset.all()
        if fact.name == "date"
    }
    for value in dates:
        if value not in allowed_dates:
            reasons.append("date_mismatch")

    if any(word in reply_text.casefold() for word in _STATUS_WORDS):
        status = factset.get("payment_status")
        if status is None or status.state not in (
            EvidenceState.VERIFIED,
            EvidenceState.SMS,
        ):
            reasons.append("status_not_evidenced")

    return not reasons, reasons


def _normalize_date(value: str) -> str:
    value = value.replace("/", "-")
    parts = value.split("-")
    if len(parts) == 3 and len(parts[0]) == 4:
        return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return value


def safe_fallback(receipt_id: str) -> str:
    return f"इस जानकारी की पुष्टि का रिकॉर्ड मेरे पास नहीं है। रसीद ID: {receipt_id}।"
