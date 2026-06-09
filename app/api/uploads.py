import csv
import json
from io import StringIO
from typing import Any

from fastapi import HTTPException, UploadFile

from app.config import MAX_UPLOAD_BYTES
from app.models.schemas import AcquirerType


async def read_upload_bytes(file: UploadFile, *, label: str) -> bytes:
    if not file.filename:
        raise HTTPException(status_code=400, detail=f"{label}: filename is required")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"{label} ({file.filename}) exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit",
        )
    if not content:
        raise HTTPException(status_code=400, detail=f"{label} ({file.filename}) is empty")
    return content


def parse_json_records(content: bytes, *, label: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=400, detail=f"{label}: invalid JSON — {e}"
        ) from e
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("transactions", "settlements", "records"):
            if key in data and isinstance(data[key], list):
                return data[key]
    raise HTTPException(
        status_code=400,
        detail=f"{label}: expected a JSON array or object with transactions/settlements key",
    )


def parse_file_records(
    content: bytes, filename: str, *, label: str
) -> list[dict[str, Any]]:
    lower = filename.lower()
    if lower.endswith(".json"):
        return parse_json_records(content, label=label)
    if lower.endswith(".csv"):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise HTTPException(
                status_code=400, detail=f"{label}: CSV must be UTF-8"
            ) from e
        rows = list(csv.DictReader(StringIO(text)))
        if not rows:
            raise HTTPException(status_code=400, detail=f"{label}: CSV has no data rows")
        return rows
    raise HTTPException(
        status_code=400,
        detail=f"{label} ({filename}): only .json and .csv files are supported",
    )


async def parse_ledger_upload(file: UploadFile) -> list[dict[str, Any]]:
    content = await read_upload_bytes(file, label="ledger")
    return parse_file_records(content, file.filename or "ledger.json", label="ledger")


async def parse_settlement_upload(
    file: UploadFile, acquirer: AcquirerType
) -> tuple[AcquirerType, list[dict[str, Any]]]:
    content = await read_upload_bytes(file, label=acquirer.value)
    records = parse_file_records(
        content, file.filename or f"{acquirer.value}.json", label=acquirer.value
    )
    return acquirer, records
