"""Read queries for the public directory.

Every queryset here starts from ``directory_queryset`` so that the joins the
list page needs are declared once. A missing ``select_related`` on this page
is 20 extra queries per screen, not one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.db.models import Case, F, IntegerField, Prefetch, Q, QuerySet, When

from apps.providers.models import (
    BankType,
    ClaimDecision,
    ClaimStatus,
    Language,
    Provider,
    ProviderClaim,
    ServiceOffering,
    Tier,
)
from apps.registry.models import LicenceStatus

if TYPE_CHECKING:
    from collections.abc import Sequence

    from django.http import QueryDict

MAX_COMPARE = 3


class SortOption:
    RECOMMENDED = "recommended"
    RATING = "rating"
    NAME = "name"
    NEWEST = "newest"

    CHOICES = (RECOMMENDED, RATING, NAME, NEWEST)
    DEFAULT = RECOMMENDED


def directory_queryset() -> QuerySet[Provider]:
    """Published providers with the licensee row every card and page reads.

    ``select_related`` on the licensee is not an optimisation here but a
    correctness requirement: the card shows the official name, district and
    licence status, and COMPLIANCE section 1 requires the source and sync time
    alongside them.
    """
    return Provider.objects.filter(is_published=True).select_related("licensee")


@dataclass(frozen=True, slots=True)
class DirectoryFilters:
    """The filter state of one list-page request, parsed from the query string."""

    query: str = ""
    district: str = ""
    language: str = ""
    bank_support: bool = False
    remote_onboarding: bool = False
    tier: str = ""
    bank_type: str = ""
    include_deregistered: bool = False
    sort: str = SortOption.DEFAULT

    @classmethod
    def from_request(cls, params: QueryDict) -> DirectoryFilters:
        """Build from ``request.GET``. Unknown values fall back to the default.

        A bad filter value returns the unfiltered page rather than an error:
        these URLs get shared and edited by hand, and a 400 helps nobody.
        """
        language = params.get("language", "")
        tier = params.get("tier", "")
        bank_type = params.get("bank_type", "")
        sort = params.get("sort", "")
        return cls(
            query=params.get("q", "").strip(),
            district=params.get("district", "").strip(),
            language=language if language in Language.values else "",
            bank_support=params.get("bank_support") == "1",
            remote_onboarding=params.get("remote_onboarding") == "1",
            tier=tier if tier in Tier.values else "",
            bank_type=bank_type if bank_type in BankType.values else "",
            include_deregistered=params.get("include_deregistered") == "1",
            sort=sort if sort in SortOption.CHOICES else SortOption.DEFAULT,
        )

    @property
    def is_active(self) -> bool:
        """Whether anything is narrowing the list, for the 'clear filters' UI."""
        return any(
            [
                self.query,
                self.district,
                self.language,
                self.bank_support,
                self.remote_onboarding,
                self.tier,
                self.bank_type,
                self.include_deregistered,
            ]
        )


def _apply_sort(queryset: QuerySet[Provider], sort: str) -> QuerySet[Provider]:
    # Providers still on the register always sort ahead of those removed from
    # it, whatever the chosen sort - a deregistered company is findable, but it
    # is never the first thing a buyer sees.
    on_register_first = Case(
        When(licensee__status=LicenceStatus.ACTIVE, then=0), default=1, output_field=IntegerField()
    )
    if sort == SortOption.NAME:
        return queryset.order_by(on_register_first, "licensee__name_en")
    if sort == SortOption.RATING:
        # nulls_last: a provider with no verified reviews has no score, and
        # must not outrank one that earned a low score.
        return queryset.order_by(
            on_register_first, F("rating_cached").desc(nulls_last=True), "licensee__name_en"
        )
    if sort == SortOption.NEWEST:
        return queryset.order_by(on_register_first, "-licensee__first_seen_at")
    return queryset.order_by(on_register_first, "-ranking_score", "licensee__name_en")


def filter_directory(filters: DirectoryFilters) -> QuerySet[Provider]:
    """Apply one request's filters. Read-only; no side effects."""
    queryset = directory_queryset()

    if not filters.include_deregistered:
        queryset = queryset.filter(licensee__status=LicenceStatus.ACTIVE)

    if filters.query:
        queryset = queryset.filter(
            Q(licensee__licence_no__iexact=filters.query)
            | Q(licensee__name_en__icontains=filters.query)
            | Q(licensee__name_zh__icontains=filters.query)
        )
    if filters.district:
        queryset = queryset.filter(licensee__district=filters.district)
    if filters.language:
        queryset = queryset.filter(languages__contains=[filters.language])
    if filters.bank_support:
        queryset = queryset.filter(bank_account_support=True)
    if filters.remote_onboarding:
        queryset = queryset.filter(remote_onboarding=True)
    if filters.tier:
        queryset = queryset.filter(tier=filters.tier)
    if filters.bank_type:
        queryset = queryset.filter(bank_types__contains=[filters.bank_type])

    return _apply_sort(queryset, filters.sort)


