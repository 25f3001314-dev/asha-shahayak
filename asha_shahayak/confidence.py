from dataclasses import dataclass
from enum import StrEnum


class ConfidenceBand(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class ConfidenceInput:
    asr_score: float
    schema_valid: bool
    business_rules_valid: bool


@dataclass(frozen=True)
class ConfidenceDecision:
    band: ConfidenceBand
    action: str


def evaluate_confidence(
    evidence: ConfidenceInput,
    *,
    high_threshold: float,
    medium_threshold: float,
) -> ConfidenceDecision:
    """Classify extracted evidence without calculating or changing its values."""
    if not 0 <= evidence.asr_score <= 1:
        raise ValueError("asr_score must be between 0 and 1")
    if not 0 <= medium_threshold <= high_threshold <= 1:
        raise ValueError("thresholds must satisfy 0 <= medium <= high <= 1")

    if (
        evidence.asr_score >= high_threshold
        and evidence.schema_valid
        and evidence.business_rules_valid
    ):
        return ConfidenceDecision(ConfidenceBand.HIGH, "proceed_to_read_back")

    if evidence.asr_score >= medium_threshold and evidence.schema_valid:
        return ConfidenceDecision(ConfidenceBand.MEDIUM, "request_confirmation_again")

    return ConfidenceDecision(ConfidenceBand.LOW, "fallback_to_keypad_or_human_callback")
