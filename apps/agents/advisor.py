"""A6 - answer an education question out of the platform's own guides.

AI_AGENTS A6. The design is one sentence repeated in four places: **the agent
may only say what an article of ours says.** Retrieval hands it passages, the
prompt tells it those passages are all that exists, the schema makes it cite
them, and :func:`screen_answer` deletes every citation that is not verbatim in
the passage it names. An answer with nothing left is not published - the reader
is told the library has no reliable answer and given the articles instead.

Refusing is therefore a designed outcome, not a failure. A first-time buyer
cannot tell a reliable answer from a confident one, which is exactly why a
confident guess is the worst thing this platform could hand them.

Three more refusals, each with a compliance edge:

* **No company may be named** (COMPLIANCE section 5). The platform compares
  licensed firms neutrally; an answer that recommends one is that firm's
  advertisement, whether or not anybody paid for it. Every answer is checked
  against the register's own names, and a hit drops the whole answer - a
  sentence recommending a company cannot be repaired by editing it.
* **No banned phrase** (COMPLIANCE section 2) - a guarantee about a bank
  account, a claim to official status, an absolute superlative.
* **Tax planning, offshore exemption and investment returns** get the answer
  plus a line saying to ask a licensed professional. The line is added here
  rather than trusted to the prompt, because it is the one sentence a wrong
  answer most needs to carry.

The fallback publishes no generated text at all: it hands back the top three
passages as links. With the model switched off a reader still gets our articles,
which is what the answer was made of in the first place.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, ClassVar, Final

from django.core.cache import cache
from django.utils.translation import gettext

from apps.agents.base import BaseAgent
from apps.agents.schemas import AdvisorOut, Citation
from apps.core.compliance import check_banned_phrases

if TYPE_CHECKING:
    from collections.abc import Sequence

#: How many passages retrieval hands the model (AI_AGENTS A6).
RETRIEVAL_LIMIT = 8

#: How many articles the refusal points at. Three is a next step; ten is a
#: search results page nobody asked for.
FALLBACK_SOURCES = 3

#: Cut from the start of a passage when the fallback quotes it. Long enough to
#: show what the passage is about, and verbatim so the quote is still a quote.
FALLBACK_QUOTE_CHARS = 80

#: COMPLIANCE section 7, and the same sentence the guide pages carry. The page
#: a buyer reads and the answer a model gives out of that page must not
#: disagree about what this is.
DISCLAIMER: Final = "以上为一般资讯，不构成法律或专业意见。"

#: Added when the question touches something only a licensed professional may
#: answer. AI_AGENTS A6 calls this "downgrading to general information".
PROFESSIONAL_NOTE: Final = "这类问题涉及税务或法律判断，请咨询持牌会计师或律师后再决定。"

_SENSITIVE = re.compile(
    "税务筹划|稅務籌劃|税务规划|稅務規劃|避税|避稅|逃税|逃稅|离岸豁免|離岸豁免"
    "|投资回报|投資回報|报酬率|報酬率|节税|節稅"
)

#: Company names shorter than this are not matched against an answer: a
#: four-character register entry is as likely to be an ordinary phrase as a
#: firm, and dropping good answers over "ABC" helps nobody.
MIN_NAME_CHARS = 6

_NAME_CACHE_KEY = "agents:advisor:licensed-names:v1"
#: Five minutes. The register changes once a day; this only has to be fresher
#: than the mistake it prevents.
_NAME_CACHE_TTL = 300


class AdvisorAgent(BaseAgent):
    name: ClassVar[str] = "advisor"
    model: ClassVar[str] = "claude-sonnet-5"
    prompt_file: ClassVar[str] = "advisor_v1.md"
    output_schema: ClassVar[type[AdvisorOut]] = AdvisorOut
    max_tokens: ClassVar[int] = 1536
    object_type: ClassVar[str] = "content.Article"

    def object_id(self, ctx: dict[str, Any]) -> str:
        """No row is the subject of a question, so nothing is recorded here.

        The passages that were retrieved are in the run's input hash; the
        question itself is not stored, because a question is something a person
        typed about their own situation (COMPLIANCE section 4).
        """
        return ""

    def build_user_prompt(self, ctx: dict[str, Any]) -> str:
        passages = list(ctx.get("passages", []))
        lines = [
            "--- the question ---",
            str(ctx.get("question", "")).strip(),
            "",
            f"--- passages ({len(passages)}), the only thing you know ---",
            "",
        ]
        for passage in passages:
            lines.append(_passage_block(passage))
        return "\n".join(lines)

    def fallback(self, ctx: dict[str, Any], reason: str) -> AdvisorOut:
        """The articles, and no sentence anybody generated.

        A rule-based answer to "how long does incorporation take" would be a
        rule-based answer about a real fee to a real buyer, so there is not one.
        """
        return refusal(list(ctx.get("passages", [])))


# ------------------------------------------------------------------- screening


def screen_answer(
    data: AdvisorOut, passages: Sequence[dict[str, Any]], *, question: str = ""
) -> AdvisorOut:
    """Everything that has to be true before an answer is shown to anybody.

    Applied to the model's answer and to the fallback's alike, so there is one
    definition of a publishable answer rather than one per path.
    """
    index = {
        (str(passage.get("article_slug")), int(passage.get("ordinal", 0))): str(
            passage.get("text", "")
        )
        for passage in passages
    }
    kept = [citation for citation in data.citations if is_grounded(citation, index)]
    text = data.answer_zh_hans.strip()

    if data.out_of_scope or not text or not kept:
        return refusal(passages)
    if check_banned_phrases(text):
        return refusal(passages)
    if mentions_licensed_company(text):
        return refusal(passages)

    if _SENSITIVE.search(text) or _SENSITIVE.search(question):
        text = f"{text}\n\n{PROFESSIONAL_NOTE}"
    if DISCLAIMER not in text:
        text = f"{text}\n\n{DISCLAIMER}"
    return data.model_copy(update={"answer_zh_hans": text, "citations": kept})


def refusal(passages: Sequence[dict[str, Any]]) -> AdvisorOut:
    """What the reader gets when the library cannot answer.

    The passages come back as citations rather than as prose: they are real
    quotes from real articles, so the reader can see what the platform does
    have and decide whether it is close enough to their question.
    """
    citations = [
        Citation(
            article_slug=str(passage.get("article_slug", "")),
            chunk_ordinal=int(passage.get("ordinal", 1)),
            quote=str(passage.get("text", ""))[:FALLBACK_QUOTE_CHARS],
        )
        for passage in list(passages)[:FALLBACK_SOURCES]
        if passage.get("article_slug")
    ]
    body = gettext("这个问题我们的资料库暂时没有可靠答案，建议咨询持牌专业人士。")
    if citations:
        body = gettext(
            "这个问题我们的资料库暂时没有可靠答案，建议咨询持牌专业人士。"
            "下面这几篇指南可能相关，你可以自己判断。"
        )
    return AdvisorOut(
        answer_zh_hans=f"{body}\n\n{DISCLAIMER}",
        citations=citations,
        out_of_scope=True,
        confidence=0.0,
    )


def is_grounded(citation: Citation, index: dict[tuple[str, int], str]) -> bool:
    """Whether this citation quotes a passage that was actually retrieved.

    Whitespace is normalised on both sides before the comparison because a
    model that re-wraps a line has not changed the words; anything else is a
    quote of something nobody wrote.
    """
    text = index.get((citation.article_slug, citation.chunk_ordinal))
    if text is None:
        return False
    return _normalise(citation.quote) in _normalise(text)


def _normalise(value: str) -> str:
    return re.sub(r"\s+", "", value)


def mentions_licensed_company(text: str) -> bool:
    """Whether the answer names a company on the TCSP register.

    Checked against the register rather than against a list of firms we work
    with: the rule is that no licensed company gets named in an answer, and the
    register is the list of licensed companies.
    """
    haystack = text.lower()
    return any(name in haystack for name in licensed_names())


def licensed_names() -> tuple[str, ...]:
    """Lower-cased register names, cached for a few minutes.

    Read here rather than passed in because the caller of an answer has no
    reason to know about the register, and a check somebody has to remember to
    perform is a check that will be forgotten.
    """
    cached: tuple[str, ...] | None = cache.get(_NAME_CACHE_KEY)
    if cached is not None:
        return cached

    from apps.registry.models import Licensee

    names = {
        name.strip().lower()
        for pair in Licensee.objects.values_list("name_en", "name_zh")
        for name in pair
        if name and len(name.strip()) >= MIN_NAME_CHARS
    }
    result = tuple(sorted(names))
    cache.set(_NAME_CACHE_KEY, result, _NAME_CACHE_TTL)
    return result


def _passage_block(passage: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"[{passage.get('article_slug')} #{passage.get('ordinal')}]"
            f" {passage.get('title') or ''}".rstrip(),
            f"  section: {passage.get('heading') or 'none'}",
            f"  text: {passage.get('text', '')}",
            "",
        ]
    )
