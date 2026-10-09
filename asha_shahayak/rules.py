import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .extraction import ExtractedClaim


@dataclass(frozen=True)
class RateCard:
    activity: str
    rate: int
    source: str
    effective_date: date
    rate_type: str = "per_unit"


def load_rate_cards(path: str | Path) -> list[RateCard]:
    cards = []
    with open(path, newline="", encoding="utf-8") as file:
        for row in csv.DictReader(line for line in file if not line.startswith("#")):
            cards.append(
                RateCard(
                    activity=row["activity"],
                    rate=int(row["rate"]),
                    source=row["source"],
                    effective_date=date.fromisoformat(row["effective_date"]),
                    rate_type=row.get("rate_type", "per_unit"),
                )
            )
    return cards


def month_start(month: str | None, today: date | None = None) -> date:
    today = today or date.today()
    if month is None:
        return today
    month_numbers = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    number = month_numbers.get(month.lower())
    if number is None:
        raise ValueError("no applicable rate card")
    year = today.year if number <= today.month else today.year - 1
    return date(year, number, 1)


def find_rate_card(
    claim: ExtractedClaim, cards: list[RateCard], on_date: date
) -> RateCard:
    matching = [
        card for card in cards
        if card.activity == claim.activity and card.effective_date <= on_date
    ]
    if not claim.count or not matching:
        raise ValueError("no applicable rate card")
    return max(matching, key=lambda item: item.effective_date)


def calculate_amount(claim: ExtractedClaim, cards: list[RateCard], on_date: date) -> int:
    card = find_rate_card(claim, cards, on_date)
    if card.rate_type == "flat_monthly":
        return card.rate
    return card.rate * claim.count
