import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models.schemas import (
    AcquirerType,
    InternalTransaction,
    NormalizedSettlement,
    SettlementStatus,
    TransactionStatus,
)


def _parse_datetime(value: str) -> datetime:
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(value.replace("Z", ""), fmt.replace("Z", ""))
        except ValueError:
            continue
    raise ValueError(f"Unable to parse datetime: {value}")


def _parse_settlement_status(value: str) -> SettlementStatus:
    normalized = value.lower().strip().replace(" ", "_")
    mapping = {
        "settled": SettlementStatus.SETTLED,
        "refunded": SettlementStatus.REFUNDED,
        "partial_refund": SettlementStatus.PARTIAL_REFUND,
        "partial": SettlementStatus.PARTIAL_REFUND,
        "failed": SettlementStatus.FAILED,
        "pending": SettlementStatus.PENDING,
    }
    return mapping.get(normalized, SettlementStatus.SETTLED)


def ingest_ledger(data: list[dict[str, Any]] | dict[str, Any]) -> list[InternalTransaction]:
    records = data if isinstance(data, list) else data.get("transactions", [])
    transactions: list[InternalTransaction] = []
    for row in records:
        transactions.append(
            InternalTransaction(
                transaction_id=row["transaction_id"],
                merchant_id=row["merchant_id"],
                order_id=row["order_id"],
                amount=float(row["amount"]),
                currency=row["currency"],
                status=TransactionStatus(row["status"].lower()),
                created_at=_parse_datetime(row["created_at"]),
                processor_name=row["processor_name"],
                processor_reference=row.get("processor_reference"),
            )
        )
    return transactions


def _normalize_mpesa(row: dict[str, Any], idx: int) -> NormalizedSettlement:
    return NormalizedSettlement(
        settlement_id=row.get("settlement_id", f"mpesa-{idx}"),
        acquirer=AcquirerType.MPESA_TZ,
        transaction_id=row["transaction_id"],
        merchant_id=row["merchant_id"],
        settlement_amount=float(row["settlement_amount"]),
        currency=row.get("currency", "TZS"),
        settlement_date=_parse_datetime(row["settlement_date"]),
        fee_charged=float(row.get("fee_charged", 0)),
        settlement_status=_parse_settlement_status(row.get("settlement_status", "settled")),
        raw_data=row,
    )


def _normalize_ovo(row: dict[str, Any], idx: int) -> NormalizedSettlement:
    return NormalizedSettlement(
        settlement_id=row.get("settlement_id", f"ovo-{idx}"),
        acquirer=AcquirerType.OVO_ID,
        processor_reference=row["processor_reference"],
        order_id=row.get("order_id"),
        merchant_id=row["merchant_id"],
        settlement_amount=float(row["settlement_amount"]),
        currency=row.get("currency", "IDR"),
        settlement_date=_parse_datetime(row["settlement_date"]),
        fee_charged=float(row.get("fee_charged", 0)),
        settlement_status=_parse_settlement_status(row.get("settlement_status", "settled")),
        raw_data=row,
    )


def _normalize_scb(row: dict[str, Any], idx: int) -> NormalizedSettlement:
    return NormalizedSettlement(
        settlement_id=row.get("settlement_id", f"scb-{idx}"),
        acquirer=AcquirerType.SCB_TH,
        order_id=row["order_id"],
        merchant_id=row["merchant_id"],
        settlement_amount=float(row["settlement_amount"]),
        currency=row.get("currency", "THB"),
        settlement_date=_parse_datetime(row["settlement_date"]),
        fee_charged=float(row.get("fee_charged", 0)),
        settlement_status=_parse_settlement_status(row.get("settlement_status", "settled")),
        raw_data=row,
    )


NORMALIZERS = {
    AcquirerType.MPESA_TZ: _normalize_mpesa,
    AcquirerType.OVO_ID: _normalize_ovo,
    AcquirerType.SCB_TH: _normalize_scb,
}


def ingest_settlements(
    acquirer: AcquirerType,
    records: list[dict[str, Any]],
) -> list[NormalizedSettlement]:
    normalizer = NORMALIZERS[acquirer]
    return [normalizer(row, idx) for idx, row in enumerate(records)]


def ingest_settlement_file(path: Path, acquirer: AcquirerType) -> list[NormalizedSettlement]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        with path.open() as f:
            data = json.load(f)
        records = data if isinstance(data, list) else data.get("settlements", [])
    elif suffix == ".csv":
        with path.open(newline="") as f:
            records = list(csv.DictReader(f))
    else:
        raise ValueError(f"Unsupported file format: {suffix}")
    return ingest_settlements(acquirer, records)
