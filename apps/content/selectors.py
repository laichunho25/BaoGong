# Read queries only. Never writes (ARCHITECTURE section 3).
"""Reads from the education library.

Every function here starts from :func:`published_articles`. A draft is a page
that has not been checked yet, and this platform's whole claim is that what it
publishes has been checked - so "published" is a filter the callers cannot
forget rather than one they have to remember.

Search is substring matching, not Postgres full-text search. The corpus is
Simplified Chinese and this database has no Chinese tokeniser (``zhparser`` is
not installed anywhere the project deploys), so ``to_tsvector`` would treat a
whole sentence as one token and match almost nothing. A few dozen articles are
small enough that a scan is honest and fast; when the library outgrows that,
the fix is a tokeniser, not a bigger regular expression.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Q

from apps.content.models import Article, ArticleStatus, Chunk

if TYPE_CHECKING:
    from django.db.models import QuerySet

#: How many articles a list page shows before paginating.
PAGE_SIZE = 12


def published_articles() -> QuerySet[Article]:
    return Article.objects.filter(status=ArticleStatus.PUBLISHED, published_at__isnull=False)


def articles_in_category(category: str | None) -> QuerySet[Article]:
    queryset = published_articles()
    return queryset.filter(category=category) if category else queryset


def article_by_slug(slug: str) -> Article | None:
    return published_articles().filter(slug=slug).select_related("author").first()


def latest_articles(limit: int = 3) -> QuerySet[Article]:
    """The newest few, for the home page and for the foot of an article."""
    return published_articles()[:limit]


def related_articles(article: Article, limit: int = 3) -> QuerySet[Article]:
    """Other pages in the same category. Same question, different angle."""
    return published_articles().filter(category=article.category).exclude(pk=article.pk)[:limit]


def search_articles(query: str, limit: int = 10) -> QuerySet[Article]:
    term = query.strip()
    if not term:
        return published_articles().none()
    return published_articles().filter(
        Q(title_zh_hans__icontains=term)
        | Q(title_zh_hant__icontains=term)
        | Q(title_en__icontains=term)
        | Q(summary__icontains=term)
        | Q(body_md__icontains=term)
    )[:limit]


def search_chunks(query: str, limit: int = 8) -> QuerySet[Chunk]:
    """Passages that mention the query, for the Advisor agent to cite.

    Only chunks of published articles: the agent may not quote a page a reader
    cannot open (AI_AGENTS A6). Ranking is by article recency for now - once an
    embedding provider is configured this is where the vector search goes, and
    the callers should not have to change when it does.
    """
    term = query.strip()
    if not term:
        return Chunk.objects.none()
    return (
        Chunk.objects.filter(
            article__status=ArticleStatus.PUBLISHED,
            article__published_at__isnull=False,
            text__icontains=term,
        )
        .select_related("article")
        .order_by("-article__published_at", "ordinal")[:limit]
    )
