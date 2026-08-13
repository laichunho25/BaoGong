"""``manage.py sync_tcsp [--dry-run] [--file path] [--url url]``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.registry.models import SyncStatus
from apps.registry.services import sync_registry


class Command(BaseCommand):
    help = "Synchronise the official TCSP licensee register."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Download, parse and report the changes without writing them.",
        )
        parser.add_argument(
            "--file",
            type=Path,
            default=None,
            help="Read a local CSV instead of downloading (e.g. an archived copy).",
        )
        parser.add_argument(
            "--url",
            default=None,
            help="Override TCSP_CSV_URL for this run.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        file_path: Path | None = options["file"]
        if file_path is not None and not file_path.is_file():
            raise CommandError(f"No such file: {file_path}")

        report = sync_registry(
            source_url=options["url"], file_path=file_path, dry_run=options["dry_run"]
        )

        if report.status == SyncStatus.FAILED:
            raise CommandError(f"Sync failed: {report.error}")
        if report.status == SyncStatus.ABORTED_SANITY:
            raise CommandError(f"Sync aborted by the sanity check: {report.error}")

        prefix = "[dry run] " if report.dry_run else ""
        self.stdout.write(
            f"{prefix}{report.row_count} rows (previous run: {report.prev_row_count}), "
            f"sha256={report.checksum[:12]}…"
        )
        if report.raw_file_key:
            self.stdout.write(f"{prefix}archived source: {report.raw_file_key}")
        if not report.changes:
            self.stdout.write(self.style.SUCCESS(f"{prefix}No changes."))
            return
        for change_type, count in sorted(report.changes.items()):
            self.stdout.write(f"{prefix}  {change_type}: {count}")
        self.stdout.write(self.style.SUCCESS(f"{prefix}{report.change_total} change(s) recorded."))
