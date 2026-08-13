"""TCSP registry sync. The only code permitted to write ``Licensee``.

Implements ARCHITECTURE.md section 5:

1. download (or read a local file)
2. archive the untouched bytes to object storage, record the sha256
3. sanity check the row count against the previous successful run
4. upsert licensees and mark disappeared ones inactive
5. record every field-level difference as a ``LicenseeChange``

The whole database phase runs in one transaction: a sync either lands
completely or not at all, so a crash can never leave the register half
updated with a ``SyncRun`` claiming success.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import requests
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.db import transaction
from django.utils import timezone

from apps.registry.models import (
    ChangeSeverity,
    ChangeType,
    LicenceStatus,
    Licensee,
    LicenseeChange,
    SyncRun,
    SyncStatus,
    allow_registry_writes,
)
from apps.registry.parsing import COMPARED_FIELDS, CsvFormatError, LicenseeRow, parse_csv

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

logger = logging.getLogger(__name__)

# ARCHITECTURE section 5 step 3: a swing this large means the upstream file is
# truncated or the format changed, not that a third of Hong Kong lost its
# licence overnight. Expressed as integer percent to keep float out of it.
SANITY_MAX_DELTA_PERCENT = 15

DOWNLOAD_TIMEOUT_SECONDS = 60
RAW_KEY_TEMPLATE = "raw/tcsp/{date:%Y-%m-%d}.csv"


class SyncAborted(Exception):
    """The sanity check rejected the downloaded file. Nothing was written."""


class _DryRunRollback(Exception):
    """Internal signal: unwind the transaction after a dry run has measured it."""


@dataclass(frozen=True, slots=True)
class SyncReport:
    status: str
    row_count: int
    prev_row_count: int | None
    checksum: str
    dry_run: bool
    sync_run_id: str | None = None
    raw_file_key: str = ""
    changes: dict[str, int] = field(default_factory=dict)
    error: str = ""

    @property
    def change_total(self) -> int:
        return sum(self.changes.values())


def fetch_source(url: str, *, timeout: int = DOWNLOAD_TIMEOUT_SECONDS) -> bytes:
    """Download the register. Raises ``requests.HTTPError`` on a bad status."""
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content


def _read_local(path: Path) -> bytes:
    return path.read_bytes()


def _archive(content: bytes, *, when: datetime) -> str:
    """Store the untouched download and return the storage key.

    A second run on the same day gets a suffixed key rather than overwriting
    the first: an archive that can be silently replaced is not an archive.

    Best effort: losing the archive copy is not a reason to skip a sync, so a
    storage failure is logged and the key left blank.
    """
    key = RAW_KEY_TEMPLATE.format(date=when)
    try:
        return storages["default"].save(key, ContentFile(content))
    except Exception:
        logger.exception("Could not archive the TCSP source file to %s", key)
        return ""


def _last_successful_row_count() -> int | None:
    previous = (
        SyncRun.objects.filter(status=SyncStatus.SUCCESS, is_dry_run=False)
        .order_by("-started_at")
        .values_list("row_count", flat=True)
        .first()
    )
    return previous


def _sanity_check(row_count: int, prev_row_count: int | None) -> None:
    if not prev_row_count:
        return
    delta = abs(row_count - prev_row_count)
    if delta * 100 > prev_row_count * SANITY_MAX_DELTA_PERCENT:
        raise SyncAborted(
            f"Row count moved from {prev_row_count} to {row_count} "
            f"(>{SANITY_MAX_DELTA_PERCENT}%); refusing to write."
        )


def _snapshot(licensee: Licensee) -> dict[str, Any]:
    return {name: getattr(licensee, name) for name in COMPARED_FIELDS}


def _row_snapshot(row: LicenseeRow) -> dict[str, Any]:
    return {name: getattr(row, name) for name in COMPARED_FIELDS}


def _diff_existing(
    licensee: Licensee, row: LicenseeRow, *, sync_run: SyncRun
) -> list[LicenseeChange]:
    """Field-level differences for a licensee already in the register."""
    changes: list[LicenseeChange] = []

    if licensee.status == LicenceStatus.INACTIVE:
        changes.append(
            LicenseeChange(
                sync_run=sync_run,
                licence_no=row.licence_no,
                change_type=ChangeType.REACTIVATED,
                before={"status": licensee.status},
                after={"status": LicenceStatus.ACTIVE.value},
                severity=ChangeSeverity.WARN,
            )
        )

    renamed = {
        name: (getattr(licensee, name), getattr(row, name))
        for name in ("name_en", "name_zh")
        if getattr(licensee, name) != getattr(row, name)
    }
    if renamed:
        changes.append(
            LicenseeChange(
                sync_run=sync_run,
                licence_no=row.licence_no,
                change_type=ChangeType.RENAMED,
                before={name: old for name, (old, _new) in renamed.items()},
                after={name: new for name, (_old, new) in renamed.items()},
                # A provider may have been claimed under its former name.
                severity=ChangeSeverity.WARN,
            )
        )

    if licensee.business_address != row.business_address:
        changes.append(
            LicenseeChange(
                sync_run=sync_run,
                licence_no=row.licence_no,
                change_type=ChangeType.ADDRESS_CHANGED,
                before={"business_address": licensee.business_address},
                after={"business_address": row.business_address},
                severity=ChangeSeverity.INFO,
            )
        )
    return changes


def _apply(rows: list[LicenseeRow], *, sync_run: SyncRun, now: datetime) -> dict[str, int]:
    """Upsert every row, retire the absentees and record the differences."""
    existing = {licensee.licence_no: licensee for licensee in Licensee.objects.all()}
    seen: set[str] = set()
    changes: list[LicenseeChange] = []
    to_create: list[Licensee] = []
    to_update: list[Licensee] = []

    for row in rows:
        seen.add(row.licence_no)
        licensee = existing.get(row.licence_no)
        if licensee is None:
            to_create.append(
                Licensee(
                    licence_no=row.licence_no,
                    name_en=row.name_en,
                    name_zh=row.name_zh,
                    business_address=row.business_address,
                    remarks_en=row.remarks_en,
                    remarks_zh=row.remarks_zh,
                    district=row.district,
                    status=LicenceStatus.ACTIVE,
                    first_seen_at=now,
                    last_seen_at=now,
                    raw=row.raw,
                    last_synced_at=now,
                )
            )
            changes.append(
                LicenseeChange(
                    sync_run=sync_run,
                    licence_no=row.licence_no,
                    change_type=ChangeType.NEW,
                    before=None,
                    after=_row_snapshot(row),
                    severity=ChangeSeverity.INFO,
                )
            )
            continue

        changes.extend(_diff_existing(licensee, row, sync_run=sync_run))
        licensee.name_en = row.name_en
        licensee.name_zh = row.name_zh
        licensee.business_address = row.business_address
        licensee.remarks_en = row.remarks_en
        licensee.remarks_zh = row.remarks_zh
        licensee.district = row.district
        licensee.status = LicenceStatus.ACTIVE
        licensee.last_seen_at = now
        licensee.last_synced_at = now
        licensee.raw = row.raw
        licensee.updated_at = now
        to_update.append(licensee)

    retired = [
        licensee
        for licence_no, licensee in existing.items()
        if licence_no not in seen and licensee.status == LicenceStatus.ACTIVE
    ]
    for licensee in retired:
        changes.append(
            LicenseeChange(
                sync_run=sync_run,
                licence_no=licensee.licence_no,
                change_type=ChangeType.REMOVED,
                before=_snapshot(licensee),
                after=None,
                # A licence disappearing is the single most consequential event
                # this pipeline can detect: the provider may still be listed.
                severity=ChangeSeverity.CRITICAL,
            )
        )
        licensee.status = LicenceStatus.INACTIVE
        licensee.last_synced_at = now
        licensee.updated_at = now

    Licensee.objects.bulk_create(to_create, batch_size=500)
    Licensee.objects.bulk_update(
        to_update,
        [
            "name_en",
            "name_zh",
            "business_address",
            "remarks_en",
            "remarks_zh",
            "district",
            "status",
            "last_seen_at",
            "last_synced_at",
            "raw",
            "updated_at",
        ],
        batch_size=500,
    )
    Licensee.objects.bulk_update(
        retired, ["status", "last_synced_at", "updated_at"], batch_size=500
    )
    LicenseeChange.objects.bulk_create(changes, batch_size=500)

    tally: dict[str, int] = {}
    for change in changes:
        tally[change.change_type] = tally.get(change.change_type, 0) + 1
    return tally


def sync_registry(
    *,
    source_url: str | None = None,
    file_path: Path | None = None,
    dry_run: bool = False,
) -> SyncReport:
    """Run one sync and return what happened.

    ``file_path`` reads a local file instead of downloading, which is how the
    management command replays an archived register. ``dry_run`` performs the
    download, parse and sanity check, then rolls the database phase back - it
    reports the changes a real run would make without making them.
    """
    from django.conf import settings

    url = source_url or (str(file_path) if file_path else settings.TCSP_CSV_URL)
    now = timezone.now()
    sync_run = SyncRun.objects.create(
        source_url=url, started_at=now, status=SyncStatus.RUNNING, is_dry_run=dry_run
    )
    prev_row_count = _last_successful_row_count()

    try:
        content = _read_local(file_path) if file_path else fetch_source(url)
        checksum = hashlib.sha256(content).hexdigest()
        rows = parse_csv(content)
    except (requests.RequestException, OSError, CsvFormatError) as exc:
        logger.error("TCSP sync failed while reading the source: %s", exc)
        sync_run.status = SyncStatus.FAILED
        sync_run.error = str(exc)
        sync_run.finished_at = timezone.now()
        sync_run.save(update_fields=["status", "error", "finished_at", "updated_at"])
        return SyncReport(
            status=SyncStatus.FAILED,
            row_count=0,
            prev_row_count=prev_row_count,
            checksum="",
            dry_run=dry_run,
            sync_run_id=str(sync_run.pk),
            error=str(exc),
        )

    sync_run.checksum = checksum
    sync_run.row_count = len(rows)
    sync_run.prev_row_count = prev_row_count
    sync_run.raw_file_key = "" if dry_run else _archive(content, when=now)

    try:
        _sanity_check(len(rows), prev_row_count)
    except SyncAborted as exc:
        # COMPLIANCE: stale data is safer than wrong data, so nothing is written.
        logger.critical("TCSP sync aborted by the sanity check: %s", exc)
        sync_run.status = SyncStatus.ABORTED_SANITY
        sync_run.error = str(exc)
        sync_run.finished_at = timezone.now()
        sync_run.save()
        return SyncReport(
            status=SyncStatus.ABORTED_SANITY,
            row_count=len(rows),
            prev_row_count=prev_row_count,
            checksum=checksum,
            dry_run=dry_run,
            sync_run_id=str(sync_run.pk),
            raw_file_key=sync_run.raw_file_key,
            error=str(exc),
        )

    tally: dict[str, int] = {}
    try:
        with transaction.atomic(), allow_registry_writes():
            tally = _apply(rows, sync_run=sync_run, now=now)
            if dry_run:
                raise _DryRunRollback
    except _DryRunRollback:
        logger.info("TCSP dry run complete; database changes rolled back.")

    sync_run.status = SyncStatus.SUCCESS
    sync_run.finished_at = timezone.now()
    sync_run.save()
    logger.info(
        "TCSP sync %s: %s rows, changes=%s%s",
        sync_run.pk,
        len(rows),
        tally,
        " (dry run)" if dry_run else "",
    )
    return SyncReport(
        status=SyncStatus.SUCCESS,
        row_count=len(rows),
        prev_row_count=prev_row_count,
        checksum=checksum,
        dry_run=dry_run,
        sync_run_id=str(sync_run.pk),
        raw_file_key=sync_run.raw_file_key,
        changes=tally,
    )
