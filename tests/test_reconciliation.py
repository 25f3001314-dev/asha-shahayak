from datetime import date

from asha_shahayak.complaints import ComplaintStore
from asha_shahayak.config import Settings
from asha_shahayak.extraction import extract_claim
from asha_shahayak.gaps import find_gap
from asha_shahayak.reconciliation import reconcile_saved_text
from asha_shahayak.rules import (
    calculate_amount,
    find_rate_card,
    load_rate_cards,
    month_start,
)


def test_rules_use_sample_rate_card_without_ai():
    claim = extract_claim("vaccination march count 3 amount 300")
    cards = load_rate_cards("data/sample_rate_cards.csv")

    assert calculate_amount(claim, cards, date(2026, 3, 1)) == 300


def test_gap_trace_shows_rupee_difference():
    gap = find_gap(expected=300, reported=250)

    assert gap.found is True
    assert gap.rupees == 50
    assert "rate_card_expected_300" in gap.trace


def test_missing_reported_amount_is_a_gap():
    gap = find_gap(expected=300, reported=None)

    assert gap.rupees == 300
    assert gap.trace == ("reported_amount_missing",)


def test_rate_card_follows_claim_month(tmp_path):
    path = tmp_path / "rates.csv"
    path.write_text(
        "activity,rate,source,effective_date\n"
        "vaccination,100,TEST,2026-01-01\n"
        "vaccination,120,TEST,2026-04-01\n"
    )
    cards = load_rate_cards(path)

    march = extract_claim("vaccination march count 1 amount 100")
    april = extract_claim("vaccination april count 1 amount 120")

    assert calculate_amount(march, cards, month_start(march.month, date(2026, 10, 7))) == 100
    assert calculate_amount(april, cards, month_start(april.month, date(2026, 10, 7))) == 120


def test_find_rate_card_returns_latest_card_for_date(tmp_path):
    path = tmp_path / "rates.csv"
    path.write_text(
        "activity,rate,source,effective_date\n"
        "vaccination,100,TEST,2026-01-01\n"
        "vaccination,120,TEST,2026-04-01\n"
    )
    card = find_rate_card(
        extract_claim("vaccination april count 1"),
        load_rate_cards(path),
        date(2026, 4, 1),
    )

    assert card.rate == 120
    assert card.effective_date == date(2026, 4, 1)


def test_overpayment_is_a_negative_gap():
    gap = find_gap(expected=300, reported=350)

    assert gap.found is True
    assert gap.rupees == -50
    assert "reported_more_than_expected" in gap.trace


def test_gap_context_has_rate_and_source():
    gap = find_gap(300, 250, ("rate_100_x_count_3", "rate_source_SAMPLE_ONLY"))

    assert "rate_100_x_count_3" in gap.trace
    assert "rate_source_SAMPLE_ONLY" in gap.trace


def test_missing_month_uses_today_and_leaves_a_trace(tmp_path):
    settings = Settings(database_path=str(tmp_path / "missing-month.sqlite3"))

    result = reconcile_saved_text(
        "vaccination count 3 amount 300", "ASHA-input-missing-month", settings
    )

    assert result["gap"]["found"] is False
    assert "month_missing_used_today" in result["gap"]["trace"]


def test_reconcile_creates_actionable_underpayment_complaint(tmp_path):
    settings = Settings(database_path=str(tmp_path / "reconcile.sqlite3"))

    result = reconcile_saved_text(
        "vaccination march count 3 amount 250", "ASHA-input-1", settings
    )

    assert result["gap"]["found"] is True
    assert result["gap"]["rupees"] == 50
    assert result["complaint"]["state"] == "draft"
    complaint = ComplaintStore(settings.database_path).pending()[0]
    assert "vaccination" in complaint["draft"]
    assert "march" in complaint["draft"]
    assert "250" in complaint["draft"]
    assert "SAMPLE_ONLY" in complaint["draft"]


def test_reconcile_records_overpayment_without_complaint(tmp_path):
    settings = Settings(database_path=str(tmp_path / "overpaid.sqlite3"))

    result = reconcile_saved_text(
        "vaccination march count 3 amount 350", "ASHA-input-2", settings
    )

    assert result["gap"]["found"] is True
    assert result["gap"]["rupees"] == -50
    assert "complaint" not in result
