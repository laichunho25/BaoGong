"""The backfill entry points, and the model-level presentation helpers."""

from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING

import pytest
from django.core.management import call_command
from django.db.utils import IntegrityError
from django.utils import translation

from apps.providers.models import (
    PriceItem,
    Provider,
    ServiceCategory,
    ServiceOffering,
)
from apps.providers.tasks import backfill_providers

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.registry.models import Licensee

pytestmark = pytest.mark.django_db


class TestBackfillEntryPoints:
    def test_the_command_reports_what_it_did(self, make_licensee: Callable[..., Licensee]) -> None:
        make_licensee()
        out = StringIO()

        call_command("backfill_providers", stdout=out)

        assert "created 1" in out.getvalue()
        assert Provider.objects.count() == 1

    def test_the_task_returns_a_summary(self, make_licensee: Callable[..., Licensee]) -> None:
        # Runs on its own schedule after the sync so a failure here can never
        # roll back the mirror of the official file.
        make_licensee()

        result = backfill_providers()

        assert result == {"created": 1, "skipped": 0, "rescored": 0}


class TestPriceItem:
    def _offering(self, provider: Provider) -> ServiceOffering:
        return ServiceOffering.objects.create(
            provider=provider, category=ServiceCategory.INCORPORATION
        )

    def test_a_point_price_formats_with_its_currency(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        price = PriceItem.objects.create(
            offering=self._offering(make_provider()),
            label="Standard",
            currency="HKD",
            amount_minor=450000,
        )

        assert price.display == "HKD 4,500.00"

    def test_a_range_names_the_currency_once(self, make_provider: Callable[..., Provider]) -> None:
        price = PriceItem.objects.create(
            offering=self._offering(make_provider()),
            label="Range",
            currency="HKD",
            min_amount_minor=300000,
            max_amount_minor=800000,
        )

        assert price.display == "HKD 3,000.00 - 8,000.00"

    def test_a_price_with_neither_is_rejected_by_the_database(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        # A blank price cell in the compare table reads as "free".
        with pytest.raises(IntegrityError):
            PriceItem.objects.create(offering=self._offering(make_provider()), label="Empty")


class TestProviderDisplayHelpers:
    def test_the_official_name_is_used_even_on_a_claimed_page(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        provider = make_provider(
            licensee_kwargs={"name_en": "Official Name Limited"}, claim_status="claimed"
        )

        assert provider.display_name == "Official Name Limited"

    def test_array_choices_render_as_labels(self, make_provider: Callable[..., Provider]) -> None:
        provider = make_provider(languages=["mandarin", "bogus"], bank_types=["virtual"])

        # A value the enum does not know is dropped rather than printed raw, and
        # what is printed is printed in the reader's language: these labels are
        # English msgids in the source, so an untranslated one reaches a buyer's
        # screen as "Virtual bank" in the middle of a Simplified Chinese page.
        with translation.override("zh-hans"):
            assert provider.language_labels == ["普通话"]
            assert provider.bank_type_labels == ["虚拟银行"]
