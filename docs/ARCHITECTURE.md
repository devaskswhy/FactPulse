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

All eight steps are implemented.

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
      repository.py    SQL for documents and chunks
    api/
      health.py        GET /health
      documents.py     upload, list, inspect, extract, link, rechunk, delete
      facts.py         GET /facts, /facts/{id}, /evidence, /relationships,
                       /schema, /review
      review.py        GET /review-queue, POST /review-queue/{id}/resolve
      progress.py      GET /progress and the per-document SSE stream
    schemas/           pydantic models for every request and response
    services/
      pdf.py           PyMuPDF parsing, text cleanup, storage, quote grounding
      chunker.py       page-aware chunking with overlap
      extract.py       Gemini structured-output fact extraction
      pipeline.py      verify, ground, persist, route for review
      embed.py         fact statement embeddings, packed as float32 BLOBs
      link.py          cosine retrieval + batched relationship classification
      render.py        page PNG rendering, caching, and point->pixel scaling
      selfcheck.py     post-ingestion queries for ambiguous or borderline facts
      grounding.py     does the quote SUPPORT the claim, not just exist in it
      temporal.py      which of two facts describes the later state
      model_pool.py    rotate models when a daily free-tier quota is spent
      progress.py      in-memory phase/progress state for the SSE endpoint
      ingest_log.py    one JSONL line per run, for timing comparisons
      ingest.py        all eight steps wired together in one transaction
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
      bbox_x0, bbox_y0, bbox_x1, bbox_y1, confidence, created_at,
      evidence_strength, evidence_gaps)
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

## 5. Extraction and grounding

Steps 4, 5 and 8. `extract.py` calls the model; `pipeline.py` decides what to
trust and what to write.

### Structured output, not parsed text

The model is called with `response_schema`, so it returns typed JSON directly.
Nothing scrapes JSON out of a markdown fence, and there is no repair pass for
malformed output — a response that does not fit the schema is a failed call,
handled as one.

Two properties of that schema are load-bearing:

**`fact_type` is a free `STRING` with no `enum`.** The prompt gives no list of
candidate labels and no house vocabulary to match — only a format (lowercase
kebab-case, one to three words) and an instruction to invent whatever label
fits. Constraining it in the schema would defeat section 4 just as thoroughly
as constraining it in SQL, so it is left open in both places.

**`attributes` is an ARRAY of `{key, value}` pairs, not an object.** This is a
concession to the API, not a design preference. Gemini's response schema is an
OpenAPI subset with no way to describe an object with arbitrary unknown keys,
so an open-ended `attributes: {...}` cannot be expressed. The array form keeps
the keys completely unconstrained — which is the property that matters — and
maps one-to-one onto `fact_attributes`. `_coerce_attributes()` folds it back
into a dict, and also accepts a plain object in case a future model version
returns one.

### Quote verification

The model is asked for a verbatim quote. It is not trusted to have supplied
one.

`verify_quote()` matches case-insensitively with whitespace normalized, because
models reflow and re-case text they are quoting, and rejecting a fact over a
collapsed newline would be pedantry rather than verification. What it returns
is the span **as it appears in the chunk**, recovered through an index map back
to the original string — so the `quote` column holds the document's wording,
not the model's rendering of it.

A quote that cannot be found is the interesting case: it means the model
produced a plausible sentence that is not in the source. That is exactly the
failure this system exists to catch.

### Grounding

`locate_quote_bbox()` reopens the stored PDF and uses `page.search_for()`,
trying the chunk's page range first and the rest of the document second. The
box returned is the union of every rectangle the match spans, so a quote
wrapping across lines yields one box covering all of them.

Verification and grounding are kept separate because they fail for different
reasons. Chunk text has been cleaned — ligatures folded, hyphenation joined,
whitespace collapsed — so a quote can verify against the chunk and still not
match the raw glyph stream in the PDF. That is a missing highlight box, not a
fabricated quote, and it gets its own issue type.

When no box is found, `page_number` is set from the chunk only if the chunk
sits on a single page. For a multi-page chunk the page would be a guess, so it
stays NULL rather than being invented.

### Grounding is not the same as sufficiency

