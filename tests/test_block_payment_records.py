import pytest

from asha_shahayak.config import Settings
from asha_shahayak.factset import factset_from_result
from asha_shahayak.rules import calculate_amount, load_rate_cards
from asha_shahayak.status import StatusStore
from asha_shahayak.validator import validate_reply
from asha_shahayak.whatsapp import reply_for_text
from asha_shahayak.extraction import ExtractedClaim, extract_claim


def _csv(stage: str, head: str = "vaccination") -> bytes:
    return (
        b"asha_id,month,head,claimed_amount,approved_amount,stage,stage_date\n"
        + f"ASHA-1,march,{head},250,300,{stage},2026-04-05\n".encode()
    )


@pytest.mark.parametrize(
    "stage",
    ["anm_pending", "moic_pending", "bam_pending", "paid"],
)
def test_each_block_stage_is_reported_from_the_matching_record(tmp_path, stage):
    database = str(tmp_path / f"{stage}.sqlite3")
    store = StatusStore(database)
    store.import_csv(_csv(stage))
    store.add_alias("टीकाकरण", "vaccination")

    reply = reply_for_text(
        "टीकाकरण march count 3 amount 250",
        "ASHA-input",
        Settings(database_path=database, session_salt="salt", _env_file=None),
        "sender",
    )

    stage_text = {
        "anm_pending": "ANM voucher pending",
        "moic_pending": "MOIC approval pending",
        "bam_pending": "BAM payment pending",
        "paid": "भुगतान हो चुका है",
    }
    assert stage_text[stage] in reply
    facts = factset_from_result(
        {
            "officer_status": store.find("vaccination", "march"),
            "expected_amount": 300,
            "gap": {"found": True, "rupees": 50},
        }
    )
    assert validate_reply(reply, facts)[0] is True


def test_no_block_row_is_explicit_and_offers_complaint_confirmation(tmp_path):
    database = str(tmp_path / "missing.sqlite3")
    store = StatusStore(database)
    store.import_csv(_csv("paid", head="home_visit"))
    store.add_alias("टीकाकरण", "vaccination")
    store.add_alias("vaccination", "vaccination")
    settings = Settings(database_path=database, session_salt="salt", _env_file=None)

    reply = reply_for_text(
        "टीकाकरण march count 3 amount 250", "ASHA-input", settings, "sender"
    )

    assert "इस मद की प्रविष्टि ब्लॉक के रिकॉर्ड में नहीं मिली" in reply
    assert "हाँ या नहीं" in reply
    assert validate_reply(
        reply,
        factset_from_result(
            {
                "officer_status_checked": True,
                "expected_amount": 300,
                "reported_amount": 250,
                "gap": {"found": True, "rupees": 50},
            }
        ),
    )[0] is True


def test_flat_monthly_rate_does_not_multiply_by_count(tmp_path):
    path = tmp_path / "rates.csv"
    path.write_text(
        "activity,rate,source,effective_date,rate_type\n"
        "nutrition,500,TEST,2026-01-01,flat_monthly\n",
        encoding="utf-8",
    )
    claim = ExtractedClaim(
        "nutrition", "march", 3, "reported", 500, False, ()
    )
    assert calculate_amount(claim, load_rate_cards(path), claim_date()) == 500


def test_alias_miss_lists_heads_for_month_without_guessing(tmp_path):
    store = StatusStore(str(tmp_path / "aliases.sqlite3"))
    store.import_csv(_csv("paid", head="nutrition"))
    assert store.resolve_head("पीली गोली") is None
    assert store.heads_for_month("march", "ASHA-1") == ["nutrition"]


def test_unknown_stage_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown stage"):
        StatusStore(str(tmp_path / "bad.sqlite3")).import_csv(_csv("delayed"))


def claim_date():
    from datetime import date

    return date(2026, 3, 1)
