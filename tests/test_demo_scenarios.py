"""Demo scenarios 3 and 4: weekday date resolution and wrong-amount blocking."""
from datetime import datetime

from asha_shahayak.dates import resolve_day
from asha_shahayak.factset import EvidenceState, Fact, FactSet
from asha_shahayak.validator import validate_reply

NOW = datetime(2026, 10, 8, 12, 0)  # Thursday


def test_budhwar_resolves_only_to_wednesdays():
    result = resolve_day("Budhwar ka paisa nahi mila", NOW)

    assert result["candidates"], "Budhwar should resolve to at least one date"
    assert all(d.weekday() == 2 for d in result["candidates"])
    assert all(d < NOW.date() for d in result["candidates"])


def _facts(expected):
    return FactSet(
        (
            Fact("expected_amount", expected, EvidenceState.DERIVED, "SAMPLE_ONLY"),
            Fact("payment_status", "received", EvidenceState.SMS, "department_sms"),
        )
    )


def test_wrong_amount_20000_instead_of_2000_is_blocked():
    ok, reasons = validate_reply("₹20000 मिल गया", _facts(2000))

    assert not ok
    assert "amount_mismatch" in reasons


def test_wrong_amount_with_comma_is_blocked():
    ok, reasons = validate_reply("₹20,000 मिल गया", _facts(2000))

    assert not ok
    assert "amount_mismatch" in reasons


def test_correct_amount_2000_is_allowed():
    assert validate_reply("₹2000 मिल गया", _facts(2000)) == (True, [])
