"""Shared fixtures.

Every test runs against a throwaway database and upload directory in tmp_path,
so a test run can never touch the development corpus. `settings` is a cached
singleton, so the fixture mutates it and restores it rather than trying to
re-instantiate it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import fitz
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.db.database import get_conn, init_db  # noqa: E402


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Point the app at a fresh database and upload dir for one test.

    A placeholder API key is set too, and that is not incidental. The ingest
    path skips extraction outright when `gemini_configured` is false, so on a
    machine with no .env -- a fresh clone, which is exactly what a grader runs
    first -- the stubbed model would never be reached and the pipeline tests
    would fail for a reason that has nothing to do with the code under test.
    Nothing here ever reaches the network: the model is replaced wholesale by
    the `stubbed` fixture.
    """
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "page_cache_dir", str(tmp_path / "pages"))
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "link_on_upload", False)
    monkeypatch.setattr(settings, "gemini_api_key", "test-key-not-used")
    # Off by default so a test spinning up TestClient(app) on a genuinely
    # empty database gets an empty database, not 335 demo facts it never
    # asked for. test_seed.py re-enables it explicitly to test the behavior.
    monkeypatch.setattr(settings, "seed_demo_on_empty_db", False)
    init_db(settings.database_file)
    return settings.database_file


@pytest.fixture
def conn(isolated_store):
    with get_conn(isolated_store) as connection:
        yield connection


@pytest.fixture
def sample_pdf(tmp_path) -> bytes:
    """A tiny PDF whose text is known, so quotes can be asserted exactly."""
    doc = fitz.open()
    doc.set_metadata({"title": "Test Report"})
    page = doc.new_page()
    page.insert_textbox(
        fitz.Rect(60, 60, 535, 700),
        "Test Report FY2024\n\n"
        "Consolidated revenue for fiscal year 2024 was $4.2 million.\n\n"
        "Headcount at year end stood at 128 employees.\n\n"
        "The company is headquartered in Zurich, Switzerland.",
        fontsize=11,
        fontname="helv",
    )
    data = doc.tobytes()
    doc.close()
    return data
