"""Demo scenario 1 (extraction side): spoken-style text to structured claim."""
from datetime import datetime

from asha_shahayak.extraction import extract_claim

NOW = datetime(2026, 10, 8, 12, 0)


def test_clean_claim_is_extracted():
    claim = extract_claim("vaccination march count 5 amount 2000", now=NOW)

    assert claim.activity == "vaccination"
    assert claim.month == "march"
    assert claim.count == 5
    assert claim.reported_amount == 2000
    assert claim.errors == ()
    assert not claim.emergency


def test_devanagari_activity_and_month_are_recognised():
    claim = extract_claim("टीकाकरण मार्च count 4", now=NOW)

    assert claim.activity == "vaccination"
    assert claim.month == "march"
    assert claim.count == 4


def test_absurd_amount_is_flagged_not_trusted():
    claim = extract_claim("vaccination march count 5 amount 200000", now=NOW)

    assert "amount_out_of_range" in claim.errors


def test_unrecognised_text_reports_errors_instead_of_guessing():
    claim = extract_claim("xyz qqq", now=NOW)

    assert claim.activity is None
    assert "activity_not_found" in claim.errors


def test_emergency_words_are_detected():
    assert extract_claim("ambulance chahiye", now=NOW).emergency
