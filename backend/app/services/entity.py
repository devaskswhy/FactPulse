"""Does "Delhivery Limited" name the same subject as "Delhivery Ltd."?

Two facts about the same real-world entity should sit together in this
knowledge layer -- filterable as one, groupable as one -- even when two
documents spell that entity differently. "Acme Corp", "Acme Corporation" and
"ACME CORP." are one company written three ways. "14 Charter Street" and "14
Charter St." are one address written two ways. Left as raw strings, a subject
filter for one form silently misses the others, and the fact explorer's
grouping view splits one entity into several rows for no reason a reader can
see.

This module produces a `canonical_subject`: a normalized key two spellings of
the same thing collapse to, computed once at write time and used for grouping
and filtering. It is deliberately narrow about what counts as "the same
thing".

**Only mechanical differences are collapsed -- never semantic ones.** Case,
punctuation, whitespace, a small set of unambiguous legal-form suffixes (Ltd,
Inc, Corp, ...), and a small set of unambiguous address-word abbreviations (St,
Rd, Ave, ...) are folded together, because two spellings differing only in
those respects are the same subject under any reading. "Reserve Bank of India"
and "RBI" are NOT folded together, even though a human reader knows they match,
because that identity is not verifiable from the string alone -- "RBI" could in
principle be something else, and guessing would risk merging two facts that
happen to abbreviate the same way but are not actually about the same subject.
The same caution excludes generic corporate-structure words like "Group" or
"Holdings": stripping "Ltd" cannot turn one real company into a different one,
but stripping "Holdings" might, since a holding company and its subsidiary are
often distinct subjects that happen to share a name.

The result is a key for MATCHING, not a name for DISPLAY. It is lowercased and
stripped of exactly the punctuation and words that would otherwise cause a
false split, which makes it unreadable as a label; callers show one of the
original spellings to a person and use the canonical form only to decide which
spellings belong together.
"""

from __future__ import annotations

import re

# Legal-form suffixes safe to fold: they denote the SAME kind of entity under a
# different name for its structure, not a different entity. Deliberately
# excludes structural words like "Group" or "Holdings" that often distinguish
# a parent from a subsidiary -- folding those could merge two real, different
# subjects, which a filter or a grouped view would then present as one.
_LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "ltd", "limited",
    "llc", "llp", "plc", "co", "company", "gmbh", "pvt", "pte", "sa",
}

# Address words with one unambiguous expansion. Grounded in the corpus: filings
# write a registered office as "...St", "...Rd", "...Ave" in one place and in
# full elsewhere, and a subject search or grouped view should not split on that.
_ADDRESS_WORDS = {
    "st": "street", "rd": "road", "ave": "avenue", "blvd": "boulevard",
    "ln": "lane", "dr": "drive", "hwy": "highway", "apt": "apartment",
    "ste": "suite", "fl": "floor", "mt": "mount", "ft": "fort",
}

_LEADING_ARTICLE = re.compile(r"^(the|a|an)\s+")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def canonicalize_subject(subject: str | None) -> str | None:
    """The matching key for `subject`, or None when there is nothing to key.

    Idempotent and pure: calling it twice on its own output returns the same
    value, which is what makes a plain re-run of the backfill safe.
    """
    if not subject or not subject.strip():
        return None

    text = subject.strip().lower()
    text = text.replace("&", " and ")
    text = _NON_WORD.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    text = _LEADING_ARTICLE.sub("", text)

    tokens = [_ADDRESS_WORDS.get(tok, tok) for tok in text.split(" ") if tok]

    # Strip legal suffixes from the END only, and repeatedly -- "Acme Corp
    # Ltd" sheds "ltd" then "corp" to reach "acme". Suffix words appearing
    # mid-name are left alone: they are far more likely to be part of the
    # actual name there (e.g. a division literally named "Company Co-op").
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()

    key = " ".join(tokens)
    return key or None
