"""Evidence-backed facts used to constrain ASHA-facing replies."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EvidenceState(StrEnum):
    VERIFIED = "VERIFIED"
    SMS = "SMS"
    DERIVED = "DERIVED"
    REPORTED = "REPORTED"
    UNKNOWN = "UNKNOWN"
    CONFLICTING = "CONFLICTING"


@dataclass(frozen=True)
class Fact:
    name: str
    value: Any
    state: EvidenceState
    source: str


class FactSet:
    def __init__(self, facts: tuple[Fact, ...] = ()) -> None:
        self._facts = facts

    def get(self, name: str) -> Fact | None:
        return next((fact for fact in self._facts if fact.name == name), None)

    def all(self) -> tuple[Fact, ...]:
        return self._facts


def factset_from_result(result: dict[str, Any]) -> FactSet:
    officer_status = result.get("officer_status")
    facts: list[Fact] = [
        Fact(
            "payment_status",
            officer_status["stage"] if officer_status else None,
            EvidenceState.VERIFIED if officer_status else EvidenceState.UNKNOWN,
            "block_csv" if officer_status else "no_payment_evidence",
        )
    ]
    if officer_status:
        facts.extend(
            (
                Fact("block_stage", officer_status["stage"], EvidenceState.VERIFIED, "block_csv"),
                Fact("block_head", officer_status["head"], EvidenceState.VERIFIED, "block_csv"),
            )
        )
        for name in ("claimed_amount", "approved_amount"):
            if officer_status.get(name) is not None:
                facts.append(
                    Fact(name, officer_status[name], EvidenceState.VERIFIED, "block_csv")
                )
        if officer_status.get("stage_date"):
            facts.append(
                Fact("stage_date", officer_status["stage_date"], EvidenceState.VERIFIED, "block_csv")
            )
    elif result.get("officer_status_checked"):
        facts.append(Fact("no_block_record", True, EvidenceState.VERIFIED, "block_csv"))
        facts.append(
            Fact("payment_status", "not_found", EvidenceState.VERIFIED, "block_csv")
        )
    gap = result.get("gap") or {}
    trace = tuple(gap.get("trace") or ())
    rate_source = next(
        (item.removeprefix("rate_source_") for item in trace if item.startswith("rate_source_")),
        "rate_card",
    )
    expected = result.get("expected_amount")
    if expected is not None:
        facts.append(
            Fact("expected_amount", expected, EvidenceState.DERIVED, rate_source)
        )
    query_date = result.get("query_date")
    if query_date:
        facts.append(Fact("date", query_date, EvidenceState.DERIVED, "date_resolver"))
    if "reported_amount" in result:
        facts.append(
            Fact(
                "reported_amount",
                result["reported_amount"],
                EvidenceState.REPORTED,
                "asha_statement",
            )
        )
    for item in trace:
        if item.startswith("reported_amount_"):
            reported = int(item.removeprefix("reported_amount_"))
            facts.append(
                Fact("reported_amount", reported, EvidenceState.REPORTED, "asha_statement")
            )
    amount_sources = result.get("amount_sources") or ()
    values = {item["value"] for item in amount_sources}
    if len(values) > 1:
        facts.append(
            Fact(
                "amount",
                tuple(sorted(values)),
                EvidenceState.CONFLICTING,
                "multiple_sources",
            )
        )
    if gap.get("found") and gap.get("rupees") is not None:
        facts.append(
            Fact("gap_amount", abs(gap["rupees"]), EvidenceState.DERIVED, rate_source)
        )
    return FactSet(tuple(facts))
