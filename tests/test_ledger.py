from asha_shahayak.ledger import Ledger


def test_ledger_hash_chain_and_unique_receipts(tmp_path):
    ledger = Ledger(str(tmp_path / "ledger.sqlite3"))
    first = ledger.append({"amount": 100})
    second = ledger.append({"amount": 200})
    entries = ledger.entries()

    assert first["receipt_id"] != second["receipt_id"]
    assert entries[0][2] == "GENESIS"
    assert entries[1][2] == entries[0][3]
