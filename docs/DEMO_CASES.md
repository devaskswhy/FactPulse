# Demo cases — shot list

Four cases, all reproducible in the running app against the committed corpus.
Fact and relationship IDs below are stable for the database this was recorded
against; re-ingesting from scratch will renumber them, so the search terms are
given as well.

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
fact 10 has no bounding box, so use relationship 14 for the highlighter shot.

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
21–26 of the annual report (9 and 17 chunks respectively).