`verify_quote()` answers one question: is this quote a real substring of the
source? That catches invention and nothing else. It happily accepts a quote of
`Nil` attached to the sentence "Delhivery has no corrective action taken or
underway on issues related to anti-competitive conduct", because `Nil` really
is in the document, in a table cell whose meaning comes from a row label and a
column header the quote does not include.

That fact was stored with `confidence: 1.00`, a real bounding box, and a
`grounded: true` flag. Every signal the system had said it was fine.

So there is a second question, asked in `services/grounding.py`: does the quote
*support* the claim? A statement asserts a small number of components -- a
subject, a value, a period -- and a quote is sufficient to the extent that it
carries them. Only components the fact actually asserts are checked, so a fact
that claims no period is not penalised for a quote lacking one.

| Strength | Meaning |
| --- | --- |
| `full` | The quote states the claim on its own |
| `partial` | The quote carries the value but leans on surrounding context |
| `insufficient` | Read alone, the quote does not support the statement |

Three implementation choices worth stating.

**Local, not a model call.** The check is string and number comparison. It
costs no quota, adds no latency, and returns the same answer every time -- which
also means it could be backfilled across an already-ingested corpus. 335 facts
were assessed in 0.02 seconds with zero API calls. Re-extracting them to get
the same information would have cost hundreds.

**Three tiers, not a score.** "0.62 grounded" invites false precision.
full / partial / insufficient maps onto what a reviewer would actually do.

**Numeric comparison, not string matching.** A quote reading `576,188.2` has to
match a `normalized_value` of `576188.2`. Without comparing numerically the
check reports the value absent and under-grades a correct extraction -- a real
miss on this corpus before it was fixed.

Two rules keep the check from flagging ordinary prose. Running text routinely
omits or pronominalises its subject, so the subject is only required in short
quotes where there is genuinely no way to tell what the text is about. And a
document saying "the Company" has named its subject, so self-reference counts.

Measured over the working corpus of 335 facts:

| Strength | Facts |
| --- | --- |
| full | 129 |
| partial | 103 |
| insufficient | 103 |

Insufficient facts are queued as `weak_evidence`, kept and visible rather than
deleted, and both the fact list and the evidence viewer show the tier with the
specific gaps spelled out. A grade alone tells a reviewer nothing; "missing the
subject, the period, enough surrounding text to read as a claim" tells them
what to check.

The honest reading of that table is that under a third of extracted facts carry
their own evidence. That number was always true. What changed is that it is now
visible instead of hidden behind a green `grounded` flag.

### Nothing doubtful is discarded

A fact that fails verification or scores low confidence is **still written to
`facts`**, and additionally gets a `review_queue` row pointing at it. The
failure stays visible and queryable instead of vanishing between the model and
the database — which is the whole reason the review queue exists.

| Issue type | Meaning |
| --- | --- |
| `unverified_quote` | Quote is not a substring of the source chunk. The fact is not grounded in the document. |
| `ungrounded_quote` | Quote verified against the chunk but could not be located in the PDF, so it has no bounding box. |
| `low_confidence` | Model's own confidence is below `REVIEW_CONFIDENCE_THRESHOLD` (default 0.9). |
| `weak_evidence` | The quote is real but does not support the claim on its own. |
| `uncertain_supersession` | The classifier's direction and the dates in the two facts pointed opposite ways. The dates won; a human should confirm. |
| `extraction_failed` | The model call failed for a chunk. Recorded against the chunk, so the gap in coverage is visible. |

Both can apply to one fact, producing two rows.

The single exception is a fact returned with no type, no statement, or no quote
at all. There is nothing to store and nothing to review, so it is dropped in
`extract.py` — with a log line, not in silence.

### Calibrating the confidence threshold

The threshold is model-specific and has to be set against observed behaviour,
not guessed. Measured over the sample corpus, `gemini-3.6-flash` reports 1.0
for directly-stated facts and bottoms out near 0.70 for hedged prose
("preliminary figures suggest", "it is understood that"). It does not use the
low end of the scale at all.

A threshold of 0.5 therefore never fires, and a rule that never fires surfaces
nothing. The default is 0.9, which catches facts the model expressed a real
reservation about while leaving directly-stated ones alone. Re-tune it when the
model changes.

### Transient failures are retried

