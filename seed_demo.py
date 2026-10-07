import argparse

from asha_shahayak.complaints import ComplaintStore
from asha_shahayak.ledger import Ledger
from asha_shahayak.status import StatusStore


def seed(path: str) -> None:
    ledger = Ledger(path)
    entry = ledger.append({"label": "DEMO", "activity": "vaccination", "amount": 250})
    ledger.record_gap(entry["receipt_id"], 50, ("DEMO", "rules_engine", "expected_300_reported_250"))
    ComplaintStore(path).create(
        "DEMO-complaint-1",
        entry["receipt_id"],
        "DEMO: sample complaint for officer review",
    )
    StatusStore(path).import_csv(
        b"activity,month,status,amount\nvaccination,march,DEMO credited,300\n"
    )
    print(f"DEMO rows added to {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="asha_shahayak.sqlite3")
    seed(parser.parse_args().database)
