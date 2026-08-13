"""Mirror of the official TCSP licensee register.

CLAUDE.md rule 1: the fields in this module hold official data and are
read-only to the rest of the codebase. Enforcement is in three layers:

1. ``Licensee.save``/``delete`` refuse to run outside the sync pipeline.
2. ``LicenseeQuerySet`` refuses ``update``/``delete``/``bulk_create``/
   ``bulk_update`` for the same reason - a queryset write bypasses ``save``.
3. The admin registers ``Licensee`` read-only (see ``admin.py``).

The only code that opens the gate is ``apps.registry.services``, via
``allow_registry_writes()``. The flag lives in a ``ContextVar`` so that a
concurrent request in the same process cannot borrow the sync task's
permission. Platform-side enrichment belongs in ``apps.providers``.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel

if TYPE_CHECKING:
    from collections.abc import Iterator

_writes_allowed: ContextVar[bool] = ContextVar("registry_writes_allowed", default=False)


class RegistryWriteError(RuntimeError):
    """Raised when code outside the sync pipeline tries to mutate official data."""


@contextmanager
def allow_registry_writes() -> Iterator[None]:
    """Open the write gate on ``Licensee`` for the duration of the block.

    Only the sync pipeline may use this. Anything else that needs to record
    information about a licensee should write to ``apps.providers`` instead.
    """
    token = _writes_allowed.set(True)
    try:
        yield
    finally:
        _writes_allowed.reset(token)


def _assert_writable(operation: str) -> None:
    if not _writes_allowed.get():
        raise RegistryWriteError(
            f"Licensee.{operation} is only permitted inside the TCSP sync pipeline "
            f"(see apps.registry.models.allow_registry_writes). Official registry data "
            f"must not be edited by hand; put platform-side data in apps.providers."
        )


class LicenseeQuerySet(models.QuerySet["Licensee"]):
    """Queryset that blocks the bulk write paths, which never call ``save``."""

    def update(self, **kwargs: Any) -> int:
        _assert_writable("update")
        return super().update(**kwargs)

    def delete(self) -> tuple[int, dict[str, int]]:
        _assert_writable("delete")
        return super().delete()

    def bulk_create(self, objs: Any, *args: Any, **kwargs: Any) -> list[Licensee]:
        _assert_writable("bulk_create")
        return super().bulk_create(objs, *args, **kwargs)

    def bulk_update(self, objs: Any, fields: Any, *args: Any, **kwargs: Any) -> int:
        _assert_writable("bulk_update")
        return super().bulk_update(objs, fields, *args, **kwargs)


class LicenceStatus(models.TextChoices):
    ACTIVE = "active", _("Active")
    INACTIVE = "inactive", _("Inactive")


class Licensee(BaseModel):
    """One row of the official register, keyed by its licence number.

    The register publishes no status column: a licensee is ``active`` while it
    appears in the feed and flipped to ``inactive`` the first time it does not.
    ``raw`` keeps the untouched source row so that a mapping mistake can always
    be recovered from without re-downloading a superseded file.
    """

    licence_no = models.CharField(max_length=32, unique=True, db_index=True)
    name_en = models.CharField(max_length=255)
    # 2,037 of 7,457 rows in the register carry no Chinese name.
    name_zh = models.CharField(max_length=255, blank=True)
    business_address = models.TextField()
    remarks_en = models.TextField(blank=True)
    remarks_zh = models.TextField(blank=True)
    # Derived from the address by the sync pipeline; null when unrecognised.
    district = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(
        max_length=16, choices=LicenceStatus.choices, default=LicenceStatus.ACTIVE, db_index=True
    )
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField(db_index=True)
    raw = models.JSONField(default=dict)
    last_synced_at = models.DateTimeField()

    objects = LicenseeQuerySet.as_manager()

    class Meta:
        ordering = ["name_en"]
        indexes = [
            # Fuzzy name search for the P2 directory (requires pg_trgm).
            GinIndex(fields=["name_en"], opclasses=["gin_trgm_ops"], name="registry_name_en_trgm"),
            GinIndex(fields=["name_zh"], opclasses=["gin_trgm_ops"], name="registry_name_zh_trgm"),
        ]

    def __str__(self) -> str:
        return f"{self.licence_no} {self.name_en}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        _assert_writable("save")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        _assert_writable("delete")
        return super().delete(*args, **kwargs)


class SyncStatus(models.TextChoices):
    RUNNING = "running", _("Running")
    SUCCESS = "success", _("Success")
    FAILED = "failed", _("Failed")
    ABORTED_SANITY = "aborted_sanity", _("Aborted by sanity check")


class SyncRun(BaseModel):
    """One execution of the TCSP sync, successful or not."""

    source_url = models.CharField(max_length=512)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=SyncStatus.choices, default=SyncStatus.RUNNING)
    row_count = models.PositiveIntegerField(default=0)
    prev_row_count = models.PositiveIntegerField(null=True, blank=True)
    checksum = models.CharField(max_length=64, blank=True)
    raw_file_key = models.CharField(max_length=512, blank=True)
    error = models.TextField(blank=True)
    is_dry_run = models.BooleanField(default=False)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"{self.started_at:%Y-%m-%d %H:%M} {self.status} ({self.row_count} rows)"


class ChangeType(models.TextChoices):
    NEW = "new", _("New licensee")
    REMOVED = "removed", _("Removed from register")
    REACTIVATED = "reactivated", _("Back on the register")
    RENAMED = "renamed", _("Name changed")
    ADDRESS_CHANGED = "address_changed", _("Address changed")


class ChangeSeverity(models.TextChoices):
    INFO = "info", _("Info")
    WARN = "warn", _("Warning")
    CRITICAL = "critical", _("Critical")


class LicenseeChange(BaseModel):
    """A single field-level difference detected by one sync run.

    ``ai_summary`` is filled in by A7 RegistryDiffAgent in P6. CLAUDE.md rule 3:
    it is a human-readable annotation, never a source of truth.
    """

    sync_run = models.ForeignKey(SyncRun, on_delete=models.CASCADE, related_name="changes")
    licence_no = models.CharField(max_length=32, db_index=True)
    change_type = models.CharField(max_length=20, choices=ChangeType.choices)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    severity = models.CharField(
        max_length=10, choices=ChangeSeverity.choices, default=ChangeSeverity.INFO
    )
    ai_summary = models.TextField(blank=True)
    notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["licence_no"]
        indexes = [models.Index(fields=["sync_run", "change_type"])]

    def __str__(self) -> str:
        return f"{self.licence_no} {self.change_type}"