Model overload (503) and rate limiting (429) are retried with exponential
backoff and jitter, up to `EXTRACTION_MAX_ATTEMPTS`. Client errors such as 400
and 404 are not retried -- they will not succeed on a second attempt, and
retrying only wastes quota and delays the real error.

Without this, a momentary 503 permanently cost a chunk its facts, which
happened on the first real run.

### Failure isolation

One chunk failing does not lose the document. The failure is recorded against
the chunk and the run continues, ending in status `extracted_with_errors`
rather than `extracted`. Likewise a document whose extraction fails entirely
keeps its rows: ingestion is complete and useful without the model, so a
missing key leaves the document at `chunked` rather than failing the upload.

### Where extraction runs

Inline, in the upload request, right after chunking. That keeps upload-to-facts
a single call at the cost of a slow upload for a large document. A background
job is the obvious next move; `POST /documents/{id}/extract` already exists as
the re-run path and would become the queue's entry point.

---

## 6. Reconciliation: the relationship engine

Steps 6 and 7. `embed.py` produces vectors, `link.py` retrieves candidates and
judges them. Runs as a batch at the end of each document's ingestion, comparing
that document's new facts against everything already in the layer.

### Verdicts

| Type | Meaning |
| --- | --- |
| `corroborates` | Same underlying claim, independently stated. Wording or precision may differ. |
| `contradicts` | Genuinely incompatible under the *same* subject, scope, period, unit and basis. |
| `reconciled` | Looks like a conflict, but a named difference explains it — period, scope, unit, definition, or basis. |
| `supersedes` | Same property of the same subject at two different times, where a later document records a change. **Directional.** |
| `unrelated` | Similar enough to be retrieved, but not about the same thing. |

`reconciled` is the category the whole schema exists to serve, and the reason
`normalized_value`, `unit` and `time_scope` are real columns rather than EAV
rows: the classifier needs them side by side to tell "different period" from
"disagreement".

Unlike `fact_type`, this **is** a closed set, enforced by an `enum` in the
response schema. The distinction is deliberate. Fact types are a property of
the corpus and must grow; relationship types are the product's own vocabulary,
they are what the UI renders, and a model inventing one would silently break
that rendering. The database column stays free text so the set can grow
later without a migration — the constraint lives at the point of decision, not
in the schema.

`unrelated` verdicts are **not** stored. The table answers "what does this fact
relate to", and most retrieved candidates are unrelated; storing them would
bury the answer.

### Supersession: when the world changed rather than a source being wrong

A 2023 annual report lists a board of directors. A 2024 filing records that one
of them resigned. Under a four-way vocabulary the only honest label available
is `contradicts`, and that tells a reader something false — that one of the two
documents is wrong. Neither is. Both were true when written.

`supersedes` is the verdict for that, and it is the first one where **direction
is the content of the claim**. "A supersedes B" and "B supersedes A" are
opposite statements, so the pair can no longer be stored in whichever order it
arrived in. The invariant is that for a `supersedes` row, `fact_id_a` is the
current fact and `fact_id_b` is the one it replaced. `link.py` is the single
place that establishes it and everything downstream depends on it.

**Two independent signals decide the arrow, and the checkable one wins.** The
classifier returns a `current_fact` field naming which side is later. That is
an unverifiable judgement. But the facts themselves usually carry evidence a
program can read — a `time_scope`, an effective-from date, a year in the
statement — and `services/temporal.py` parses it. When the two disagree the
dates win, the rationale is prefixed `[direction corrected]` so the correction
is visible rather than silent, and the pair is queued as
`uncertain_supersession` for a human.

The parser exists because one period is written several ways across a single
corpus: `FY2024`, `2024-25`, `FY2024/25`, `31 March 2024`, `2024-03-31`,
`Q3 2024`. A fiscal span resolves to its **closing** year, so `FY2024-25` is
later than `FY2024`, and the two-digit tail is read as a suffix rather than a
year of its own — otherwise `1999-00` orders before the first century.

**It refuses to rank what it cannot rank.** `FY2024` and `March 2024` are not
ordered, because one contains the other; nor are `March 2024` and
`31 March 2024`. Different years settle it whatever else is missing, the same
year decides nothing unless both sides name a month, and the same month decides
nothing unless both name a day. A wrong ordering here does not produce a vague
answer, it produces a confident and inverted one, so every function would
rather return "cannot tell".

