# FactPulse

[![CI](https://github.com/devaskswhy/FactPulse/actions/workflows/ci.yml/badge.svg)](https://github.com/devaskswhy/FactPulse/actions/workflows/ci.yml)

A **fact knowledge layer**. FactPulse extracts facts from PDFs, grounds each
one in the exact source span it came from, and detects whether facts across
documents **corroborate**, **contradict**, can be **reconciled** through
context — time period, scope, or units — or **supersede** one another when a
later document records that the situation changed.

> Two documents say revenue was $4.2M and $5.1M. The useful answer is rarely
> "contradiction" — it is usually "different fiscal period" or "one is a
> segment, one is consolidated". FactPulse is built to tell those apart, and to
> show you the page and rectangle each claim came from.

All four required cases are reproducible in the running app against the
committed corpus, and none of them is hardcoded anywhere in the pipeline:

| Case | Example |
| --- | --- |
| **Corroborates** | RBI and the IMF both report FY2024-25 headline inflation at 4.6%, in wording that shares almost no phrasing |
| **Contradicts** | RBI says global growth was 3.5% in 2023; the Economic Survey says 3.3%. Same metric, same period, different WEO vintages — and neither document says so |
| **Reconciled** | RBI's *actual* 3.3% global growth for 2024 vs the Survey's IMF *projection* of 3.2% for the same year — reconciled by definition, not by period |
| **Failure** | A waste-intensity figure of 23.3 extracted correctly with no unit, because the table never states one. Caught by the self-check, not discarded |

Exact fact IDs are in [docs/DEMO_CASES.md](docs/DEMO_CASES.md).

---

## Setup and Run Instructions

**Requirements:** Python 3.11+, Node 18+, and a free Gemini API key.

### 1. Get a free Gemini API key

Go to **<https://aistudio.google.com/apikey>**, sign in with a Google account,
and click *Create API key*. No billing setup is required — the free tier is
enough to run this project. Copy the key; you will paste it into `.env` below.

### 2. Backend — http://127.0.0.1:8000

```bash
cd backend

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt

cp .env.example .env            # then open .env and set GEMINI_API_KEY

uvicorn main:app --reload
```

The SQLite schema is created automatically on first start, and so is the
demo corpus — a fresh database has no facts yet, and the backend notices that
and loads the committed 335-fact corpus with zero model calls (see
"The offline demo seed" below). Check it came up:

- Health: <http://127.0.0.1:8000/health>
- Interactive API docs: <http://127.0.0.1:8000/docs>
- `curl http://127.0.0.1:8000/facts?limit=1` should already report `"total": 335`

### 3. Frontend — http://localhost:3000

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:3000>. Scroll past the explainer to reach the app.

`frontend/.env.local` is optional — the API URL defaults to
`http://127.0.0.1:8000`. Copy `.env.local.example` to `.env.local` only if your
backend runs elsewhere.

### 4. Add a document

Use the **Upload** screen in the UI, or:

```bash
curl -F "file=@samples/starter-datasets/india-macroeconomy/02-rbi-annual-report-2024-25-excerpt.pdf" \
     http://127.0.0.1:8000/documents
```

A 100-page report takes several minutes and roughly one model call per chunk.
On the free tier, **start with a 10–15 page slice** — see *Limitations*. To
ingest a folder with a page limit:

```bash
cd backend
python scripts/bulk_ingest.py ../samples/starter-datasets/india-macroeconomy --pages 1-12
python scripts/bulk_ingest.py <folder> --dry-run   # chunk counts, no API calls
```

### 5. Tests

```bash
cd backend
pytest
```

96 tests. Most cover ingestion, quote grounding, evidence sufficiency,
temporal ordering, supersession direction, canonical subject resolution, the
offline demo seed, the review queue and key/model rotation with the model
stubbed, so they are deterministic and need no API key. Five exercise the
live relationship classifier on synthetic inputs — including the brief's own
resigned-director case — and they **skip cleanly** when no key is configured
or every model's daily free-tier quota is spent.

---

## Video Demo

**[Demo video — link to be added]**

A shot-by-shot script is in [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md), built
against the specific facts catalogued in
[docs/DEMO_CASES.md](docs/DEMO_CASES.md), so nothing has to be found live on
camera.

---

## Approach

### The fact knowledge layer

A document goes through eight steps. Each writes to its own table, so any stage
can be re-run without redoing the ones before it.

```
PDF → parse → chunk → extract → ground → embed → link → review
        │       │        │         │        │      │       │
    documents chunks   facts    bbox on  embeddings │  review_queue
                  fact_attributes  facts      relationships
                    fact_types
```

The interesting problem is not extraction. It is **reconciliation** — deciding
whether two numbers that differ actually disagree. That requirement drives
every design decision below.

### Why `fact_type` is free text with EAV attributes, not a fixed schema

`facts.fact_type` is an unconstrained `TEXT` column. There is no enum, no
lookup table, and no allowed-values list in the extraction prompt. Per-fact
qualifiers live in `fact_attributes(fact_id, key, value)` — an
entity-attribute-value sidecar whose keys are equally unconstrained.

**Why:** we cannot know in advance what kinds of facts are in the documents. A
financial filing yields revenue and headcount; a clinical paper yields dosages
and cohort sizes; a regulatory notice yields deadlines and thresholds. The set
of fact kinds is a property of the *corpus*, discovered at extraction time —
not a property of the *application*, decidable at design time.

A closed vocabulary does not prevent unexpected facts from existing. It only
prevents you from finding out about them. Faced with a value it cannot express,
an extractor either files a `regulatory_deadline` as `date` and loses the
distinction, or drops the fact entirely — and both destroy information
silently. Free text turns "a kind we did not anticipate" into a new row in the
`fact_types` registry: visible, countable, reviewable.

Running the three macro documents plus two Delhivery extracts produced **179
distinct fact types**, including `waste-intensity`, `emission-intensity`,
`current-account-deficit` and `bond-yield-trend`. None of those strings appears
anywhere in the codebase.

**What we give up, and what replaces it.** The database no longer validates
that a type is meaningful. Those guarantees move up a layer: `fact_types` makes
the vocabulary *observable* — near-duplicates show up as two low-count rows,
which is a review signal — and `review_queue` catches what validation would
have rejected and routes it to a human instead of to an exception.

Crucially, the three axes that must be **machine-comparable** —
`normalized_value`, `unit`, `time_scope` — *are* real indexed columns, because
the linking step filters and groups on them constantly. The open parts are the
parts only humans and models read. Adding a new kind of fact costs one
`INSERT`, not one migration.

### A contradiction and a change are not the same thing

A 2023 annual report lists a board of directors. A 2024 filing records that one
of them resigned. With only corroborates / contradicts / reconciled to choose
from, the closest label is `contradicts` — which tells a reader that one of the
two documents is wrong. Neither is. Both were true when written.

`supersedes` is the fifth verdict, and the only one where **direction is the
claim**. "A supersedes B" and "B supersedes A" are opposite statements, so the
arrow has to be right.

Two signals decide it, and the checkable one wins. The classifier names which
side is later; that is a judgement. The facts themselves usually carry a
`time_scope` or a date in the statement; that is evidence, and
`services/temporal.py` reads it. When they disagree, the dates win, the
rationale is prefixed `[direction corrected]` so the correction is visible, and
the pair is queued for a human as `uncertain_supersession`.

The parser handles the several ways one period is written across a single
corpus — `FY2024`, `2024-25`, `31 March 2024`, `2024-03-31`, `Q3 2024` — and,
more importantly, refuses to rank what it cannot rank. `FY2024` and
`March 2024` are not ordered, because one contains the other. When neither the
classifier nor the dates can name a direction, nothing is stored: a
supersession without an arrow is not a weaker claim, it is a different and
unmade one, and guessing would put a confidently backwards statement in front
of a user.

Superseded facts are **kept and marked**, not filtered out — they were true
once, and that history is the point. The fact list shows a `superseded` badge
and the comparison view strikes through whichever card is no longer current.

> The committed corpus contains no supersessions, and that is the correct
> answer for it: macroeconomic and ESG reports disagree about *measurements*,
> which is contradiction or reconciliation, not change. Supersession needs
> documents that restate a **current state** — a board, a registered office, a
> credit rating — across two dates. The behaviour is covered by tests against
> the live model in `tests/test_relationships.py`.

### The same entity, written two ways

Two documents call the same company "Delhivery" and "Delhivery Limited". This
isn't hypothetical — it's the committed corpus's own sustainability report,
where both spellings sit a few facts apart. A subject search for one used to
miss the other; a grouped view split one company into two rows.

`services/entity.py` computes a matching key at write time — mechanical
differences only (case, punctuation, a legal-form suffix, an address
abbreviation like `St`/`Street`), never a semantic guess. "RBI" and "Reserve
Bank of India" are deliberately **not** merged: that identity isn't verifiable
from the string, and guessing risks merging two facts that aren't actually
about the same subject.

**See it live:** Fact Explorer → group by `subject` → the "Delhivery" group
carries a `2 spellings merged` badge. Or `GET /subjects` for the registry
view, which mirrors `GET /schema`'s "observed, not designed" framing — this
lists the subject identities the corpus actually produced, not ones anyone
declared in advance.

### Grounding is not the same as sufficiency

Most systems that cite a source stop at "the quote exists". This one asks a
second question, because the first is not enough.

`verify_quote` proves a quote is a real substring of the document. It cannot
tell that a quote of `Nil` does not support the sentence "Delhivery has no
corrective action taken or underway on issues related to anti-competitive
conduct" -- and that fact was stored with confidence 1.00, a real bounding box,
and a green "grounded" flag.

So every fact is also graded on whether the quote *carries* the claim: does it
contain the value asserted, the subject, the period. Only components the fact
actually asserts are checked. The result is three-valued -- full, partial,
insufficient -- because a score like "0.62 grounded" invites false precision
while three tiers map onto what a reviewer would do.

The check is local string and number comparison, no model call. That is what
made it possible to backfill across an already-ingested corpus: **335 facts
assessed in 0.02 seconds with zero API calls.**

| Strength | Facts |
| --- | --- |
| full | 129 |
| partial | 103 |
| insufficient | 103 |

Under a third of extracted facts carry their own evidence. That was always
true; it is now visible in the UI and queued for review instead of hidden.

### Why SQLite + brute-force cosine, not a vector database

Embeddings are stored as float32 BLOBs in a SQLite table. Candidate retrieval
loads every vector from *other* documents into one numpy matrix and scores it
with a single dot product.

At this scale that is simply the right answer. 1,000 facts × 768 dims is ~3 MB;
the multiply is sub-millisecond. Measured on a 55-fact document: **0.3 ms** to
build the pool, **0.01 ms** per similarity search. A vector database would add
a service to run, a schema to keep in sync, and a failure mode to debug, in
exchange for nothing measurable.

**The trade-off, honestly:** this is O(n) per new fact and does not scale. It
is fine into the low thousands of facts and stops being fine somewhere in the
high tens of thousands, when reloading every vector per document begins to
dominate. FAISS (in-process), pgvector (if the store moves to Postgres), or
Qdrant (standalone) are the upgrade paths. `embeddings` is the only table that
would change, and `load_candidate_pool()` / `find_candidates()` are the only
callers — the seam is deliberately narrow.

One scaling bug was found and fixed along the way: the pool was originally
loaded *per fact* rather than per document, making ingestion O(new × existing)
BLOB unpacks. Hoisting it out took a 55-fact document from 55 corpus loads to 1.

### Why Gemini

- **A free tier that can actually run this.** No billing setup, which matters
  for a project someone else has to reproduce.
- **Structured output via `response_schema`.** The extractor returns typed JSON
  directly against a schema built from the SDK's types — no markdown fence to
  strip, no JSON-repair pass. This is load-bearing: `fact_type` is declared as
  a free `STRING` with no `enum`, so the schema constrains the *shape* without
  constraining the *vocabulary*.
- **Long context.** A 900-token chunk plus instructions is comfortable, and
  batching a fact's whole candidate shortlist into one classification call is
  what keeps the relationship engine affordable.
- **Native PDF handling** was a factor in choosing it, though this
  implementation does not use it: PyMuPDF parses locally, which keeps the
  bounding-box grounding exact and avoids re-uploading documents per call.

One concession the API forced: Gemini's response schema is an OpenAPI subset
with no way to describe an object with arbitrary unknown keys, so open-ended
`attributes` travel as an **array of `{key, value}` pairs** rather than an
object. The keys stay unconstrained — the property that matters — and the shape
maps one-to-one onto the EAV table.

### Claude Code was used throughout

This project was built with Claude Code (Opus 5) as the implementation tool,
across eleven prompts, from the initial scaffold to this README. To be specific
rather than vague about what that means:

- **I specified, it implemented.** Every architectural decision above — free
  text `fact_type`, EAV attributes, SQLite over a vector DB, evidence-first
  comparison over a graph view — was specified in the prompts. Claude Code
  wrote the code, and flagged trade-offs where an instruction and the evidence
  disagreed.
- **It found bugs by running things, not by reading them.** Several defects in
  this repository were caught by Claude Code driving the system and measuring,
  then reported rather than quietly patched. The notable ones: a quote locator
  that matched a short fallback on the wrong page (13 of 56 facts affected); an
  `async def` upload route that blocked the event loop and made the SSE
  progress stream — the very thing meant to report on it — unresponsive; an
  embedding call with no retry that silently lost a whole document's vectors to
  a per-minute rate limit; and a preloader that was invisible in development
  because of a React StrictMode double-invoke.
- **It reported negative results.** A parallel page-parsing pass was
  implemented, measured (1 worker 1.60s → 4 workers 3.43s, because PyMuPDF does
  not release the GIL), and removed — with the numbers recorded in the code so
  the idea is not re-attempted. A 3× concurrency speedup measured on a short
  burst was explicitly *not* claimed for a 100-page document, because the
  sustained run did not reproduce it.
- **The git history is the record.** Each commit corresponds to a prompt, and
  its message explains what was decided and why, including what did not work.
  `git log` reads as the build plan.

---

## Limitations and Next Steps

### Free-tier rate limits are the binding constraint

Gemini's free tier caps requests **per project, per model, per day**, and one
chunk is one call — a 100-page report is ~220 calls. During development, eight
different models were exhausted in a single day.

Because a key belongs to a project, the unit that runs out is the **(key,
model) pair**, and that is what the pipeline rotates through. Keys are tried
before models, so it stays on the preferred model as long as any key still has
quota for it, and only then falls back to a lesser one. Three keys against
seven models is 21 independent daily allowances.

Configure as many as you have in `backend/.env` — only the first is required:

```
GEMINI_API_KEY=...
GEMINI_API_KEY_2=...     # a key from a DIFFERENT Google account
GEMINI_API_KEY_3=...
```

Keys from the same account share a project and therefore a quota, so a second
key only helps if it comes from a second account. `GET /health` reports how
many slots remain and which is active, labelled `key1/model-name` — never the
key itself. If every slot is spent the run stops cleanly, writes one review
entry naming how many chunks went unprocessed, and
`POST /documents/{id}/extract` resumes later.

It still means a first-time reviewer should **start with a 10–15 page slice**
rather than a full report — for ingesting something *new*. Nobody has to spend
quota just to see the committed corpus populated; see "The offline demo seed"
just below.

Concurrency does not rescue this. Bounded concurrency measured **3.08×** on a
10-chunk burst (58.3s → 18.9s), but on a sustained 221-chunk run the per-chunk
time drifted from 1.89s to 6.05s with no 429s returned — free-tier throughput
appears to be paced server-side. The 3× figure is a burst result and should not
be read as a whole-document one.

### The offline demo seed

Extracting the committed corpus cost real Gemini calls, over real time. Nobody
who clones this repo — or redeploys it onto a fresh volume — should have to
pay that again just to see a populated knowledge layer.

`backend/seed/demo_corpus.sql` is a data-only export of `documents`, `chunks`,
`facts`, `fact_attributes`, `fact_types`, `embeddings`, and `relationships`,
with every id preserved exactly — `docs/DEMO_CASES.md` cites specific fact ids,
and a seed that renumbered them would break every citation in it. The backend
loads it automatically whenever the `facts` table is empty (a fresh clone, a
fresh Railway volume, any database that was ever reset), and never otherwise —
a real corpus, seeded or organically grown, is never touched by this.

What it does not ship: the source PDFs, gitignored from the start and staying
that way. A fact's stored quote and grounding metadata work fully without one;
only the highlighted-page-image panel in the evidence viewer needs the
underlying file, and it already degrades to `grounded: false` rather than
erroring when the file is absent. `python scripts/seed_demo.py export` /
`import [--force]` also work as a standalone CLI — full detail and the one
non-obvious bug it took to get table ordering right (SQLite's `iterdump()`
emits tables alphabetically, not in foreign-key-safe order) is in
`docs/ARCHITECTURE.md` §12.

### Table extraction is the weakest link

Text is extracted linearly, so a table becomes *label, value, value*. Column
association survives only by position. It works surprisingly often and fails
silently when it does not. The concrete case is written up in
[docs/FAILURE_CASE.md](docs/FAILURE_CASE.md): a waste-intensity figure of
`23.3` extracted correctly with **no unit**, because the table never states one
— a correct number that cannot be compared with anything.

Relatedly, **footnotes are not attached to the values that reference them**. In
that same case an asterisk pointed to text explaining a 2.4× year-on-year jump
as an acquisition — exactly the reconciling context this product exists to
surface — and it sat 784 characters away *in the same chunk*, unused.

*Next steps:* attach footnote markers to the facts that reference them (highest
value, and the information is already in the chunk); a table-aware pass using
`page.find_tables()` so column-to-value association is structural rather than
positional; and a narrowly scoped unit-inference second call for facts already
flagged `ambiguous_unit` — which must be allowed to answer "none", or it will
convert a visible gap into an invisible error.

### No OCR fallback for scanned pages

A PDF that opens but yields no text is rejected with a 422 naming OCR as the
missing piece. That is honest, but it is still a refusal — image-only documents
cannot be ingested at all. Tesseract or a vision-model pass would close it.

### Brute-force similarity does not scale past a few thousand facts

Covered under *Approach* above. Fine at current scale, wrong by ~10⁴ facts, and
the replacement seam is deliberately narrow.

### Other known gaps

- **Ingestion runs inline in the upload request**, so a large document is a
  multi-minute HTTP call. It is off the event loop, but a background job queue
  is the right shape.
- **The whole ingest is one transaction**, so nothing is durable until it
  finishes. A crash at minute 21 of a 22-minute run loses everything.
- **Re-running linking re-judges `unrelated` pairs.** Those verdicts are not
  stored — deliberately, since the table answers "what does this relate to" —
  so a re-run pays to reach the same conclusion again. A separate judged-pairs
  ledger is the fix if re-runs become common.
- **Document titles are often just filenames.** Inferring a title from body
  text was implemented and removed: these are excerpts, so page one is usually
  a contents page, and every heuristic produced labels like "Page No." A wrong
  title looks authoritative; no title is honest.
- **The confidence threshold is model-specific.** `gemini-3.6-flash` reports
  1.0 for stated facts and bottoms out near 0.70 on hedged prose, so the
  default is 0.9. Re-tune it when changing models.
- **Contents pages produce junk facts.** Types like `page-number` and
  `chapter-start-page` are real extractions from front matter. Ingesting body
  page ranges avoids them; filtering them automatically would need a
  document-structure pass.

---

## Additional Notes

Two design decisions are worth calling out because they were deliberate
departures from the obvious implementation.

### The comparison card is not a graph

The obvious way to show fact relationships is a node-and-edge diagram. This
does not do that, and the reason is that a graph shows **that** two facts are
connected while hiding **why** — and the why is the entire product.

The primary view is two evidence cards side by side — statement, value, scope,
subject, verbatim quote and source for each — with a verdict ribbon between
them and the model's full rationale underneath. For a `reconciled` pair the
reconciling axis is pulled out of the rationale into a bold
`DIFFERS BY TIME PERIOD` / `DIFFERS BY DEFINITION` chip, because that phrase is
the answer the user came for. Everything is readable without interaction and
without hovering a node.

A small network tab exists as a secondary view, since seeing that one fact is a
hub is genuinely useful. It uses an analytic radial layout rather than a force
simulation — this is always one centre and its direct neighbours, so the
positions are known, and a physics engine would add jitter and a dependency for
nothing.

### The taxonomy panel is labelled "Discovered fact types"

The wording is the point. These are not categories anyone designed. Each label
was invented by the model when it met a fact it had no name for, and the list
grows visibly as documents are ingested — 137 types before one upload during
development, 179 after, gaining `waste-generation`, `air-emissions` and
`emission-intensity` from sustainability pages nothing in the code anticipated.

Calling that a "taxonomy" or "categories" would misrepresent it. Each row shows
a count and a proportional bar, because the *shape* of the distribution is the
interesting part: a long tail of count-1 types is what an evolving vocabulary
actually looks like, and near-duplicates with low counts are the drift signal a
fixed enum would have hidden.

### Motion is deliberately split in two

The landing explainer pins, scrubs and scroll-jacks. The app does none of those
things — 200–300 ms stagger reveals only, no pinning, no scroll hijacking. The
explainer is a pitch someone scrolls once, where pacing them through four ideas
*is* the argument. The app is a tool someone operates for the tenth time today,
where motion beyond making a state change legible is a tax charged on every
repetition. That distinction is written at the top of `app-shell.tsx` so it
does not get "improved" into more scroll-jacking later.

---

## Layout

```
backend/     FastAPI, SQLite, PyMuPDF, google-genai, numpy
  app/       api/ · services/ · db/ · schemas/
  scripts/   bulk_ingest.py
  tests/     pytest
frontend/    Next.js App Router, TypeScript, Tailwind, GSAP, Lenis
docs/        ARCHITECTURE.md · DEMO_CASES.md · FAILURE_CASE.md · DEMO_SCRIPT.md
samples/     starter datasets (PDFs are gitignored)
```

`.env` files, `factpulse.db`, uploaded PDFs and rendered page images are all
gitignored.

## Documentation

| Document | What is in it |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | The schema table by table, the pipeline, and the reasoning behind each decision |
| [docs/DEMO_CASES.md](docs/DEMO_CASES.md) | The four required cases with exact fact IDs, verified against the API |
| [docs/FAILURE_CASE.md](docs/FAILURE_CASE.md) | What the system got wrong, why the source made it hard, and what to do about it |
| [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | Shot-by-shot script for the demo video |
| [docs/DEPLOY.md](docs/DEPLOY.md) | Deploying to Railway (backend) and Vercel (frontend), and what the free tier actually costs |

## API

24 endpoints. The ones that matter:

| | |
| --- | --- |
| `POST /documents` | Upload and run the whole pipeline |
| `GET /documents` | Workspace view with per-document counts |
| `GET /documents/{id}/progress/stream` | Live ingestion progress (SSE) |
| `GET /facts` | Cross-document by default; `?document_id=` to scope |
| `GET /facts/{id}/evidence` | Page image URL + highlight box in image pixels |
| `GET /facts/{id}/relationships` | Verdict, rationale, and the related fact in full |
| `GET /schema` | The discovered `fact_types` registry |
| `GET /review-queue` | Flagged items with context |
| `POST /review-queue/{id}/resolve` | accepted / rejected / edited |
