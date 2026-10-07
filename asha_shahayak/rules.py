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
                )
            )
    return cards


def calculate_amount(claim: ExtractedClaim, cards: list[RateCard], on_date: date) -> int:
    matching = [
        card for card in cards
        if card.activity == claim.activity and card.effective_date <= on_date
    ]
    if not claim.count or not matching:
        raise ValueError("no applicable rate card")
    card = max(matching, key=lambda item: item.effective_date)
    return card.rate * claim.count
