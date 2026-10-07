from datetime import date

from asha_shahayak.extraction import extract_claim
from asha_shahayak.gaps import find_gap
from asha_shahayak.rules import calculate_amount, load_rate_cards


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