When neither the classifier nor the dates can name a direction, **nothing is
stored**. This is the one place the pipeline discards a judgement, and it is
consistent with the rule rather than an exception to it: a supersession without
an arrow is not a weaker claim, it is a different and unmade one, and defaulting
to either side would put a confidently backwards statement in front of a user.

On the read side, "has this fact been replaced?" is **derived, not stored**. A
`still_current` column on `facts` would need rewriting every time a document
arrived and would be wrong in between; `repo.superseded_by_map()` is one query
over an indexed column. `GET /facts` carries `superseded_by`, and
`GET /facts/{id}/relationships` carries `direction` — `outgoing` when the fact
being asked about is the current one, `incoming` when it is the one replaced.
The same stored row read from either end is the opposite claim, which is
exactly what a client needs to know before rendering a label.

### Retrieval: brute force, deliberately

Candidates come from cosine similarity over `embeddings`, loaded into one numpy
matrix and scored with a single vectorized dot product — O(n) per new fact.

That is the right call at this system's scale. At 1k facts by 768 float32 dims
the entire matrix is ~3 MB and the multiply is sub-millisecond, so a vector
index would add operational weight for no measurable gain. It stops being
right somewhere in the high tens of thousands, where reloading every vector per
fact begins to dominate. **FAISS** (in-process), **pgvector** (if the store
moves to Postgres), or **Qdrant** (standalone) are the natural upgrade paths.
`embeddings` is the only table that would change, and `find_candidates()` is
the only caller.

Facts from the *same* document are excluded. Two sentences in one report
restating the same figure is a property of that document's prose, not a
cross-document corroboration.

`SIMILARITY_THRESHOLD` (0.75) and `TOP_K` (8) trade model calls against recall:
too high and a real contradiction phrased differently never reaches the
classifier; too low and the classifier spends its budget rejecting noise. Both
are measured starting points, not derived constants.

### Normalisation is not optional

Gemini returns unit-length vectors at its native 3072 dimensions, but a
*truncated* output is not renormalised for you — `gemini-embedding-001` at 768
dims comes back with a norm around 0.58. Every vector is therefore normalised
on write, which also makes similarity a plain dot product and keeps retrieval a
single matrix multiply.

### One call per fact, not per pair

The first implementation judged each pair in its own request. At top-k
candidates per fact that is k calls per fact, and it exhausted a free-tier
**daily** quota on a two-document corpus — 21 calls for 4 new facts.

Since every candidate is compared against the *same* fact A, they share one
request: the model sees A once and returns a verdict per candidate, keyed by
index. That is O(facts) calls instead of O(facts x k) — 26 pairs judged in 4
calls — and it lets the model see all candidates together, which helps it pick
the closest match rather than judging each in isolation.

A candidate the model returns no verdict for is skipped and counted as an
error. Defaulting it to `unrelated` would silently bury a pair; defaulting it
to anything else would invent a claim.

### Quota errors are not all alike

A 429 from a per-minute rate limit is worth retrying. A 429 from a **daily**
quota is not — no backoff we would sit through will clear it, so retrying just
burns four attempts and thirty seconds before failing anyway. Gemini names the
quota in the error body (`GenerateRequestsPerDayPerProjectPerModel`), which is
how the two are distinguished. A daily-quota error stops the linking run
immediately rather than grinding through the remaining facts.

Quotas are metered **per project, per model, per day**, and an API key belongs
to a project. So the unit that actually runs out is neither a key nor a model
but the **pair** of them, and that pair is what `services/model_pool.py` hands
out as a `Slot`. Three keys against seven models is twenty-one independent
daily allowances rather than seven.

**Keys rotate before models.** Given models [A, B] and keys [1, 2, 3] the order
is A/1, A/2, A/3, B/1, B/2, B/3. Every key is tried on the preferred model
before the pipeline settles for a lesser one; exhausting one key across all its
models first would degrade output quality while a fresh key still had the good
model available.

