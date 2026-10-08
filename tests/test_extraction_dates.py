from datetime import datetime
from asha_shahayak.dates import IST
from asha_shahayak.extraction import extract_claim

NOW = datetime(2026, 10, 8, 10, 0, tzinfo=IST)  # Thursday


def test_budhwar_gives_date_and_month():
    c = extract_claim("Budhwar ko vaccination kiya count 3 amount 250", now=NOW)
    assert c.day_candidates == ("2026-10-07",)
    assert c.month == "october"
    assert "month_not_found" not in c.errors


def test_same_weekday_needs_confirmation():
    c = extract_claim("guruvar ko vaccination kiya count 3", now=NOW)
    assert "date_needs_confirmation" in c.errors
    assert len(c.day_candidates) == 2
