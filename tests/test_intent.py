import pytest

from asha_shahayak.intent import classify_intent, intent_reply


@pytest.mark.parametrize(
    "text",
    ["पिछला स्टेटस बताओ", "uska status batao", "woh payment ka kya hua"],
)
def test_followup_status_intent(text):
    assert classify_intent(text) == "followup_status"


@pytest.mark.parametrize(
    "text",
    ["पैसा कब आएगा", "payment kab aayega", "भुगतान कब मिलेगा"],
)
def test_payment_status_intent(text):
    assert classify_intent(text) == "payment_status"


@pytest.mark.parametrize(
    "text",
    ["शिकायत करनी है", "shikayat darj karo", "complaint karni hai"],
)
def test_grievance_request_intent(text):
    assert classify_intent(text) == "grievance_request"


def test_unknown_intent_is_safe():
    assert classify_intent("नमस्ते") == "unknown"
    assert "दावा" not in intent_reply("unknown")


def test_payment_status_does_not_promise_payment_date():
    reply = intent_reply(classify_intent("paisa kab aayega"))

    assert "रिकॉर्ड से तारीख पक्की नहीं" in reply
    assert "आएगा" not in reply
