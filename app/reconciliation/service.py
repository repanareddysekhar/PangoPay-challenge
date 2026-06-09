import uuid
from collections import defaultdict
from datetime import UTC, datetime

from app.matching.engine import MatchingEngine, _amount_within_tolerance
from app.models.schemas import (
    Discrepancy,
    DiscrepancyCategory,
    InternalTransaction,
    MatchResult,
    NormalizedSettlement,
    ReconciliationReport,
    ReconciliationSummary,
    RiskLevel,
    SettlementStatus,
    TransactionStatus,
)

HIGH_VALUE_THRESHOLD = 100.0
LOW_VALUE_THRESHOLD = 20.0


def _assess_risk(category: DiscrepancyCategory, amount: float) -> RiskLevel:
    if category == DiscrepancyCategory.AMOUNT_MISMATCH:
        return RiskLevel.HIGH if abs(amount) > HIGH_VALUE_THRESHOLD else RiskLevel.MEDIUM
    if category == DiscrepancyCategory.ORPHANED_SETTLEMENT:
        return RiskLevel.HIGH if amount > HIGH_VALUE_THRESHOLD else RiskLevel.MEDIUM
    if category == DiscrepancyCategory.MISSING_SETTLEMENT:
        return RiskLevel.LOW if amount < LOW_VALUE_THRESHOLD else RiskLevel.MEDIUM
    if category == DiscrepancyCategory.STATUS_CONFLICT:
        return RiskLevel.HIGH
    if category == DiscrepancyCategory.DUPLICATE_SETTLEMENT:
        return RiskLevel.HIGH
    return RiskLevel.LOW


def _status_conflict(
    tx: InternalTransaction, settlements: list[NormalizedSettlement]
) -> bool:
    if tx.status == TransactionStatus.REFUNDED:
        return any(s.settlement_status == SettlementStatus.SETTLED for s in settlements)
    if tx.status == TransactionStatus.FAILED:
        return any(
            s.settlement_status in (SettlementStatus.SETTLED, SettlementStatus.PARTIAL_REFUND)
            for s in settlements
        )
    if tx.status == TransactionStatus.SETTLED:
        return any(s.settlement_status == SettlementStatus.REFUNDED for s in settlements)
    return False