def available_districts() -> list[str]:
    """Districts that actually have a published provider, for the filter list.

    Blank districts are excluded: the parser leaves them empty when it cannot
    tell, and an empty option in a filter reads as a place.
    """
    return sorted(
        directory_queryset()
        .exclude(licensee__district="")
        .values_list("licensee__district", flat=True)
        .distinct()
    )


def get_provider_detail(slug: str) -> Provider | None:
    """One provider with everything its page renders, in three queries."""
    return (
        directory_queryset()
        .prefetch_related(
            Prefetch(
                "offerings",
                queryset=ServiceOffering.objects.filter(is_active=True).prefetch_related("prices"),
            ),
            "certifications",
        )
        .filter(slug=slug)
        .first()
    )


def get_providers_for_compare(slugs: Sequence[str]) -> list[Provider]:
    """Up to ``MAX_COMPARE`` providers, in the order the URL asked for them.

    Order matters: the columns should stay where the user put them, and a
    queryset would return them in whatever order the index prefers.
    """
    wanted = list(dict.fromkeys(slug for slug in slugs if slug))[:MAX_COMPARE]
    if not wanted:
        return []
    found = {
        provider.slug: provider
        for provider in directory_queryset()
        .prefetch_related(
            Prefetch(
                "offerings",
                queryset=ServiceOffering.objects.filter(is_active=True).prefetch_related("prices"),
            )
        )
        .filter(slug__in=wanted)
    }
    return [found[slug] for slug in wanted if slug in found]


# ---------------------------------------------------------------- claims


def get_claim(claim_id: str) -> ProviderClaim | None:
    """One claim with everything its page and the review queue render."""
    return (
        ProviderClaim.objects.select_related(
            "provider", "provider__licensee", "submitted_by", "reviewer"
        )
        .prefetch_related("evidence")
        .filter(pk=claim_id)
        .first()
    )


def claims_for_user(user_id: str) -> QuerySet[ProviderClaim]:
    return (
        ProviderClaim.objects.filter(submitted_by__pk=user_id)
        .select_related("provider", "provider__licensee")
        .order_by("-created_at")
    )


def pending_claims() -> QuerySet[ProviderClaim]:
    """The moderation queue, oldest first - the queue is answered in order."""
    return (
        ProviderClaim.objects.filter(status=ClaimDecision.PENDING)
        .select_related("provider", "provider__licensee", "submitted_by")
        .order_by("created_at")
    )


def claimable_provider(slug: str) -> Provider | None:
    """A provider that may still be claimed, or None.

    Unpublished profiles and candidates with no licence behind them are
    excluded here as well as in ``services.submit_claim``: the view needs to
    answer 404 before rendering a form that could never be accepted.
    """
    provider = directory_queryset().filter(slug=slug, licensee__isnull=False).first()
    if provider is None or provider.claim_status == ClaimStatus.CLAIMED:
        return None
    return provider