Two consequences shape the code. A client is **never pinned for the length of a
run** — building one at the top of `ingest` or `link_document_facts` would pin
its key too, so a quota that ran out mid-document could not rotate. Each call
resolves its own slot and looks up a cached client for that key. And the
exhausted set is process-global, keyed on `(key index, model)`: exhaustion is a
property of the account and the day, not of one request, so a document that
burns through a slot does not leave the next document to rediscover it.

Embeddings get the same treatment for half the reason. There is exactly one
embedding model and no substitute, so the model fallbacks cannot help — but a
second key still can, and `slot_for()` walks the keys for a single model.
Embedding quota is metered separately from generate quota, so exhausting one
says nothing about the other.

`GET /health` reports slots as `key1/model-name`. **No key, and no fragment of
one, appears in that payload or in any log line** — the endpoint is
unauthenticated and its output is the sort of thing that gets pasted into an
issue.

### Known limits

- **Re-running re-judges unrelated pairs.** The skip-already-judged guard reads
  the `relationships` table, and `unrelated` verdicts are not stored, so a
  second run pays to reach the same conclusion about them. Storing negatives
  would fix the cost and defeat the table's purpose; a separate
  judged-pairs ledger is the real fix if re-runs become common.
- **Linking runs inline in the upload request**, so a document with many facts
  makes for a slow upload.
- **New facts are compared against older ones, not vice versa.** A relationship
  is discovered when the *second* of a pair is ingested. Re-running
  `POST /documents/{id}/link` on an older document picks up anything added
  since.

---

## 7. Evidence bundles and the review queue

### Evidence: coordinates the frontend can use directly

`GET /facts/{id}/evidence` returns the fact, the URL of a rendered page image,
and the highlight box **in pixels of that image** — not PDF points.

PDF geometry is in points (1/72 inch) and the image is in pixels, so something
has to convert. Doing it in the browser means shipping the render scale to
every client and trusting each to apply it identically; doing it server-side
means the API hands over coordinates already correct for the image it also
serves. `render.py` owns both halves so they cannot drift apart, and the cache
filename includes the scale — changing `PAGE_RENDER_SCALE` cannot serve a stale
image whose pixels no longer match the coordinates.

`bbox_pdf_points` is returned alongside for reference. `page_image_width` and
`page_image_height` let a frontend that scales the image to fit scale the box
by the same ratio.

Page images are rendered on demand and cached under the document's hash. The
source PDF is immutable and content-addressed, so the image is too, and the
response is marked `immutable` for a year.

A fact whose quote was never located is still returned, with `grounded: false`
and a null bbox, rather than 404. An ungrounded fact is exactly what a reviewer
needs to see.

### A grounding bug worth recording

The first version of `locate_quote_bbox()` looped pages outer and candidate
strings inner: for each page, try the full quote, then progressively shorter
leading fragments. That is wrong, and quietly so.

In a report that repeats boilerplate — "Gross foreign exchange reserves were
placed at ..." appears on 40 of 60 pages here — the five-word fallback matches
almost everywhere, while the full quote matches exactly one page. Looping pages
outer meant an early page's *fallback* match won before the correct page's
*exact* match was ever attempted. The result was a highlight over the
right-looking sentence on the wrong page, showing a different number than the
fact claimed. 13 of 56 facts in the test document were affected.

The fix is to exhaust the most specific candidate across every page before
falling back to a shorter one — candidates outer, pages inner.

`POST /documents/{id}/reground` recomputes page and bbox from stored quotes.
Grounding is deterministic and uses no model calls, so it is free to re-run
after a fix like this one; only the grounding columns move.

### The review queue as a workflow

`GET /review-queue` returns unresolved items each carrying the context needed
to judge it without a second request: the fact with its confidence and values,
the chunk text a failed call came from, or the two facts a relationship
judgement was made between. Every fact-bearing entry includes an `evidence_url`.

`POST /review-queue/{id}/resolve` takes `{action, resolution_note}`:

| Action | Effect |
| --- | --- |
| `accepted` | The fact stands as extracted. |
| `rejected` | The fact is deleted; attributes, embedding and relationships cascade. |
| `edited` | The supplied `correction` is written onto the fact. |

Grounding fields are deliberately **not** editable. Quote, page and bbox are
derived from the document; letting a reviewer type over them would let a fact
claim evidence the PDF does not support, which is the opposite of the point. A
fact whose quote is wrong should be rejected, not patched.

