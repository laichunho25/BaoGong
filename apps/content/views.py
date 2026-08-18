# Thin HTMX/HTML views. Never write to the ORM directly (CLAUDE.md section 3).
"""The education library, as a reader sees it.

Public and unauthenticated: these pages are how somebody who has never
registered a Hong Kong company finds out what the questions are, and most of
them arrive from a search engine rather than from the site (PRD section 3.6).

A draft is a 404 rather than a 403. Whether an unpublished page exists is not
information a visitor is owed, and a "not authorised" answer says it does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render

from apps.content import selectors
from apps.content.models import ArticleCategory
from apps.content.rendering import render_markdown

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse


def article_index(request: HttpRequest) -> HttpResponse:
    category = request.GET.get("category") or ""
    if category and category not in ArticleCategory.values:
        category = ""
    paginator = Paginator(selectors.articles_in_category(category or None), selectors.PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page", "1"))
    return render(
        request,
        "content/article_list.html",
        {
            "page_obj": page,
            "categories": ArticleCategory.choices,
            "active_category": category,
        },
    )


def article_detail(request: HttpRequest, slug: str) -> HttpResponse:
    article = selectors.article_by_slug(slug)
    if article is None:
        raise Http404
    return render(
        request,
        "content/article_detail.html",
        {
            "article": article,
            "body_html": render_markdown(article.body_md),
            "related": selectors.related_articles(article),
        },
    )
