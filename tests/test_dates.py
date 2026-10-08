from datetime import date, datetime
from asha_shahayak.dates import IST, resolve_day

NOW = datetime(2026, 10, 8, 10, 0, tzinfo=IST)  # Thursday


def test_budhwar_is_yesterday():
    r = resolve_day("Budhwar ko hum kaam kiye the", NOW)
    assert r["candidates"] == [date(2026, 10, 7)] and not r["needs_confirmation"]


def test_devanagari():
    assert resolve_day("बुधवार का पैसा", NOW)["candidates"] == [date(2026, 10, 7)]


def test_same_weekday_is_ambiguous():
    r = resolve_day("guruvar ka paisa", NOW)
    assert r["candidates"] == [date(2026, 10, 8), date(2026, 10, 1)] and r["needs_confirmation"]


def test_kal_and_parso():
    assert resolve_day("kal kaam kiya", NOW)["candidates"] == [date(2026, 10, 7)]
    assert resolve_day("parso kaam kiya", NOW)["candidates"] == [date(2026, 10, 6)]


def test_no_date_asks():
    r = resolve_day("paisa nahi mila", NOW)
    assert r["candidates"] == [] and r["needs_confirmation"]
