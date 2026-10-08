"""Resolve spoken Hindi/Awadhi day words to exact IST dates. Never guesses."""
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

WEEKDAYS = {
    0: ["somvar", "somvaar", "सोमवार"],
    1: ["mangalvar", "mangalwar", "mangalvaar", "मंगलवार"],
    2: ["budhwar", "budhvar", "budhwaar", "budhvaar", "बुधवार", "बुद्धवार"],
    3: ["guruvar", "guruwar", "brihaspativar", "veervar", "virvar", "गुरुवार", "बृहस्पतिवार", "वीरवार"],
    4: ["shukravar", "shukrawar", "shukravaar", "शुक्रवार"],
    5: ["shanivar", "shaniwar", "shanivaar", "शनिवार"],
    6: ["ravivar", "raviwar", "itwar", "itvar", "इतवार", "रविवार"],
}
RELATIVE = {"aaj": 0, "आज": 0, "kal": -1, "कल": -1, "parso": -2, "parson": -2, "परसों": -2}
LOOKUP = {w: d for d, words in WEEKDAYS.items() for w in words}


def resolve_day(text: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(IST)
    today = now.astimezone(IST).date()
    tokens = re.findall(r"[\w\u0900-\u097F]+", text.casefold())
    for t in tokens:
        if t in LOOKUP:
            wd = LOOKUP[t]
            back = (today.weekday() - wd) % 7
            if back == 0:
                cands = [today, today - timedelta(days=7)]
            else:
                cands = [today - timedelta(days=back)]
            return _out("weekday", cands, t)
    for t in tokens:
        if t in RELATIVE:
            return _out("relative", [today + timedelta(days=RELATIVE[t])], t)
    return {"kind": "none", "candidates": [], "needs_confirmation": True, "matched": None}


def _out(kind: str, cands: list[date], matched: str) -> dict:
    return {"kind": kind, "candidates": cands, "needs_confirmation": len(cands) > 1, "matched": matched}


def format_date(date_value: date) -> str:
    return date_value.strftime("%d-%m-%Y")
