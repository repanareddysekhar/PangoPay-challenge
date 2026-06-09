from fastapi import FastAPI

from app import __version__
from app.api.routes import router

app = FastAPI(
    title="PangoPay Reconciliation API",
    description="Match internal ledger transactions against acquirer settlement reports",
    version=__version__,
)

app.include_router(router, prefix="/api/v1", tags=["reconciliation"])


@app.get("/health")
async def health():
    return {"status": "ok", "version": __version__}