Resolving an already-resolved item is a 409 rather than a silent overwrite, so
two reviewers cannot quietly clobber each other's decisions.

Note that `rejected` removes the review row along with the fact, via the
existing cascade. The decision is reported in the response but does not persist
as a resolved row — there is no fact left for it to describe.

### The self-check

Extraction catches quotes that do not verify and confidence below the review
threshold. Two quieter problems have no single step positioned to notice them,
so a pass runs over the finished document:

| Issue | Rule |
| --- | --- |
| `ambiguous_unit` | `normalized_value` is numeric but `unit` is empty |
| `borderline_confidence` | confidence in [0.50, 0.70) |

Both are queries over what was written — no model calls, so the step is free
and runs unconditionally, including when extraction was skipped.

`ambiguous_unit` deliberately excludes ISO dates and bare years: those are
numeric in form but complete without a unit, and flagging them would fill the
queue with noise nobody can action.

This is the check that earns its place in practice. In the 60-page test report
it flagged 13 facts, and the cause was real: `Table 12. State-wise gross
capital formation` omits the `(Rs. crore)` that its sibling tables carry, so
the model extracted `32,507.0` with nothing to interpret it by. The number is
correct and useless — precisely a case for a human, and precisely what
`edited` exists to fix.

`borderline_confidence` overlaps with `low_confidence` at the current 0.9
threshold, since anything in [0.5, 0.7) is also below 0.9. Both fire; the
borderline label is the more specific signal. Lowering
`REVIEW_CONFIDENCE_THRESHOLD` below 0.7 separates them.

---

## 8. Incremental multi-document ingestion

The layer is meant to accumulate. Adding the Nth document must cost what
adding the first did, plus one comparison pass against what is already there --
never a reprocessing of it.

### What a new document touches

| Step | Scope |
| --- | --- |
| parse / chunk | the new file only |
| extract | the new document's chunks only |
| embed | the new document's facts, and only those without a vector |
| link | the new facts as the "A" side; existing facts are **read**, never rewritten |
| self-check | the new document's facts only |

Existing facts gain relationships -- their `relationships` list grows to
include the new matches -- but no existing fact is re-extracted, re-embedded,
or re-classified as the subject of a comparison.

### The one place this was wrong

`find_candidates()` originally took a connection and loaded every embedding in
the corpus itself. It is called once per new fact, so ingesting a document with
200 facts against a pool of 1000 meant 200 full corpus reads and 200,000 BLOB
unpacks -- O(new x existing), quadratic in the size of the knowledge layer and
the worst scaling property the pipeline had.

The pool is now loaded once per document by `load_candidate_pool()` and passed
in. Measured on a 55-fact document: **1 corpus load instead of 55**, 0.3 ms to
build the pool and 0.01 ms per similarity search. The pool is a snapshot, which
is sound because the facts in it belong to already-ingested documents and
cannot change while this document is being linked.

Nothing else needed fixing. `resync_fact_types()` does scan all facts, but it
runs only on a `replace=true` re-extraction or a document delete, never on a
normal ingest.

### bulk_ingest.py

`backend/scripts/bulk_ingest.py` ingests a folder one file at a time through
`ingest_pdf()` -- the same function the API route calls, not a parallel
implementation. The HTTP layer only reads the upload and maps exceptions to
status codes; everything below is shared.

```
python scripts/bulk_ingest.py ../samples/starter-datasets/india-macroeconomy --pages 1-12
python scripts/bulk_ingest.py <folder> --dry-run     # chunk counts, no calls
```

`--pages` crops each PDF before ingesting, producing a genuinely smaller
document so page counts and bounding boxes stay consistent with what was
stored. It carries the source metadata across, since `insert_pdf` copies pages
but not document metadata.

### Document titles

`_infer_title()` returns the PDF's metadata title or nothing. Guessing from
body text was implemented and then removed: these documents are excerpts, so
page one is typically a contents page or a running header, and every heuristic
tried still produced labels like "Page No.", "BSE Limited" and
"Phiroze Jeejeebhoy Towers,". A wrong title is worse than none because it looks
authoritative. The filename is always returned alongside and is the honest
fallback.

---

## 9. Large-PDF performance

