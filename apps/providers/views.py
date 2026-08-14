"""Public directory. Reads through selectors only; no ORM writes here."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.utils.safestring import SafeString, mark_safe
from django.utils.translation import gettext_lazy as _

from apps.providers import selectors
from apps.providers.models import BankType, Language, Provider, Tier
from apps.registry.selectors import registry_last_synced_at

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

PAGE_SIZE = 20

# Labels live here, beside the options they name, rather than as an if-chain in
# the template. SortOption itself stays free of UI copy so selectors.py has no
# translation dependency.
SORT_LABELS = (
    (selectors.SortOption.RECOMMENDED, _("推荐排序")),
    (selectors.SortOption.RATING, _("评分由高到低")),
    (selectors.SortOption.NAME, _("按名称")),
    (selectors.SortOption.NEWEST, _("最新登记")),
)


def _shared_context(request: HttpRequest) -> dict[str, Any]:
    """Attribution COMPLIANCE section 1 requires wherever registry data shows."""
    return {"registry_last_synced_at": registry_last_synced_at()}


def provider_list(request: HttpRequest) -> HttpResponse:
    """The directory.

    HTMX swaps only the results block, so the same view serves both the full
    page and the fragment. The querystring is the whole state - it has to be
    shareable, and hx-push-url keeps the address bar honest.
    """
    filters = selectors.DirectoryFilters.from_request(request.GET)
    page_number = request.GET.get("page", "1")
    paginator = Paginator(selectors.filter_directory(filters), PAGE_SIZE)
    page = paginator.get_page(page_number)

    context: dict[str, Any] = {
        "page": page,
        "filters": filters,
        "districts": selectors.available_districts(),
        "languages": Language.choices,
        "bank_types": BankType.choices,
        "tiers": Tier.choices,
        "sort_options": SORT_LABELS,
        "total": paginator.count,
        "max_compare": selectors.MAX_COMPARE,
        **_shared_context(request),
    }

    if request.headers.get("HX-Request"):
        return render(request, "providers/_results.html", context)
    return render(request, "providers/list.html", context)


def _organization_jsonld(request: HttpRequest, provider: Provider) -> SafeString:
    """schema.org Organization for the detail page.

    ``aggregateRating`` is emitted only when verified reviews exist. Search
    results are a page the platform does not control, so an unearned 5.00 must
    not leak into them any more than it may appear on the page itself
    (RATING_SYSTEM section 4).
    """
    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": provider.display_name,
        "url": request.build_absolute_uri(provider.get_absolute_url()),
        "identifier": provider.licensee.licence_no if provider.licensee else provider.slug,
    }
    if provider.licensee and provider.licensee.business_address:
        data["address"] = {
            "@type": "PostalAddress",
            "streetAddress": provider.licensee.business_address,
            "addressCountry": "HK",
        }
    if provider.website:
        data["sameAs"] = [provider.website]
    if provider.has_verified_reviews and provider.rating_cached is not None:
        data["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": str(provider.rating_cached),
            "reviewCount": provider.verified_review_count,
            "bestRating": "5",
        }

    # "<" is escaped so that a name containing "</script>" cannot break out of
    # the tag; json.dumps does not do this on its own.
    return mark_safe(json.dumps(data, ensure_ascii=False).replace("<", "\\u003c"))


def provider_detail(request: HttpRequest, slug: str) -> HttpResponse:
    provider = selectors.get_provider_detail(slug)
    if provider is None:
        raise Http404("No such provider")

    return render(
        request,
        "providers/detail.html",
        {
            "provider": provider,
            "licensee": provider.licensee,
            "offerings": provider.offerings.all(),
            "certifications": [cert for cert in provider.certifications.all() if cert.is_current],
            "jsonld": _organization_jsonld(request, provider),
            **_shared_context(request),
        },
    )


def provider_compare(request: HttpRequest) -> HttpResponse:
    """Side-by-side comparison of up to three providers.

    Over the limit the extras are dropped rather than rejected: a shared link
    with four ids should still show something useful.
    """
    raw = request.GET.get("ids", "")
    providers = selectors.get_providers_for_compare([part.strip() for part in raw.split(",")])

    return render(
        request,
        "providers/compare.html",
        {
            "providers": providers,
            "max_compare": selectors.MAX_COMPARE,
            **_shared_context(request),
        },
    )
