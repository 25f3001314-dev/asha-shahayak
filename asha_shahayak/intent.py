"""Small rule-based intent classifier for ASHA messages."""

LEXICON = {
    "greeting": (
        "नमस्ते",
        "नमस्कार",
        "प्रणाम",
        "hello",
        "hi",
        "hii",
        "hey",
        "namaste",
        "namaskar",
    ),
    "followup_status": (
        "उसका",
        "उसका स्टेटस",
        "वह",
        "पिछला",
        "uska",
        "woh",
        "pichhla",
        "status batao",
    ),
    "grievance_request": (
        "शिकायत",
        "शिकायत करना",
        "शिकायत दर्ज",
        "शिकayat",
        "shikayat",
        "complaint",
        "gila",
    ),
    "payment_status": (
        "पैसा कब",
        "पैसे कब",
        "भुगतान कब",
        "payment kab",
        "paisa kab",
        "paise kab",
        "payment status",
        "भुगतान का स्टेटस",
    ),
}


def classify_intent(text: str) -> str:
    normalized = " ".join(text.casefold().split())
    words = set(normalized.split())
    if any(
        keyword == normalized or keyword in words
        for keyword in LEXICON["greeting"]
    ):
        return "greeting"
    if any(keyword in normalized for keyword in LEXICON["followup_status"]):
        return "followup_status"
    if any(keyword in normalized for keyword in LEXICON["grievance_request"]):
        return "grievance_request"
    if any(keyword in normalized for keyword in LEXICON["payment_status"]):
        return "payment_status"
    return "unknown"


def intent_reply(intent: str) -> str:
    if intent == "greeting":
        return "नमस्ते। मैं ASHA Shahayak हूँ। भुगतान से जुड़ी जानकारी के लिए काम, महीना, गिनती और मिली हुई राशि बताएं।"
    if intent == "payment_status":
        return "रिकॉर्ड से तारीख पक्की नहीं है।"
    if intent == "followup_status":
        return "आप किस भुगतान की बात कर रही हैं?"
    if intent == "grievance_request":
        return "कृपया भुगतान का काम, महीना और गिनती बताएं।"
    return "कृपया भुगतान का काम, महीना और गिनती बताएं।"
