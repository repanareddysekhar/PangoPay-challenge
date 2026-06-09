#!/usr/bin/env python3
"""CLI for running reconciliation locally with bundled or custom files."""

import argparse
import json
import sys
from pathlib import Path

from app.ingestion.normalizers import ingest_settlement_file
from app.models.schemas import AcquirerType
from app.services.reconcile import run_reconciliation

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def load_demo_batches() -> tuple[list, list]:
    with (DATA_DIR / "ledger.json").open() as f:
        ledger_raw = json.load(f)
    records = ledger_raw["transactions"]

    batches: list[tuple[AcquirerType, list]] = []
    for acquirer, filename in [
        (AcquirerType.MPESA_TZ, "mpesa_tanzania.json"),
        (AcquirerType.OVO_ID, "ovo_indonesia.json"),
        (AcquirerType.SCB_TH, "scb_thailand.json"),
    ]:
        path = DATA_DIR / "settlements" / filename
        if path.exists():
            normalized = ingest_settlement_file(path, acquirer)
            batches.append((acquirer, [s.raw_data for s in normalized]))
    return records, batches


def print_summary(report) -> None:
    s = report.summary
    print("\n" + "=" * 60)
    print("PANGOPAY RECONCILIATION REPORT")
    print("=" * 60)
    print(f"Run ID:     {report.run_id}")
    print(f"Generated:  {report.generated_at.isoformat()}")
    print("-" * 60)
    print(f"Internal transactions:  {s.total_internal}")
    print(f"Settlement records:     {s.total_settlements}")
    print(f"Matched:                {s.matched_count}")
    print(f"Missing settlements:    {s.missing_settlement_count}")
    print(f"Orphaned settlements:   {s.orphaned_settlement_count}")
    print(f"Amount mismatches:      {s.amount_mismatch_count}")
    print(f"Status conflicts:       {s.status_conflict_count}")
    print(f"Duplicate settlements:  {s.duplicate_settlement_count}")
    print(f"Unreconciled value:     ${s.total_unreconciled_value:,.2f}")
    print("-" * 60)
    print("Mismatch rate by acquirer:")
    for acq, rate in s.mismatch_rate_by_acquirer.items():
        print(f"  {acq}: {rate:.1f}%")
    print("-" * 60)
    print("Discrepancy breakdown:")
    for cat, pct in s.discrepancy_breakdown_pct.items():
        print(f"  {cat}: {pct:.1f}%")

    non_matched = [d for d in report.discrepancies if d.category.value != "matched"][:5]
    if non_matched:
        print("-" * 60)
        print("Sample discrepancies (first 5):")
        for d in non_matched:
            tx_id = (
                d.internal_transaction.transaction_id if d.internal_transaction else "N/A"
            )
            print(f"  [{d.risk_level.value.upper()}] {d.category.value}: {tx_id}")
            print(f"    Reason: {d.suspected_reason}")


def main() -> None:
    parser = argparse.ArgumentParser(description="PangoPay Reconciliation CLI")
    parser.add_argument("--demo", action="store_true", help="Use bundled test data")
    parser.add_argument("--output", "-o", type=Path, help="Write full JSON report to file")
    parser.add_argument("--ledger", type=Path, help="Path to ledger JSON file")
    parser.add_argument(
        "--settlements",
        nargs="+",
        metavar="ACQUIRER:PATH",
        help="Settlement files as acquirer:path",
    )
    args = parser.parse_args()

    if args.demo:
        records, batches = load_demo_batches()
    elif args.ledger and args.settlements:
        with args.ledger.open() as f:
            raw = json.load(f)
        records = raw if isinstance(raw, list) else raw["transactions"]
        batches = []
        for spec in args.settlements:
            acquirer_str, path_str = spec.split(":", 1)
            acquirer = AcquirerType(acquirer_str)
            normalized = ingest_settlement_file(Path(path_str), acquirer)
            batches.append((acquirer, [s.raw_data for s in normalized]))
    else:
        parser.print_help()
        print("\nQuick start: python -m app.cli --demo")
        sys.exit(1)

    report = run_reconciliation(records, batches)
    print_summary(report)

    if args.output:
        args.output.write_text(report.model_dump_json(indent=2))
        print(f"\nFull report written to {args.output}")


if __name__ == "__main__":
    main()
