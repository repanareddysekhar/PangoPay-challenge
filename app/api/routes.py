from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.api.uploads import parse_ledger_upload, parse_settlement_upload
from app.ingestion.normalizers import ingest_ledger, ingest_settlements
from app.models.schemas import AcquirerType, ReconciliationReport
from app.services.reconcile import run_reconciliation

router = APIRouter()

SETTLEMENT_FIELDS: dict[str, AcquirerType] = {
    "mpesa_tanzania": AcquirerType.MPESA_TZ,
    "ovo_indonesia": AcquirerType.OVO_ID,
    "scb_thailand": AcquirerType.SCB_TH,
}


@router.post("/reconcile", response_model=ReconciliationReport)
async def reconcile_files(
    ledger: UploadFile = File(..., description="Internal ledger JSON or CSV (max 5MB)"),
    mpesa_tanzania: UploadFile | None = File(
        default=None, description="M-Pesa Tanzania settlement report"
    ),
    ovo_indonesia: UploadFile | None = File(
        default=None, description="OVO Indonesia settlement report"
    ),
    scb_thailand: UploadFile | None = File(
        default=None, description="SCB Thailand settlement report"
    ),
):
    """
    Stateless reconciliation: upload ledger + one or more acquirer settlement files.
    Returns the full reconciliation report in a single response.
    """
    ledger_records = await parse_ledger_upload(ledger)

    settlement_batches: list[tuple[AcquirerType, list[dict[str, Any]]]] = []
    uploads = [
        (mpesa_tanzania, AcquirerType.MPESA_TZ),
        (ovo_indonesia, AcquirerType.OVO_ID),
        (scb_thailand, AcquirerType.SCB_TH),
    ]
    for upload, acquirer in uploads:
        if upload is not None and upload.filename:
            settlement_batches.append(await parse_settlement_upload(upload, acquirer))

    if not settlement_batches:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one settlement file (mpesa_tanzania, ovo_indonesia, or scb_thailand)",
        )

    return run_reconciliation(ledger_records, settlement_batches)


@router.post("/reconcile/json", response_model=ReconciliationReport)
async def reconcile_json(payload: dict[str, Any]):
    """
    Stateless reconciliation via JSON body (no file upload).
    Body: { "transactions": [...], "settlements": { "mpesa_tanzania": [...], ... } }
    """
    records = payload.get("transactions", [])
    if not records:
        raise HTTPException(status_code=400, detail="transactions array is required")

    settlement_batches: list[tuple[AcquirerType, list[dict[str, Any]]]] = []
    settlements_obj = payload.get("settlements", {})
    if not settlements_obj:
        raise HTTPException(status_code=400, detail="settlements object is required")

    for key, acquirer in SETTLEMENT_FIELDS.items():
        batch = settlements_obj.get(key, [])
        if batch:
            settlement_batches.append((acquirer, batch))

    if not settlement_batches:
        raise HTTPException(
            status_code=400,
            detail=f"settlements must include at least one of: {', '.join(SETTLEMENT_FIELDS)}",
        )

    return run_reconciliation(records, settlement_batches)


@router.post("/validate/ledger")
async def validate_ledger(ledger: UploadFile = File(...)):
    """Parse and validate a ledger file without running reconciliation."""
    records = await parse_ledger_upload(ledger)
    txs = ingest_ledger(records)
    return {"valid": True, "transaction_count": len(txs)}


@router.post("/validate/settlements/{acquirer}")
async def validate_settlements(
    acquirer: AcquirerType,
    file: UploadFile = File(...),
):
    """Parse and validate a settlement file for a given acquirer."""
    _, records = await parse_settlement_upload(file, acquirer)
    normalized = ingest_settlements(acquirer, records)
    return {"valid": True, "acquirer": acquirer.value, "record_count": len(normalized)}
