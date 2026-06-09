from typing import Any

from app.ingestion.normalizers import ingest_ledger, ingest_settlements
from app.models.schemas import AcquirerType, ReconciliationReport
from app.reconciliation.service import ReconciliationService


def run_reconciliation(
    ledger_records: list[dict[str, Any]],
    settlement_batches: list[tuple[AcquirerType, list[dict[str, Any]]]],
) -> ReconciliationReport:
    """Run reconciliation from in-memory records (no filesystem access)."""
    transactions = ingest_ledger(ledger_records)
    settlements = []
    for acquirer, records in settlement_batches:
        settlements.extend(ingest_settlements(acquirer, records))
    return ReconciliationService(transactions, settlements).run()
