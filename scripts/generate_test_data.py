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
    "stripe_kenya": ("KES", "Stripe Kenya"),
}
PROCESSORS = [
    ("mpesa_tanzania", 0.30),
    ("ovo_indonesia", 0.35),
    ("scb_thailand", 0.25),
    ("stripe_kenya", 0.10),
]

START_DATE = datetime(2025, 11, 1)
DAYS = 30
LEDGER_SIZE = 580  # sized to fit all scenario budgets (~90% acquirer-backed)

# Scenario budgets
NORMAL_MATCHES = 400
AMOUNT_MISMATCHES = 10
STATUS_CONFLICTS = 10
SPLIT_SETTLEMENTS = 20  # 20 internal txs → 40 settlement rows
ORPHANED_SETTLEMENTS = 10
MISSING_SETTLEMENTS = 55


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
    if currency in ("KES", "TZS", "THB"):
        return round(random.uniform(500, 80_000), 2)
    return round(random.uniform(5, 2000), 2)


def random_status() -> str:
    r = random.random()
    if r < 0.80:
        return "settled"
    if r < 0.90:
        return "refunded"
    if r < 0.95:
        return "failed"
    return random.choice(["captured", "pending"])


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
        tx_id = gen_id("TXN")
        order_id = gen_id("ORD")
        proc_ref = gen_id("PRC") if processor != "mpesa_tanzania" else None

        transactions.append(
            {
                "transaction_id": tx_id,
                "merchant_id": random.choice(MERCHANTS),
                "order_id": order_id,
                "amount": random_amount(currency),
                "currency": currency,
                "status": random_status(),
                "created_at": created.strftime("%Y-%m-%dT%H:%M:%S"),
                "processor_name": processor_name,
                "processor_reference": proc_ref,
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

    acquirer_txs = [
        tx for tx in ledger
        if tx["_processor_key"] in ("mpesa_tanzania", "ovo_indonesia", "scb_thailand")
    ]
    random.shuffle(acquirer_txs)

    used: set[int] = set()

    def take(count: int, *, processor: str | None = None) -> list[dict]:
        picked = []
        for tx in acquirer_txs:
            if len(picked) >= count:
                break
            if tx["_idx"] in used:
                continue
            if processor and tx["_processor_key"] != processor:
                continue
            used.add(tx["_idx"])
            picked.append(tx)
        return picked

    # 1. Reserve missing settlements (no settlement rows created)
    missing_txs = take(MISSING_SETTLEMENTS)
    for tx in missing_txs:
        tx["status"] = random.choice(["settled", "captured"])

    # 2. Normal matches
    normal_txs = take(NORMAL_MATCHES)
    for tx in normal_txs:
        if tx["status"] not in ("settled", "captured"):
            tx["status"] = "settled"
        append_settlement(tx, mpesa_settlements, ovo_settlements, scb_settlements)

    # 3. Amount mismatches
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

    # 4. Status conflicts
    for tx in take(STATUS_CONFLICTS):
        tx["status"] = "refunded"
        append_settlement(
            tx,
            mpesa_settlements,
            ovo_settlements,
            scb_settlements,
            {"settlement_status": "settled"},
        )

    # 5. Split / partial refund (one internal → two settlements)
    split_txs = take(SPLIT_SETTLEMENTS, processor="ovo_indonesia")
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

    # 6. Orphaned settlements (no internal record)
    for _ in range(ORPHANED_SETTLEMENTS):
        processor = random.choice(["mpesa_tanzania", "ovo_indonesia", "scb_thailand"])
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
    print(f"  Normal matches:     {NORMAL_MATCHES}")
    print(f"  Amount mismatches:  {AMOUNT_MISMATCHES}")
    print(f"  Status conflicts:   {STATUS_CONFLICTS}")
    print(f"  Split settlements:    {SPLIT_SETTLEMENTS} ({SPLIT_SETTLEMENTS * 2} rows)")
    print(f"  Orphaned:             {ORPHANED_SETTLEMENTS}")
    print(f"  Missing settlements:  {len(missing_txs)}")


if __name__ == "__main__":
    main()
