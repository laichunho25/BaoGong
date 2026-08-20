"""Backfill, slugs and the ranking inputs."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from django.utils import timezone

from apps.providers import services
from apps.providers.models import Provider, Tier

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.registry.models import Licensee

pytestmark = pytest.mark.django_db


class TestBuildSlug:
    def test_the_slug_carries_the_licence_number(
        self, make_licensee: Callable[..., Licensee]
    ) -> None:
        licensee = make_licensee(name_en="ABC Secretarial Limited", licence_no="TC001234")

        assert services.build_slug(licensee) == "abc-secretarial-limited-tc001234"

    def test_two_companies_with_the_same_name_get_different_slugs(
        self, make_licensee: Callable[..., Licensee]
    ) -> None:
        # Name collisions are real in this register, and a counter would make
        # the URL depend on insertion order.
        first = make_licensee(name_en="Same Name Limited", licence_no="TC000001")
        second = make_licensee(name_en="Same Name Limited", licence_no="TC000002")

        assert services.build_slug(first) != services.build_slug(second)

    def test_a_cjk_only_name_still_produces_a_usable_slug(
        self, make_licensee: Callable[..., Licensee]
    ) -> None:
        # slugify strips non-ASCII, so the name can reduce to nothing; the
        # licence number has to keep the URL both valid and unique.
        licensee = make_licensee(name_en="秘書有限公司", licence_no="TC009999")

        assert services.build_slug(licensee).endswith("tc009999")
        assert services.build_slug(licensee) != "-tc009999"

    def test_a_very_long_name_is_truncated_to_the_field_length(
        self, make_licensee: Callable[..., Licensee]
    ) -> None:
        # Long enough to overflow the 140-char slug, short enough to be a legal
        # name_en (255) - the register really does carry names this long.
        licensee = make_licensee(name_en="Very " * 40 + "Long Limited", licence_no="TC008888")

        slug = services.build_slug(licensee)
        assert len(slug) <= services.SLUG_MAX_LENGTH
        assert slug.endswith("tc008888")


class TestEnsureProviders:
    def test_every_licensee_gets_a_page(self, make_licensee: Callable[..., Licensee]) -> None:
        make_licensee()
        make_licensee()

        report = services.ensure_providers()

        assert report.created == 2
        assert Provider.objects.count() == 2

    def test_a_new_page_starts_unclaimed_and_published(
        self, make_licensee: Callable[..., Licensee]
    ) -> None:
        make_licensee()
        services.ensure_providers()

        provider = Provider.objects.get()
        assert provider.claim_status == "unclaimed"
        assert provider.tier == Tier.FREE
        assert provider.is_published

    def test_running_twice_creates_nothing_and_changes_nothing(
        self, make_licensee: Callable[..., Licensee]
    ) -> None:
        # This runs after every daily sync, so a second pass must never touch
        # what a company has told us about itself.
        make_licensee()
        services.ensure_providers()
        provider = Provider.objects.get()
        provider.website = "https://example.com"
        provider.save()

        report = services.ensure_providers()

        assert report.created == 0
        assert report.skipped == 0
        assert Provider.objects.get().website == "https://example.com"

    def test_it_can_be_limited_to_named_licences(
        self, make_licensee: Callable[..., Licensee]
    ) -> None:
        wanted = make_licensee()
        make_licensee()

        report = services.ensure_providers(licence_nos=[wanted.licence_no])

        assert report.created == 1
        assert Provider.objects.get().licensee_id == wanted.pk


class TestProfileCompleteness:
    def test_an_untouched_page_is_zero(self, make_provider: Callable[..., Provider]) -> None:
        assert services.compute_profile_completeness(make_provider()) == Decimal("0")

    def test_filling_fields_raises_it(self, make_provider: Callable[..., Provider]) -> None:
        # A free page's denominator is {website, logo}: founded_year is not a
        # field this tier can reach, so filling it moves nothing.
        provider = make_provider(website="https://example.com", founded_year=2015)

        assert services.compute_profile_completeness(provider) == Decimal("0.500")

    def test_the_denominator_is_what_this_tier_can_reach(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        """COMPLIANCE section 6: a subscription must not move a page up the
        natural ranking. A fixed denominator did exactly that - six of the eight
        fields are paid-only, so a free page could never pass 0.25 however
        completely it filled in what it was allowed to."""
        free = make_provider(website="https://example.com")
        paid = make_provider(website="https://example.com", tier=Tier.VERIFIED)

        assert services.completeness_fields(free) == ("website", "logo")
        assert services.compute_profile_completeness(free) == Decimal("0.500")
        # The same page, with more fields it may fill and none of them filled.
        assert services.compute_profile_completeness(paid) < Decimal("0.500")

    def test_a_suspended_page_is_measured_as_the_free_tier(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        provider = make_provider(tier=Tier.PREMIUM, paid_placement_suspended_at=timezone.now())

        assert services.completeness_fields(provider) == ("website", "logo")

    def test_an_empty_list_does_not_count_as_filled(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        provider = make_provider(languages=[], industry_specialties=[])

        assert services.compute_profile_completeness(provider) == Decimal("0")


class TestRankingScore:
    def test_a_provider_with_no_verified_reviews_scores_nothing_on_rating(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        # The Bayesian prior would hand an unreviewed provider a 5.00. Refusing
        # to display that number while still ranking on it would be incoherent
        # (RATING_SYSTEM sections 4 and 5).
        provider = make_provider(rating_cached=Decimal("5.00"), verified_review_count=0)

        assert services.compute_ranking_score(provider) == Decimal("0")

    def test_reviews_and_tier_both_raise_the_score(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        plain = make_provider()
        reviewed = make_provider(rating_cached=Decimal("4.50"), verified_review_count=12)
        premium = make_provider(tier=Tier.PREMIUM)

        assert services.compute_ranking_score(reviewed) > services.compute_ranking_score(plain)
        assert services.compute_ranking_score(premium) > services.compute_ranking_score(plain)

    def test_the_score_stays_within_zero_and_one(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        best = make_provider(
            rating_cached=Decimal("5.00"),
            verified_review_count=500,
            tier=Tier.PREMIUM,
            website="https://example.com",
            founded_year=2010,
            team_size=40,
            languages=["mandarin", "cantonese", "english"],
            industry_specialties=["trading"],
            bank_types=["traditional"],
            logo="providers/logos/x.png",
            office_photos=["a.jpg"],
            responsiveness_score=Decimal("1"),
        )

        assert Decimal("0") <= services.compute_ranking_score(best) <= Decimal("1")

    def test_review_volume_is_capped(self) -> None:
        assert services.compute_review_volume_score(50) == services.compute_review_volume_score(
            5000
        )
        assert services.compute_review_volume_score(0) == Decimal("0")


class TestRecomputeRankingInputs:
    def test_it_writes_the_cached_columns(self, make_provider: Callable[..., Provider]) -> None:
        provider = make_provider(website="https://example.com", tier=Tier.PREMIUM)

        changed = services.recompute_ranking_inputs()

        provider.refresh_from_db()
        assert changed == 1
        # Premium: seven reachable fields, one of them filled.
        assert provider.profile_completeness == Decimal("0.143")
        assert provider.ranking_score > Decimal("0")

    def test_a_second_pass_reports_nothing_changed(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        make_provider(tier=Tier.PREMIUM)
        services.recompute_ranking_inputs()

        assert services.recompute_ranking_inputs() == 0

    def test_responsiveness_is_left_alone(self, make_provider: Callable[..., Provider]) -> None:
        # Owned by the RFQ app (P5). Writing a placeholder here would make an
        # empty signal look measured.
        provider = make_provider(responsiveness_score=Decimal("0.5"))

        services.recompute_ranking_inputs()

        provider.refresh_from_db()
        assert provider.responsiveness_score == Decimal("0.5")
