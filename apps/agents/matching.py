"""A2 - order a screened shortlist of companies for one requirement.

AI_AGENTS A2. Two sentences in that spec do all the design work here:

* **Hard filtering in SQL, ranking by the model.** The model never searches. It
  receives at most thirty candidate summaries that
  ``providers.selectors.match_candidates`` already screened, and its job is the
  order and the explanation. A model asked to retrieve companies will name
  companies.
* **``reasons`` may only cite facts that are in the summary it was given.**
  Every reason is checked against the candidate's own facts before it is stored
  (``ground_reasons``), and one that cites something absent - or something the
  candidate is not - is dropped. AI_AGENTS A2 puts the grounding violation rate
  at zero, and the only way to hold a rate at zero is to make the violating
  sentence unable to reach the database.

Two more rules with a compliance edge (COMPLIANCE section 2):

* Nothing here may say anything about the odds of an account being opened. The
  banned-phrase screen from ``core.compliance`` runs over every reason and
  concern, and a hit removes the sentence rather than the candidate - the
  company did nothing; the sentence did.
* A concern is a thing to ask about, never a warning about a company. The
  fallback writes them in that register and the prompt asks for the same.

The fallback is the ranking score from RATING_SYSTEM section 5, in the order
SQL already produced, with reasons built from the same facts by template. With
every model switched off a buyer still gets a shortlist and still gets told why
each row is on it - what they lose is the ordering that understood their
paragraph.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, ClassVar, Final

from django.utils.translation import gettext

from apps.agents.base import BaseAgent
from apps.agents.schemas import MatchingOut, MatchItem
from apps.core.compliance import check_banned_phrases

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel

#: How many rows the buyer is shown. Ten is a page of reading; the pool behind
#: it is thirty, and the rest of the register is a click away in the directory.
MAX_MATCHES = 10


class MatchingAgent(BaseAgent):
    name: ClassVar[str] = "matching"
    model: ClassVar[str] = "claude-sonnet-5"
    prompt_file: ClassVar[str] = "matching_v1.md"
    output_schema: ClassVar[type[BaseModel]] = MatchingOut
    max_tokens: ClassVar[int] = 3072
    object_type: ClassVar[str] = "rfq.Rfq"

    def build_user_prompt(self, ctx: dict[str, Any]) -> str:
        """The requirement, then the candidates, as facts and nothing else.

        The buyer does not appear: who wrote a requirement has no bearing on
        which companies can serve it, and the requirement wall does not show
        them either (COMPLIANCE section 4).
        """
        lines = [
            "--- the requirement ---",
            f"Services needed: {', '.join(ctx.get('services_needed', [])) or 'not stated'}",
            f"Company type: {ctx.get('company_type') or 'undecided'}",
            f"Business nature: {ctx.get('business_nature') or 'not stated'}",
            f"Bank account needed: {bool(ctx.get('needs_bank_account'))}",
            f"Budget (HKD): {_budget(ctx)}",
            f"Timeline: {ctx.get('timeline') or 'undecided'}",
            "",
            f"--- candidates ({len(ctx.get('candidates', []))}, already screened by SQL) ---",
            "Rank these. Cite only what appears below. Do not add a company.",
            "",
        ]
        for candidate in ctx.get("candidates", []):
            lines.append(_candidate_block(candidate))
        return "\n".join(lines)

    def fallback(self, ctx: dict[str, Any], reason: str) -> MatchingOut:
        """The SQL order, with reasons written from the same facts by template.

        No judgement is claimed: ``fit_score`` here is the position in a list
        that was sorted by service coverage and ranking score, so it says "this
        one came first", not "this one fits better".
        """
        candidates = list(ctx.get("candidates", []))[:MAX_MATCHES]
        wanted = list(ctx.get("services_needed", []))
        items = [
            MatchItem(
                provider_id=str(candidate.get("provider_id", "")),
                rank=position,
                fit_score=round(1.0 - (position - 1) / MAX_MATCHES, 2),
                reasons=template_reasons(candidate, wanted=wanted),
                concerns=template_concerns(candidate, wanted=wanted),
            )
            for position, candidate in enumerate(candidates, start=1)
        ]
        return MatchingOut(
            items=items,
            unmatched_requirements=unmatched_services(candidates, wanted=wanted),
            # Sorted, not ranked: nobody read the buyer's paragraph.
            confidence=0.3,
        )


# ------------------------------------------------------------------ grounding

#: What a sentence may claim, and the fact that has to be true for it to stay.
#: Each entry is (pattern, fact key). A reason matching a pattern whose fact is
#: false is a fabricated fact about a licensed company, which is the failure
#: this whole module is arranged around.
CLAIM_TERMS: Final[tuple[tuple[str, str], ...]] = (
    (r"开户|開戶|银行|銀行", "bank_account_support"),
    (r"远程|遠程|线上|線上|无需到港|無需到港|免到港", "remote_onboarding"),
    (r"简体|簡體|普通话|普通話", "supports_simplified"),
    (r"非本地|非居民|外籍|内地股东|內地股東", "non_resident_shareholder_experience"),
    (r"认证|認證", "certified"),
    (r"评价|評價|评分|評分|口碑", "has_reviews"),
    (r"价格|價格|报价|報價|预算|預算|收费|收費", "has_published_price"),
    (r"成立|经营年|經營年|年经验|年經驗", "has_years_active"),
    (r"平台上报价|平台上報價|已认领|已認領", "claimed"),
)

_COMPILED: Final[tuple[tuple[re.Pattern[str], str], ...]] = tuple(
    (re.compile(pattern), key) for pattern, key in CLAIM_TERMS
)

#: Words that turn a claim into its opposite. A concern is normally an absence -
#: "平台上没有公开报价" cites the missing price, and it is true exactly when the
#: fact is false - so a sentence carrying one of these is grounded the other way
#: round. Saying a company lacks something it publishes is as much a fabrication
#: as saying it has something it does not.
_ABSENCE = re.compile(r"没有|沒有|未|无法|無法|不提供|尚未|缺|需要向|需向|不确定|不確定")


def candidate_facts(candidate: dict[str, Any]) -> dict[str, bool]:
    """The claims this candidate's summary supports, as booleans.

    Derived from the summary the model was given rather than from the database:
    the check has to be against what was on the page in front of it, or it is
    testing a different question than the one that was asked.
    """
    return {
        "bank_account_support": bool(candidate.get("bank_account_support")),
        "remote_onboarding": bool(candidate.get("remote_onboarding")),
        "supports_simplified": bool(candidate.get("supports_simplified"))
        or "mandarin" in list(candidate.get("languages", [])),
        "non_resident_shareholder_experience": bool(
            candidate.get("non_resident_shareholder_experience")
        ),
        "certified": bool(candidate.get("certified")),
        "has_reviews": int(candidate.get("verified_review_count") or 0) > 0,
        "has_published_price": candidate.get("price_from_hkd") is not None,
        "has_years_active": candidate.get("years_active") is not None,
        "claimed": bool(candidate.get("claimed")),
    }


def ground_reasons(sentences: Iterable[str], candidate: dict[str, Any]) -> list[str]:
    """Keep only sentences that cite something true about this candidate.

    Three ways to be dropped, and all three are the same mistake seen from
    different sides:

    * citing a fact the candidate does not have ("协助开户" for a company that
      does not) - a fabrication;
    * citing nothing checkable at all ("专业可靠") - a claim the platform cannot
      stand behind about a named licensed company;
    * containing a banned phrase (a guarantee, an absolute superlative) -
      COMPLIANCE section 2.

    The district and the service names are handled separately from the boolean
    facts because their "fact" is a string the sentence has to match.

    Concerns run through the same function on purpose. A concern is usually an
    absence - "平台上没有公开报价" - so a sentence carrying a negation is checked
    against the fact being false rather than true. That keeps one rule for both
    lists, and it catches the mirror-image fabrication of telling a buyer a
    company lacks something it actually publishes.
    """
    facts = candidate_facts(candidate)
    kept: list[str] = []
    for sentence in sentences:
        text = str(sentence).strip()
        if not text or check_banned_phrases(text):
            continue
        # An absence sentence claims the fact is missing, so the truth it has
        # to match is the fact's opposite.
        wants = not _ABSENCE.search(text)
        supported = False
        violated = False
        for pattern, key in _COMPILED:
            if pattern.search(text):
                if facts[key] is wants:
                    supported = True
                else:
                    violated = True
        if _mentions_district(text, candidate):
            supported = True
        if _mentions_service(text, candidate, wants=wants):
            supported = True
        if violated or not supported:
            continue
        kept.append(text)
    return kept


def _mentions_district(text: str, candidate: dict[str, Any]) -> bool:
    district = str(candidate.get("district") or "").strip()
    return bool(district) and district in text


def _mentions_service(text: str, candidate: dict[str, Any], *, wants: bool = True) -> bool:
    """Whether the sentence names a service the candidate's profile bears out.

    ``wants`` follows the sentence: a plain reason has to name a service the
    company publishes, and an absence sentence ("平台资料未列出这些服务：X") has
    to name one it does not.
    """
    offered = set(candidate.get("services", []))
    for code, label in _service_labels().items():
        if not label or label not in text:
            continue
        if (code in offered) is wants:
            return True
    return False


def _service_labels() -> dict[str, str]:
    from apps.providers.models import ServiceCategory

    return {value: str(label) for value, label in ServiceCategory.choices}


def screen_matches(data: MatchingOut, candidates: list[dict[str, Any]]) -> MatchingOut:
    """Everything that has to be true of a stored recommendation.

    Applied to the model's answer *and* to the fallback's, so there is one
    definition of an acceptable recommendation rather than one per path:

    * every item names a candidate that was actually screened - an id the model
      invented, or one it remembered from another requirement, is dropped;
    * no candidate appears twice;
    * every reason and concern survives ``ground_reasons``;
    * ranks are renumbered from one, so the list the buyer reads is the list
      that survived rather than one with holes where the dropped rows were.
    """
    by_id = {str(candidate.get("provider_id")): candidate for candidate in candidates}
    seen: set[str] = set()
    kept: list[MatchItem] = []
    for item in sorted(data.items, key=lambda entry: entry.rank):
        candidate = by_id.get(item.provider_id)
        if candidate is None or item.provider_id in seen:
            continue
        seen.add(item.provider_id)
        kept.append(
            item.model_copy(
                update={
                    "rank": len(kept) + 1,
                    "reasons": ground_reasons(item.reasons, candidate)[:3],
                    "concerns": ground_reasons(item.concerns, candidate)[:2],
                }
            )
        )
        if len(kept) >= MAX_MATCHES:
            break
    return data.model_copy(update={"items": kept})


# ------------------------------------------------------------------- fallback


def template_reasons(candidate: dict[str, Any], *, wanted: list[str]) -> list[str]:
    """Up to three facts about this candidate, written as sentences.

    Facts only, in the order a buyer weighs them: does it do the thing I asked
    for, can I do it from where I am, and has anyone said anything about it.
    """
    labels = _service_labels()
    reasons: list[str] = []

    covered = [code for code in wanted if code in set(candidate.get("services", []))]
    if covered:
        named = "、".join(str(labels.get(code, code)) for code in covered[:3])
        reasons.append(gettext("提供你需要的服务：%(services)s") % {"services": named})
    if candidate.get("bank_account_support"):
        reasons.append(gettext("提供银行开户协助"))
    if candidate.get("remote_onboarding"):
        reasons.append(gettext("可远程办理，无需亲自到港"))
    if candidate.get("supports_simplified"):
        reasons.append(gettext("提供简体中文服务"))
    if int(candidate.get("verified_review_count") or 0) > 0:
        reasons.append(
            gettext("有 %(count)s 条已核验评价")
            % {"count": int(candidate.get("verified_review_count") or 0)}
        )
    return reasons[:3]


def template_concerns(candidate: dict[str, Any], *, wanted: list[str]) -> list[str]:
    """Up to two things this candidate's profile does not answer.

    Written as gaps in what is published, never as doubts about the company: a
    company that has not filled in a form is a company that has not filled in a
    form (COMPLIANCE section 2).
    """
    labels = _service_labels()
    concerns: list[str] = []

    missing = [code for code in wanted if code not in set(candidate.get("services", []))]
    if missing:
        named = "、".join(str(labels.get(code, code)) for code in missing[:2])
        concerns.append(
            gettext("平台资料未列出这些服务：%(services)s，建议直接询问") % {"services": named}
        )
    if candidate.get("price_from_hkd") is None:
        concerns.append(gettext("平台上没有公开报价，价格需要向服务商确认"))
    return concerns[:2]


def unmatched_services(candidates: list[dict[str, Any]], *, wanted: list[str]) -> list[str]:
    """Requested services nobody in the pool publishes.

    Said out loud rather than left to be noticed: a shortlist that silently
    covers four of five requirements reads as if it covered five.
    """
    labels = _service_labels()
    offered = {code for candidate in candidates for code in candidate.get("services", [])}
    return [str(labels.get(code, code)) for code in wanted if code not in offered][:5]


def _budget(ctx: dict[str, Any]) -> str:
    low, high = ctx.get("budget_min_hkd"), ctx.get("budget_max_hkd")
    if low is None and high is None:
        return "not stated"
    floor = low if low is not None else "unspecified"
    ceiling = high if high is not None else "unspecified"
    return f"{floor} - {ceiling}"


def _candidate_block(candidate: dict[str, Any]) -> str:
    """One candidate as flat facts. No prose, so there is nothing to echo."""
    price = candidate.get("price_from_hkd")
    years = candidate.get("years_active") or "unknown"
    lines = [
        f"[{candidate.get('provider_id')}] {candidate.get('name')}",
        f"  district: {candidate.get('district') or 'unknown'}",
        f"  services: {', '.join(candidate.get('services', [])) or 'none published'}",
        f"  languages: {', '.join(candidate.get('languages', [])) or 'not stated'}",
        f"  simplified Chinese: {bool(candidate.get('supports_simplified'))}",
        f"  bank account help: {bool(candidate.get('bank_account_support'))}"
        f" (bank types: {', '.join(candidate.get('bank_types', [])) or 'not stated'})",
        f"  remote onboarding: {bool(candidate.get('remote_onboarding'))}",
        f"  non-resident shareholder experience: "
        f"{bool(candidate.get('non_resident_shareholder_experience'))}",
        f"  platform certified: {bool(candidate.get('certified'))}",
        f"  verified reviews: {candidate.get('verified_review_count') or 0}"
        f" (rating: {candidate.get('rating') if candidate.get('rating') is not None else 'none'})",
        f"  cheapest published price (HKD): {price if price is not None else 'not published'}",
        f"  years listed: {years}",
        f"  claimed this profile: {bool(candidate.get('claimed'))}",
        "",
    ]
    return "\n".join(lines)
