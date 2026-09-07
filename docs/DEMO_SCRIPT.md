# Demo script — under 3 minutes

Every fact in this script is catalogued in [DEMO_CASES.md](DEMO_CASES.md) with
its ID. Nothing needs to be found live on camera.

---

## Before recording

**1. Both servers up.**

```bash
cd backend  && uvicorn main:app --reload      # terminal 1
cd frontend && npm run dev                    # terminal 2
```

**2. Confirm the demo facts are present.** This asserts every case below and
prints PASS/FAIL — if anything fails, the corpus has changed and the IDs need
re-checking against DEMO_CASES.md before you record:

```bash
cd backend
python - <<'PY'
import json, urllib.request
g = lambda u: json.load(urllib.request.urlopen(f"http://127.0.0.1:8000{u}"))
rel = lambda f, o, t: any(r["fact_id"] == o and r["relationship_type"] == t
                          for r in g(f"/facts/{f}/relationships")["relationships"])
checks = [
    ("corroborates 99<->194", rel(99, 194, "corroborates")),
    ("contradicts  12<->83",  rel(12, 83, "contradicts")),
    ("reconciled   78<->13",  rel(78, 13, "reconciled")),
    ("review item 43 open",   any(e["id"] == 43 for e in
                                  g("/review-queue?issue_type=ambiguous_unit&limit=200")["entries"])),
]
for name, ok in checks: print(("PASS " if ok else "FAIL ") + name)
PY
```

**3. Pick the upload PDF.** Anything not already ingested. A 5–8 page slice
finishes in about a minute; a full report will not fit in the shot.

**4. Reset the intro.** The preloader is once per browser session:

```js
sessionStorage.removeItem('factpulse:preloaded')   // devtools console, then reload
```

**5. Browser.** 1600×950 or wider so the split view has room. Close devtools.
Hide bookmarks. Zoom at 100%.

**6. Quota.** Every model has a separate daily cap. Confirm the one in `.env`
still works before you start, or the upload shot dies on camera:

```bash
cd backend && python -c "
from app.services.extract import build_client, extract_facts_from_chunk
print('model OK:', len(extract_facts_from_chunk('Revenue was 5 crore in FY24.', client=build_client())), 'facts')"
```

---

## 0:00 – 0:20 · What this is

**On screen:** load `localhost:3000`. Preloader counts 000 → 100 and wipes.
Scroll slowly through the explainer — pause about a second on each of Extract,
Ground, Corroborate & contradict, Reconcile. Keep moving; do not read them out.

**Say:**

> FactPulse pulls facts out of PDFs, ties every one to the exact place it came
> from, and then compares facts across documents — to work out whether they
> agree, genuinely conflict, or only look like they conflict.

**Note:** scroll with the wheel, not the scrollbar — the section is pinned and
scrubbed, so wheel motion is what drives it smoothly.

---

## 0:20 – 0:50 · Ingest a document live

**On screen:** scroll past the explainer into the app. Click **Upload**. Drag
the PDF onto the dropzone.

The theater starts: the cell strip fills as chunks complete, the stage label
moves through *reading pages → splitting into chunks → extracting facts*, and
the four counters climb — pages, chunks, **facts**, relationships.

**Say:**

> This is a live ingest, not a progress bar. Each cell is a real chunk of the
> document. It's parsing, chunking, then calling the model per chunk to pull
> facts out — you can watch the fact count climb as they land.

**When it finishes,** the summary appears with the fact count and a row of
fact-type chips.

> And those types weren't in a list anywhere. The model named each kind of fact
> as it met it.

**Timing note:** if the upload will overrun, cut here and rejoin after
completion. Do not wait in silence on camera.

---

## 0:50 – 1:20 · Corroboration

**On screen:** click **Fact Explorer**. Search `Headline inflation`. Open
**fact 99** (RBI Annual Report). Evidence tab loads: statement, values, and the
page image with the amber highlight over the quote.

Click the **relationships** tab.

**Say:**

> This fact came from the RBI's annual report — headline inflation averaged
> 4.6 per cent in 2024-25, and the highlight is the exact line it came from on
> page 9.
>
> The IMF's Article IV report says the same thing in completely different
> words: *"headline inflation was 4.6 percent on average in FY2024/25."* Almost
> no shared phrasing, and even the period is written differently — `2024-25`
> versus `FY2024/25`. The engine matched them on meaning, and the rationale
> says why.

