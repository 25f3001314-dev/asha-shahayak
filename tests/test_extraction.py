from asha_shahayak.extraction import amount_words, extract_claim, parse_number


def test_hindi_and_awadhi_numbers_are_parsed():
    assert parse_number("पचास") == 50
    assert parse_number("pachhas") == 50
    assert parse_number("एक सौ") == 100


def test_extraction_fuzzy_matches_fixed_fields():
    claim = extract_claim("teekakaran march count teen amount pachaas")

    assert claim.activity == "vaccination"
    assert claim.month == "march"
    assert claim.count == 3
    assert claim.reported_amount == 50
    assert claim.errors == ()


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
