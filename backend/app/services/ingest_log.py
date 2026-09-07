"""One JSON line per ingested document, for measuring the pipeline over time.

JSONL rather than a table: these are append-only observations about runs, not
part of the knowledge layer, and keeping them out of SQLite means a profiling
session cannot interfere with the data being profiled. `jq` reads them, and so
does the comparison in scripts/bulk_ingest.py.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# Appends come from one ingest at a time today, but the file is opened per
# write and a lock keeps concurrent runs from interleaving partial lines.
_lock = threading.Lock()


def log_path() -> Path:
    return settings.storage_path / "ingest_log.jsonl"


def record(entry: dict[str, Any]) -> None:
    """Append one run. Never raises -- a logging failure must not fail an ingest."""
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with _lock, path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("could not write ingest log: %s", exc)


def read_all() -> list[dict[str, Any]]:
    """Every logged run, oldest first. Skips lines that are not valid JSON."""
    path = log_path()
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries
