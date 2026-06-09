#!/usr/bin/env python3
"""Generate realistic test data for PangoPay reconciliation demo."""

import json
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SETTLEMENTS_DIR = DATA_DIR / "settlements"

MERCHANTS = ["MCH-1001", "MCH-1002", "MCH-1003", "MCH-1004", "MCH-1005"]
CURRENCIES = {
    "mpesa_tanzania": ("TZS", "M-Pesa Tanzania"),
    "ovo_indonesia": ("IDR", "OVO Indonesia"),
    "scb_thailand": ("THB", "SCB Thailand"),
}
# Only the three acquirers with settlement feeds
PROCESSORS = [
    ("mpesa_tanzania", 0.34),
    ("ovo_indonesia", 0.33),
    ("scb_thailand", 0.33),
]

START_DATE = datetime(2025, 11, 1)
DAYS = 30

# Scenario budgets (must fit within acquirer-backed ledger rows)
NORMAL_MATCHES = 400
AMOUNT_MISMATCHES = 10
STATUS_CONFLICTS = 10
SPLIT_SETTLEMENTS = 20
ORPHANED_SETTLEMENTS = 10
MISSING_SETTLEMENTS = 55
FILLER_ROWS = 25  # extra ledger rows with failed/pending (not reconciled)

LEDGER_SIZE = (
    MISSING_SETTLEMENTS
    + NORMAL_MATCHES
    + AMOUNT_MISMATCHES
    + STATUS_CONFLICTS
    + SPLIT_SETTLEMENTS
    + FILLER_ROWS
)


def pick_processor() -> str:
    r = random.random()
    cumulative = 0.0
    for name, weight in PROCESSORS:
        cumulative += weight
        if r <= cumulative:
            return name
    return PROCESSORS[-1][0]


def random_amount(currency: str) -> float:
    if currency == "IDR":
        return round(random.uniform(50_000, 5_000_000), 0)
    return round(random.uniform(500, 80_000), 2)


def gen_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def generate_ledger() -> list[dict]:
    transactions = []
    for i in range(LEDGER_SIZE):
        processor = pick_processor()
        currency, processor_name = CURRENCIES[processor]
        created = START_DATE + timedelta(
            days=random.randint(0, DAYS - 1),
            hours=random.randint(0, 23),
            minutes=random.randint(0, 59),
        )
        transactions.append(
            {
                "transaction_id": gen_id("TXN"),
                "merchant_id": random.choice(MERCHANTS),
                "order_id": gen_id("ORD"),
                "amount": random_amount(currency),
                "currency": currency,
                "status": "settled",
                "created_at": created.strftime("%Y-%m-%dT%H:%M:%S"),
                "processor_name": processor_name,
                "processor_reference": (
                    gen_id("PRC") if processor != "mpesa_tanzania" else None
                ),
                "_processor_key": processor,
                "_idx": i,
            }
        )
    return transactions


def settlement_for_tx(tx: dict, overrides: dict | None = None) -> dict:
    processor = tx["_processor_key"]
    created = datetime.strptime(tx["created_at"], "%Y-%m-%dT%H:%M:%S")
    if processor == "scb_thailand":
        settlement_date = created + timedelta(hours=random.randint(1, 47))
    else:
        settlement_date = created + timedelta(days=random.randint(1, 5))

    base = {
        "settlement_id": gen_id("STL"),
        "merchant_id": tx["merchant_id"],
        "settlement_amount": tx["amount"],
        "currency": tx["currency"],
        "settlement_date": settlement_date.strftime("%Y-%m-%dT%H:%M:%S"),
        "fee_charged": round(tx["amount"] * 0.025, 2),
        "settlement_status": "settled",
    }

    if processor == "mpesa_tanzania":
        base["transaction_id"] = tx["transaction_id"]
    elif processor == "ovo_indonesia":
        base["processor_reference"] = tx["processor_reference"]
        base["order_id"] = tx["order_id"]
    elif processor == "scb_thailand":
        base["order_id"] = tx["order_id"]

    if overrides:
        base.update(overrides)
    return base


def append_settlement(
    tx: dict,
    mpesa: list,
    ovo: list,
    scb: list,
    overrides: dict | None = None,
) -> dict:
    s = settlement_for_tx(tx, overrides)
    key = tx["_processor_key"]
    if key == "mpesa_tanzania":
        mpesa.append(s)
    elif key == "ovo_indonesia":
        ovo.append(s)
    else:
        scb.append(s)
    return s


