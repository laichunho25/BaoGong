"""The pages a search engine and a first-time buyer land on.

Public, unauthenticated, and never showing a draft - including in the sitemap,
which is the one place an unfinished page could be handed to Google without
anyone visiting it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.urls import reverse

from apps.content.models import Article, ArticleCategory

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

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
