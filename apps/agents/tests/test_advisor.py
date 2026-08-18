"""A6's screen, which is where the agent's promises are actually kept.

Whether the model writes a good answer is the eval harness's question. The
question here is narrower and more important: nothing reaches a reader unless
every sentence of it is backed by a passage we retrieved, and the four things
this platform must never say cannot get through even if the model says them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from django.core.cache import cache

from apps.agents import advisor
from apps.agents.advisor import (
    DISCLAIMER,
    PROFESSIONAL_NOTE,
    AdvisorAgent,
    mentions_licensed_company,
    refusal,
    screen_answer,
)
from apps.agents.schemas import AdvisorOut, Citation

if TYPE_CHECKING:
    from collections.abc import Callable

PASSAGE_TEXT = "在香港注册一家私人有限公司，公司注册处收取的注册费与商业登记费是固定的。"

# Every screened answer is checked against the register, so every test here
# reads from the database - and none of them may inherit another test's cached
# copy of it.
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _forget_cached_names() -> None:
    cache.clear()


def _passages() -> list[dict[str, Any]]:
    return [
        {
            "article_slug": "fees-guide",
            "ordinal": 1,
            "heading": "政府费用",
            "title": "香港公司注册费用说明",
            "text": PASSAGE_TEXT,
        },
        {
            "article_slug": "banking-guide",
            "ordinal": 2,
            "heading": "开户准备",
            "title": "银行开户要准备什么",
            "text": "银行会要求解释公司的业务实质，包括客户来自哪里、资金如何流动。",
        },
    ]


def _answer(text: str, *, quote: str = PASSAGE_TEXT, **overrides: Any) -> AdvisorOut:
    data: dict[str, Any] = {
        "answer_zh_hans": text,
        "citations": [Citation(article_slug="fees-guide", chunk_ordinal=1, quote=quote)],
        "out_of_scope": False,
        "confidence": 0.8,
    }
    data.update(overrides)
    return AdvisorOut(**data)


# -------------------------------------------------------------------- grounding


def test_an_answer_backed_by_a_retrieved_passage_survives() -> None:
    out = screen_answer(_answer("注册费与商业登记费由公司注册处收取，是固定的。"), _passages())

    assert not out.out_of_scope
    assert out.citations[0].article_slug == "fees-guide"


def test_a_quote_nobody_wrote_is_dropped_and_takes_the_answer_with_it() -> None:
    """The failure mode this agent exists to prevent: a plausible invention."""
    out = screen_answer(_answer("三天就能办好。", quote="三个工作日内一定完成登记。"), _passages())

    assert out.out_of_scope
    assert "暂时没有可靠答案" in out.answer_zh_hans


def test_a_quote_from_a_passage_we_did_not_retrieve_is_not_grounded() -> None:
    """Right words, wrong passage. The citation has to point where it claims."""
    out = screen_answer(
        _answer("注册费是固定的。", quote=PASSAGE_TEXT).model_copy(
            update={
                "citations": [
                    Citation(article_slug="fees-guide", chunk_ordinal=9, quote=PASSAGE_TEXT)
                ]
            }
        ),
        _passages(),
    )

    assert out.out_of_scope


def test_a_re_wrapped_quote_still_counts_as_the_same_words() -> None:
    out = screen_answer(
        _answer("注册费是固定的。", quote="在香港注册一家私人有限公司，\n公司注册处收取的注册费"),
        _passages(),
    )

    assert not out.out_of_scope


def test_an_answer_with_no_citations_at_all_is_refused() -> None:
    out = screen_answer(_answer("大概两周。", citations=[]), _passages())

    assert out.out_of_scope


def test_an_empty_answer_is_refused_rather_than_shown_blank() -> None:
    out = screen_answer(_answer("   "), _passages())

    assert out.out_of_scope
    assert DISCLAIMER in out.answer_zh_hans


def test_the_model_saying_it_is_out_of_scope_is_believed() -> None:
    out = screen_answer(_answer("我不确定。", out_of_scope=True), _passages())

    assert out.out_of_scope


# ------------------------------------------------------------------- compliance


def test_a_promised_bank_outcome_drops_the_whole_answer() -> None:
    """COMPLIANCE section 2. Not edited out - the answer is thrown away."""
    out = screen_answer(_answer("按指南准备文件就保证开户成功。"), _passages())

    assert out.out_of_scope
    assert "保证开户成功" not in out.answer_zh_hans


def test_a_claim_of_official_status_drops_the_whole_answer() -> None:
    out = screen_answer(_answer("本平台经官方认证，注册费是固定的。"), _passages())

    assert out.out_of_scope


def test_naming_a_licensed_company_drops_the_whole_answer(
    make_licensee: Callable[..., Any],
) -> None:
    """COMPLIANCE section 5: a recommendation is an advertisement either way."""
    make_licensee(name_en="Harbour Corporate Services Limited")
    out = screen_answer(
        _answer("可以找 Harbour Corporate Services Limited 办理，注册费是固定的。"), _passages()
    )

    assert out.out_of_scope


def test_a_short_register_name_does_not_swallow_ordinary_answers(
    make_licensee: Callable[..., Any],
) -> None:
    """A four-letter entry is as likely to be a word as a firm (MIN_NAME_CHARS)."""
    make_licensee(name_en="ABC")

    assert not mentions_licensed_company("abc 公司注册费是固定的。")


def test_the_register_is_read_once_and_then_cached(make_licensee: Callable[..., Any]) -> None:
    """The register changes daily; reading it per answer would be per question."""
    make_licensee(name_en="First Corporate Services Limited")
    first = advisor.licensed_names()
    make_licensee(name_en="Second Corporate Services Limited", licence_no="TC998877")

    assert advisor.licensed_names() == first


def test_a_tax_question_gets_the_professional_note() -> None:
    """The one sentence a wrong answer most needs to carry."""
    out = screen_answer(_answer("注册费是固定的。"), _passages(), question="离岸豁免要怎么申请？")

    assert PROFESSIONAL_NOTE in out.answer_zh_hans


def test_an_ordinary_question_does_not_get_the_professional_note() -> None:
    out = screen_answer(_answer("注册费是固定的。"), _passages(), question="注册费是多少？")

    assert PROFESSIONAL_NOTE not in out.answer_zh_hans


def test_every_answer_carries_the_disclaimer() -> None:
    out = screen_answer(_answer("注册费是固定的。"), _passages())

    assert DISCLAIMER in out.answer_zh_hans


def test_the_disclaimer_is_not_repeated_when_the_model_already_wrote_it() -> None:
    out = screen_answer(_answer(f"注册费是固定的。\n\n{DISCLAIMER}"), _passages())

    assert out.answer_zh_hans.count(DISCLAIMER) == 1


# --------------------------------------------------------------------- fallback


def test_the_fallback_generates_no_prose_and_hands_back_real_quotes() -> None:
    out = AdvisorAgent().fallback({"passages": _passages()}, "disabled")

    assert out.out_of_scope
    assert out.confidence == 0.0
    assert [citation.quote for citation in out.citations] == [
        passage["text"][: advisor.FALLBACK_QUOTE_CHARS] for passage in _passages()
    ]


def test_the_fallback_survives_having_nothing_to_point_at() -> None:
    out = refusal([])

    assert out.citations == []
    assert DISCLAIMER in out.answer_zh_hans


def test_the_fallbacks_own_citations_pass_the_screen() -> None:
    """The refusal quotes retrieved passages, so it must be groundable too."""
    out = screen_answer(refusal(_passages()), _passages())

    assert out.out_of_scope
    assert out.citations


def test_the_question_is_never_recorded_as_the_subject_of_the_run() -> None:
    """COMPLIANCE section 4: a question is about somebody's own situation."""
    assert AdvisorAgent().object_id({"question": "注册要多久？"}) == ""


def test_the_prompt_shows_the_model_which_passage_a_quote_would_come_from() -> None:
    prompt = AdvisorAgent().build_user_prompt(
        {"question": "注册费是多少？", "passages": _passages()}
    )

    assert "fees-guide #1" in prompt
    assert PASSAGE_TEXT in prompt
