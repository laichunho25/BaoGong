"""``manage.py backfill_providers``: give every licensee a directory page."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.providers.services import ensure_providers, recompute_ranking_inputs


class Command(BaseCommand):
    help = "Create an unclaimed Provider for every licensee that lacks one."

    def handle(self, *args: Any, **options: Any) -> None:
        report = ensure_providers()
        self.stdout.write(f"created {report.created}, already present {report.skipped}")

        rescored = recompute_ranking_inputs()
        self.stdout.write(self.style.SUCCESS(f"ranking inputs refreshed on {rescored} provider(s)"))
