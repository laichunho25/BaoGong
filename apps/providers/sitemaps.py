"""Sitemaps for the public directory.

Only providers still on the register are submitted. A deregistered page stays
reachable and linked - it answers a real question - but asking a search engine
to index thousands of pages about companies that are no longer listed would
put stale licence information in front of buyers who never visited the site.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from apps.providers.selectors import directory_queryset
from apps.registry.models import LicenceStatus

if TYPE_CHECKING:
    from datetime import datetime

    from django.db.models import QuerySet

    from apps.providers.models import Provider


# django-stubs types Sitemap as generic; it is not subscriptable at runtime on
# Django 5.1, so the parameter cannot be supplied here (same as ModelAdmin).
class ProviderSitemap(Sitemap):  # type: ignore[type-arg]
    changefreq = "weekly"
    priority = 0.6
    limit = 5000

    def items(self) -> QuerySet[Provider]:
        return directory_queryset().filter(licensee__status=LicenceStatus.ACTIVE)

    def lastmod(self, item: Provider) -> datetime:
        return item.updated_at


class StaticViewSitemap(Sitemap):  # type: ignore[type-arg]
    changefreq = "daily"
    priority = 0.8

    def items(self) -> list[str]:
        return ["home", "providers:list", "content:list"]

    def location(self, item: str) -> str:
        return reverse(item)
