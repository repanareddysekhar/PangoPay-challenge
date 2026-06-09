# PangoPay Reconciliation — Architecture

## 1. Problem Statement

PangoPay processes payments through 8+ acquirers, each with different settlement report formats and identifier schemes. The reconciliation engine normalizes heterogeneous inputs into a common model, applies prioritized matching strategies, and categorizes discrepancies for finance team investigation.

## 2. Data Model

### InternalTransaction (Ledger)

The source of truth for what PangoPay believes happened. Key fields:

- `transaction_id` — PangoPay's canonical identifier
- `processor_reference` — Acquirer's reference (when available)
- `order_id` — Merchant's order reference
- `status` — Lifecycle state (settled, refunded, failed, etc.)

### NormalizedSettlement

All acquirer reports are transformed into this uniform structure before matching:

| Field | M-Pesa (A) | OVO (B) | SCB (C) |
|-------|-----------|---------|---------|
| `transaction_id` | ✓ | — | — |
| `processor_reference` | — | ✓ | — |
| `order_id` | — | ✓ | ✓ |
| `settlement_amount` | ✓ | ✓ | ✓ |
| `merchant_id` | ✓ | ✓ | ✓ |

The `acquirer` field preserves provenance for per-acquirer analytics.

### MatchResult

Links one internal transaction to one or more settlements:

- `strategy` — Which matching pass succeeded
- `confidence` — 0.0–1.0 based on match quality
- `within_tolerance` — Whether total settled amount is within 0.5% of internal amount

### Discrepancy

A categorized issue with risk assessment:

- `category` — Type of mismatch
- `risk_level` — high / medium / low based on amount thresholds
- `monetary_impact` — Dollar value at stake
- `suspected_reason` — Human-readable explanation

## 3. Matching Algorithm

### Design Principle: Priority Cascade

Rather than scoring all possible pairs globally (O(n²) at scale), we use a **three-pass cascade** where each pass only considers unmatched settlements. This mirrors how finance teams work: try exact matches first, then progressively looser criteria.

```
Pass 1: Direct ID     →  O(n) hash lookup on transaction_id
Pass 2: Processor Ref →  O(n) hash lookup on processor_reference
Pass 3: Fuzzy         →  O(n × k) where k = candidates per order_id+merchant
```

### Pass Details

**Pass 1 — Direct ID (confidence: 1.0)**

Used by M-Pesa Tanzania. Settlement `transaction_id` maps directly to ledger `transaction_id`. One-to-one only in this pass.

**Pass 2 — Processor Reference (confidence: 0.95)**

Used by OVO Indonesia. Settlement `processor_reference` maps to ledger field. Supports one-to-many: if the transaction is already matched, additional settlements are appended (split settlements, partial refunds).

**Pass 3 — Fuzzy Composite (confidence: 0.7–1.0)**

Used by SCB Thailand which only provides `order_id`. Match requires:

1. Exact `order_id` + `merchant_id`
2. Amount within 2% (0.5% = within tolerance)
3. Settlement date within 48h of transaction creation

Confidence scoring rewards tighter amount match and shorter time gap.

### Amount Tolerance

- **0.5%**: Considered a match but may be flagged for review
- **> 0.5%**: Categorized as `amount_mismatch`
- Currency conversion rounding is the primary expected cause

## 4. Discrepancy Detection

After matching, the service performs a **residual analysis**:

1. **Matched pairs** — Validate amount tolerance and status consistency
2. **Unmatched internal** — Status in (captured, settled, refunded) → `missing_settlement`
3. **Unmatched settlements** → `orphaned_settlement`
4. **Status conflicts** — Cross-reference `TransactionStatus` vs `SettlementStatus`
5. **Duplicate detection** — Same merchant + rounded amount within 24h

### Risk Scoring

| Condition | Risk |
|-----------|------|
| Amount delta > $100 | High |
| Orphaned settlement > $100 | High |
| Status conflict | High |
| Missing settlement < $20 | Low |
| Missing settlement ≥ $20 | Medium |

Thresholds are constants in `reconciliation/service.py` and can be configured per environment.

## 5. Service Architecture

```
app/
├── models/schemas.py      # Pydantic models (domain types)
├── ingestion/
│   └── normalizers.py     # Acquirer-specific parsers
├── matching/
│   └── engine.py          # Three-pass matching cascade
├── reconciliation/
│   └── service.py         # Orchestration + discrepancy detection
├── api/routes.py          # FastAPI HTTP interface
└── cli.py                 # Command-line interface
```

### Separation of Concerns

- **Ingestion** knows about file formats and acquirer quirks
- **Matching** knows only about normalized records and strategies
- **Reconciliation** orchestrates matching and applies business rules for discrepancy categorization
- **API/CLI** are thin transport layers

This allows the matching engine to be unit-tested independently and reused in batch jobs, streaming pipelines, or the HTTP API.

## 6. Design Decisions

### In-Memory State (API)

The API uses in-memory storage for simplicity in the demo. Production deployment would persist to PostgreSQL with:

- `ledger_transactions` table indexed on `transaction_id`, `processor_reference`, `(order_id, merchant_id)`
- `settlements` table indexed similarly
- `reconciliation_runs` and `discrepancies` for audit trail

### No External Database

For the assessment scope, file-based ingestion + in-memory processing demonstrates the core logic without infrastructure overhead. The 94K transaction scale would require database indexing and batch processing — the algorithm design supports this via the O(n) hash-based passes.

### Extensibility

Adding a new acquirer requires:

1. Add `AcquirerType` enum value
2. Implement a normalizer function in `ingestion/normalizers.py`
3. Register in `NORMALIZERS` dict

If the new acquirer uses a novel identifier scheme, add a matching pass or extend fuzzy criteria.

## 7. Future Enhancements

- **Async batch processing** for 94K+ records with progress tracking
- **Configurable tolerance** per currency/acquirer
- **Machine learning** fuzzy matching for merchant typos in order IDs
- **Webhook ingestion** for real-time settlement notifications
- **Idempotent reconciliation runs** with diff against previous run
