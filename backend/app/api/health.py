"""GET /health"""

from fastapi import APIRouter

from app.core.config import settings
from app.db.database import table_names
from app.schemas.health import HealthResponse
from app.services.model_pool import pool_status

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse, summary="Liveness + readiness")
def health() -> HealthResponse:
    """Report API liveness, whether the SQLite schema is applied, and whether
    a Gemini key is configured. Never raises -- a broken database is reported
    in the payload, not as a 500."""
    try:
        tables = table_names()
        db_status = "ok" if tables else "uninitialized"
    except Exception as exc:  # pragma: no cover - defensive
        tables = []
        db_status = f"error: {exc}"

    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version=settings.version,
        database=db_status,
        tables=tables,
        gemini_configured=settings.gemini_configured,
        model_pool=pool_status(),
    )
