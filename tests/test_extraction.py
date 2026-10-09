import pytest

from asha_shahayak.extraction import amount_words, extract_claim, parse_number


def test_hindi_and_awadhi_numbers_are_parsed():
    assert parse_number("पचास") == 50
    assert parse_number("pachhas") == 50
    assert parse_number("एक सौ") == 100
    assert parse_number("तीन") == 3
    assert parse_number("teen") == 3
    assert parse_number("dhai sau") == 250
    assert parse_number("ढाई सौ") == 250
    assert parse_number("दो सौ पचास") == 250
    assert parse_number("३") == 3
    assert parse_number("२५०") == 250


def test_extraction_fuzzy_matches_fixed_fields():
    claim = extract_claim("teekakaran march count teen amount pachaas")

    assert claim.activity == "vaccination"
    assert claim.month == "march"
    assert claim.count == 3
    assert claim.reported_amount == 50
    assert claim.errors == ()


def test_devanagari_activity_month_and_numbers_are_extracted():
    claim = extract_claim("वैक्सीनेशन जुलाई count तीन amount दो सौ पचास")

    assert claim.activity == "vaccination"
    assert claim.month == "july"
    assert claim.count == 3
    assert claim.reported_amount == 250
    assert claim.errors == ()


def test_devanagari_digits_are_extracted():
    claim = extract_claim("टीकाकरण जुलाई count ३ amount २५०")

    assert claim.activity == "vaccination"
    assert claim.month == "july"
    assert claim.count == 3
    assert claim.reported_amount == 250
    assert claim.errors == ()


@pytest.mark.parametrize(
    "text",
    [
        "Vaccination March Count 3 Amount 259",
        "VACCINATION march count 3 amount 250",
    ],
)
def test_extraction_matches_case_insensitively(text):
    claim = extract_claim(text)

    assert claim.activity == "vaccination"
    assert claim.month == "march"
    assert claim.count == 3
    assert claim.reported_amount in (250, 259)
    assert claim.errors == ()


@pytest.mark.parametrize("text", [
    "Vaccination march count 3 amount 250",
    "VACCINATION march count 3 amount 250",
    "vaccination march, count 3 amount 250",
    "  vaccination   march   count 3 amount 250  ",
])
def test_extraction_normalises_case_punctuation_and_spaces(text):
    claim = extract_claim(text)

    assert claim.activity == "vaccination"
    assert claim.month == "march"
    assert claim.errors == ()


def test_activity_without_details_reports_missing_month():
    claim = extract_claim("vaccination")

    assert "month_not_found" in claim.errors


def test_emergency_does_not_become_a_claim():
    claim = extract_claim("ambulance chahiye, saans nahi aa rahi")

    assert claim.emergency is True


def test_out_of_range_amount_is_reported():
    claim = extract_claim("vaccination march count 1 amount 999999")

    assert "amount_out_of_range" in claim.errors


def test_wrong_month_is_rejected():
    claim = extract_claim("vaccination blorp count 1 amount 100")

    assert "month_not_found" in claim.errors


def test_amount_has_digit_and_word_readback_form():
    assert amount_words(300) == "three hundred"
