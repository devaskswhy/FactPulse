# FactPulse

A **fact knowledge layer**. FactPulse extracts facts from PDFs, grounds each
one in the exact source span it came from, and detects whether facts across
documents **corroborate**, **contradict**, or can be **reconciled** through
context — time period, scope, or units.

> Two documents say revenue was $4.2M and $5.1M. The useful answer is rarely
> "contradiction" — it is usually "different fiscal period" or "one is a
> segment, one is consolidated". FactPulse is built to tell those apart, and
> to show you the page and rectangle each claim came from.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the data model and the
reasoning behind it.

## Status

Scaffold. The schema, config, health endpoint, pydantic models, and a landing
page are in place. Ingestion, extraction, grounding, and linking are next.

## Stack

| | |
| --- | --- |
| Backend | Python 3.11, FastAPI, Uvicorn, SQLite, PyMuPDF, google-genai, numpy |
| Frontend | Next.js (App Router), TypeScript, Tailwind CSS |
| Database | SQLite, schema in [`backend/app/db/schema.sql`](backend/app/db/schema.sql) |

## Setup

### Backend — http://127.0.0.1:8000

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt

cp .env.example .env            # then add your GEMINI_API_KEY

python run.py                   # or: uvicorn app.main:app --reload
```

The schema is applied automatically on startup; `init_db()` is idempotent.

- Health: <http://127.0.0.1:8000/health>
- API docs: <http://127.0.0.1:8000/docs>

### Frontend — http://localhost:3000

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

The landing page shows a live indicator for whether the backend is reachable.

## Layout

```
backend/     FastAPI app, SQLite schema, pydantic models
frontend/    Next.js App Router + TypeScript + Tailwind
docs/        ARCHITECTURE.md
```

`.env` files and `factpulse.db` are gitignored.
