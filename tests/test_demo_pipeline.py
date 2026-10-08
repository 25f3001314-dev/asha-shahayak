"""Demo scenarios 1, 2 and 6: confidence gate, validator and safe fallback."""
from asha_shahayak.confidence import ConfidenceBand, ConfidenceInput, evaluate_confidence
from asha_shahayak.factset import EvidenceState, Fact, FactSet
from asha_shahayak.validator import safe_fallback, validate_reply

THRESHOLDS = {"high_threshold": 0.9, "medium_threshold": 0.6}


def test_normal_hindi_high_confidence_and_matching_reply_is_sent():
    decision = evaluate_confidence(ConfidenceInput(0.95, True, True), **THRESHOLDS)
    facts = FactSet(
        (
            Fact("expected_amount", 2000, EvidenceState.DERIVED, "SAMPLE_ONLY"),
            Fact("payment_status", "received", EvidenceState.SMS, "department_sms"),
        )
    )

    assert decision.band is ConfidenceBand.HIGH
    assert decision.action == "proceed_to_read_back"
    assert validate_reply("₹2000 मिल गया", facts) == (True, [])


def test_noisy_speech_does_not_proceed_to_read_back():
    decision = evaluate_confidence(ConfidenceInput(0.35, True, True), **THRESHOLDS)

    assert decision.band is ConfidenceBand.LOW
    assert decision.action != "proceed_to_read_back"


def test_failure_path_unevidenced_reply_blocked_and_fallback_allowed():
    ok, reasons = validate_reply("₹2000 pending है", FactSet())

    assert not ok
    assert reasons
    assert validate_reply(safe_fallback("ASHA-1"), FactSet()) == (True, [])
