from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TransactionStatus(str, Enum):
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    SETTLED = "settled"
    REFUNDED = "refunded"
    VOIDED = "voided"
    FAILED = "failed"
    PENDING = "pending"


class SettlementStatus(str, Enum):
    SETTLED = "settled"
    REFUNDED = "refunded"
    PARTIAL_REFUND = "partial_refund"
    FAILED = "failed"
    PENDING = "pending"


class AcquirerType(str, Enum):
    MPESA_TZ = "mpesa_tanzania"  # Acquirer A - uses transaction_id
    OVO_ID = "ovo_indonesia"  # Acquirer B - processor_reference + order_id
    SCB_TH = "scb_thailand"  # Acquirer C - order_id + amount only


class MatchStrategy(str, Enum):
    DIRECT_ID = "direct_id"
    PROCESSOR_REFERENCE = "processor_reference"
    FUZZY = "fuzzy"


class DiscrepancyCategory(str, Enum):
    MATCHED = "matched"
    MISSING_SETTLEMENT = "missing_settlement"
    ORPHANED_SETTLEMENT = "orphaned_settlement"
    AMOUNT_MISMATCH = "amount_mismatch"
    STATUS_CONFLICT = "status_conflict"
    DUPLICATE_SETTLEMENT = "duplicate_settlement"
    REVIEW_TOLERANCE = "review_tolerance"


class RiskLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class InternalTransaction(BaseModel):
    transaction_id: str
    merchant_id: str
    order_id: str
    amount: float
    currency: str
    status: TransactionStatus
    created_at: datetime
    processor_name: str
    processor_reference: str | None = None


class NormalizedSettlement(BaseModel):
    settlement_id: str
    acquirer: AcquirerType
    transaction_id: str | None = None
    processor_reference: str | None = None
    order_id: str | None = None
    merchant_id: str
    settlement_amount: float
    currency: str
    settlement_date: datetime
    fee_charged: float
    settlement_status: SettlementStatus
    raw_data: dict[str, Any] = Field(default_factory=dict)


class MatchResult(BaseModel):
    internal_transaction_id: str
    settlement_ids: list[str]
    strategy: MatchStrategy
    confidence: float = Field(ge=0.0, le=1.0)
    amount_delta: float = 0.0
    within_tolerance: bool = True
    notes: str | None = None


class Discrepancy(BaseModel):
    category: DiscrepancyCategory
    risk_level: RiskLevel
    suspected_reason: str
    internal_transaction: InternalTransaction | None = None
    settlements: list[NormalizedSettlement] = Field(default_factory=list)
    match_result: MatchResult | None = None
    monetary_impact: float = 0.0


class ReconciliationSummary(BaseModel):
    total_internal: int
    total_settlements: int
    matched_count: int
    missing_settlement_count: int
    orphaned_settlement_count: int
    amount_mismatch_count: int
    status_conflict_count: int
    duplicate_settlement_count: int
    review_tolerance_count: int
    total_unreconciled_value: float
    mismatch_rate_by_acquirer: dict[str, float] = Field(default_factory=dict)
    discrepancy_breakdown_pct: dict[str, float] = Field(default_factory=dict)


class ReconciliationReport(BaseModel):
    run_id: str
    generated_at: datetime
    summary: ReconciliationSummary
    matches: list[MatchResult]
    discrepancies: list[Discrepancy]
