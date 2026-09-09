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
        ..., description="True when at least one Gemini API key is configured."
    )
    embedding_model: str = Field(
        "",
        description=(
            "The embedding model name actually in use, after normalisation. "
            "Exposed because a misconfigured value here breaks linking while "
            "extraction keeps working, so uploads silently produce facts with "
            "no relationships -- a failure that is otherwise invisible from "
            "outside the container."
        ),
    )
    model_pool: dict = Field(
        default_factory=dict,
        description=(
            "Quota rotation state. Free-tier quota is per project per model per "
            "day and a key belongs to a project, so the unit that runs out is a "
            "(key, model) pair -- `slots` is how many exist, `remaining` how "
            "many still have quota, and `active` the one in use. Slots are "
            "labelled key1/model; no key or fragment of one appears here. This "
            "is the first thing to check when ingestion stops producing facts."
        ),
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
