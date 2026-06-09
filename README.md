# PangoPay Reconciliation Engine

Stateless backend service that matches PangoPay's internal transaction ledger against external acquirer settlement reports, detects discrepancies, and returns a reconciliation report.

Designed for **serverless deployment** (Vercel): upload files per request — no local filesystem or session state required.

## Quick Start (< 5 minutes)

```bash
cd pangopay-reconciliation
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

python scripts/generate_test_data.py
python -m app.cli --demo
```

## API Usage

### Primary endpoint — file upload (stateless)

`POST /api/v1/reconcile` — multipart form, **5MB max per file**

| Field | Required | Description |
|-------|----------|-------------|
| `ledger` | Yes | Internal ledger (`.json` or `.csv`) |
| `mpesa_tanzania` | No* | M-Pesa settlement report |
| `ovo_indonesia` | No* | OVO settlement report |
| `scb_thailand` | No* | SCB settlement report |

\* At least one settlement file is required.

```bash
# Start server locally
uvicorn app.main:app --reload --port 8000

# Upload files and get report
curl -X POST http://localhost:8000/api/v1/reconcile \
  -F "ledger=@data/ledger.json" \
  -F "mpesa_tanzania=@data/settlements/mpesa_tanzania.json" \
  -F "ovo_indonesia=@data/settlements/ovo_indonesia.json" \
  -F "scb_thailand=@data/settlements/scb_thailand.json" \
  | python -m json.tool
```

Interactive docs: http://localhost:8000/docs

### JSON body (no files)

`POST /api/v1/reconcile/json`

```json
{
  "transactions": [ ... ],
  "settlements": {
    "mpesa_tanzania": [ ... ],
    "ovo_indonesia": [ ... ],
    "scb_thailand": [ ... ]
  }
}
```

### Validation helpers

- `POST /api/v1/validate/ledger` — parse ledger file only
- `POST /api/v1/validate/settlements/{acquirer}` — parse one settlement file

## Deploy to Vercel

```bash
npm i -g vercel   # if needed
cd pangopay-reconciliation
vercel
```

The repo includes:

- `api/index.py` — exports the FastAPI app for `@vercel/python`
- `vercel.json` — routes all traffic to the Python handler
- `requirements.txt` — dependencies for Vercel builds

After deploy, call:

```
POST https://<your-project>.vercel.app/api/v1/reconcile
```

with the same multipart form as the local curl example.

**Limits:** 5MB per uploaded file (configurable in `app/config.py`). Vercel serverless functions also have a ~4.5MB request body limit on the Hobby plan — keep files under that for production.

## Matching Strategies

| Priority | Strategy | Acquirer | Confidence |
|----------|----------|----------|------------|
| 1 | Direct ID | M-Pesa Tanzania (`transaction_id`) | 1.0 |
| 2 | Processor Reference | OVO Indonesia (`processor_reference`) | 0.95 |
| 3 | Fuzzy | SCB Thailand (`order_id` + amount + date) | 0.7–1.0 |

Fuzzy matching accepts partial settlement legs (split refunds) when `order_id` and `merchant_id` match.

## Expected Demo Output

After `python -m app.cli --demo`:

```
Matched:                440
Missing settlements:    55
Orphaned settlements:   10
Amount mismatches:      10
Status conflicts:       10
```

## Test Data

```bash
python scripts/generate_test_data.py
```

Creates `data/ledger.json` and `data/settlements/*.json` with 520 ledger rows and 470 settlement rows covering all discrepancy types.

## Tests

```bash
pytest tests/ -v
```

## Project Structure

```
app/
├── api/routes.py          # Stateless upload endpoints
├── api/uploads.py         # File parsing + size limits
├── ingestion/normalizers.py
├── matching/engine.py     # 3-pass matching cascade
├── reconciliation/service.py
└── services/reconcile.py  # Core orchestration (no I/O)
api/index.py               # Vercel entrypoint
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for design details.
