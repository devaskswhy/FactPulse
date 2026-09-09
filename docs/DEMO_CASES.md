# The four required cases

All four are reproducible in the running app against the committed corpus,
which a fresh clone loads automatically with no API key and no model calls.
Fact and relationship IDs below are stable for that committed corpus;
re-ingesting from scratch renumbers them, so the search terms are given too.

**Nothing here is hardcoded.** No document name, fact id, or figure appears
anywhere in the extraction or relationship code. Every case below is what the
general mechanism produced from the starter documents. The only selection I did
was choosing *which* documents to ingest and *which* of the resulting
relationships to feature.

## The corpus these come from

| Doc | File | Pages ingested | Facts |
| --- | --- | --- | --- |
| 1 | `01-india-economic-survey-2024-25-excerpt.pdf` | 1–12 | 42 |
| 2 | `02-rbi-annual-report-2024-25-excerpt.pdf` | 1–12 | 102 |
| 3 | `03-imf-india-2025-article-iv-excerpt.pdf` | 1–12 | 86 |
| 5 | `delhivery-q4-fy24-deck-p1-8.pdf` | 1–8 | 8 |
| 8 | `delhivery-annual-p61-65.pdf` | 61–65 | 97 |

335 facts, 179 discovered fact types, 53 relationships (all cross-document),
45 open review items.

The three macro documents are the productive pair set: three institutions
reporting on the same economy over the same period, which is exactly the
overlap the relationship engine needs. Every relationship in the corpus is
between two of documents 1, 2 and 3.

---

## Case 1 — Corroboration

**Relationship 19** · `corroborates` · confidence 1.00

| | Fact 99 | Fact 194 |
| --- | --- | --- |
| Source | RBI Annual Report 2024-25, **p9** | IMF Article IV 2025, **p10** |
| Statement | "Headline inflation moderated to an average of 4.6 per cent during 2024-25." | "Headline inflation was 4.6 percent on average in FY2024/25." |
| Value / scope | 4.6 percent · `2024-25` | 4.6 percent · `FY2024/25` |
| Quote | *"Headline inflation moderated to an average of 4.6 per cent during 2024-25"* | *"down from 4.6 percent (FY2024/25 average)"* |

**Rationale:** "Both facts state that headline inflation averaged 4.6 percent
during the 2024-25 fiscal year."

Two different institutions, wording that shares almost no phrasing — one says
"moderated to an average of", the other "was ... on average", and the periods
are written differently (`2024-25` vs `FY2024/25`). The engine matched them on
meaning, not string overlap. **Both facts have bounding boxes**, so the
evidence viewer shows the highlight on both source pages.

