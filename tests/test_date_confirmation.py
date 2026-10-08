from datetime import datetime

from asha_shahayak.extraction import extract_claim
from asha_shahayak.whatsapp import add_clear_date, date_gate_reply


NOW = datetime.fromisoformat("2026-10-08T12:00:00+05:30")


def test_ambiguous_guruvar_requires_date_confirmation():
    claim = extract_claim("vaccination guruvar count 3 amount 250", now=NOW)

    reply = date_gate_reply(claim)

    assert "आप किस तारीख की बात कर रही हैं?" in reply
    assert "08-10-2026" in reply
    assert "01-10-2026" in reply


def test_clear_budhwar_adds_understood_date():
    claim = extract_claim("vaccination budhwar count 3 amount 250", now=NOW)

    reply = add_clear_date("रिकॉर्ड से अंतर तय नहीं हो सका।", claim)

    assert reply.startswith("मैंने समझा: 07-10-2026।")


def test_no_date_has_no_date_prefix_or_gate():
    claim = extract_claim("vaccination march count 3 amount 250", now=NOW)

    assert date_gate_reply(claim) is None
    assert add_clear_date("उत्तर", claim) == "उत्तर"
