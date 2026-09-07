# FactPulse — Architecture

FactPulse is a **fact knowledge layer**. It takes PDFs, pulls discrete factual
claims out of them, ties every claim back to the exact span of source it came
from, and then compares claims *across* documents to work out whether they
agree, disagree, or only appear to disagree because they are talking about
different times, scopes, or units.

The interesting problem is not extraction. It is **reconciliation**: two
documents say "revenue was $4.2M" and "revenue was $5.1M", and the useful
answer is rarely "contradiction" — it is usually "different fiscal period",
"one is a segment and one is consolidated", "one is GAAP and one is not", or
"the second restates the first". Getting that right requires keeping enough
context attached to each fact to tell those cases apart, and that requirement
drives the whole data model below.

---

## 1. Pipeline

```
PDF upload
    │
    ├─ 1. ingest      sha256, dedupe, page count            → documents
    │
    ├─ 2. parse       PyMuPDF text + per-span coordinates
    │
    ├─ 3. chunk       page-aware windows                    → chunks
    │
    ├─ 4. extract     Gemini → candidate facts              → facts
    │                 each with a verbatim quote            → fact_attributes
    │                                                       → fact_types
    │
    ├─ 5. ground      locate quote on page, capture bbox    → facts.bbox_*
    │                 quote not found → review_queue
    │
    ├─ 6. embed       Gemini embeddings over statements     → embeddings
    │
    ├─ 7. link        cosine top-k → candidate pairs
    │                 Gemini adjudicates each pair          → relationships
    │
    └─ 8. review      low confidence / contradictions       → review_queue
```

Steps 5 and 7 are the two that make this more than a summarizer. Step 5 means
no fact exists in the system without a page and a rectangle you can point at.
Step 7 is where the cross-document judgement happens.

---

## 2. Layout

```
/backend
  app/
    main.py            FastAPI app, CORS, lifespan (applies schema on boot)
    core/config.py     pydantic-settings, reads .env
    db/
      schema.sql       the single source of truth for the schema
      database.py      sqlite3 connections, init_db(), FastAPI dependency
    api/               routers (health today; documents/facts/graph next)
    schemas/           pydantic models for every request and response
    services/          parsing, chunking, extraction, embedding, linking
  run.py               dev entrypoint
/frontend              Next.js App Router, TypeScript, Tailwind
/docs                  this file
```

**Backend** — Python 3.11, FastAPI, Uvicorn, SQLite, PyMuPDF (`fitz`),
`google-genai` for Gemini, numpy for vector math. All API request and response
bodies are pydantic models.

**Frontend** — Next.js (App Router) + TypeScript + Tailwind on port 3000,
talking to the backend on port 8000. Currently a scaffold.

### Why raw `sqlite3` rather than SQLAlchemy

The schema below is deliberately open: `fact_type` is an unconstrained string
and per-fact detail lives in a key/value table. An ORM's value is mapping rows
onto typed classes with known columns — which is exactly the thing this design
gives up on purpose. Declaring `class Fact(Base)` with a fixed column set would
reintroduce, in Python, the rigidity the SQL was written to avoid. Queries here
are a handful of hand-written statements, so `sqlite3` with
`row_factory = sqlite3.Row` is less code and less indirection. `schema.sql`
stays the one place the schema is defined.

---

## 3. Schema

```
documents(id, filename, title, sha256, uploaded_at, page_count, status)
chunks(id, document_id, page_start, page_end, text, token_count)
facts(id, document_id, chunk_id, fact_type, subject, statement,
      normalized_value, unit, time_scope, quote, page_number,
      bbox_x0, bbox_y0, bbox_x1, bbox_y1, confidence, created_at)
fact_attributes(id, fact_id, key, value)
fact_types(name PRIMARY KEY, first_seen_at, example_fact_id, fact_count)
embeddings(fact_id, vector BLOB, dim)
relationships(id, fact_id_a, fact_id_b, relationship_type, rationale,
              confidence, created_at)
review_queue(id, fact_id, chunk_id, issue_type, note, resolved, created_at)
```

### `documents`

