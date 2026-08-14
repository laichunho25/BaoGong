"""Query budgets for the public pages.

The directory is the page every visitor lands on, and it renders 20 rows that
each read the licensee. A missing ``select_related`` here is 20 extra queries
per screen, not one - so the budget is asserted rather than reviewed by eye
(PROMPT_LIBRARY P2: under 15 queries).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.urls import reverse

from apps.providers.models import PriceItem, Provider, ServiceCategory, ServiceOffering
from apps.providers.views import PAGE_SIZE

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client
    from django.test.utils import CaptureQueriesContext

pytestmark = pytest.mark.django_db

QUERY_BUDGET = 15


class TestQueryBudget:
    def test_a_full_page_of_results_stays_within_budget(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        django_assert_max_num_queries: Callable[[int], CaptureQueriesContext],
    ) -> None:
        for _ in range(PAGE_SIZE + 5):
            make_provider()

        with django_assert_max_num_queries(QUERY_BUDGET):
            response = client.get(reverse("providers:list"))

        assert response.status_code == 200

    def test_the_count_does_not_grow_with_the_number_of_rows(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        django_assert_max_num_queries: Callable[[int], CaptureQueriesContext],
    ) -> None:
        make_provider()
        with django_assert_max_num_queries(QUERY_BUDGET) as small:
            client.get(reverse("providers:list"))

        for _ in range(PAGE_SIZE):
            make_provider()
        with django_assert_max_num_queries(QUERY_BUDGET) as large:
            client.get(reverse("providers:list"))

        assert len(large.captured_queries) == len(small.captured_queries)

    def test_the_detail_page_stays_within_budget(
        self,
        client: Client,
        make_provider: Callable[..., Provider],
        django_assert_max_num_queries: Callable[[int], CaptureQueriesContext],
    ) -> None:
        provider = make_provider()
        for category in (ServiceCategory.INCORPORATION, ServiceCategory.ACCOUNTING):
            offering = ServiceOffering.objects.create(provider=provider, category=category)
            PriceItem.objects.create(
                offering=offering, label="Standard", currency="HKD", amount_minor=500000
            )

        with django_assert_max_num_queries(QUERY_BUDGET):
            client.get(provider.get_absolute_url())
