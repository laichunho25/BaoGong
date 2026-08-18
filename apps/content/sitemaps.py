"""Sitemap for the education library.

Published articles only, which is what :func:`published_articles` already
guarantees - a draft submitted to a search engine would be indexed before
anyone had checked it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib.sitemaps import Sitemap

from apps.content.selectors import published_articles

if TYPE_CHECKING:
    from datetime import datetime

    from django.db.models import QuerySet

    from apps.content.models import Article


# django-stubs types Sitemap as generic; it is not subscriptable at runtime.
class ArticleSitemap(Sitemap):  # type: ignore[type-arg]
    changefreq = "monthly"
    priority = 0.7

    def items(self) -> QuerySet[Article]:
        return published_articles()

    def lastmod(self, item: Article) -> datetime:
        return item.updated_at