class ReconciliationService:
    def __init__(
        self,
        transactions: list[InternalTransaction],
        settlements: list[NormalizedSettlement],
    ):
        self.transactions = transactions
        self.settlements = settlements
        self._settlement_map = {s.settlement_id: s for s in settlements}
        self._tx_map = {t.transaction_id: t for t in transactions}

    def run(self) -> ReconciliationReport:
        engine = MatchingEngine(self.transactions, self.settlements)
        matches, matched_tx_ids, matched_settlement_ids = engine.run()

        discrepancies: list[Discrepancy] = []
        confirmed_matches: list[MatchResult] = []

        for match in matches:
            tx = self._tx_map[match.internal_transaction_id]
            linked = [self._settlement_map[sid] for sid in match.settlement_ids]
            total_settled = sum(s.settlement_amount for s in linked)

            if _status_conflict(tx, linked):
                discrepancies.append(
                    Discrepancy(
                        category=DiscrepancyCategory.STATUS_CONFLICT,
                        risk_level=_assess_risk(
                            DiscrepancyCategory.STATUS_CONFLICT, total_settled
                        ),
                        suspected_reason=(
                            f"Internal status '{tx.status.value}' conflicts with "
                            f"settlement status '{linked[0].settlement_status.value}'"
                        ),
                        internal_transaction=tx,
                        settlements=linked,
                        match_result=match,
                        monetary_impact=abs(total_settled - tx.amount),
                    )
                )
            elif not match.within_tolerance:
                discrepancies.append(
                    Discrepancy(
                        category=DiscrepancyCategory.AMOUNT_MISMATCH,
                        risk_level=_assess_risk(
                            DiscrepancyCategory.AMOUNT_MISMATCH, match.amount_delta
                        ),
                        suspected_reason=(
                            f"Amount delta {match.amount_delta:.2f} exceeds 0.5% tolerance "
                            f"(internal: {tx.amount}, settled: {total_settled:.2f})"
                        ),
                        internal_transaction=tx,
                        settlements=linked,
                        match_result=match,
                        monetary_impact=abs(match.amount_delta),
                    )
                )
            elif match.notes and "tolerance" in (match.notes or "").lower():
                discrepancies.append(
                    Discrepancy(
                        category=DiscrepancyCategory.REVIEW_TOLERANCE,
                        risk_level=RiskLevel.LOW,
                        suspected_reason=match.notes,
                        internal_transaction=tx,
                        settlements=linked,
                        match_result=match,
                        monetary_impact=abs(match.amount_delta),
                    )
                )
                confirmed_matches.append(match)
            else:
                confirmed_matches.append(match)
                discrepancies.append(
                    Discrepancy(
                        category=DiscrepancyCategory.MATCHED,
                        risk_level=RiskLevel.LOW,
                        suspected_reason="Records aligned",
                        internal_transaction=tx,
                        settlements=linked,
                        match_result=match,
                        monetary_impact=0.0,
                    )
                )

        # Missing settlements: internal captured/settled with no match
        for tx in self.transactions:
            if tx.transaction_id in matched_tx_ids:
                continue
            if tx.status in (
                TransactionStatus.CAPTURED,
                TransactionStatus.SETTLED,
                TransactionStatus.REFUNDED,
            ):
                discrepancies.append(
                    Discrepancy(
                        category=DiscrepancyCategory.MISSING_SETTLEMENT,
                        risk_level=_assess_risk(
                            DiscrepancyCategory.MISSING_SETTLEMENT, tx.amount
                        ),
                        suspected_reason=(
                            "Internal record exists but no settlement report entry found"
                        ),
                        internal_transaction=tx,
                        monetary_impact=tx.amount,
                    )
                )

        # Orphaned settlements: no internal match
        for settlement in self.settlements:
            if settlement.settlement_id not in matched_settlement_ids:
                discrepancies.append(
                    Discrepancy(
                        category=DiscrepancyCategory.ORPHANED_SETTLEMENT,
                        risk_level=_assess_risk(
                            DiscrepancyCategory.ORPHANED_SETTLEMENT,
                            settlement.settlement_amount,
                        ),
                        suspected_reason=(
                            "Settlement report entry with no matching internal record"
                        ),
                        settlements=[settlement],
                        monetary_impact=settlement.settlement_amount,
                    )
                )

        # Stretch A: Duplicate settlement detection
        discrepancies.extend(self._detect_duplicate_settlements())

        summary = self._build_summary(confirmed_matches, discrepancies, matched_tx_ids)
        return ReconciliationReport(
            run_id=str(uuid.uuid4()),
            generated_at=datetime.now(UTC),
            summary=summary,
            matches=confirmed_matches,
            discrepancies=discrepancies,
        )

    def _detect_duplicate_settlements(self) -> list[Discrepancy]:
        """Flag settlements with same merchant, amount, within 24h."""
        duplicates: list[Discrepancy] = []
        by_key: dict[tuple[str, float], list[NormalizedSettlement]] = defaultdict(list)
        for s in self.settlements:
            rounded = round(s.settlement_amount, 2)
            by_key[(s.merchant_id, rounded)].append(s)

        for (_merchant, _amount), group in by_key.items():
            if len(group) < 2:
                continue
            group.sort(key=lambda x: x.settlement_date)
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    a, b = group[i], group[j]
                    if abs((a.settlement_date - b.settlement_date).total_seconds()) <= 86400:
                        if a.order_id and b.order_id and a.order_id == b.order_id:
                            continue  # likely split settlement
                        duplicates.append(
                            Discrepancy(
                                category=DiscrepancyCategory.DUPLICATE_SETTLEMENT,
                                risk_level=RiskLevel.HIGH,
                                suspected_reason=(
                                    f"Potential duplicate: {a.settlement_id} and "
                                    f"{b.settlement_id} same merchant/amount within 24h"
                                ),
                                settlements=[a, b],
                                monetary_impact=a.settlement_amount,
                            )
                        )
        return duplicates

    def _build_summary(
        self,
        matches: list[MatchResult],
        discrepancies: list[Discrepancy],
        matched_tx_ids: set[str],
    ) -> ReconciliationSummary:
        cats = defaultdict(int)
        unreconciled_value = 0.0
        for d in discrepancies:
            if d.category != DiscrepancyCategory.MATCHED:
                cats[d.category.value] += 1
                unreconciled_value += d.monetary_impact

        total_disc = sum(
            1 for d in discrepancies if d.category != DiscrepancyCategory.MATCHED
        )
        breakdown_pct = {
            k: (v / total_disc * 100 if total_disc else 0.0)
            for k, v in cats.items()
        }

        # Mismatch rate by acquirer (stretch B)
        acquirer_totals: dict[str, int] = defaultdict(int)
        acquirer_mismatches: dict[str, int] = defaultdict(int)
        mismatched_settlement_ids = set()
        for d in discrepancies:
            if d.category in (
                DiscrepancyCategory.ORPHANED_SETTLEMENT,
                DiscrepancyCategory.AMOUNT_MISMATCH,
                DiscrepancyCategory.STATUS_CONFLICT,
            ):
                for s in d.settlements:
                    mismatched_settlement_ids.add(s.settlement_id)

        for s in self.settlements:
            acquirer_totals[s.acquirer.value] += 1
            if s.settlement_id in mismatched_settlement_ids:
                acquirer_mismatches[s.acquirer.value] += 1

        mismatch_rate = {
            acq: (acquirer_mismatches[acq] / acquirer_totals[acq] * 100)
            for acq in acquirer_totals
        }

        return ReconciliationSummary(
            total_internal=len(self.transactions),
            total_settlements=len(self.settlements),
            matched_count=len(matched_tx_ids),
            missing_settlement_count=cats.get("missing_settlement", 0),
            orphaned_settlement_count=cats.get("orphaned_settlement", 0),
            amount_mismatch_count=cats.get("amount_mismatch", 0),
            status_conflict_count=cats.get("status_conflict", 0),
            duplicate_settlement_count=cats.get("duplicate_settlement", 0),
            review_tolerance_count=cats.get("review_tolerance", 0),
            total_unreconciled_value=round(unreconciled_value, 2),
            mismatch_rate_by_acquirer={
                k: round(v, 2) for k, v in mismatch_rate.items()
            },
            discrepancy_breakdown_pct={
                k: round(v, 2) for k, v in breakdown_pct.items()
            },
        )
