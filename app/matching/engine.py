from datetime import timedelta

from app.models.schemas import (
    InternalTransaction,
    MatchResult,
    MatchStrategy,
    NormalizedSettlement,
)

AMOUNT_TOLERANCE_PCT = 0.005  # 0.5%
FUZZY_DATE_WINDOW_HOURS = 48


def _amount_within_tolerance(internal_amount: float, settlement_amount: float) -> bool:
    if internal_amount == 0:
        return settlement_amount == 0
    delta_pct = abs(internal_amount - settlement_amount) / abs(internal_amount)
    return delta_pct <= AMOUNT_TOLERANCE_PCT


def _amount_delta(internal_amount: float, settlement_amount: float) -> float:
    return settlement_amount - internal_amount


class MatchingEngine:
    """Multi-strategy matching: direct ID → processor reference → fuzzy composite."""

    def __init__(
        self,
        transactions: list[InternalTransaction],
        settlements: list[NormalizedSettlement],
    ):
        self.transactions = transactions
        self.settlements = settlements
        self._settlement_map = {s.settlement_id: s for s in settlements}
        self._by_tx_id: dict[str, InternalTransaction] = {
            t.transaction_id: t for t in transactions
        }
        self._by_processor_ref: dict[str, InternalTransaction] = {
            t.processor_reference: t
            for t in transactions
            if t.processor_reference
        }
        self._by_order_merchant: dict[tuple[str, str], list[InternalTransaction]] = {}
        for t in transactions:
            key = (t.order_id, t.merchant_id)
            self._by_order_merchant.setdefault(key, []).append(t)

    def run(self) -> tuple[list[MatchResult], set[str], set[str]]:
        """
        Returns (matches, matched_tx_ids, matched_settlement_ids).
        Supports one-to-many: one internal tx can match multiple settlements.
        """
        matches: list[MatchResult] = []
        matched_tx_ids: set[str] = set()
        matched_settlement_ids: set[str] = set()

        # Pass 1: Direct transaction_id match (Acquirer A)
        for settlement in self.settlements:
            if settlement.settlement_id in matched_settlement_ids:
                continue
            if not settlement.transaction_id:
                continue
            tx = self._by_tx_id.get(settlement.transaction_id)
            if not tx or tx.transaction_id in matched_tx_ids:
                continue
            self._record_match(
                matches,
                matched_tx_ids,
                matched_settlement_ids,
                tx,
                [settlement],
                MatchStrategy.DIRECT_ID,
                1.0,
            )

        # Pass 2: Processor reference match (Acquirer B)
        for settlement in self.settlements:
            if settlement.settlement_id in matched_settlement_ids:
                continue
            if not settlement.processor_reference:
                continue
            tx = self._by_processor_ref.get(settlement.processor_reference)
            if not tx:
                continue
            existing = self._find_existing_match(matches, tx.transaction_id)
            if existing:
                existing.settlement_ids.append(settlement.settlement_id)
                existing.strategy = MatchStrategy.PROCESSOR_REFERENCE
                existing.notes = (
                    "One-to-many: additional settlement linked via processor reference"
                )
                self._refresh_match_totals(existing, tx)
            else:
                self._record_match(
                    matches,
                    matched_tx_ids,
                    matched_settlement_ids,
                    tx,
                    [settlement],
                    MatchStrategy.PROCESSOR_REFERENCE,
                    0.95,
                )
            matched_tx_ids.add(tx.transaction_id)
            matched_settlement_ids.add(settlement.settlement_id)

        # Pass 3: Fuzzy match on order_id + merchant_id + amount + date window (Acquirer C)
        for settlement in self.settlements:
            if settlement.settlement_id in matched_settlement_ids:
                continue
            if not settlement.order_id:
                continue
            candidates = self._by_order_merchant.get(
                (settlement.order_id, settlement.merchant_id), []
            )
            best_tx: InternalTransaction | None = None
            best_score = 0.0
            for tx in candidates:
                if not _amount_within_tolerance(tx.amount, settlement.settlement_amount):
                    delta_pct = (
                        abs(tx.amount - settlement.settlement_amount) / abs(tx.amount)
                        if tx.amount
                        else 1.0
                    )
                    # Allow partial settlement legs (split / partial refund)
                    is_partial_leg = (
                        0 < settlement.settlement_amount < tx.amount * 0.99
                    )
                    if delta_pct > 0.02 and not is_partial_leg:
                        continue
                hours_diff = abs(
                    (tx.created_at - settlement.settlement_date).total_seconds()
                ) / 3600
                if hours_diff > FUZZY_DATE_WINDOW_HOURS:
                    continue
                score = 0.7
                if _amount_within_tolerance(tx.amount, settlement.settlement_amount):
                    score += 0.2
                if hours_diff < 24:
                    score += 0.1
                if score > best_score:
                    best_score = score
                    best_tx = tx
            if best_tx and best_score >= 0.7:
                existing = self._find_existing_match(matches, best_tx.transaction_id)
                if existing:
                    existing.settlement_ids.append(settlement.settlement_id)
                    existing.strategy = MatchStrategy.FUZZY
                    existing.confidence = min(existing.confidence, best_score)
                    existing.notes = (
                        "One-to-many: fuzzy match linked additional settlement"
                    )
                    self._refresh_match_totals(existing, best_tx)
                else:
                    self._record_match(
                        matches,
                        matched_tx_ids,
                        matched_settlement_ids,
                        best_tx,
                        [settlement],
                        MatchStrategy.FUZZY,
                        best_score,
                    )
                matched_tx_ids.add(best_tx.transaction_id)
                matched_settlement_ids.add(settlement.settlement_id)

        return matches, matched_tx_ids, matched_settlement_ids

    def _find_existing_match(
        self, matches: list[MatchResult], transaction_id: str
    ) -> MatchResult | None:
        for m in matches:
            if m.internal_transaction_id == transaction_id:
                return m
        return None

    def _refresh_match_totals(
        self, match: MatchResult, tx: InternalTransaction
    ) -> None:
        settlements = [self._settlement_map[sid] for sid in match.settlement_ids]
        total_settlement = sum(s.settlement_amount for s in settlements)
        match.amount_delta = _amount_delta(tx.amount, total_settlement)
        match.within_tolerance = _amount_within_tolerance(tx.amount, total_settlement)
        match.notes = (
            None if match.within_tolerance else "Amount outside 0.5% tolerance"
        )

    def _record_match(
        self,
        matches: list[MatchResult],
        matched_tx_ids: set[str],
        matched_settlement_ids: set[str],
        tx: InternalTransaction,
        settlements: list[NormalizedSettlement],
        strategy: MatchStrategy,
        confidence: float,
    ) -> None:
        total_settlement = sum(s.settlement_amount for s in settlements)
        within_tol = _amount_within_tolerance(tx.amount, total_settlement)
        delta = _amount_delta(tx.amount, total_settlement)
        matches.append(
            MatchResult(
                internal_transaction_id=tx.transaction_id,
                settlement_ids=[s.settlement_id for s in settlements],
                strategy=strategy,
                confidence=confidence,
                amount_delta=delta,
                within_tolerance=within_tol,
                notes=None if within_tol else "Amount outside 0.5% tolerance",
            )
        )
        matched_tx_ids.add(tx.transaction_id)
        for s in settlements:
            matched_settlement_ids.add(s.settlement_id)
