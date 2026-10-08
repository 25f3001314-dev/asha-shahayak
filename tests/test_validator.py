from asha_shahayak.factset import EvidenceState, Fact, FactSet
from asha_shahayak.validator import safe_fallback, validate_reply


def test_unknown_status_and_wrong_amount_are_blocked():
    facts = FactSet(
        (
            Fact("expected_amount", 250, EvidenceState.DERIVED, "SAMPLE_ONLY"),
            Fact("payment_status", None, EvidenceState.UNKNOWN, "no_payment_evidence"),
        )
    )

    ok, reasons = validate_reply("₹2000 pending है", facts)

    assert not ok
    assert "amount_mismatch" in reasons
    assert "status_not_evidenced" in reasons


def test_matching_amount_is_allowed():
    facts = FactSet(
        (
            Fact("expected_amount", 250, EvidenceState.DERIVED, "SAMPLE_ONLY"),
            Fact("payment_status", "received", EvidenceState.SMS, "department_sms"),
        )
    )

    assert validate_reply("₹250 मिल गया", facts) == (True, [])


def test_devanagari_amount_mismatch_is_blocked():
    facts = FactSet(
        (Fact("expected_amount", 250, EvidenceState.DERIVED, "SAMPLE_ONLY"),)
    )

    ok, reasons = validate_reply("₹२,०००", facts)

    assert not ok
    assert reasons == ["amount_mismatch"]


def test_safe_fallback_passes_validation():
    reply = safe_fallback("ASHA-1")

    assert validate_reply(reply, FactSet()) == (True, [])
