# PangoPay Reconciliation Engine

Backend service that matches PangoPay's internal transaction ledger against external acquirer settlement reports, detects discrepancies, and produces actionable reconciliation reports.

## Quick Start (< 5 minutes)

```bash
cd pangopay-reconciliation

# Create virtual environment and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Generate test data
python scripts/generate_test_data.py

# Run reconciliation demo via CLI
python -m app.cli --demo

# Or save full JSON report
python -m app.cli --demo -o reconciliation_report.json
```

### API Server

```bash
uvicorn app.main:app --reload --port 8000
```

Then open http://localhost:8000/docs for interactive API docs.

**One-shot demo via API:**

```bash
curl -X POST http://localhost:8000/api/v1/demo/load | python -m json.tool
```

## Architecture Overview

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Ledger     │────▶│   Ingestion      │────▶│  Normalized     │
│  (JSON/API) │     │   Normalizers    │     │  Records        │
└─────────────┘     └──────────────────┘     └────────┬────────┘
                                                       │
┌─────────────┐     ┌──────────────────┐              │
│  Settlement │────▶│  Acquirer-specific│─────────────┤
│  Reports    │     │  Parsers (A/B/C) │              │
└─────────────┘     └──────────────────┘              ▼
                                            ┌─────────────────┐
                                            │ Matching Engine │
                                            │ (3 strategies)  │
                                            └────────┬────────┘
                                                     ▼
                                            ┌─────────────────┐
                                            │  Discrepancy    │
                                            │  Detection      │
                                            └────────┬────────┘
                                                     ▼
                                            ┌─────────────────┐
                                            │ Reconciliation  │
                                            │ Report          │
                                            └─────────────────┘
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed design decisions.

## Matching Strategies

The engine applies strategies in priority order. Once a settlement is matched, it is not re-matched by a lower-priority strategy.

| Priority | Strategy | When Used | Confidence |
|----------|----------|-----------|------------|
| 1 | **Direct ID** | Settlement includes PangoPay `transaction_id` (M-Pesa Tanzania) | 1.0 |
| 2 | **Processor Reference** | Settlement includes `processor_reference` mapping to internal record (OVO Indonesia) | 0.95 |
| 3 | **Fuzzy** | Only `order_id` + `merchant_id` + amount + date window available (SCB Thailand) | 0.7–1.0 |

### Fuzzy Match Criteria

- `order_id` and `merchant_id` must match exactly
- Amount within 2% hard cutoff (0.5% flagged for review)
- Settlement date within 48 hours of transaction `created_at`
- Higher confidence when amount is within 0.5% tolerance and date within 24 hours

### One-to-Many Matches

Split settlements and partial refunds are supported: one internal transaction can match multiple settlement records (common with OVO and SCB acquirers).

## Discrepancy Categories

| Category | Description | Risk Assessment |
|----------|-------------|-----------------|
| `matched` | Internal and external records align | Low |
| `missing_settlement` | Internal captured/settled record with no settlement entry | Medium (Low if < $20) |
| `orphaned_settlement` | Settlement entry with no internal record | High if > $100 |
| `amount_mismatch` | Matched by ID but amounts differ > 0.5% | High if delta > $100 |
| `status_conflict` | e.g., internal `refunded` but settlement `settled` | High |
| `duplicate_settlement` | Same merchant + amount within 24h (stretch goal) | High |

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/ledger` | Ingest ledger JSON |
| `POST` | `/api/v1/ledger/file` | Upload ledger file |
| `POST` | `/api/v1/settlements/{acquirer}` | Ingest settlements for acquirer |
| `POST` | `/api/v1/settlements/{acquirer}/file` | Upload settlement file |
| `POST` | `/api/v1/reconcile` | Run reconciliation |
| `GET` | `/api/v1/report` | Get last report |
| `POST` | `/api/v1/demo/load` | Load test data and reconcile |
| `DELETE` | `/api/v1/reset` | Clear state |

**Acquirer values:** `mpesa_tanzania`, `ovo_indonesia`, `scb_thailand`

## Test Data

The generator creates:

- **580** internal ledger transactions over 30 days
- **450+** settlement records across 3 acquirers
- **~50** missing settlements (internal only)
- **10** amount mismatches
- **10** orphaned settlements
- **10** status conflicts
- **~20** split/partial refund scenarios

```bash
python scripts/generate_test_data.py
```

Data is written to `data/ledger.json` and `data/settlements/*.json`.

## Running Tests

```bash
pytest tests/ -v
```

## Interpreting the Report

The reconciliation report JSON contains:

- **`summary`**: Counts by category, total unreconciled value, mismatch rate per acquirer
- **`matches`**: Successful pairings with strategy used and confidence score
- **`discrepancies`**: All issues with risk level, suspected reason, and monetary impact

Example CLI output:

```
Matched:                412
Missing settlements:    55
Orphaned settlements:   10
Amount mismatches:      10
Status conflicts:       10
```

Focus investigation on `high` risk discrepancies first, especially `amount_mismatch` and `orphaned_settlement` entries with large monetary impact.
