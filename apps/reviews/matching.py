"""Rule-based comparison of an NNC1's named secretary against the register.

What an NNC1 (Incorporation Form) proves is narrow and exact: the company it
names appointed the secretary it names. That is the only fact this platform
needs in order to say a reviewer really was a client - and it is a fact about
two strings, so it can be checked without a model.

The asymmetry below is the whole design, and it is deliberate:

* a **mismatch is evidence**. If the document's secretary is not the company
  being reviewed, the review is not about a client relationship, whatever else
  is true.
* a **match is not**. The declared name is typed in by the person asking to be
  verified, so agreeing with the register proves only that they can read. The
  match is shown to a moderator beside the official record; the moderator is
  the one who opens the document and decides.

So nothing here returns "verified". It returns how close two names are and by
what method, and ``services.decide_verification`` needs a human either way.
Same shape as the P3 website check: evidence, not a gate.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from django.db import models

from apps.registry.models import Licensee

#: Corporate suffixes and punctuation carry no identifying information but
#: differ constantly between a filed form and the register ("Ltd" / "Limited",
#: "Co." / "Company"). Stripping them is what makes exact matching useful at
#: all; keeping them would push most genuine pairs into the fuzzy branch.
_NOISE_WORDS = frozenset(
    {
        "limited",
        "ltd",
        "company",
        "co",
        "corporation",
        "corp",
        "incorporated",
        "inc",
        "the",
        "hong",
        "kong",
        "hk",
        "services",
        "service",
    }
)

_PUNCTUATION = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+")

#: Above this, two names are the same name spelled differently; below it, a
#: moderator is looking at two different companies. Tuned to be forgiving -
#: a false "similar" costs a moderator one comparison, a false "different"
#: costs a real client their verification.
FUZZY_THRESHOLD = 0.82


class MatchMethod(models.TextChoices):
    EXACT = "exact", "Exact (after normalisation)"
    FUZZY = "fuzzy", "Similar"
    NONE = "none", "No match"
    MANUAL = "manual", "Decided by a reviewer"


@dataclass(frozen=True, slots=True)
class NameMatch:
    """How close the declared secretary is to a licensed company's name."""

    method: str
    score: float
    licence_no: str = ""
    matched_name: str = ""

    @property
    def is_plausible(self) -> bool:
        """Whether this is worth putting in front of a moderator as agreeing.

        Not "whether to verify" - that decision is never made here.
        """
        return self.method in (MatchMethod.EXACT, MatchMethod.FUZZY)


def normalise_name(name: str) -> str:
    """Fold a company name to the part that identifies it.

    Case, punctuation, width (full-width Latin is common in forms typed on a
    Chinese IME) and corporate suffixes all go. What is left is compared.
    """
    folded = unicodedata.normalize("NFKC", name).casefold()
    folded = _PUNCTUATION.sub(" ", folded)
    words = [word for word in _WHITESPACE.split(folded) if word and word not in _NOISE_WORDS]
    return " ".join(words)


def similarity(left: str, right: str) -> float:
    """0.0 - 1.0 over the normalised forms.

    ``difflib`` rather than Postgres trigram: this compares one pair of strings
    that are both already in memory, and a database round trip per comparison
    would buy nothing. The trigram indexes exist for searching 7,457 rows,
    which is a different problem.
    """
    a, b = normalise_name(left), normalise_name(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def match_against_provider(declared_secretary: str, *, licensee: Licensee) -> NameMatch:
    """Compare the declared secretary with the company being reviewed.

    The comparison that matters: this document has to name *this* company, not
    some licensed company.
    """
    if not declared_secretary.strip():
        return NameMatch(method=MatchMethod.NONE, score=0.0)

    best = 0.0
    matched = ""
    for candidate in (licensee.name_en, licensee.name_zh):
        if not candidate:
            continue
        score = similarity(declared_secretary, candidate)
        if score > best:
            best, matched = score, candidate

    if best >= 1.0:
        method = MatchMethod.EXACT
    elif best >= FUZZY_THRESHOLD:
        method = MatchMethod.FUZZY
    else:
        return NameMatch(method=MatchMethod.NONE, score=best, matched_name=matched)

    return NameMatch(
        method=method,
        score=best,
        licence_no=licensee.licence_no,
        matched_name=matched,
    )


def find_licensee(declared_secretary: str) -> NameMatch:
    """Which licensed company, if any, the declared secretary looks like.

    Used when the declared name does *not* match the company under review: a
    moderator seeing "this names a different licensed TCSP" can close the case
    immediately, and seeing "this names nobody on the register" is a different
    and more serious signal.

    Scans the register in Python rather than in SQL. It is 7,457 short strings
    and it runs once per upload, in a worker; a trigram query would need the
    same normalisation duplicated in SQL to agree with ``normalise_name``, and
    two implementations of "the same name" is exactly the bug this module is
    supposed to prevent.
    """
    declared = normalise_name(declared_secretary)
    if not declared:
        return NameMatch(method=MatchMethod.NONE, score=0.0)

    best = NameMatch(method=MatchMethod.NONE, score=0.0)
    rows = Licensee.objects.values_list("licence_no", "name_en", "name_zh")
    for licence_no, name_en, name_zh in rows.iterator(chunk_size=1000):
        for candidate in (name_en, name_zh):
            if not candidate:
                continue
            score = similarity(declared_secretary, candidate)
            if score > best.score:
                best = NameMatch(
                    method=MatchMethod.EXACT if score >= 1.0 else MatchMethod.FUZZY,
                    score=score,
                    licence_no=licence_no,
                    matched_name=candidate,
                )
    if best.score < FUZZY_THRESHOLD:
        return NameMatch(method=MatchMethod.NONE, score=best.score)
    return best
