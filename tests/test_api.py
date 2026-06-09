import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture
def demo_files():
    ledger_path = DATA_DIR / "ledger.json"
    if not ledger_path.exists():
        pytest.skip("Run python scripts/generate_test_data.py first")
    return {
        "ledger": ledger_path,
        "mpesa": DATA_DIR / "settlements" / "mpesa_tanzania.json",
        "ovo": DATA_DIR / "settlements" / "ovo_indonesia.json",
        "scb": DATA_DIR / "settlements" / "scb_thailand.json",
    }


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_reconcile_json(demo_files):
    ledger = json.loads(demo_files["ledger"].read_text())
    settlements = {
        "mpesa_tanzania": json.loads(demo_files["mpesa"].read_text()),
        "ovo_indonesia": json.loads(demo_files["ovo"].read_text()),
        "scb_thailand": json.loads(demo_files["scb"].read_text()),
    }
    r = client.post(
        "/api/v1/reconcile/json",
        json={"transactions": ledger["transactions"], "settlements": settlements},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["total_internal"] == len(ledger["transactions"])
    assert body["summary"]["amount_mismatch_count"] == 10
    assert body["summary"]["status_conflict_count"] == 10
    assert body["summary"]["missing_settlement_count"] == 55
    assert body["summary"]["orphaned_settlement_count"] == 10


def test_reconcile_files(demo_files):
    with (
        open(demo_files["ledger"], "rb") as ledger,
        open(demo_files["mpesa"], "rb") as mpesa,
        open(demo_files["ovo"], "rb") as ovo,
        open(demo_files["scb"], "rb") as scb,
    ):
        r = client.post(
            "/api/v1/reconcile",
            files={
                "ledger": ("ledger.json", ledger, "application/json"),
                "mpesa_tanzania": ("mpesa.json", mpesa, "application/json"),
                "ovo_indonesia": ("ovo.json", ovo, "application/json"),
                "scb_thailand": ("scb.json", scb, "application/json"),
            },
        )
    assert r.status_code == 200
    assert r.json()["summary"]["matched_count"] >= 400


def test_reconcile_requires_settlement_file(demo_files):
    with open(demo_files["ledger"], "rb") as ledger:
        r = client.post(
            "/api/v1/reconcile",
            files={"ledger": ("ledger.json", ledger, "application/json")},
        )
    assert r.status_code == 400
