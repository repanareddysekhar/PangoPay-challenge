from app.ingestion.normalizers import ingest_ledger, ingest_settlements
from app.matching.engine import MatchingEngine
from app.models.schemas import AcquirerType, DiscrepancyCategory
from app.reconciliation.service import ReconciliationService


def test_split_settlement_within_tolerance():
    """Two partial settlements summing to the internal amount should not flag mismatch."""
    txs = ingest_ledger(
        [
            {
                "transaction_id": "TXN-SPLIT",
                "merchant_id": "MCH-1001",
                "order_id": "ORD-SPLIT",
                "amount": 1000.0,
                "currency": "IDR",
                "status": "settled",
                "created_at": "2025-11-15T10:00:00",
                "processor_name": "OVO Indonesia",
                "processor_reference": "PRC-SPLIT",
            }
        ]
    )
    stls = ingest_settlements(
        AcquirerType.OVO_ID,
        [
            {
                "settlement_id": "STL-1",
                "processor_reference": "PRC-SPLIT",
                "order_id": "ORD-SPLIT",
                "merchant_id": "MCH-1001",
                "settlement_amount": 500.0,
                "currency": "IDR",
                "settlement_date": "2025-11-16T10:00:00",
                "fee_charged": 12.5,
                "settlement_status": "partial_refund",
            },
            {
                "settlement_id": "STL-2",
                "processor_reference": "PRC-SPLIT",
                "order_id": "ORD-SPLIT",
                "merchant_id": "MCH-1001",
                "settlement_amount": 500.0,
                "currency": "IDR",
                "settlement_date": "2025-11-16T11:00:00",
                "fee_charged": 12.5,
                "settlement_status": "settled",
            },
        ],
    )
    matches, _, _ = MatchingEngine(txs, stls).run()
    assert len(matches) == 1
    assert len(matches[0].settlement_ids) == 2
    assert matches[0].within_tolerance is True

    report = ReconciliationService(txs, stls).run()
    amount_mismatches = [
        d for d in report.discrepancies if d.category == DiscrepancyCategory.AMOUNT_MISMATCH
    ]
    assert len(amount_mismatches) == 0
