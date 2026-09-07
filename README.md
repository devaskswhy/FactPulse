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

All eight pipeline steps are implemented: ingest, parse, chunk, extract,
ground, embed, link, review. The frontend is still a scaffold.

Extraction needs `GEMINI_API_KEY`. Without it the backend still runs and still
ingests documents; they simply stop at `chunked` and `POST /extract` answers
503.

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

### Endpoints

| | |
| --- | --- |
| `POST /documents` | Upload a PDF: parse, chunk, store. Deduped by SHA-256 |
| `GET /documents` | List documents |
| `GET /documents/{id}` | One document, with chunk count |
| `GET /documents/{id}/chunks` | Its chunks, with page ranges |
| `GET /documents/{id}/file` | The stored original PDF |
| `POST /documents/{id}/extract` | Run or re-run fact extraction |
| `POST /documents/{id}/rechunk` | Rebuild chunks after changing settings |
| `DELETE /documents/{id}` | Delete, cascading to everything derived |
| `GET /facts` | List facts; filter by document, type, subject, confidence |
| `GET /facts/{id}` | One fact with its grounding and EAV attributes |
| `GET /schema` | The `fact_types` registry: labels the model invented |
| `GET /facts/{id}/relationships` | Related facts with verdict and rationale |
| `GET /facts/{id}/evidence` | Page image URL + highlight box, in image pixels |
| `GET /documents/{id}/pages/{n}/image` | Rendered page PNG (cached) |
| `POST /documents/{id}/reground` | Recompute page/bbox from stored quotes (free) |
| `GET /review-queue` | Unresolved items with full context |
| `POST /review-queue/{id}/resolve` | accepted / rejected / edited |
| `POST /documents/{id}/link` | Run or re-run the relationship engine |
| `GET /review` | The review queue: facts the pipeline flagged |
| `GET /health` | Liveness, schema state, whether a Gemini key is set |

```bash
curl -F "file=@report.pdf" http://127.0.0.1:8000/documents
```

Uploading the same file twice returns the existing document with
`deduplicated: true` and a 200 rather than a 201.

Chunk size is tunable via `CHUNK_MAX_TOKENS` and `CHUNK_OVERLAP_TOKENS`;
`POST /documents/{id}/rechunk` reapplies them to an already-ingested PDF.

### Facts and the evolving schema

Extraction runs per chunk right after chunking. `fact_type` is chosen by the
model — there is no enum, no allowed-values list in the prompt, and no
validation against one. `GET /schema` reports the labels that actually turned
up:

```bash
curl http://127.0.0.1:8000/schema
curl "http://127.0.0.1:8000/facts?fact_type=financial-metric"
```

Every fact carries a quote verified as a real substring of its source chunk,
plus the page and bounding box where that quote sits in the PDF.

### Nothing doubtful is thrown away

A fact whose quote cannot be verified, or whose confidence falls below
`REVIEW_CONFIDENCE_THRESHOLD` (default 0.9), is **still stored** — and also
gets a `review_queue` row pointing at it:

```bash
curl http://127.0.0.1:8000/review
```

| Issue type | Meaning |
| --- | --- |
| `unverified_quote` | Quote is not a substring of the source chunk |
| `ungrounded_quote` | Quote verified, but not locatable in the PDF (no bbox) |
| `low_confidence` | Below the review threshold |
| `extraction_failed` | The model call failed for that chunk |

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

### Cross-document relationships

After extraction, each document's facts are embedded and compared against
everything already in the layer. Candidates come from cosine similarity
(brute-force numpy, appropriate to this scale); a single Gemini call per fact
judges its whole shortlist at once.

| Verdict | Meaning |
| --- | --- |
| `corroborates` | Same claim, independently stated |
| `contradicts` | Incompatible under the same scope, period, unit and basis |
| `reconciled` | Looks like a conflict; a named difference explains it |
| `unrelated` | Retrieved but not actually about the same thing (not stored) |

```bash
curl http://127.0.0.1:8000/facts/1/relationships
```

Each entry carries the related fact, its document, page, quote and the
rationale, so a comparison view renders without a second request.

### Evidence grounding

`GET /facts/{id}/evidence` returns the highlight box already scaled to the
pixels of the page image it also links, so the frontend draws it directly:

```json
{
  "page_image_url": "/documents/3/pages/8/image",
  "page_image_width": 1190, "page_image_height": 1684, "render_scale": 2.0,
  "bbox": { "x0": 100.0, "y0": 494.04, "x1": 663.62, "y1": 551.06 },
  "quote": "Gross foreign exchange reserves were placed at 668 at the end...",
  "page_number": 8, "grounded": true
}
```

Pages are rendered on demand and cached by document hash.

### The review queue

Nothing doubtful is discarded. Facts that fail a check are stored *and* queued:

| Issue type | Meaning |
| --- | --- |
| `unverified_quote` | Quote is not a substring of the source chunk |
| `ungrounded_quote` | Quote verified but not locatable in the PDF |
| `low_confidence` | Below `REVIEW_CONFIDENCE_THRESHOLD` |
| `ambiguous_unit` | Numeric value with no unit — the number is uninterpretable |
| `borderline_confidence` | Confidence in the 0.50–0.70 band |
| `extraction_failed` | The model call failed for that chunk |
| `quota_exhausted` | Daily model quota spent; the document is incomplete |

```bash
curl http://127.0.0.1:8000/review-queue
curl -X POST http://127.0.0.1:8000/review-queue/40/resolve   -H 'Content-Type: application/json'   -d '{"action":"edited","resolution_note":"Unit is Rs. crore per the table heading convention","correction":{"unit":"Rs. crore"}}'
```

`rejected` deletes the fact; `edited` writes corrections back. Grounding fields
(quote, page, bbox) are **not** editable — a fact whose quote is wrong should be
rejected, not patched into claiming evidence the PDF does not support.