Profiled against `02-delhivery-annual-report-fy24-excerpt.pdf` -- 100 pages,
6.5 MB, 221 chunks, ~152k estimated tokens.

### Where the time actually goes

| Stage | 100 pages | Share |
| --- | --- | --- |
| PDF text extraction | 1.7s | ~0.4% |
| Chunking | 0.01s | negligible |
| **Gemini extraction calls** | **minutes** | **~99%** |
| Embedding | one batched call per 100 facts | small |
| Relationship classification | one call per fact with candidates | minutes |

Everything that is not a network call is already fast. The work went where the
time is.

### Parallel page parsing: measured and rejected

The obvious first move is a thread pool over pages. It was implemented,
measured, and removed, because it made things **worse**:

| Workers | 100-page text extraction |
| --- | --- |
| 1 | 1.60s |
| 2 | 2.11s |
| 4 | 3.43s |
| 8 | 3.52s |

Monotonically worse. PyMuPDF's text extraction does not release the GIL, so
extra threads add contention and buy no parallelism. Opening a second handle is
not the cause -- `fitz.open()` measures at 1 ms. Rendering behaves the same way:
2 threads gained 9%, 4 threads were twice as slow.

Real parallelism would need processes, and it is not worth it: 1.7s against an
ingest dominated by minutes of model calls is 0.4% of the total. `_extract_pages()`
carries these numbers in a comment so the idea is not re-attempted.

### Bounded-concurrency extraction: the actual fix

`extract_chunks_concurrently()` runs the per-chunk model calls through a thread
pool of `EXTRACTION_CONCURRENCY` (default 5). Results are returned in input
order regardless of completion order, so fact ids stay deterministic, and
**database writes stay on the calling thread** -- only the network calls fan
out, so the SQLite connection is never touched concurrently. A chunk that fails
carries its exception rather than raising, so one bad chunk cannot lose the
document.

Threads rather than asyncio, deliberately. These are I/O-bound HTTPS requests,
so N workers give exactly the bounded concurrency an asyncio semaphore would.
The difference is at the boundaries: the ingest chain is synchronous and is
called from a sync script, a sync route, and an async one. `asyncio.run()`
raises inside a running event loop, so an async implementation would need a
thread-with-its-own-loop shim for the async caller anyway. Threads all the way
down is the same concurrency with one fewer moving part.

Scaling of the mechanism itself, with model latency mocked at the measured
1.9s so the numbers are not confounded by rate limits:

| Workers | 40 chunks | Speedup |
| --- | --- | --- |
| 1 | 76.0s | 1.00x |
| 3 | 26.6s | 2.86x |
| 5 | 15.2s | 5.00x |
| 8 | 9.5s | 8.00x |

Linear, as expected for I/O-bound work.

### Live API: 3x on a burst, unproven at scale

| Run | Chunks | Concurrency | Wall | Per chunk |
| --- | --- | --- | --- | --- |
| 12-page slice | 10 | 1 | 58.3s | 5.83s |
| 12-page slice | 10 | 5 | 18.9s | **1.89s** |
| 100 pages | 221 | 5 | 1339.5s | 6.05s |
| 100 pages | 37 of 221 | 1 | 297.8s | 8.02s (incomplete) |

**3.08x on the short burst**, clean, zero failures.

**On the sustained 221-chunk run the gain does not hold.** Per-chunk time
degraded from 1.89s to 6.05s, and notably **no 429s were returned** -- chunk
density does not explain it either (645 vs 690 mean tokens, ~9 facts per chunk
in both). Free-tier throughput appears to be paced server-side over a sustained
load, in a way the client cannot see or retry around.

The sequential 100-page baseline that would settle this **could not be
completed**: it exhausted the daily quota after 37 of 221 chunks. Its 8.02s per
chunk is a 37-sample figure that also includes backoff incurred while the quota
condition was being detected, so it is suggestive of a modest gain at sustained
scale and no more than that.

So: the 3.08x is real for bursts and must not be quoted for a 100-page
document. The mocked scaling above is what a paid tier should deliver, since
there the pacing constraint is removed.

An unintended demonstration: the quota short-circuit worked exactly as
designed. The sequential run detected the exhausted quota, wrote **one** review
entry naming 184 unprocessed chunks, set status `extraction_incomplete`, and
stopped in 5 minutes rather than grinding through 184 doomed calls.