def main() -> None:
    SETTLEMENTS_DIR.mkdir(parents=True, exist_ok=True)

    ledger = generate_ledger()
    mpesa_settlements: list[dict] = []
    ovo_settlements: list[dict] = []
    scb_settlements: list[dict] = []

    random.shuffle(ledger)
    used: set[int] = set()

    def take(count: int, *, processor: str | None = None) -> list[dict]:
        picked = []
        for tx in ledger:
            if len(picked) >= count:
                break
            if tx["_idx"] in used:
                continue
            if processor and tx["_processor_key"] != processor:
                continue
            used.add(tx["_idx"])
            picked.append(tx)
        return picked

    missing_txs = take(MISSING_SETTLEMENTS)
    for tx in missing_txs:
        tx["status"] = random.choice(["settled", "captured"])

    for tx in take(NORMAL_MATCHES):
        tx["status"] = "settled"
        append_settlement(tx, mpesa_settlements, ovo_settlements, scb_settlements)

    for tx in take(AMOUNT_MISMATCHES):
        tx["status"] = "settled"
        delta = round(tx["amount"] * random.uniform(-0.02, -0.005), 2)
        append_settlement(
            tx,
            mpesa_settlements,
            ovo_settlements,
            scb_settlements,
            {"settlement_amount": tx["amount"] + delta},
        )

    for tx in take(STATUS_CONFLICTS):
        tx["status"] = "refunded"
        append_settlement(
            tx,
            mpesa_settlements,
            ovo_settlements,
            scb_settlements,
            {"settlement_status": "settled"},
        )

    # OVO uses processor_reference; SCB uses fuzzy with partial-leg support
    split_txs = take(SPLIT_SETTLEMENTS // 2, processor="ovo_indonesia")
    split_txs += take(SPLIT_SETTLEMENTS - len(split_txs), processor="scb_thailand")
    for tx in split_txs:
        tx["status"] = "settled"
        half = round(tx["amount"] / 2, 2)
        remainder = round(tx["amount"] - half, 2)
        append_settlement(
            tx,
            mpesa_settlements,
            ovo_settlements,
            scb_settlements,
            {"settlement_amount": half, "settlement_status": "partial_refund"},
        )
        append_settlement(
            tx,
            mpesa_settlements,
            ovo_settlements,
            scb_settlements,
            {"settlement_amount": remainder, "settlement_status": "settled"},
        )

    for _ in range(ORPHANED_SETTLEMENTS):
        processor = random.choice(list(CURRENCIES.keys()))
        currency, _ = CURRENCIES[processor]
        fake = {
            "transaction_id": gen_id("TXN"),
            "merchant_id": random.choice(MERCHANTS),
            "order_id": gen_id("ORD"),
            "amount": random_amount(currency),
            "currency": currency,
            "created_at": (START_DATE + timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%S"),
            "processor_reference": gen_id("PRC"),
            "_processor_key": processor,
            "_idx": -1,
        }
        append_settlement(fake, mpesa_settlements, ovo_settlements, scb_settlements)

    # Filler rows: failed/pending — should NOT appear as missing settlements
    for tx in ledger:
        if tx["_idx"] not in used:
            tx["status"] = random.choice(["failed", "pending", "voided"])

    clean_ledger = [{k: v for k, v in tx.items() if not k.startswith("_")} for tx in ledger]

    with (DATA_DIR / "ledger.json").open("w") as f:
        json.dump({"transactions": clean_ledger}, f, indent=2)
    with (SETTLEMENTS_DIR / "mpesa_tanzania.json").open("w") as f:
        json.dump(mpesa_settlements, f, indent=2)
    with (SETTLEMENTS_DIR / "ovo_indonesia.json").open("w") as f:
        json.dump(ovo_settlements, f, indent=2)
    with (SETTLEMENTS_DIR / "scb_thailand.json").open("w") as f:
        json.dump(scb_settlements, f, indent=2)

    total_stl = len(mpesa_settlements) + len(ovo_settlements) + len(scb_settlements)
    print(f"Generated {len(clean_ledger)} ledger transactions")
    print(f"Generated {total_stl} settlement records:")
    print(f"  M-Pesa Tanzania: {len(mpesa_settlements)}")
    print(f"  OVO Indonesia:   {len(ovo_settlements)}")
    print(f"  SCB Thailand:    {len(scb_settlements)}")
    print(f"  Normal matches:       {NORMAL_MATCHES}")
    print(f"  Amount mismatches:    {AMOUNT_MISMATCHES}")
    print(f"  Status conflicts:     {STATUS_CONFLICTS}")
    print(f"  Split settlements:    {SPLIT_SETTLEMENTS} ({SPLIT_SETTLEMENTS * 2} rows)")
    print(f"  Orphaned:             {ORPHANED_SETTLEMENTS}")
    print(f"  Missing settlements:  {len(missing_txs)}")
    print(f"  Filler (failed/etc):  {FILLER_ROWS}")


if __name__ == "__main__":
    main()
