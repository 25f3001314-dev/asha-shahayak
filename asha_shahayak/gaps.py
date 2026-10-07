from dataclasses import dataclass


@dataclass(frozen=True)
class Gap:
    found: bool
    rupees: int
    trace: tuple[str, ...]


def find_gap(expected: int, reported: int | None) -> Gap:
    if reported is None:
        return Gap(True, expected, ("reported_amount_missing",))
    difference = expected - reported
    if difference == 0:
        return Gap(False, 0, ("rate_card_match",))
    return Gap(
        True,
        difference,
        (f"rate_card_expected_{expected}", f"reported_amount_{reported}"),
    )
