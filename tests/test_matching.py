from datetime import datetime

import pytest

from app.ingestion.normalizers import ingest_ledger, ingest_settlements
from app.matching.engine import MatchingEngine
from app.models.schemas import AcquirerType, MatchStrategy
from app.reconciliation.service import ReconciliationService


def _tx(**kwargs):
    defaults = {
        "transaction_id": "TXN-001",
        "merchant_id": "MCH-1001",
        "order_id": "ORD-001",
        "amount": 100.0,
        "currency": "USD",
        "status": "settled",
        "created_at": "2025-11-15T10:00:00",
        "processor_name": "M-Pesa Tanzania",
        "processor_reference": "PRC-001",
    }
    defaults.update(kwargs)
    return defaults


def _stl_mpesa(**kwargs):
    defaults = {
        "settlement_id": "STL-001",
        "transaction_id": "TXN-001",
        "merchant_id": "MCH-1001",
        "settlement_amount": 100.0,
        "currency": "TZS",
        "settlement_date": "2025-11-16T10:00:00",
        "fee_charged": 2.5,
        "settlement_status": "settled",
    }
    defaults.update(kwargs)
    return defaults


def test_direct_id_match():
    txs = ingest_ledger([_tx()])
    stls = ingest_settlements(AcquirerType.MPESA_TZ, [_stl_mpesa()])
    engine = MatchingEngine(txs, stls)
    matches, matched_tx, matched_stl = engine.run()
    assert len(matches) == 1
    assert matches[0].strategy == MatchStrategy.DIRECT_ID
    assert "TXN-001" in matched_tx


def test_processor_reference_match():
    txs = ingest_ledger([_tx(processor_reference="PRC-REF-99")])
    stls = ingest_settlements(
        AcquirerType.OVO_ID,
        [
            {
                "settlement_id": "STL-OVO-1",
                "processor_reference": "PRC-REF-99",
                "order_id": "ORD-001",
                "merchant_id": "MCH-1001",
                "settlement_amount": 100.0,
                "currency": "IDR",
                "settlement_date": "2025-11-16T10:00:00",
                "fee_charged": 2.5,
                "settlement_status": "settled",
            }
        ],
    )
    engine = MatchingEngine(txs, stls)
    matches, _, _ = engine.run()
    assert len(matches) == 1
    assert matches[0].strategy == MatchStrategy.PROCESSOR_REFERENCE


def test_fuzzy_match():
    txs = ingest_ledger([_tx(transaction_id="TXN-FUZZY")])
    stls = ingest_settlements(
        AcquirerType.SCB_TH,
        [
            {
                "settlement_id": "STL-SCB-1",
                "order_id": "ORD-001",
                "merchant_id": "MCH-1001",
                "settlement_amount": 100.0,
                "currency": "THB",
                "settlement_date": "2025-11-15T14:00:00",
                "fee_charged": 2.5,
                "settlement_status": "settled",
            }
        ],
    )
    engine = MatchingEngine(txs, stls)
    matches, _, _ = engine.run()
    assert len(matches) == 1
    assert matches[0].strategy == MatchStrategy.FUZZY


def test_amount_mismatch_detection():
    txs = ingest_ledger([_tx(amount=100.0)])
    stls = ingest_settlements(
        AcquirerType.MPESA_TZ,
        [_stl_mpesa(settlement_amount=95.0)],
    )
    service = ReconciliationService(txs, stls)
    report = service.run()
    cats = [d.category.value for d in report.discrepancies]
    assert "amount_mismatch" in cats


def test_missing_settlement():
    txs = ingest_ledger([_tx(status="settled")])
    service = ReconciliationService(txs, [])
    report = service.run()
    cats = [d.category.value for d in report.discrepancies]
    assert "missing_settlement" in cats


def test_orphaned_settlement():
    txs = ingest_ledger([_tx(transaction_id="TXN-OTHER")])
    stls = ingest_settlements(
        AcquirerType.MPESA_TZ,
        [_stl_mpesa(transaction_id="TXN-UNKNOWN")],
    )
    service = ReconciliationService(txs, stls)
    report = service.run()
    cats = [d.category.value for d in report.discrepancies]
    assert "orphaned_settlement" in cats


def test_status_conflict():
    txs = ingest_ledger([_tx(status="refunded")])
    stls = ingest_settlements(
        AcquirerType.MPESA_TZ,
        [_stl_mpesa(settlement_status="settled")],
    )
    service = ReconciliationService(txs, stls)
    report = service.run()
    cats = [d.category.value for d in report.discrepancies]
    assert "status_conflict" in cats
