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

    # Gemini
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.6-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 768

    # storage
    database_path: str = "factpulse.db"
    upload_dir: str = "uploads"
    page_cache_dir: str = "page_cache"

    # Pixels per PDF point when rendering a page image. 2.0 renders a US
    # Letter page at 1224x1584, legible without being enormous.
    page_render_scale: float = 2.0

    # extraction
    extract_on_upload: bool = True
    link_on_upload: bool = True
    extraction_max_attempts: int = 4
    # Calibrated against gemini-3.6-flash, whose confidence floor on hedged
    # prose sits near 0.70 -- a 0.5 threshold never fires and so never
    # surfaces anything. 0.9 catches facts the model expressed a real
    # reservation about while leaving directly-stated ones alone.
    review_confidence_threshold: float = 0.9

    # chunking
    chunk_max_tokens: int = 900
    chunk_overlap_tokens: int = 120
    max_upload_mb: int = 50

    # server
    api_host: str = "127.0.0.1"
    api_port: int = 8000
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
    def page_cache_path(self) -> Path:
        p = Path(self.page_cache_dir)
        return p if p.is_absolute() else BACKEND_DIR / p

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