One row per uploaded PDF. `sha256` is `UNIQUE`, so re-uploading the same file
is detected rather than silently duplicating every fact in it. `status` is free
text tracking pipeline position (`uploaded` → `parsing` → `chunked` →
`extracted` → `linked`, or `failed`).

### `chunks`

Text windows over a document, carrying `page_start`/`page_end` so a fact
extracted from a chunk already knows roughly where it lives before grounding
narrows it to an exact rectangle. `token_count` supports batching within model
context limits.

### `facts`

The core table. Three groups of columns:

**Identity** — `fact_type`, `subject`, `statement`. `statement` is the claim
rewritten as one self-contained sentence, so it survives being read outside its
source paragraph.

**Comparability** — `normalized_value`, `unit`, `time_scope`. These are the
three axes that decide whether two facts are even *comparable*, which is why
they are promoted to real columns rather than left in the EAV table: the
linking step filters and groups on them constantly, and they must be indexable.
`normalized_value` is `TEXT`, not `REAL`, because plenty of facts are not
numeric ("headquartered in Zurich", "rated AA-") and a numeric column would
force those into a NULL that means something different from "not applicable".

**Grounding** — `quote`, `page_number`, `bbox_x0..bbox_y1`, `confidence`. The
verbatim quote, the page it appears on, and its rectangle in PDF points. This
is what makes a fact auditable: the UI can render the page and highlight the
exact source. A fact whose quote cannot be located verbatim on its claimed page
does not get silently kept — it goes to `review_queue`.

### `fact_attributes`

Entity–Attribute–Value sidecar. Anything qualifying a fact that does not have a
column: `("segment", "EMEA")`, `("basis", "non-GAAP")`, `("restated", "true")`,
`("methodology", "survey n=1200")`. Indexed on `key` and on `(key, value)`.

### `fact_types`

A **registry**, not a constraint. Rows appear as a side effect of facts being
written. It answers "what kinds of facts does this corpus contain?" without a
`SELECT DISTINCT` over every fact, and gives the UI a `fact_count` and an
`example_fact_id` per type for free. Nothing enforces that `facts.fact_type`
appears here — it is a materialized view maintained on write, and a fact with
an unregistered type is a bug in the writer, not a constraint violation.

### `embeddings`

`vector` is a float32 numpy array stored via `.tobytes()`; `dim` records the
width so vectors can be read back correctly and so the embedding model can
change without a migration or a column type change. At this corpus size an
exhaustive cosine scan in numpy is fast enough; if that changes, this table is
the seam where a real vector index gets swapped in, and nothing else has to
move.

### `relationships`

The cross-document layer: an ordered pair of facts, a `relationship_type`, a
`rationale`, and a `confidence`. Typical types are `corroborates`,
`contradicts`, `reconcilable`, `refines`, `supersedes`, `unrelated` — but the
column is free text, for the reasons in section 4. A `UNIQUE` index on
`(fact_id_a, fact_id_b, relationship_type)` makes re-running the linking step
idempotent.

`rationale` is not decoration. A bare "contradicts" is not actionable; "both
report FY2024 revenue for the same entity, but one is consolidated and one
excludes the Asia segment" is. The rationale is the product.

### `review_queue`

Everything the pipeline was not confident about: ungrounded quotes,
low-confidence extractions, contradictions worth human adjudication, parse
failures, ambiguous scope. `fact_id` and `chunk_id` are both nullable because
an issue may attach to either level — a chunk that failed to parse has no fact
to point at.

---

## 4. Why `fact_type` is free text (and why `fact_attributes` is EAV)

This is the central design decision, so it is worth stating the reasoning
rather than just the rule.

**The constraint.** We do not know in advance what kinds of facts are in the
documents. A financial filing yields revenue figures, margins, and headcounts.
A clinical paper yields dosages, cohort sizes, and p-values. A regulatory
notice yields deadlines, jurisdictions, and thresholds. The set of fact kinds
is a property of the corpus, discovered at extraction time — not a property of
the application, decidable at design time.

**Why not an enum or a lookup FK.** Both work by declaring the set of valid
types up front. Every genuinely new kind of fact then becomes a schema change:
edit the enum, write a migration, redeploy, re-run extraction. The failure mode
in between is worse than the friction. Faced with a value it cannot express, an
extractor does one of two things, and both destroy information:

