"""Settings, loaded from the environment / .env."""

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# /backend
BACKEND_DIR = Path(__file__).resolve().parents[2]

load_dotenv(BACKEND_DIR / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "FactPulse"
    version: str = "0.1.0"

    # Gemini.
    #
    # Free-tier quota is per PROJECT per model per day, and a key belongs to a
    # project, so a key from a second Google account is an independent daily
    # allowance rather than a second slice of the same one. Numbered slots
    # rather than one comma-separated variable because these are 40-character
    # opaque strings and a reviewer has to be able to see which is which.
    gemini_api_key: str | None = None
    gemini_api_key_2: str | None = None
    gemini_api_key_3: str | None = None
    # Overflow, for anyone wanting more than three. Comma-separated.
    gemini_api_keys: str = ""
    gemini_model: str = "gemini-3.6-flash"
    # Ordered fallbacks, tried in turn when a model's DAILY quota is spent.
    # Free-tier quota is per model per day, so one name is one point of
    # failure -- a reviewer's first upload would look broken rather than
    # rate-limited. Comma-separated in the environment.
    gemini_model_fallbacks: str = (
        "gemini-3.6-flash,gemini-3.7-flash,gemini-3.8-flash,"
        "gemini-3.5-flash,gemini-3-flash-preview,gemini-3.1-flash-lite,"
        "gemini-flash-latest"
    )
    gemini_embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 768

    # storage
    database_path: str = "factpulse.db"
    upload_dir: str = "uploads"
    page_cache_dir: str = "page_cache"
    storage_dir: str = "storage"

    # Pixels per PDF point when rendering a page image. 2.0 renders a US
    # Letter page at 1224x1584, legible without being enormous.
    page_render_scale: float = 2.0

    # extraction
    extract_on_upload: bool = True
    link_on_upload: bool = True
    extraction_max_attempts: int = 4
    # Model calls in flight at once. Free-tier RPM caps are the binding
    # constraint, so raising this past ~6 mostly buys 429s and backoff.
    extraction_concurrency: int = 5
    # Calibrated against gemini-3.6-flash, whose confidence floor on hedged
    # prose sits near 0.70 -- a 0.5 threshold never fires and so never
    # surfaces anything. 0.9 catches facts the model expressed a real
    # reservation about while leaving directly-stated ones alone.
    review_confidence_threshold: float = 0.9

    # chunking
    chunk_max_tokens: int = 900
    chunk_overlap_tokens: int = 120
    max_upload_mb: int = 50

    # If the facts table is empty on startup, load scripts/seed_demo.py's
    # committed corpus instead of leaving the app looking broken. Only fires
    # on emptiness -- see seed_on_empty_db() in main.py -- so a real corpus,
    # local or deployed, is never touched by this.
    seed_demo_on_empty_db: bool = True

    # server
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    # Comma-separated so a deployed instance can serve both the local dev
    # server and the deployed frontend at once, without editing .env back and
    # forth while testing against a live backend from a laptop.
    frontend_origin: str = "http://localhost:3000"

    @property
    def database_file(self) -> Path:
        """Absolute path to the SQLite file."""
        p = Path(self.database_path)
        return p if p.is_absolute() else BACKEND_DIR / p

    @property
    def upload_path(self) -> Path:
        p = Path(self.upload_dir)
        return p if p.is_absolute() else BACKEND_DIR / p

    @property
    def storage_path(self) -> Path:
        p = Path(self.storage_dir)
        return p if p.is_absolute() else BACKEND_DIR / p

    @property
    def page_cache_path(self) -> Path:
        p = Path(self.page_cache_dir)
        return p if p.is_absolute() else BACKEND_DIR / p

    @property
    def embedding_model(self) -> str:
        """The embedding model name, normalised.

        Read through this rather than the raw setting. A deployment that sets
        GEMINI_EMBEDDING_MODEL to an empty value -- easy to do by adding the
        variable in a dashboard and leaving the box blank -- produced a model
        name of "" and every embedding call failed with "unexpected model name
        format". That broke linking on upload while extraction kept working,
        so documents ingested and simply had no relationships, with the cause
        only visible in a log line nobody was watching. A blank value now
        means "unset", and a redundant "models/" prefix is tolerated.
        """
        name = (self.gemini_embedding_model or "").strip().removeprefix("models/")
        return name or "gemini-embedding-001"

    @property
    def fallback_models(self) -> list[str]:
        return [m.strip() for m in self.gemini_model_fallbacks.split(",") if m.strip()]

    @property
    def api_keys(self) -> list[str]:
        """Every configured key, in priority order, deduplicated.

        Deduplication is not cosmetic. Two slots backed by the same key share
        one quota, so a duplicate would make the pool advertise capacity it
        does not have and turn one exhausted allowance into two dead slots.
        """
        candidates = [
            self.gemini_api_key,
            self.gemini_api_key_2,
            self.gemini_api_key_3,
            *self.gemini_api_keys.split(","),
        ]
        keys: list[str] = []
        for candidate in candidates:
            key = (candidate or "").strip()
            if key and key not in keys:
                keys.append(key)
        return keys

    @property
    def gemini_configured(self) -> bool:
        return bool(self.api_keys)

    @property
    def frontend_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
