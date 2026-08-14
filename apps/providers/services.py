"""Writes to the provider layer. Views never touch the ORM directly.

Two jobs live here:

1. Keeping a ``Provider`` in existence for every licensee, so the directory has
   a stable URL for each company before anyone claims it.
2. Recomputing the cached inputs the ranking reads, because RATING_SYSTEM
   section 5 mixes data from four apps and a list page cannot join across all
   of them per request.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.providers.models import Provider, Tier
from apps.registry.models import Licensee

if TYPE_CHECKING:
    from collections.abc import Iterable

# RATING_SYSTEM section 5. The weights are the product definition; the inputs
# they read are defined in this module.
WEIGHT_RATING = Decimal("0.45")
WEIGHT_REVIEW_VOLUME = Decimal("0.20")
WEIGHT_CERTIFICATION = Decimal("0.15")
WEIGHT_COMPLETENESS = Decimal("0.12")
WEIGHT_RESPONSIVENESS = Decimal("0.08")

# "log10(1+n) normalised, capped at n=50" (RATING_SYSTEM section 5).
REVIEW_VOLUME_CAP = 50

TIER_CERTIFICATION_LEVEL = {
    Tier.FREE: Decimal("0"),
    Tier.VERIFIED: Decimal("0.6"),
    Tier.PREMIUM: Decimal("1.0"),
}

# Fields a company can fill in about itself. Completeness is the share of them
# that carry information - a plain, explainable definition, because this number
# feeds a public ranking and has to be defensible when a provider asks why it
# ranks where it does.
COMPLETENESS_FIELDS = (
    "website",
    "founded_year",
    "team_size",
    "languages",
    "industry_specialties",
    "bank_types",
    "logo",
    "office_photos",
)

SLUG_MAX_LENGTH = 140


@dataclass(frozen=True, slots=True)
class BackfillReport:
    created: int
    skipped: int

    @property
    def total(self) -> int:
        return self.created + self.skipped


def build_slug(licensee: Licensee) -> str:
    """A stable, readable URL for a licensee.

    The licence number is appended rather than a counter: two companies do
    share a name, and a counter would depend on insertion order, so the same
    register could produce different URLs on two machines.
    """
    base = slugify(licensee.name_en) or "provider"
    suffix = slugify(licensee.licence_no)
    room = SLUG_MAX_LENGTH - len(suffix) - 1
    return f"{base[:room].rstrip('-')}-{suffix}"


def ensure_providers(*, licence_nos: Iterable[str] | None = None) -> BackfillReport:
    """Create an unclaimed ``Provider`` for every licensee that lacks one.

    Idempotent, and safe to run after every sync: a licensee that already has
    a provider is left completely alone, so this can never overwrite what a
    company has told us about itself.
    """
    pending = Licensee.objects.filter(provider__isnull=True)
    if licence_nos is not None:
        pending = pending.filter(licence_no__in=list(licence_nos))

    new_providers = [Provider(licensee=licensee, slug=build_slug(licensee)) for licensee in pending]
    with transaction.atomic():
        before = Provider.objects.count()
        # ignore_conflicts absorbs the slug collision two workers would hit if
        # the backfill somehow ran twice at once; the licence number in every
        # slug means a collision is always the same company, never two.
        # bulk_create returns the objects it was given either way, so the row
        # count is the only honest measure of what landed.
        Provider.objects.bulk_create(new_providers, ignore_conflicts=True)
        created = Provider.objects.count() - before

    return BackfillReport(created=created, skipped=len(new_providers) - created)


def compute_profile_completeness(provider: Provider) -> Decimal:
    """Share of the self-declared fields that carry information, 0-1.

    Deliberately counts only fields a provider controls. Rating and review
    count are excluded: they are earned, not filled in, and they already carry
    their own weight in the ranking.
    """
    filled = 0
    for field in COMPLETENESS_FIELDS:
        value = getattr(provider, field)
        if isinstance(value, list):
            filled += 1 if value else 0
        elif value:
            filled += 1
    return (Decimal(filled) / Decimal(len(COMPLETENESS_FIELDS))).quantize(Decimal("0.001"))


def compute_review_volume_score(verified_review_count: int) -> Decimal:
    """log10(1+n) normalised against the cap, 0-1."""
    if verified_review_count <= 0:
        return Decimal("0")
    capped = min(verified_review_count, REVIEW_VOLUME_CAP)
    ratio = math.log10(1 + capped) / math.log10(1 + REVIEW_VOLUME_CAP)
    return Decimal(str(round(ratio, 3)))


def compute_ranking_score(provider: Provider) -> Decimal:
    """The default sort key for search results (RATING_SYSTEM section 5).

    A provider with no verified reviews contributes zero from both rating
    terms rather than a default score. RATING_SYSTEM section 4 refuses to show
    an unearned 5.00, and it would be incoherent to refuse to show it while
    still ranking on it.
    """
    if provider.has_verified_reviews and provider.rating_cached is not None:
        normalised_rating = provider.rating_cached / Decimal("5")
    else:
        normalised_rating = Decimal("0")

    score = (
        WEIGHT_RATING * normalised_rating
        + WEIGHT_REVIEW_VOLUME * compute_review_volume_score(provider.verified_review_count)
        + WEIGHT_CERTIFICATION * TIER_CERTIFICATION_LEVEL[Tier(provider.tier)]
        + WEIGHT_COMPLETENESS * provider.profile_completeness
        + WEIGHT_RESPONSIVENESS * provider.responsiveness_score
    )
    return score.quantize(Decimal("0.0001"))


def recompute_ranking_inputs(*, provider_ids: list[str] | None = None) -> int:
    """Refresh ``profile_completeness`` and ``ranking_score``; return how many changed.

    ``responsiveness_score`` stays untouched: it is owned by the RFQ app (P5)
    and there is no data for it yet. Writing a placeholder would make an empty
    signal look like a measured one.
    """
    queryset = Provider.objects.all()
    if provider_ids is not None:
        queryset = queryset.filter(pk__in=provider_ids)

    now = timezone.now()
    changed = []
    for provider in queryset.iterator(chunk_size=500):
        completeness = compute_profile_completeness(provider)
        was = (provider.profile_completeness, provider.ranking_score)
        provider.profile_completeness = completeness
        provider.ranking_score = compute_ranking_score(provider)
        if (provider.profile_completeness, provider.ranking_score) != was:
            # bulk_update skips auto_now, so updated_at is set by hand.
            provider.updated_at = now
            changed.append(provider)

    Provider.objects.bulk_update(
        changed, ["profile_completeness", "ranking_score", "updated_at"], batch_size=500
    )
    return len(changed)