- it forces the fact into the nearest permitted type, so a
  `regulatory_deadline` is filed as `date` and the distinction is gone; or
- it drops the fact, and the fact is simply absent with no record that it was
  ever seen.

A closed vocabulary does not prevent unexpected facts from existing. It only
prevents you from finding out about them. Free text turns "a kind we did not
anticipate" from a write failure into a new row in `fact_types` — visible,
countable, and reviewable.

**Why EAV for the rest.** The same argument, one level down. Even within a
known type, the qualifiers vary: a revenue fact needs `basis` and `segment`, a
dosage fact needs `route` and `population`. Modelling that as columns means
either a wide table that is mostly NULL, or a table per fact type and a JOIN
whose shape depends on the data. `fact_attributes` lets each fact carry exactly
the qualifiers it has, and lets the linker ask a uniform question — *do these
two facts differ on any attribute?* — without knowing in advance what the
attributes are.

**What we give up, and what we do instead.** The database no longer validates
that `fact_type` is meaningful, or that a revenue fact has a `basis`. Those
guarantees move up a layer:

- `fact_types` makes the vocabulary *observable*. Near-duplicates (`revenue`
  vs `total_revenue`) show up as two rows with low counts, which is a review
  signal — the drift is visible instead of silent.
- `review_queue` catches what validation would have rejected, and routes it to
  a human rather than to an exception.
- The three axes that must be machine-comparable — value, unit, time scope —
  *are* real columns, precisely because they carry weight in the linking step.
  The open parts are the parts only humans and models read.

The trade is deliberate: **the schema stays fixed while the vocabulary
evolves.** Adding a new kind of fact costs one `INSERT`, not one migration.

This is also why the pydantic models type `fact_type` as `str` rather than as
an `Enum`. Validating it against a closed set at the API boundary would
reimpose exactly the constraint the SQL was written to avoid.

---

## 5. Reconciliation

Given two facts about the same subject, the linking step decides between
roughly:

| Type | Meaning |
| --- | --- |
| `corroborates` | Same claim, independently stated. Raises confidence. |
| `contradicts` | Incompatible, and the context does *not* explain the gap. |
| `reconcilable` | Differ, but the difference is explained by time, scope, unit, or basis. |
| `refines` | One is a more specific version of the other. |
| `supersedes` | A later statement replaces an earlier one. |
| `unrelated` | Looked similar under cosine; is not actually about the same thing. |

`reconcilable` is the category that earns the schema. Distinguishing it from
`contradicts` requires exactly the fields above: `time_scope` (FY2024 vs
CY2024), `unit` (thousands vs millions), `subject` plus `fact_attributes`
(consolidated vs segment, GAAP vs non-GAAP). A model given only the two
sentences has to guess. A model given the sentences *plus* their normalized
values, units, scopes, and attributes can state which axis they differ on —
and that statement goes in `rationale`, where a person can check it against the
two highlighted source rectangles.

---

## 6. Configuration

`backend/.env` (see `backend/.env.example`; `.env` is gitignored):

| Variable | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Gemini API key |
| `GEMINI_MODEL` | Extraction / adjudication model |
| `GEMINI_EMBEDDING_MODEL` | Embedding model |
| `EMBEDDING_DIM` | Embedding width, mirrored into `embeddings.dim` |
| `DATABASE_PATH` | SQLite file, default `factpulse.db` |
| `FRONTEND_ORIGIN` | CORS allowlist entry |

`frontend/.env.local` sets `NEXT_PUBLIC_API_BASE_URL`.

`.env` and `factpulse.db` are gitignored. The schema is applied on backend
startup via `init_db()`, which is idempotent (`CREATE TABLE IF NOT EXISTS`
throughout).

---

## 7. Current status

Scaffold. Working today:

- SQLite schema, applied automatically on boot
- `GET /health` — reports liveness, whether the schema is applied, the table
  list, and whether a Gemini key is present
- `GET /` — service banner
- pydantic models for documents, chunks, facts, attributes, fact types,
  relationships, and review items
- Next.js "Hello FactPulse" page with a live backend status indicator

Not built yet: PDF upload, parsing, chunking, extraction, grounding, embedding,
linking, and the UI beyond the landing page.
