"""Health / readiness schemas."""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(..., description="'ok' when the API is serving requests.")
    app: str = Field(..., description="Application name.")
    version: str = Field(..., description="Application version.")
    database: str = Field(..., description="'ok', 'uninitialized', or an error string.")
    tables: list[str] = Field(
        default_factory=list, description="Tables present in the SQLite database."
    )
    gemini_configured: bool = Field(
        ..., description="True when GEMINI_API_KEY is set in the environment."
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "ok",
                "app": "FactPulse",
                "version": "0.1.0",
                "database": "ok",
                "tables": ["chunks", "documents", "facts"],
                "gemini_configured": False,
            }
        }
    }
