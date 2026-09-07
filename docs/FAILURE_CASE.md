# Failure case: a correct number that means nothing

**Fact 295** · review item 43 · issue type `ambiguous_unit`
Source: Delhivery FY24 Annual Report (BRSR section), page 2 of the ingested
slice, chunk 107.

> **"The waste intensity per rupee of turnover was 23.3 in FY24."**
> `normalized_value: 23.3` · `unit: null` · `confidence: 1.00`

23.3 what? The system does not know, and neither does the document.

---

## What went wrong

The number is correct. It was pulled from the right row and the right column,
it is grounded to a real bounding box on the page, and the model's own
confidence was 1.00 — justifiably, because as an extraction it is right.

It is also **useless**. A value with no unit cannot be compared against another
value, cannot be sanity-checked, and cannot be reconciled with a figure from
another document — which is the entire purpose of this system. A fact that
cannot enter the comparison step is a fact that failed, however correct it is.

Worse, it is *invitingly* useless. The same table gives FY23 as 9.9 (fact 296).
23.3 versus 9.9 is a 2.4x year-on-year increase, which looks alarming and is
the kind of thing a reader would repeat. Without the unit — or the footnote,
below — there is no way to tell whether that jump is meaningful.

---

## Why it is hard

This is not a hallucination, and it is not a prompt failure. It is what the
source actually says.

### The unit is genuinely absent from the row

Here is the table as PyMuPDF linearises it, which is exactly what the model
received:

```
Parameter
FY24*
FY23
Waste intensity per rupee of turnover
(Total waste generated/Revenue from operations)
23.3
9.9
Waste intensity per rupee of turnover adjusted for Purchasing Power Parity (PPP)
(Total waste generated/Revenue from operations adjusted for PPP)
556.3
239.4
```

The parenthetical under the label is a **formula, not a unit**. "Total waste
generated / Revenue from operations" tells you how the ratio was computed; it
does not tell you that the numerator is in metric tonnes and the denominator in
rupees, nor at what scale. The implied unit is something like tonnes per rupee
of turnover, but the document never writes it down, and inferring it would mean
inventing information the source does not contain.

The extraction was **consistent**, which is the strongest evidence that the
model behaved correctly. Two rows further down the same table:

```
For each category of waste generated, total waste recovered ... (in metric tonnes)
Category of waste (e-waste)
(i) Recycled
113.8
17.0
```

Fact 301 from that row came out as `113.8 metric tonnes`. The model took the
unit where the table supplies one and left it null where it does not. It did
not guess. That is the behaviour you want, and the reason the gap surfaces as a
review item rather than as a plausible-looking fabrication.

### Column association survives only by position

The header row is `Parameter | FY24* | FY23`, and linear text extraction
flattens each row to *label, first value, second value*. The model paired 23.3
with FY24 and 9.9 with FY23 correctly — but only because the column order in
the header happens to match the value order in the row. Nothing in the
extracted text encodes the association. A table with a merged cell, a
right-aligned column, or a footnote marker between values would break it
silently, and the result would still look confident.

### The explanation exists, and was missed

The asterisk on `FY24*` points to a footnote at the bottom of the same page:

> \* The increase in total waste generated in FY24 compared to FY23 is
> attributed to multiple factors, such as — shift from plastic pallets used in
> operations to wooden pallets, disposal of excess materials taken over due to
> **acquisition of Spoton Logistics Private Limited** by the Company in both
> FY24 & FY23.

That footnote was **inside the same chunk**, 784 characters after the value. So
this is not a chunking failure — the model had the text and did not connect it
to the fact.

This is the more interesting miss of the two. That footnote is precisely the
kind of reconciling context the whole system is built to surface: a 2.4x jump
explained by an acquisition, which is a *scope* change. Had it been attached as
an attribute, a later comparison against another Delhivery waste figure could
have been classified `reconciled` with the acquisition named, instead of
`contradicts`. The mechanism for that already exists — `fact_attributes` is an
open key/value table designed for exactly this — but nothing in the extraction
prompt tells the model to look for footnote markers and attach what they point
at.

---

## What the system did about it

Nothing was discarded. The fact is stored, grounded, and queryable, and the
post-ingestion self-check flagged it automatically:

```
review 43 · ambiguous_unit · fact 295
  normalized_value is '23.3' with no unit recorded, so the number cannot be
  compared against another value or interpreted on its own.
```

The check is a plain query over what was written — numeric `normalized_value`,
empty `unit`, excluding ISO dates and bare years — so it costs nothing and
cannot itself fail. It caught **8 facts** across this corpus.

In the Review Queue this entry offers Accept / Reject / **Edit**. Edit is the
right action here: a reviewer who knows the BRSR convention can write the unit
back onto the fact, and the number becomes comparable. Grounding fields stay
locked, because the quote and box are what the document says and a reviewer
should not be able to make a fact claim evidence the PDF does not support.

---

## What I would do with more time

Ranked by value per unit of effort.

**1. Attach footnotes to the values that reference them.** The highest-value
fix, because the information is already in the chunk. A marker (`*`, `†`, a
superscript digit) on a column header or value, matched to the paragraph that
begins with the same marker, attached to the fact as an attribute such as
`footnote: "increase attributed to ... acquisition of Spoton Logistics"`. This
is a prompt change plus a light post-parse pass, not new infrastructure, and it
would have turned this case from a gap into exactly the reconciling context the
product is meant to find.

**2. A table-aware extraction pass.** PyMuPDF exposes `page.find_tables()`,
which recovers real cell geometry instead of a flattened reading order. For
chunks that are mostly tabular, extracting rows as structured cells would make
the column-to-value association explicit rather than positional, and would let
a unit stated in a header or a units column be carried down to every value
under it. This is the structural fix for the whole class of problem; it is
listed second only because it is more work than the footnote pass.

**3. A unit-inference second pass, scoped narrowly.** For facts already flagged
`ambiguous_unit`, one cheap follow-up call that sees the *whole* table and is
asked only "what unit does this value carry, or none?" — with "none" an
explicitly acceptable answer. It must be allowed to decline, or it will invent
a unit for every unitless number and convert a visible gap into an invisible
error, which is worse than the bug.

**4. Flag suspicious year-on-year movement.** A self-check that notices two
facts of the same type and subject differing by more than some ratio across
adjacent periods, and queues them for review. It would have caught the 23.3 vs
9.9 jump independently of the unit problem, and it is another pure query — no
model calls.

**What I would not do:** hardcode BRSR units, or any table-, document- or
issuer-specific rule. The correct answer to "what unit is waste intensity in"
is not knowledge this system should carry. The fix is to read it from the
document when the document says it, and to keep saying "I don't know" — loudly,
in the review queue — when it does not.
