"""The editing desk.

Only what is customised: that saving from the console goes through the service
(so the passages follow the body), and that the publish action refuses an
article the service would refuse.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from apps.content.models import Article, ArticleCategory, ArticleStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

    from apps.accounts.models import User

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:content_article_changelist"


@pytest.fixture
def editor(django_user_model: type) -> User:
    user = django_user_model.objects.create_user(email="editor@example.com", password="pw12345!")
    user.is_staff = True
    user.is_superuser = True
    user.save()
    return user


def test_saving_from_the_console_rebuilds_the_passages(
    client: Client, editor: User, make_article: Callable[..., Article]
) -> None:
    article = make_article()
    client.force_login(editor)

    client.post(
        reverse("admin:content_article_change", args=[article.pk]),
        {
            "slug": article.slug,
            "category": article.category,
            "status": ArticleStatus.PUBLISHED,
            "published_at_0": article.published_at.date().isoformat(),  # type: ignore[union-attr]
            "published_at_1": "09:00:00",
            "title_zh_hans": article.title_zh_hans,
            "title_zh_hant": "",
            "title_en": "",
            "summary": article.summary,
            "body_md": "## 新章节\n\n改写后的唯一一段。\n",
            "seo": "{}",
            "chunks-TOTAL_FORMS": "0",
            "chunks-INITIAL_FORMS": "0",
        },
    )

    assert [chunk.text for chunk in article.chunks.all()] == ["改写后的唯一一段。"]


def test_the_publish_action_publishes_and_the_author_is_the_editor(
    client: Client, editor: User, make_article: Callable[..., Article]
) -> None:
    article = make_article(published=False)
    client.force_login(editor)

    client.post(reverse(CHANGELIST), {"action": "publish", "_selected_action": [str(article.pk)]})

    article.refresh_from_db()
    assert article.status == ArticleStatus.PUBLISHED
    assert article.author == editor
    assert article.chunks.count() == 2


def test_the_publish_action_reports_an_article_it_cannot_publish(
    client: Client, editor: User
) -> None:
    empty = Article.objects.create(
        slug="empty",
        category=ArticleCategory.FEES,
        title_zh_hans="标题",
        summary="",
        body_md="",
    )
    client.force_login(editor)

    response = client.post(
        reverse(CHANGELIST),
        {"action": "publish", "_selected_action": [str(empty.pk)]},
        follow=True,
    )

    empty.refresh_from_db()
    assert empty.status == ArticleStatus.DRAFT
    assert any("empty" in str(message) for message in get_messages(response.wsgi_request))


def test_the_unpublish_action_takes_the_passages_with_it(
    client: Client, editor: User, make_article: Callable[..., Article]
) -> None:
    article = make_article()
    client.force_login(editor)

    client.post(reverse(CHANGELIST), {"action": "unpublish", "_selected_action": [str(article.pk)]})

    article.refresh_from_db()
    assert article.status == ArticleStatus.DRAFT
    assert article.chunks.count() == 0


def test_passages_cannot_be_typed_by_hand(
    client: Client, editor: User, make_article: Callable[..., Article]
) -> None:
    """They are derived; an editable copy would one day disagree with the body."""
    article = make_article()
    client.force_login(editor)

    html = client.get(reverse("admin:content_article_change", args=[article.pk])).content.decode()

    assert "chunks-0-text" not in html
