"""Read queries over the official register. Never writes (ARCHITECTURE section 3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Q, QuerySet

from apps.registry.models import (
    ChangeSeverity,
    LicenceStatus,
    Licensee,
    LicenseeChange,
    SyncRun,
    SyncStatus,
)

if TYPE_CHECKING:
    from datetime import datetime


def active_licensees() -> QuerySet[Licensee]:
    """Licensees currently on the register - the only ones safe to list publicly."""
    return Licensee.objects.filter(status=LicenceStatus.ACTIVE)


def search_licensees(query: str) -> QuerySet[Licensee]:
    """Fuzzy name match plus exact licence number, over active licensees only."""
    term = query.strip()
    if not term:
        return active_licensees()
    return active_licensees().filter(
        Q(licence_no__iexact=term) | Q(name_en__icontains=term) | Q(name_zh__icontains=term)
    )


def last_successful_sync() -> SyncRun | None:
    return (
        SyncRun.objects.filter(status=SyncStatus.SUCCESS, is_dry_run=False)
        .order_by("-started_at")
        .first()
    )


def registry_last_synced_at() -> datetime | None:
    """Timestamp every page showing registry data must display (COMPLIANCE section 1)."""
    run = last_successful_sync()
    return run.finished_at if run else None


def changes_for_run(sync_run: SyncRun) -> QuerySet[LicenseeChange]:
    return LicenseeChange.objects.filter(sync_run=sync_run)


def critical_changes(*, limit: int = 50) -> QuerySet[LicenseeChange]:
    """Licences that vanished from the register - the alerting queue."""
    return LicenseeChange.objects.filter(severity=ChangeSeverity.CRITICAL).order_by("-created_at")[
        :limit
    ]
