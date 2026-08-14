"""Directory filtering, sorting and comparison."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from django.http import QueryDict

from apps.providers import selectors
from apps.providers.models import Provider, Tier
from apps.registry.models import LicenceStatus

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.django_db


def filters_from(query_string: str) -> selectors.DirectoryFilters:
    return selectors.DirectoryFilters.from_request(QueryDict(query_string))


class TestDirectoryFilters:
    def test_an_empty_querystring_is_the_default_view(self) -> None:
        filters = filters_from("")

        assert filters.sort == selectors.SortOption.RECOMMENDED
        assert not filters.is_active
        assert not filters.include_deregistered

    def test_a_junk_value_falls_back_instead_of_failing(self) -> None:
        # These URLs get shared and hand-edited; a 400 helps nobody.
        filters = filters_from("sort=drop-table&language=klingon&tier=platinum")

        assert filters.sort == selectors.SortOption.RECOMMENDED
        assert filters.language == ""
        assert filters.tier == ""

    def test_it_reads_the_filters_it_supports(self) -> None:
        filters = filters_from("q=+abc+&district=Central&language=mandarin&bank_support=1")

        assert filters.query == "abc"
        assert filters.district == "Central"
        assert filters.language == "mandarin"
        assert filters.bank_support
        assert filters.is_active


class TestFilterDirectory:
    def test_unpublished_providers_are_never_listed(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        make_provider(is_published=False)

        assert not selectors.filter_directory(filters_from("")).exists()

    def test_a_deregistered_provider_is_hidden_by_default_but_findable(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        # It must not vanish from the platform: the record answers "what
        # happened to this company?". It is simply not the default view.
        make_provider(licensee_kwargs={"status": LicenceStatus.INACTIVE})

        assert not selectors.filter_directory(filters_from("")).exists()
        assert selectors.filter_directory(filters_from("include_deregistered=1")).exists()

    def test_search_matches_the_licence_number_and_both_names(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        make_provider(licensee_kwargs={"licence_no": "TC777777", "name_zh": "測試秘書有限公司"})

        assert selectors.filter_directory(filters_from("q=tc777777")).count() == 1
        assert selectors.filter_directory(filters_from("q=測試秘書")).count() == 1
        assert selectors.filter_directory(filters_from("q=Test Company")).count() == 1

    def test_platform_filters_narrow_the_list(self, make_provider: Callable[..., Provider]) -> None:
        make_provider(bank_account_support=True, languages=["mandarin"])
        make_provider()

        assert selectors.filter_directory(filters_from("bank_support=1")).count() == 1
        assert selectors.filter_directory(filters_from("language=mandarin")).count() == 1
        assert selectors.filter_directory(filters_from("language=english")).count() == 0

    def test_every_remaining_filter_narrows_the_list(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        make_provider(
            licensee_kwargs={"district": "Sha Tin"},
            remote_onboarding=True,
            tier=Tier.PREMIUM,
            bank_types=["virtual"],
        )
        make_provider(licensee_kwargs={"district": "Central"})

        for query_string in (
            "district=Sha Tin",
            "remote_onboarding=1",
            "tier=premium",
            "bank_type=virtual",
        ):
            assert selectors.filter_directory(filters_from(query_string)).count() == 1, query_string

    def test_districts_come_only_from_listed_providers_and_never_blank(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        make_provider(licensee_kwargs={"district": "Wan Chai"})
        make_provider(licensee_kwargs={"district": ""})

        assert selectors.available_districts() == ["Wan Chai"]


class TestSorting:
    def test_deregistered_providers_sort_last_whatever_the_sort(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        gone = make_provider(
            licensee_kwargs={"status": LicenceStatus.INACTIVE, "name_en": "AAA Limited"},
            tier=Tier.PREMIUM,
        )
        listed = make_provider(licensee_kwargs={"name_en": "ZZZ Limited"})

        for sort in selectors.SortOption.CHOICES:
            results = list(
                selectors.filter_directory(filters_from(f"include_deregistered=1&sort={sort}"))
            )
            assert results == [listed, gone], sort

    def test_an_unrated_provider_does_not_outrank_a_rated_one(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        # nulls_last: no score is not a high score.
        unrated = make_provider()
        rated = make_provider(rating_cached=Decimal("3.10"), verified_review_count=4)

        assert list(selectors.filter_directory(filters_from("sort=rating"))) == [rated, unrated]


class TestCompare:
    def test_it_keeps_the_order_from_the_url(self, make_provider: Callable[..., Provider]) -> None:
        first = make_provider(licensee_kwargs={"name_en": "ZZZ Limited"})
        second = make_provider(licensee_kwargs={"name_en": "AAA Limited"})

        result = selectors.get_providers_for_compare([first.slug, second.slug])

        assert result == [first, second]

    def test_it_caps_at_three_and_drops_duplicates(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        slugs = [make_provider().slug for _ in range(4)]

        assert len(selectors.get_providers_for_compare(slugs)) == selectors.MAX_COMPARE
        assert len(selectors.get_providers_for_compare([slugs[0], slugs[0]])) == 1

    def test_unknown_slugs_are_skipped_rather_than_raising(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        provider = make_provider()

        assert selectors.get_providers_for_compare(["nope", provider.slug]) == [provider]
        assert selectors.get_providers_for_compare(["", " "]) == []
