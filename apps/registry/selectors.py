"""Read queries over the official register. Never writes (ARCHITECTURE section 3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.db.models import Case, Count, IntegerField, Q, QuerySet, When
from django.utils import timezone

from apps.registry.models import (
    ChangeSeverity,
    ChangeType,
    LicenceStatus,
    Licensee,
    LicenseeChange,
    SyncRun,
    SyncStatus,
)

if TYPE_CHECKING:
    from datetime import datetime

# A daily 06:00 sync is stale well before 48h, but a run that starts at 06:00
# and finishes at 06:05 must not trip the check the next morning at 06:00.
DEFAULT_MAX_SYNC_AGE_HOURS = 26


def active_licensees() -> QuerySet[Licensee]:
    """Licensees currently on the register.

    This is the *eligibility* set: matching, RFQ recipients and quote rights
    must all be drawn from it. It is deliberately not the set the directory
    lists - see ``listed_licensees``.
    """
    return Licensee.objects.filter(status=LicenceStatus.ACTIVE)


def listed_licensees() -> QuerySet[Licensee]:
    """Every licensee the directory shows, on the register or not.

    A licence that leaves the register does not leave the platform. The record
    stays and carries a deregistration notice instead (``notices.py``), because
    quietly dropping it would hide the single fact a customer most needs and
    would strand every inbound link to that company. Callers must render the
    notice for anything where ``is_on_register`` is false.
    """
    return Licensee.objects.all()


def _register_status_first(queryset: QuerySet[Licensee]) -> QuerySet[Licensee]:
    """Order licensees still on the register ahead of those removed from it."""
    return queryset.order_by(
        Case(
            When(status=LicenceStatus.ACTIVE, then=0),
            default=1,
            output_field=IntegerField(),
        ),
        "name_en",
    )


def search_licensees(query: str) -> QuerySet[Licensee]:
    """Fuzzy name match plus exact licence number, over the whole directory.

    Deregistered licensees are included on purpose and sorted last; someone
    searching a company they were about to hire has to be able to find out
    that it is no longer on the register.
    """
    term = query.strip()
    if not term:
        return _register_status_first(listed_licensees())
    return _register_status_first(
        listed_licensees().filter(
            Q(licence_no__iexact=term) | Q(name_en__icontains=term) | Q(name_zh__icontains=term)
        )
    )


def deregistered_licensees() -> QuerySet[Licensee]:
    """Licensees the register has stopped listing, most recently seen first."""
    return Licensee.objects.filter(status=LicenceStatus.INACTIVE).order_by("-last_seen_at")


def removal_change(licensee: Licensee) -> LicenseeChange | None:
    """The sync that first found this licence missing, if there was one.

    Gives the detail page something to cite beyond ``last_seen_at``: which run
    detected the removal, and the official row as it last stood.
    """
    return (
        LicenseeChange.objects.filter(
            licence_no=licensee.licence_no, change_type=ChangeType.REMOVED
        )
        .order_by("-created_at")
        .first()
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


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """The register in five numbers, for the home page's market section.

    Every field is a count over the mirror as it stands, never an estimate and
    never a rounded "over 7,000" - the whole reason a visitor trusts this
    platform over a blog post is that the figures can be checked against the
    register on the same day. ``last_synced_at`` travels with them for that
    reason (COMPLIANCE section 1): a count with no date is not checkable.
    """

    total_on_register: int
    deregistered: int
    districts: int
    added_recently: int
    removed_recently: int
    window_days: int
    last_synced_at: datetime | None


def market_snapshot(*, window_days: int = 30, now: datetime | None = None) -> MarketSnapshot:
    """Counts over the licence register for the public market section.

    "Recently added" is measured by ``first_seen_at``, which is when *this
    platform* first saw the licence, not when the Registry granted it - the
    open data publishes no grant date. The wording on the page has to match
    that: it says 本平台新收录, not 新获发牌.
    """
    moment = now or timezone.now()
    since = moment - timedelta(days=window_days)
    return MarketSnapshot(
        total_on_register=active_licensees().count(),
        deregistered=Licensee.objects.filter(status=LicenceStatus.INACTIVE).count(),
        districts=(active_licensees().exclude(district="").values("district").distinct().count()),
        added_recently=active_licensees().filter(first_seen_at__gte=since).count(),
        removed_recently=Licensee.objects.filter(
            status=LicenceStatus.INACTIVE, last_seen_at__gte=since
        ).count(),
        window_days=window_days,
        last_synced_at=registry_last_synced_at(),
    )


def top_districts(*, limit: int = 8) -> list[tuple[str, int]]:
    """Districts with the most licensees on the register, biggest first.

    Feeds the home page's popular-search chips. These are counts of companies,
    not of searches: the platform keeps no search log, so a chip that claimed
    to be "what people search for" would be describing traffic nobody measured.
    """
    rows = (
        active_licensees()
        .exclude(district="")
        .values("district")
        .annotate(count=Count("pk"))
        .order_by("-count", "district")[:limit]
    )
    return [(row["district"], row["count"]) for row in rows]


def changes_for_run(sync_run: SyncRun) -> QuerySet[LicenseeChange]:
    return LicenseeChange.objects.filter(sync_run=sync_run)


def critical_changes(*, limit: int = 50) -> QuerySet[LicenseeChange]:
    """Licences that vanished from the register - the alerting queue."""
    return LicenseeChange.objects.filter(severity=ChangeSeverity.CRITICAL).order_by("-created_at")[
        :limit
    ]


def unnotified_critical_changes() -> QuerySet[LicenseeChange]:
    """Critical changes nobody has acted on yet (``notified_at`` still unset)."""
    return LicenseeChange.objects.filter(
        severity=ChangeSeverity.CRITICAL, notified_at__isnull=True
    ).order_by("-created_at")


@dataclass(frozen=True, slots=True)
class RegistryHealth:
    """Answer to 'did the register sync today, and is anything waiting on me?'."""

    last_success_at: datetime | None
    age_hours: float | None
    max_age_hours: int
    row_count: int
    last_run_status: str
    last_run_finished_at: datetime | None
    unnotified_critical: int

    @property
    def is_stale(self) -> bool:
        return self.age_hours is None or self.age_hours > self.max_age_hours

    @property
    def is_healthy(self) -> bool:
        return not self.is_stale

    @property
    def reason(self) -> str:
        """Why the check failed, in one line. Empty when healthy."""
        if self.last_success_at is None:
            return "no successful sync has ever completed"
        if self.is_stale:
            return (
                f"last successful sync was {self.age_hours:.1f}h ago, "
                f"which is over the {self.max_age_hours}h limit"
            )
        return ""

    def as_dict(self) -> dict[str, Any]:
        """Payload for ``registry_health --json`` and ``/healthz/registry``.

        One definition so the command and the endpoint cannot drift apart and
        report the same database differently. Everything here is either public
        already (COMPLIANCE section 1 requires the site to publish
        ``last_synced_at`` and the row count) or a bare count, so the endpoint
        can stay unauthenticated for uptime monitors to poll.
        """
        return {
            "healthy": self.is_healthy,
            "stale": self.is_stale,
            "reason": self.reason,
            "last_success_at": (self.last_success_at.isoformat() if self.last_success_at else None),
            "age_hours": round(self.age_hours, 2) if self.age_hours is not None else None,
            "max_age_hours": self.max_age_hours,
            "row_count": self.row_count,
            "last_run_status": self.last_run_status,
            "unnotified_critical": self.unnotified_critical,
        }


def registry_health(*, max_age_hours: int = DEFAULT_MAX_SYNC_AGE_HOURS) -> RegistryHealth:
    """Whether the mirror is fresh enough to be worth showing.

    A sync that never fires leaves no failed ``SyncRun`` behind - there is
    nothing to find, which is exactly why staleness rather than failure is the
    signal this reports on.
    """
    success = last_successful_sync()
    latest = SyncRun.objects.filter(is_dry_run=False).order_by("-started_at").first()

    age_hours: float | None = None
    if success is not None and success.finished_at is not None:
        age_hours = (timezone.now() - success.finished_at).total_seconds() / 3600

    return RegistryHealth(
        last_success_at=success.finished_at if success else None,
        age_hours=age_hours,
        max_age_hours=max_age_hours,
        row_count=success.row_count if success else 0,
        last_run_status=latest.status if latest else "",
        last_run_finished_at=latest.finished_at if latest else None,
        unnotified_critical=unnotified_critical_changes().count(),
    )
