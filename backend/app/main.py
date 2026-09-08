"""FactPulse API.

A fact knowledge layer: extract facts from PDFs, ground each one in the source
span it came from, and reconcile facts across documents.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    documents_router,
    facts_router,
    health_router,
    progress_router,
    review_router,
)
from app.core.config import settings
from app.db.database import get_conn, init_db

logger = logging.getLogger(__name__)


def _seed_if_empty() -> None:
    """Load the committed demo corpus if the database has no facts yet.

    Costs zero model calls either way: this reads scripts/seed_demo.py's
    committed SQL dump, not the live extraction pipeline. Guarded on
    emptiness so it fires exactly once per fresh database -- a first local
    run, a first deploy, or a volume that was ever reset -- and never on a
    database that already holds a real corpus, seeded or not.
    """
    if not settings.seed_demo_on_empty_db:
        return
    from scripts.seed_demo import DEFAULT_SEED_PATH, import_seed

    if not DEFAULT_SEED_PATH.exists():
        return
    with get_conn(settings.database_file) as conn:
        if conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]:
            return
        counts = import_seed(conn, DEFAULT_SEED_PATH)
    logger.info("seeded empty database with the demo corpus: %s", counts)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Apply schema.sql on boot. Idempotent, so it is safe on every start.
    init_db()
    settings.upload_path.mkdir(parents=True, exist_ok=True)
    settings.page_cache_path.mkdir(parents=True, exist_ok=True)
    settings.storage_path.mkdir(parents=True, exist_ok=True)
    _seed_if_empty()
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description=__doc__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(documents_router)
app.include_router(facts_router)
app.include_router(review_router)
app.include_router(progress_router)


@app.get("/", tags=["system"], summary="Service banner")
def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "version": settings.version,
        "docs": "/docs",
        "health": "/health",
    }
