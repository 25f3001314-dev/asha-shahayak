import pytest

from asha_shahayak.confidence import (
    ConfidenceBand,
    ConfidenceInput,
    evaluate_confidence,
)


def test_high_confidence_requires_all_evidence():
    decision = evaluate_confidence(
        ConfidenceInput(asr_score=0.91, schema_valid=True, business_rules_valid=True),
        high_threshold=0.9,
        medium_threshold=0.6,
    )

    assert decision.band is ConfidenceBand.HIGH
    assert decision.action == "proceed_to_read_back"


@pytest.mark.parametrize(
    ("evidence", "expected_band", "expected_action"),
    [
        (
            ConfidenceInput(0.85, True, False),
            ConfidenceBand.MEDIUM,
            "request_confirmation_again",
        ),
        (
            ConfidenceInput(0.95, False, True),
            ConfidenceBand.LOW,
            "fallback_to_keypad_or_human_callback",
        ),
        (
            ConfidenceInput(0.4, True, True),
            ConfidenceBand.LOW,
            "fallback_to_keypad_or_human_callback",
        ),
    ],
)
def test_non_high_evidence_is_gated(evidence, expected_band, expected_action):
    decision = evaluate_confidence(
        evidence, high_threshold=0.9, medium_threshold=0.6
    )

    assert decision.band is expected_band
    assert decision.action == expected_action


def test_invalid_score_is_rejected():
    with pytest.raises(ValueError, match="asr_score"):
        evaluate_confidence(
            ConfidenceInput(1.1, True, True),
            high_threshold=0.9,
            medium_threshold=0.6,
        )
