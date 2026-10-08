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
    facts: list[Fact] = [
        Fact(
            "payment_status",
            None,
            EvidenceState.UNKNOWN,
            "no_payment_evidence",
        )
    ]
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
