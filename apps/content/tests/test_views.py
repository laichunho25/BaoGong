"""The pages a search engine and a first-time buyer land on.

Public, unauthenticated, and never showing a draft - including in the sitemap,
which is the one place an unfinished page could be handed to Google without
anyone visiting it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.agents import services as agent_services
from apps.agents.advisor import AdvisorAgent
from apps.agents.schemas import AdvisorOut, Citation
from apps.content import views
from apps.content.models import Article, ArticleCategory

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client
    from pytest import MonkeyPatch

    from apps.accounts.models import User

pytestmark = pytest.mark.django_db


def test_the_index_is_readable_without_an_account(
    client: Client, make_article: Callable[..., Article]
) -> None:
    article = make_article()
    response = client.get(reverse("content:list"))
    assert response.status_code == 200
    assert article.title_zh_hans in response.content.decode()


def test_the_index_hides_drafts(client: Client, make_article: Callable[..., Article]) -> None:
    draft = make_article(published=False)
    html = client.get(reverse("content:list")).content.decode()
    assert draft.title_zh_hans not in html


def test_a_category_link_narrows_the_list(
    client: Client, make_article: Callable[..., Article]
) -> None:
    fees = make_article(category=ArticleCategory.FEES)
    banking = make_article(category=ArticleCategory.BANKING)
    html = client.get(reverse("content:list"), {"category": ArticleCategory.FEES}).content.decode()
    assert fees.title_zh_hans in html
    assert banking.title_zh_hans not in html


def test_an_invented_category_shows_everything_instead_of_erroring(
    client: Client, make_article: Callable[..., Article]
) -> None:
    article = make_article()
    response = client.get(reverse("content:list"), {"category": "nonsense"})
    assert response.status_code == 200
    assert article.title_zh_hans in response.content.decode()


def test_an_article_renders_its_body_and_the_disclaimer_it_owes_the_reader(
    client: Client, make_article: Callable[..., Article]
) -> None:
    article = make_article()
    html = client.get(article.get_absolute_url()).content.decode()
    assert "<h2>政府费用</h2>" in html
    assert "不构成法律、税务或任何专业意见" in html
    assert "并非香港公司注册处" in html


def test_a_draft_is_a_404_rather_than_a_403(
    client: Client, make_article: Callable[..., Article]
) -> None:
    draft = make_article(published=False)
    assert client.get(draft.get_absolute_url()).status_code == 404


def test_an_unknown_slug_is_a_404(client: Client) -> None:
    assert client.get(reverse("content:detail", args=["no-such-guide"])).status_code == 404


def test_a_script_in_the_body_never_reaches_the_rendered_page(
    client: Client, make_article: Callable[..., Article]
) -> None:
    article = make_article(body_md="## 标题\n\n正文<script>alert(1)</script>\n")
    html = client.get(article.get_absolute_url()).content.decode()
    assert "<script>alert(1)</script>" not in html


def test_the_sitemap_lists_published_guides_and_no_drafts(
    client: Client, make_article: Callable[..., Article]
) -> None:
    published = make_article()
    draft = make_article(published=False)
    index = client.get("/sitemap.xml").content.decode()
    assert "sitemap-guides.xml" in index
    xml = client.get("/sitemap-guides.xml").content.decode()
    assert published.get_absolute_url() in xml
    assert draft.get_absolute_url() not in xml


def test_the_guides_link_is_in_the_navigation_on_every_page(client: Client) -> None:
    html = client.get(reverse("content:list")).content.decode()
    assert html.count(reverse("content:list")) >= 2


# ------------------------------------------------------------------- ask box (A6)


@pytest.fixture(autouse=True)
def _empty_throttle_counters() -> None:
    cache.clear()


def test_the_ask_box_is_offered_only_to_somebody_with_an_account(
    client: Client, make_user: Callable[..., User]
) -> None:
    """Each question costs money, so an indexed page does not carry the box."""
    anonymous = client.get(reverse("content:list")).content.decode()
    assert reverse("content:ask") not in anonymous

    client.force_login(make_user())
    assert reverse("content:ask") in client.get(reverse("content:list")).content.decode()


def test_asking_without_logging_in_is_refused(client: Client) -> None:
    response = client.post(reverse("content:ask"), {"question": "注册香港公司要多久？"})
    assert response.status_code == 302


def test_a_question_is_answered_out_of_the_guides(
    client: Client,
    make_user: Callable[..., User],
    make_article: Callable[..., Article],
    monkeypatch: MonkeyPatch,
) -> None:
    """The view's job: hand the question over, render what comes back."""
    article = make_article()
    monkeypatch.setattr(
        agent_services,
        "answer_question",
        lambda *, question: agent_services.AdvisorAnswer(
            data=AdvisorOut(
                answer_zh_hans="注册费与商业登记费是固定的。",
                citations=[
                    Citation(article_slug=article.slug, chunk_ordinal=1, quote="注册费是固定的")
                ],
            ),
            sources=[article],
            used_fallback=False,
        ),
    )

    client.force_login(make_user())
    response = client.post(reverse("content:ask"), {"question": "注册香港公司的政府费用是多少？"})

    assert response.status_code == 200
    html = response.content.decode()
    assert "注册费与商业登记费是固定的。" in html
    assert article.get_absolute_url() in html


def test_an_empty_library_answers_without_paying_for_a_model_call(
    client: Client, make_user: Callable[..., User], monkeypatch: MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(AdvisorAgent, "run", lambda self, ctx: calls.append("called"))

    client.force_login(make_user())
    response = client.post(reverse("content:ask"), {"question": "注册香港公司要多久？"})

    assert response.status_code == 200
    assert calls == []
    assert "暂时没有可靠答案" in response.content.decode()


def test_a_question_too_short_to_be_one_is_sent_back_to_the_form(
    client: Client, make_user: Callable[..., User]
) -> None:
    client.force_login(make_user())
    response = client.post(reverse("content:ask"), {"question": "费"})

    assert response.status_code == 422


def test_an_account_that_asks_all_hour_is_stopped(
    client: Client, make_user: Callable[..., User], monkeypatch: MonkeyPatch
) -> None:
    """The daily budget protects the account by switching every agent off; this
    stops one reader before it gets that far."""
    monkeypatch.setattr(views, "ASKS_PER_HOUR", 2)
    client.force_login(make_user())
    for _ in range(2):
        assert client.post(reverse("content:ask"), {"question": "注册要多久？"}).status_code == 200

    response = client.post(reverse("content:ask"), {"question": "注册要多久？"})

    assert response.status_code == 429
    assert "稍后再试" in response.content.decode()


def test_the_answer_says_it_was_generated(client: Client, make_user: Callable[..., User]) -> None:
    """COMPLIANCE section 7: a reader is told what wrote the sentence."""
    client.force_login(make_user())
    html = client.post(reverse("content:ask"), {"question": "注册要多久？"}).content.decode()

    assert "未经人工审阅" in html
