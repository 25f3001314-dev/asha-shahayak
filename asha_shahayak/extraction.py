import re
from dataclasses import dataclass

from rapidfuzz import fuzz, process


ACTIVITIES = {
    "vaccination": ["vaccination", "टीकाकरण", "टीका", "teekakaran", "teeka"],
    "home_visit": ["home visit", "घर भ्रमण", "घर का दौरा", "ghar visit"],
    "antenatal_visit": ["antenatal visit", "गर्भ जांच", "गर्भ जाँच", "garbh jaanch"],
}

MONTHS = {
    "january": ["january", "jan", "जनवरी", "जन"],
    "february": ["february", "feb", "फरवरी", "फर"],
    "march": ["march", "mar", "मार्च"],
    "april": ["april", "apr", "अप्रैल"],
    "may": ["may", "मई"],
    "june": ["june", "jun", "जून"],
    "july": ["july", "jul", "जुलाई"],
    "august": ["august", "aug", "अगस्त"],
    "september": ["september", "sep", "सितंबर", "सितम्बर"],
    "october": ["october", "oct", "अक्टूबर"],
    "november": ["november", "nov", "नवंबर", "नवम्बर"],
    "december": ["december", "dec", "दिसंबर", "दिसम्बर"],
}

NUMBER_WORDS = {
    "zero": 0, "shunya": 0, "शून्य": 0,
    "one": 1, "ek": 1, "एक": 1,
    "two": 2, "do": 2, "दो": 2,
    "three": 3, "teen": 3, "तीन": 3,
    "four": 4, "char": 4, "चार": 4,
    "five": 5, "paanch": 5, "panch": 5, "पांच": 5, "पाँच": 5,
    "six": 6, "chhah": 6, "छह": 6,
    "seven": 7, "saat": 7, "सात": 7,
    "eight": 8, "aath": 8, "आठ": 8,
    "nine": 9, "nau": 9, "नौ": 9,
    "ten": 10, "das": 10, "दस": 10,
    "eleven": 11, "gyarah": 11, "ग्यारह": 11,
    "twelve": 12, "barah": 12, "बारह": 12,
    "thirteen": 13, "terah": 13, "तेरह": 13,
    "fourteen": 14, "chaudah": 14, "चौदह": 14,
    "fifteen": 15, "pandrah": 15, "पंद्रह": 15,
    "sixteen": 16, "solah": 16, "सोलह": 16,
    "seventeen": 17, "satrah": 17, "सत्रह": 17,
    "eighteen": 18, "atharah": 18, "अठारह": 18,
    "nineteen": 19, "unnis": 19, "उन्नीस": 19,
    "twenty": 20, "bees": 20, "बीस": 20,
    "thirty": 30, "tees": 30, "तीस": 30,
    "forty": 40, "chalees": 40, "चालीस": 40,
    "fifty": 50, "pachaas": 50, "pachas": 50, "pachhas": 50, "पचास": 50,
    "sixty": 60, "saath": 60, "साठ": 60,
    "seventy": 70, "sattar": 70, "सत्तर": 70,
    "eighty": 80, "assi": 80, "अस्सी": 80,
    "ninety": 90, "nabbe": 90, "नब्बे": 90,
    "hundred": 100, "sau": 100, "सौ": 100,
}


def amount_words(number: int) -> str:
    if number < 100:
        names = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
        return names[number] if number < 10 else next(
            word for word, value in NUMBER_WORDS.items() if value == number and word.isascii()
        )
    if number == 100:
        return "one hundred"
    if number < 1000:
        hundreds, rest = divmod(number, 100)
        return f"{amount_words(hundreds)} hundred" + (f" {amount_words(rest)}" if rest else "")
    thousands, rest = divmod(number, 1000)
    return f"{amount_words(thousands)} thousand" + (f" {amount_words(rest)}" if rest else "")


@dataclass(frozen=True)
class ExtractedClaim:
    activity: str | None
    month: str | None
    count: int | None
    status: str | None
    reported_amount: int | None
    emergency: bool
    errors: tuple[str, ...]


def parse_number(value: str) -> int | None:
    value = value.strip().lower()
    if value.isdigit() or re.fullmatch(r"[०-९]+", value):
        return int(value.translate(str.maketrans("०१२३४५६७८९", "0123456789")))
    words = re.split(r"[\s-]+", value)
    values = [NUMBER_WORDS.get(word) for word in words]
    if any(item is None for item in values):
        return None
    if 100 in values:
        before = values[0] if values[0] != 100 else 1
        return before * 100 + sum(values[values.index(100) + 1 :])
    return sum(values)


def _find_number(text: str, labels: tuple[str, ...]) -> int | None:
    pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?:{pattern})\s*(?:is|hai|का|की|:)?\s*([0-9०-९]+|[^\s,;]+(?:\s+[^\s,;]+)?)",
        text,
        re.I,
    )
    if not match:
        return None
    words = match.group(1).split()
    for size in (2, 1):
        number = parse_number(" ".join(words[:size]))
        if number is not None:
            return number
    return None


def _fuzzy_key(text: str, choices: dict[str, list[str]]) -> str | None:
    candidates = [(alias, key) for key, aliases in choices.items() for alias in aliases]
    match = process.extractOne(text, [item[0] for item in candidates], scorer=fuzz.token_set_ratio)
    if not match or match[1] < 65:
        return None
    return next(key for alias, key in candidates if alias == match[0])


def extract_claim(text: str, *, max_amount: int = 100_000) -> ExtractedClaim:
    lowered = re.sub(r"[^\w\s₹]", " ", text.casefold())
    lowered = " ".join(lowered.split())
    emergency_words = ("ambulance", "emergency", "सांस नहीं", "बेहोश", "खून बहुत")
    emergency = any(word in lowered for word in emergency_words)
    activity = _fuzzy_key(lowered, ACTIVITIES)
    month = _fuzzy_key(lowered, MONTHS)
    count = _find_number(lowered, ("count", "गिनती", "संख्या", "बार", "visits", "बार"))
    amount = _find_number(lowered, ("amount", "राशि", "पैसा", "payment", "₹", "rs"))
    errors = []
    if not activity:
        errors.append("activity_not_found")
    if not month:
        errors.append("month_not_found")
    if count is None or count <= 0:
        errors.append("count_invalid")
    if amount is not None and not 0 <= amount <= max_amount:
        errors.append("amount_out_of_range")
    return ExtractedClaim(
        activity=activity,
        month=month,
        count=count,
        status="reported" if amount is not None else None,
        reported_amount=amount,
        emergency=emergency,
        errors=tuple(errors),
    )
