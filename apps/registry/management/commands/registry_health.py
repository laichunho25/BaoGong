"""``manage.py registry_health [--max-age-hours N] [--json] [--fail-on-critical]``.

Exists because a sync that never fires is silent: no ``SyncRun`` row is
written, so there is no failure to notice. This command turns that silence
into a non-zero exit code that cron, CI or an uptime monitor can act on.
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from apps.registry import selectors


class Command(BaseCommand):
    help = "Report whether the TCSP register mirror is fresh; exit non-zero if it is not."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--max-age-hours",
            type=int,
            default=selectors.DEFAULT_MAX_SYNC_AGE_HOURS,
            help="How old the last successful sync may be before this fails.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Emit a single JSON object instead of human-readable lines.",
        )
        parser.add_argument(
            "--fail-on-critical",
            action="store_true",
            help="Also fail when licences have vanished and nobody has acknowledged it.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        health = selectors.registry_health(max_age_hours=options["max_age_hours"])
        pending = health.unnotified_critical
        critical_blocks = options["fail_on_critical"] and pending > 0

        if options["as_json"]:
            self.stdout.write(
                json.dumps(
                    {
                        "healthy": health.is_healthy and not critical_blocks,
                        "stale": health.is_stale,
                        "reason": health.reason,
                        "last_success_at": (
                            health.last_success_at.isoformat() if health.last_success_at else None
                        ),
                        "age_hours": (
                            round(health.age_hours, 2) if health.age_hours is not None else None
                        ),
                        "max_age_hours": health.max_age_hours,
                        "row_count": health.row_count,
                        "last_run_status": health.last_run_status,
                        "unnotified_critical": pending,
                    }
                )
            )
        else:
            self._write_report(health, pending)

        if health.is_stale:
            raise CommandError(f"Registry is stale: {health.reason}")
        if critical_blocks:
            raise CommandError(
                f"{pending} licence(s) left the register and have not been acknowledged."
            )

    def _write_report(self, health: selectors.RegistryHealth, pending: int) -> None:
        if health.last_success_at is None:
            self.stdout.write(self.style.ERROR("No successful sync has ever completed."))
        else:
            # Shown in TIME_ZONE (Asia/Hong_Kong), matching what the site
            # displays, so an operator is never comparing two clocks.
            local = timezone.localtime(health.last_success_at)
            self.stdout.write(
                f"last successful sync: {local:%Y-%m-%d %H:%M %Z} "
                f"({health.age_hours:.1f}h ago, limit {health.max_age_hours}h), "
                f"{health.row_count} rows"
            )
        if health.last_run_status and health.last_run_status != "success":
            self.stdout.write(self.style.WARNING(f"last run ended: {health.last_run_status}"))

        if pending:
            # Not a failure by default: a licence genuinely leaving the register
            # is news to act on, not a broken pipeline.
            self.stdout.write(
                self.style.WARNING(f"{pending} unacknowledged critical change(s) - licences gone")
            )
            for change in selectors.unnotified_critical_changes()[:10]:
                self.stdout.write(f"  {change.licence_no} {change.change_type}")

        if health.is_healthy:
            self.stdout.write(self.style.SUCCESS("Registry is fresh."))
