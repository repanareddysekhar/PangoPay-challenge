import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.ingestion.normalizers import ingest_ledger, ingest_settlement_file, ingest_settlements
from app.models.schemas import AcquirerType, NormalizedSettlement, ReconciliationReport
from app.reconciliation.service import ReconciliationService

router = APIRouter()

# In-memory store for demo; production would use a database
_ledger: list[dict[str, Any]] = []
_settlements: list[NormalizedSettlement] = []
_last_report: ReconciliationReport | None = None


@router.post("/ledger")
async def upload_ledger(payload: dict[str, Any] | list[dict[str, Any]]):
    """Ingest internal ledger transactions (JSON body)."""
    global _ledger
    records = payload if isinstance(payload, list) else payload.get("transactions", [])
    if not records:
        raise HTTPException(status_code=400, detail="No transactions provided")
    _ledger = records
    txs = ingest_ledger(records)
    return {"ingested": len(txs), "message": "Ledger ingested successfully"}


@router.post("/ledger/file")
async def upload_ledger_file(file: UploadFile = File(...)):
    """Upload internal ledger as JSON file."""
    content = await file.read()
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e
    return await upload_ledger(data)


@router.post("/settlements/{acquirer}")
async def upload_settlements(acquirer: AcquirerType, payload: list[dict[str, Any]]):
    """Ingest settlement report for a specific acquirer."""
    global _settlements
    if not payload:
        raise HTTPException(status_code=400, detail="No settlement records provided")
    normalized = ingest_settlements(acquirer, payload)
    _settlements.extend(normalized)
    return {
        "acquirer": acquirer.value,
        "ingested": len(normalized),
        "total_settlements": len(_settlements),
    }


@router.post("/settlements/{acquirer}/file")
async def upload_settlement_file(acquirer: AcquirerType, file: UploadFile = File(...)):
    """Upload settlement report file (JSON or CSV)."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".json", ".csv"):
        raise HTTPException(status_code=400, detail="Only JSON and CSV files supported")
    tmp = Path(f"/tmp/{file.filename}")
    content = await file.read()
    tmp.write_bytes(content)
    try:
        normalized = ingest_settlement_file(tmp, acquirer)
    finally:
        tmp.unlink(missing_ok=True)
    global _settlements
    _settlements.extend(normalized)
    return {
        "acquirer": acquirer.value,
        "ingested": len(normalized),
        "total_settlements": len(_settlements),
    }


@router.post("/reconcile", response_model=ReconciliationReport)
async def run_reconciliation():
    """Run reconciliation against ingested ledger and settlements."""
    global _last_report
    if not _ledger:
        raise HTTPException(status_code=400, detail="No ledger data ingested")
    if not _settlements:
        raise HTTPException(status_code=400, detail="No settlement data ingested")

    transactions = ingest_ledger(_ledger)
    service = ReconciliationService(transactions, _settlements)
    _last_report = service.run()
    return _last_report


@router.get("/report", response_model=ReconciliationReport)
async def get_report():
    """Get the most recent reconciliation report."""
    if _last_report is None:
        raise HTTPException(status_code=404, detail="No reconciliation run yet")
    return _last_report


@router.post("/demo/load")
async def load_demo_data():
    """Load bundled test data and run reconciliation."""
    global _ledger, _settlements, _last_report
    data_dir = Path(__file__).resolve().parents[2] / "data"

    ledger_path = data_dir / "ledger.json"
    if not ledger_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Demo data not found. Run: python scripts/generate_test_data.py",
        )

    with ledger_path.open() as f:
        _ledger = json.load(f)["transactions"]

    _settlements = []
    acquirer_files = [
        (AcquirerType.MPESA_TZ, "settlements/mpesa_tanzania.json"),
        (AcquirerType.OVO_ID, "settlements/ovo_indonesia.json"),
        (AcquirerType.SCB_TH, "settlements/scb_thailand.json"),
    ]
    for acquirer, rel_path in acquirer_files:
        path = data_dir / rel_path
        if path.exists():
            _settlements.extend(ingest_settlement_file(path, acquirer))

    transactions = ingest_ledger(_ledger)
    service = ReconciliationService(transactions, _settlements)
    _last_report = service.run()
    return _last_report


@router.delete("/reset")
async def reset_state():
    """Clear all ingested data."""
    global _ledger, _settlements, _last_report
    _ledger = []
    _settlements = []
    _last_report = None
    return {"message": "State cleared"}
