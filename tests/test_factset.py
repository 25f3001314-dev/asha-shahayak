from asha_shahayak.factset import (
    EvidenceState,
    Fact,
    FactSet,
    factset_from_result,
)


def test_factset_supports_every_evidence_state():
    facts = FactSet(
        tuple(
            Fact(f"fact_{state.value}", state.value, state, "test")
            for state in EvidenceState
        )
    )

    assert {fact.state for fact in facts.all()} == set(EvidenceState)


def test_reported_and_derived_amounts_keep_sources():
    facts = factset_from_result(
        {
            "expected_amount": 750,
            "gap": {
                "found": True,
                "rupees": 500,
                "trace": (
                    "rate_source_SAMPLE_ONLY",
                    "reported_amount_250",
                ),
            },
        }
    )

    assert facts.get("reported_amount").state == EvidenceState.REPORTED
    assert facts.get("reported_amount").source == "asha_statement"
    assert facts.get("expected_amount").state == EvidenceState.DERIVED
    assert facts.get("expected_amount").source == "SAMPLE_ONLY"


def test_no_payment_evidence_is_unknown():
    facts = factset_from_result({"gap": {"found": False, "trace": ()}})

    assert facts.get("payment_status") == Fact(
        "payment_status",
        None,
        EvidenceState.UNKNOWN,
        "no_payment_evidence",
    )


def test_different_amount_sources_are_conflicting():
    facts = factset_from_result(
        {
            "amount_sources": (
                {"value": 250, "source": "asha_statement"},
                {"value": 300, "source": "department_record"},
            )
        }
    )

    assert facts.get("amount").state == EvidenceState.CONFLICTING
