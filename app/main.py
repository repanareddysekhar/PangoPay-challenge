from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import router
from app.config import MAX_UPLOAD_BYTES

app = FastAPI(
    title="PangoPay Reconciliation API",
    description=(
        "Upload ledger and acquirer settlement files (max "
        f"{MAX_UPLOAD_BYTES // (1024 * 1024)}MB each) to run stateless reconciliation."
    ),
    version=__version__,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1", tags=["reconciliation"])


@app.get("/health")
async def health():
    return {"status": "ok", "version": __version__}
