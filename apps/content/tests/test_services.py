"""Publishing, and the passages that follow it.

The rule under test in most of these is one rule stated twice: a chunk exists
if and only if a reader can open the page it came from. That is what keeps the
Advisor agent from quoting an unpublished draft (AI_AGENTS A6).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.content import services
from apps.content.models import Article, ArticleCategory, ArticleStatus, Chunk

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.django_db


def test_publishing_stamps_a_date_and_builds_the_passages(
    make_article: Callable[..., Article],
) -> None:
    article = make_article()
    assert article.status == ArticleStatus.PUBLISHED
    assert article.published_at is not None
    assert article.chunks.count() == 2


def test_a_draft_is_saved_without_becoming_quotable(
    make_article: Callable[..., Article],
) -> None:
    article = make_article(published=False)
    assert article.pk is not None
    assert article.chunks.count() == 0


def test_an_article_without_a_body_cannot_be_published() -> None:
    article = Article(
        slug="empty",
        category=ArticleCategory.FEES,
        title_zh_hans="标题",
        summary="摘要",
        body_md="   ",
    )
    with pytest.raises(services.ArticleError):
        services.publish_article(article)
    assert Article.objects.count() == 0


def test_rewriting_the_body_replaces_the_passages(
    make_article: Callable[..., Article],
) -> None:
    article = make_article()
    old_ids = set(article.chunks.values_list("id", flat=True))

    article.body_md = "## 新章节\n\n只剩一段。\n"
    services.save_article(article)

    chunks = list(article.chunks.all())
    assert [chunk.text for chunk in chunks] == ["只剩一段。"]
    assert not old_ids & {chunk.id for chunk in chunks}


def test_unpublishing_takes_the_page_out_of_reach_but_keeps_its_first_date(
    make_article: Callable[..., Article],
) -> None:
    article = make_article()
    first_published = article.published_at

    services.unpublish_article(article)

    article.refresh_from_db()
    assert article.status == ArticleStatus.DRAFT
    assert article.published_at == first_published
    assert Chunk.objects.filter(article=article).count() == 0


def test_republishing_does_not_move_the_date_forward(
    make_article: Callable[..., Article],
) -> None:
    article = make_article()
    first_published = article.published_at
    services.unpublish_article(article)

    services.publish_article(article)

    assert article.published_at == first_published
    assert article.chunks.count() == 2


def test_the_author_is_recorded_once_and_not_overwritten_by_the_next_editor(
    make_article: Callable[..., Article],
    django_user_model: type,
) -> None:
    writer = django_user_model.objects.create_user(email="writer@example.com", password="pw12345!")
    editor = django_user_model.objects.create_user(email="editor@example.com", password="pw12345!")
    article = make_article(published=False)

    services.save_article(article, author=writer)
    services.save_article(article, author=editor)

    article.refresh_from_db()
    assert article.author == writer


def test_the_database_refuses_a_published_article_with_no_date() -> None:
    article = Article(
        slug="no-date",
        category=ArticleCategory.FEES,
        title_zh_hans="标题",
        summary="摘要",
        body_md="正文。",
        status=ArticleStatus.PUBLISHED,
        published_at=None,
    )
    with pytest.raises(IntegrityError):
        article.save()


def test_a_category_outside_the_list_is_refused_before_it_is_saved() -> None:
    article = Article(
        slug="odd-category",
        category="whatever",
        title_zh_hans="标题",
        summary="摘要",
        body_md="正文。",
    )
    with pytest.raises(ValidationError):
        services.save_article(article)
    assert Article.objects.count() == 0