**Point at:** the green `CORROBORATES` ribbon between the two evidence cards,
and the two different source documents named at the bottom of each card.

---

## 1:20 – 1:50 · Contradiction

**On screen:** search `global economy grew`. Open **fact 12** (Economic
Survey). Relationships tab.

**Say:**

> Now a real disagreement. The Economic Survey says the global economy grew
> 3.3 per cent in 2023. The RBI's annual report says 3.5 per cent — same
> metric, same year, different number.
>
> Underneath, this is two institutions quoting different vintages of the IMF's
> World Economic Outlook, which revised that figure between publications. But
> **neither document says so** — and that's exactly why this is a
> contradiction rather than something reconcilable. There's no explanation
> available in the sources.

**Point at:** the red `CONTRADICTS` ribbon, and the two `scope` fields both
reading `2023`.

---

## 1:50 – 2:20 · Reconciled — the interesting one

**On screen:** search `global economy grew` again. Open **fact 78** (RBI, 3.3%
in **2024**). Relationships tab. Two entries appear; take the one against
**fact 13**.

**Say:**

> And here's the case that makes this worth building. The RBI reports actual
> global growth of 3.3 per cent for 2024. The Economic Survey reports 3.2 per
> cent for the same year. Same metric, same period, different numbers — that
> looks like a contradiction.
>
> It isn't. One is an outcome, the other is an IMF projection. The system names
> that difference explicitly: **differs by definition**.

**Point at:** the amber `RECONCILED` ribbon and the bold `DIFFERS BY
DEFINITION` chip above the rationale.

**Optional, if time allows:** the same fact also reconciles with fact 12 by
*time period* — 3.3 per cent in 2024 versus 3.3 per cent in 2023. One fact,
two different reconciling axes.

---

## 2:20 – 2:50 · Where it fails

**On screen:** click **Review Queue**. Filter to `ambiguous_unit`. Open
**item 43**.

**Say:**

> Nothing doubtful gets thrown away. This one is a good failure: waste
> intensity per rupee of turnover, 23.3 in FY24. The number is correct and
> it's grounded to the right cell — but there's no unit, because the table
> never gives one. So it's a number that can't be compared to anything.
>
> The system caught that itself, after ingestion, and queued it instead of
> quietly storing a useless fact. A reviewer can accept, reject, or edit the
> unit back in — but not the quote or the page, because those come from the
> document.

**Point at:** the `ambiguous_unit` chip, the note explaining why, and the
Accept / Reject / Edit buttons.

**If there is a spare beat:** the same table has a footnote explaining that
year's jump as an acquisition, and the system did not attach it — that is the
top item in *Next Steps*.

---

## 2:50 – 3:00 · Close

**On screen:** back to **Fact Explorer** with no fact open, so the
**Discovered fact types** panel is visible on the right. Scroll it once so the
long tail is obvious.

**Say:**

> Every one of these types was invented by the model as it met a new kind of
> fact — none of them is in the code. That's the point: the schema grows with
> the documents instead of being decided up front.

**End on** the panel with its counts and bars.

---

## Shot list at a glance

| Time | Screen | IDs |
| --- | --- | --- |
| 0:00 | Preloader → explainer scroll | — |
| 0:20 | Upload → ingestion theater | any fresh PDF |
| 0:50 | Fact Explorer → evidence → relationships | fact **99** ↔ **194** |
| 1:20 | Relationships | fact **12** ↔ **83** |
| 1:50 | Relationships | fact **78** ↔ **13** |
| 2:20 | Review Queue | item **43** (fact 295) |
| 2:50 | Discovered fact types panel | — |

## If something goes wrong on camera

- **Upload stalls or errors** — almost certainly the daily model quota. Switch
  `GEMINI_MODEL` in `.env` to another flash model, restart the backend, and
  re-record that segment only. Quotas are per model.
- **A fact ID does not resolve** — the corpus was re-ingested and IDs shifted.
  Use the search terms in DEMO_CASES.md instead; the statements are stable even
  when the IDs are not.
- **The highlight is missing** — that fact is ungrounded. Pick another; the
  seven facts in this script are all confirmed to have bounding boxes.