*Alternative:* relationship 17 pairs fact 88 (RBI, "real gross domestic product
(GDP) growth moderated to 6.5 per cent") with fact 153 (IMF, "economic growth
was 6.5 percent"), where the rationale explicitly notes that "economic growth in
this context refers to real GDP growth" — a nice example of the model resolving
a terminology difference.

**Find it in the app:** Fact Explorer → search `Headline inflation` → open fact
99 → relationships tab.

---

## Case 2 — Genuine contradiction

**Relationship 14** · `contradicts` · confidence 1.00

| | Fact 83 | Fact 12 |
| --- | --- | --- |
| Source | RBI Annual Report 2024-25, **p7** | Economic Survey 2024-25, **p5** |
| Statement | "Global growth was 3.5 per cent in 2023." | "The global economy grew by 3.3 per cent in 2023." |
| Value / scope | **3.5** percent · `2023` | **3.3** percent · `2023` |
| Quote | *"3.5 per cent a year ago"* | *"The global economy grew by 3.3 per cent in 2023."* |

**Rationale:** "Fact A states global growth was 3.5 per cent in 2023, while
Candidate 1 states it was 3.3 per cent for the same period. These are
conflicting figures for the same metric and time period."

This is a real conflict, and the reason is real too: the two institutions are
quoting different vintages of the IMF's World Economic Outlook, which revised
2023 global growth between publications. **Neither document says so**, so
there is no reconciling context available in the sources — which is precisely
the test for `contradicts` rather than `reconciled`. Both facts are grounded.

**A second one:** relationship 18 pairs fact 88 (RBI: India's FY25 GDP growth
6.5%) with fact 10 (Economic Survey: 6.4% in FY25). Worth showing *after*
case 1, because fact 88 simultaneously **corroborates** the IMF's 6.5% and
**contradicts** the Survey's 6.4% — the Survey figure is an earlier advance
estimate later revised up. The engine is right that they conflict as stated;
a domain expert would add the vintage explanation the documents omit. Note
fact 10 has no bounding box, so relationship 14 is the one that shows a
highlight on both sides.

**Find it in the app:** Fact Explorer → search `global economy grew` → open
fact 12 → relationships tab.

---

## Case 3 — Apparent contradiction explained by context

Two good options, both `reconciled` with confidence 1.00 and both fully
grounded.

### 3a. Reconciled by **definition** — actual vs projection (recommended)

**Relationship 10**

| | Fact 78 | Fact 13 |
| --- | --- | --- |
| Source | RBI Annual Report, **p6** | Economic Survey, **p5** |
| Statement | "The global economy grew by 3.3 per cent in 2024." | "The International Monetary Fund projected global growth of 3.2 per cent for 2024." |
| Value / scope | 3.3 percent · `2024` | 3.2 percent · `2024` |

**Rationale:** *[definition]* "Fact A reports actual global growth of 3.3
percent for 2024, while Candidate 2 reports an IMF projection of 3.2 percent
for the same year. The difference is between an actual outcome and a
projection."

Same metric, same year, different numbers — and the reconciling axis is not
time or units but **what the number is**: an outcome versus a forecast. This
is the subtler and more interesting case.

### 3b. Reconciled by **time period**

**Relationship 9** — fact 78 (RBI, 3.3% in **2024**) vs fact 12 (Survey, 3.3%
in **2023**). Same value, different years. *[time period]* "The difference is
the time period."

Fact 12 is the useful anchor for the demo: it **contradicts** fact 83 and
**reconciles** with fact 78, so one fact demonstrates cases 2 and 3 back to
back.

**Find it in the app:** Fact Explorer → search `global economy grew` → open
fact 78 → relationships tab. The reconciling axis renders as a bold
`DIFFERS BY DEFINITION` / `DIFFERS BY TIME PERIOD` chip above the rationale.

---

## Case 4 — Extraction failure

Written up in full in [FAILURE_CASE.md](FAILURE_CASE.md).

**Headline:** fact 295 — "The waste intensity per rupee of turnover was 23.3 in
FY24" — extracted correctly from a BRSR table in the Delhivery annual report
(doc 8, p2), with **no unit**, because the table never states one. The number
is right and uninterpretable. Caught automatically by the post-ingestion
self-check as `ambiguous_unit`, review item 43.

**Find it in the app:** Review Queue → filter `ambiguous_unit` → item 43.

Other review items worth showing:

| Issue | Count | Example |
| --- | --- | --- |
| `ungrounded_quote` | 25 | Quote verified against the chunk but not locatable in the PDF |
| `ambiguous_unit` | 8 | Numeric value with no unit |
| `unverified_quote` | 5 | Model quoted a contents-page row that linear extraction orders differently |
| `extraction_failed` | 5 | Transient model errors |
| `quota_exhausted` | 1 | Free-tier daily cap hit mid-document |
| `low_confidence` | 1 | Below the review threshold |

---

## Case 5 — Same entity, two spellings

Fully within the committed corpus, no synthetic data. Doc 8, the Delhivery
sustainability excerpt, writes its own subject's name two ways across facts a
few rows apart:

| | Fact 238 | Fact 318 |
| --- | --- | --- |
| Stored `subject` | `Delhivery Limited` | `Delhivery` |
| Statement | "Delhivery was EBITDA profitable in FY24." | "Total Scope 3 emissions for Delhivery in FY24 were 576,188.2 metric tonnes..." |

Neither is wrong — the document itself mixes the full legal name with the
short form, the way real filings do. Left as raw strings, a subject search or
grouped view for one spelling would silently miss facts stored under the
other. Across the whole corpus 5 canonical subjects fold two raw spellings
together this way (`delhivery`, `global economy`, `headline inflation`, `core
inflation`, `fuel inflation`), out of 335 facts and 177 distinct entities.

**Find it in the app:** Fact Explorer → group by `subject` → the `delhivery`
group shows a `2 spellings merged` badge and both spellings' facts underneath
one heading. Or `GET /subjects` for the raw registry — mirrors `GET /schema`.

**Why this is not the brief's address example, exactly, but is the same
mechanism.** The brief's example is one entity's address written two ways
across two *different* documents. This corpus's naturally-occurring case
happens to be one entity's name written two ways within *one* document, plus a
second real instance where "Delhivery Limited" (doc 5, the earnings-call deck)
and "Delhivery" (doc 8, the sustainability report) are the same company across
*different* documents. The underlying mechanism — a deterministic
`canonicalize_subject()` folding mechanical spelling differences, covering
legal suffixes AND address abbreviations (`St`/`Street`, `Rd`/`Road`, ...) so
the address case works identically — is verified directly against the brief's
own address-written-two-ways pattern in `tests/test_entity.py`, since the
committed corpus does not happen to contain two documents restating one
address.

---

## What I tried and could not get

**A Delhivery prospectus-vs-annual-report pair.** The brief suggests it, and it
is a genuinely strong candidate: the 2022 prospectus reports revenue for
Fiscal 2019–2021, and the FY24 annual report's Directors' Report (p22) carries
a table with **standalone `74,540.82`** and **consolidated `81,415.38`** revenue
for the same FY24 — the brief's exact scope example.

I prepared both slices and attempted the ingest. **Every available Gemini model
hit its free-tier daily quota**, so neither slice extracted. Documented rather
than faked.

Two honest notes about that pair:

- The standalone-vs-consolidated table is **within a single document**, and the
  linker deliberately excludes same-document pairs (see ARCHITECTURE §6): one
  report restating its own figure is a property of that document's prose, not
  cross-document corroboration. Getting that comparison would need the two
  figures in *different* documents — e.g. a press release quoting consolidated
  revenue against the annual report's standalone line.
- Documents 5 and 8 are both Delhivery and produced **zero** relationships
  between them. That is correct behaviour, not a gap: doc 5 is cover and
  contents pages, doc 8 is sustainability metrics. No overlapping claims, so no
  relationships — the engine is not inventing connections to look busy.

**To reproduce once quota resets:** ingest pages 48–53 of the prospectus and
21–26 of the annual report (9 and 17 chunks respectively). Since this was
written the pipeline gained multi-key rotation (README, "Free-tier rate limits
are the binding constraint") — three keys against seven models is 21
independent daily allowances rather than 7, which makes exhausting the whole
pool mid-demo considerably less likely on a retry.

## A fifth relationship type with nothing to show it

`supersedes` — a later document recording that a current state changed, the
brief's own resigned-director example — is implemented, unit-tested against
the live model (`tests/test_relationships.py`), and verified end-to-end
including the direction-resolution logic that decides which of two facts is
current. The committed corpus contains **zero** supersessions, and that is the
correct answer for it: three macro institutions and one logistics company's
sustainability report argue about *measurements*, which is contradiction or
reconciliation, not a state that changed. Demonstrating it live would need a
document restating a current state — a board, a registered office, a credit
rating — across two dates, which none of the five source documents happen to
do. Documented rather than staged: see ARCHITECTURE §6, "Supersession: when
the world changed rather than a source being wrong".