### Retry that actually waits long enough

Gemini returns a `retryDelay` in the error body ("retry in 59s" for a
per-minute cap). The original backoff was exponential capped at 30s, so every
attempt landed inside the still-closed window and the call failed anyway. The
hint is now honoured (capped at 70s) across extraction, embedding and
classification. Per-day quota is still never retried -- nothing we would wait
through clears it.

### Candidate retrieval

Already fixed in section 8: the embeddings matrix is loaded once per document
rather than once per fact. Measured at 1 corpus load instead of 55 on a 55-fact
document, 0.3 ms to build the pool and 0.01 ms per similarity search.

### Progress reporting

`app/services/progress.py` publishes phase-by-phase state, and
`GET /documents/{id}/progress/stream` serves it as SSE. Phases are `parsing`,
`chunking`, `extracting`, `embedding`, `linking`, `checking`, then `done` or
`failed`, each with an N-of-M counter and running tallies of pages, chunks,
facts and relationships.

Progress is reported per phase rather than as one global percentage. A global
bar would have to predict extraction time, which depends on model latency and
how hard the rate limiter pushes back, so it would either lie or stall.
"extracting: chunk 84 of 221" is honest and is what a viewer needs during a
multi-minute wait.

The stream emits on every change and at least every two seconds regardless, so
a client can show a live elapsed timer through a long call where no discrete
step completes.

### The timing log

Every ingest appends one JSON line to `backend/storage/ingest_log.jsonl`:
pages, chunks, facts, relationships, per-phase seconds, model, concurrency and
final status. JSONL rather than a table because these are append-only
observations about runs, not part of the knowledge layer -- profiling should
not be able to interfere with the data being profiled.

---

## 10. Configuration

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

## 11. Current status

All eight pipeline steps are implemented: ingest, parse, chunk, extract,
ground, embed, link, review.

**Working**

- `POST /documents` runs the whole pipeline: dedupe, parse, chunk, extract
  facts, ground each quote to a page and box, embed, and relate the new facts
  to everything already in the layer
- `GET /facts`, `GET /facts/{id}` — facts with grounding and attributes
- `GET /facts/{id}/relationships` — related facts with verdict, rationale,
  and enough of the other fact and its source to render a comparison in one
  request
- `GET /schema` — the fact_types registry, the vocabulary the corpus produced
- `GET /review-queue` — the review queue with full context, and
  `POST /review-queue/{id}/resolve` to accept, reject, or edit
- `GET /facts/{id}/evidence` — page image plus a pixel-space highlight box
- `GET /documents/{id}/pages/{n}/image` — rendered, cached page PNG
- `POST /documents/{id}/extract` and `/link` — re-run either step
- Document CRUD, chunk listing, original-PDF download, rechunk
- `GET /health` — liveness, schema state, Gemini key presence
- Next.js "Hello FactPulse" page with a live backend status indicator

**Not built yet**

The UI beyond the landing page. Everything the frontend needs is served.

### Notes on ingest

- **Parse before write**, so an unreadable upload leaves no half-created row.
- **Content-addressed storage** at `uploads/<sha256>.pdf`; grounding and the
  viewer both need the original. `DELETE` leaves the file alone.
- **Dedupe answers 200, not 201** — nothing was created.
- **Scanned PDFs get 422** naming OCR, distinct from 400 for non-PDF bytes.
- **Token counts are estimates** (~4 chars/token), used only to pick cut points.
- **Chunk overlap carries page attribution**, so a fact found in repeated text
  still points at the right page.

### Model availability

Model names go stale faster than code. `gemini-2.5-flash` and
`text-embedding-004` were both specified during development and neither is
available to new API keys — the first returns 404 pointing at a successor, the
second is absent from `models.list()` entirely. Current defaults are
`gemini-3.6-flash` and `gemini-embedding-001`. If a call 404s, list what the
key can actually reach before assuming the code is wrong.

### Cost and quota

The pipeline makes, per document: one model call per chunk (extraction), one
embedding call per batch of 100 facts, and one model call per fact that has
candidates (linking). Free-tier daily quotas are per model, so switching
`GEMINI_MODEL` gives a fresh allowance.
