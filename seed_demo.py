import argparse

from asha_shahayak.complaints import ComplaintStore
from asha_shahayak.ledger import Ledger
from asha_shahayak.status import StatusStore


def seed(path: str) -> None:
    ledger = Ledger(path)
    entry = ledger.append({"label": "DEMO", "activity": "vaccination", "amount": 250})
    ledger.record_gap(entry["receipt_id"], 50, ("DEMO", "rules_engine", "expected_300_reported_250"))
    complaints = ComplaintStore(path)
    complaints.create(
        "DEMO-complaint-1",
        entry["receipt_id"],
        "DEMO: sample complaint for officer review",
    )
    complaints.decide("DEMO-complaint-1", True)
    StatusStore(path).import_csv(
        b"asha_id,month,head,claimed_amount,approved_amount,stage,stage_date\n"
        b",march,vaccination,250,300,paid,2026-04-05\n"
    )
    print(f"DEMO rows added to {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="asha_shahayak.sqlite3")
    seed(parser.parse_args().database)
