"""The public pages, including the compliance rules they have to carry."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse

from apps.registry.models import LicenceStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

    from apps.providers.models import Provider

pytestmark = pytest.mark.django_db


def body(response: object) -> str:
    return response.content.decode()  # type: ignore[attr-defined]


class TestListPage:
    def test_it_lists_published_providers(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        provider = make_provider(licensee_kwargs={"name_en": "Findable Limited"})

        response = client.get(reverse("providers:list"))

        assert response.status_code == 200
        assert "Findable Limited" in body(response)
        assert provider.get_absolute_url() in body(response)

    def test_htmx_gets_the_fragment_and_not_the_whole_page(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        make_provider()

        response = client.get(reverse("providers:list"), headers={"hx-request": "true"})

        assert 'id="results"' in body(response)
        assert "<html" not in body(response)

    def test_a_broken_querystring_still_renders(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        make_provider()

        response = client.get(reverse("providers:list"), {"sort": "nope", "page": "999999"})

        assert response.status_code == 200

    def test_it_names_the_source_and_the_sync_time(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        # COMPLIANCE section 1: registry data may not appear unattributed.
        make_provider()

        content = body(client.get(reverse("providers:list")))

        assert "tcsp.cr.gov.hk" in content
        assert "尚未完成首次同步" in content  # no sync has run in this test database


class TestDetailPage:
    def test_it_shows_the_official_fields(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        provider = make_provider()

        content = body(client.get(provider.get_absolute_url()))

        assert provider.licensee is not None
        assert provider.licensee.licence_no in content
        assert provider.licensee.business_address in content

    def test_an_unknown_slug_is_a_404(self, client: Client) -> None:
        assert client.get("/providers/no-such-company-tc000000/").status_code == 404

    def test_an_unpublished_provider_is_not_reachable(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        provider = make_provider(is_published=False)

        assert client.get(provider.get_absolute_url()).status_code == 404

    def test_an_unclaimed_page_says_so(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        provider = make_provider()

        assert "此页面尚未被认领" in body(client.get(provider.get_absolute_url()))

    def test_a_claimed_page_drops_the_cta(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        provider = make_provider(claim_status="claimed")

        assert "此页面尚未被认领" not in body(client.get(provider.get_absolute_url()))

    def test_a_commission_arrangement_is_disclosed_on_the_page(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        # COMPLIANCE section 6: disclosure belongs on the page it affects, not
        # only in the site footer.
        provider = make_provider(commission_agreement=True)

        assert "导流合作关系" in body(client.get(provider.get_absolute_url()))

    def test_a_page_without_one_carries_no_disclosure(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        provider = make_provider()

        assert "导流合作关系" not in body(client.get(provider.get_absolute_url()))


class TestDeregisteredProviderPage:
    def test_the_page_stays_reachable(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        provider = make_provider(licensee_kwargs={"status": LicenceStatus.INACTIVE})

        assert client.get(provider.get_absolute_url()).status_code == 200

    def test_it_carries_the_notice_and_the_last_listed_date(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        provider = make_provider(licensee_kwargs={"status": LicenceStatus.INACTIVE})

        content = body(client.get(provider.get_absolute_url()))

        assert "已不在官方持牌名单内" in content
        assert "最后一次在官方名单中见到该牌照的日期" in content

    def test_the_page_claims_no_reason_for_the_removal(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        # The register publishes no reason, so neither may the page.
        provider = make_provider(licensee_kwargs={"status": LicenceStatus.INACTIVE})

        content = body(client.get(provider.get_absolute_url()))

        for forbidden in ("吊销", "吊銷", "撤销", "撤銷", "除牌", "被除名", "违规", "違規"):
            assert forbidden not in content


class TestRatingDisplay:
    def test_a_provider_with_no_verified_reviews_shows_no_number(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        # RATING_SYSTEM section 4. The prior would render 5.00 here.
        provider = make_provider(rating_cached=Decimal("5.00"), verified_review_count=0)

        content = body(client.get(provider.get_absolute_url()))

        assert "暂无已验证评价" in content
        assert "5.00" not in content

    def test_a_reviewed_provider_shows_the_score_and_the_count(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        provider = make_provider(rating_cached=Decimal("4.30"), verified_review_count=7)

        content = body(client.get(provider.get_absolute_url()))

        assert "4.30" in content
        assert "7 条已验证评价" in content


class TestStructuredData:
    def _jsonld(self, client: Client, provider: Provider) -> dict[str, object]:
        content = body(client.get(provider.get_absolute_url()))
        match = re.search(r'<script type="application/ld\+json">(.*?)</script>', content, re.S)
        assert match is not None
        return json.loads(match.group(1))  # type: ignore[no-any-return]

    def test_it_describes_the_organisation(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        provider = make_provider()

        data = self._jsonld(client, provider)

        assert data["@type"] == "Organization"
        assert data["name"] == provider.display_name

    def test_no_rating_is_published_without_verified_reviews(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        # An unearned 5.00 must not leak into search results either.
        provider = make_provider(rating_cached=Decimal("5.00"), verified_review_count=0)

        assert "aggregateRating" not in self._jsonld(client, provider)

    def test_a_script_tag_in_the_name_cannot_break_out(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        provider = make_provider(licensee_kwargs={"name_en": "Evil </script><b>Limited"})

        data = self._jsonld(client, provider)

        assert data["name"] == "Evil </script><b>Limited"


class TestComparePage:
    def test_it_renders_the_selected_providers_in_order(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        first = make_provider(licensee_kwargs={"name_en": "ZZZ Limited"})
        second = make_provider(licensee_kwargs={"name_en": "AAA Limited"})

        content = body(client.get(reverse("compare"), {"ids": f"{first.slug},{second.slug}"}))

        assert content.index("ZZZ Limited") < content.index("AAA Limited")

    def test_it_is_not_indexable(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        provider = make_provider()

        content = body(client.get(reverse("compare"), {"ids": provider.slug}))

        assert 'content="noindex,follow"' in content

    def test_no_ids_gives_an_empty_state_not_an_error(self, client: Client) -> None:
        response = client.get(reverse("compare"))

        assert response.status_code == 200
        assert "尚未选择要比较的公司" in body(response)


class TestSitemapAndRobots:
    def test_the_sitemap_lists_providers_on_the_register(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        listed = make_provider()

        content = body(client.get("/sitemap-providers.xml"))

        assert listed.get_absolute_url() in content

    def test_it_leaves_out_deregistered_and_unpublished_pages(
        self, client: Client, make_provider: Callable[..., Provider]
    ) -> None:
        # Reachable and linked, but not pushed at people who never visited.
        gone = make_provider(licensee_kwargs={"status": LicenceStatus.INACTIVE})
        hidden = make_provider(is_published=False)

        content = body(client.get("/sitemap-providers.xml"))

        assert gone.get_absolute_url() not in content
        assert hidden.get_absolute_url() not in content

    def test_robots_points_at_the_sitemap_and_excludes_compare(self, client: Client) -> None:
        content = body(client.get("/robots.txt"))

        assert "Sitemap: http://testserver/sitemap.xml" in content
        assert "Disallow: /compare/" in content
