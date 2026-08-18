"""Reads, and the draft they must never return.

Every selector here starts from ``published_articles``; the point of the tests
is that a draft cannot leak through any of them, including the one the Advisor
agent retrieves with.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.utils import translation

from apps.content import selectors, services
from apps.content.models import Article, ArticleCategory

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.django_db


def test_drafts_are_invisible_to_every_read(make_article: Callable[..., Article]) -> None:
    make_article(published=False, slug="hidden")
    assert list(selectors.published_articles()) == []
    assert selectors.article_by_slug("hidden") is None
    assert list(selectors.search_articles("政府费用")) == []
    assert list(selectors.search_chunks("注册费")) == []


def test_a_category_filter_narrows_and_no_filter_does_not(
    make_article: Callable[..., Article],
) -> None:
    fees = make_article(category=ArticleCategory.FEES)
    make_article(category=ArticleCategory.BANKING)
    assert list(selectors.articles_in_category(ArticleCategory.FEES)) == [fees]
    assert selectors.articles_in_category(None).count() == 2


def test_related_articles_share_a_category_and_exclude_the_page_itself(
    make_article: Callable[..., Article],
) -> None:
    article = make_article(category=ArticleCategory.BANKING)
    sibling = make_article(category=ArticleCategory.BANKING)
    make_article(category=ArticleCategory.FEES)
    assert list(selectors.related_articles(article)) == [sibling]


def test_search_matches_the_body_a_reader_would_have_typed(
    make_article: Callable[..., Article],
) -> None:
    article = make_article()
    assert list(selectors.search_articles("商业登记费")) == [article]
    assert list(selectors.search_articles("   ")) == []


def test_chunk_search_returns_passages_the_agent_can_cite(
    make_article: Callable[..., Article],
) -> None:
    article = make_article()
    chunks = list(selectors.search_chunks("商业登记费"))
    assert len(chunks) == 1
    assert chunks[0].article == article
    assert chunks[0].heading == "政府费用"


def test_unpublishing_removes_an_article_from_retrieval(
    make_article: Callable[..., Article],
) -> None:
    article = make_article()
    services.unpublish_article(article)
    assert list(selectors.search_chunks("商业登记费")) == []


def test_a_title_falls_back_to_simplified_when_a_language_is_untranslated(
    make_article: Callable[..., Article],
) -> None:
    article = make_article(title_zh_hans="费用说明", title_zh_hant="費用說明", title_en="")
    with translation.override("zh-hant"):
        assert article.title == "費用說明"
    with translation.override("en"):
        assert article.title == "费用说明"
