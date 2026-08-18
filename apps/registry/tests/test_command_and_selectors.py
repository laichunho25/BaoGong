"""The operator-facing surfaces: management command, selectors, Celery task."""

from datetime import timedelta
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.registry import selectors
from apps.registry.models import (
    LicenceStatus,
    Licensee,
    SyncRun,
    SyncStatus,
    allow_registry_writes,
)
from apps.registry.services import sync_registry
from apps.registry.tasks import sync_tcsp_registry

pytestmark = pytest.mark.django_db


def run_command(*args: str) -> str:
    out = StringIO()
    call_command("sync_tcsp", *args, stdout=out)
    return out.getvalue()


class TestSyncTcspCommand:
    def test_loads_the_register_from_a_file(self, baseline_csv: Path) -> None:
        output = run_command("--file", str(baseline_csv))

        assert "6 rows" in output
        assert "new: 6" in output
        assert Licensee.objects.count() == 6

    def test_dry_run_reports_without_writing(self, baseline_csv: Path) -> None:
        output = run_command("--file", str(baseline_csv), "--dry-run")

        assert "[dry run]" in output
        assert "new: 6" in output
        assert Licensee.objects.count() == 0

    def test_reports_no_changes_on_a_replay(self, baseline_csv: Path) -> None:
        run_command("--file", str(baseline_csv))
        output = run_command("--file", str(baseline_csv))

        assert "No changes." in output

    def test_a_missing_file_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(CommandError, match="No such file"):
            run_command("--file", str(tmp_path / "absent.csv"))

    def test_a_tripped_sanity_check_exits_non_zero(
        self, baseline_csv: Path, collapsed_csv: Path
    ) -> None:
        # The command must fail loudly so cron/CI notices; the DB is untouched.
        run_command("--file", str(baseline_csv))

        with pytest.raises(CommandError, match="sanity check"):
            run_command("--file", str(collapsed_csv))
        assert Licensee.objects.count() == 6


class TestSelectors:
    @pytest.fixture(autouse=True)
    def _loaded(self, baseline_csv: Path, changed_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)
        sync_registry(file_path=changed_csv)

    def test_active_licensees_excludes_the_deregistered(self) -> None:
        licence_nos = set(selectors.active_licensees().values_list("licence_no", flat=True))

        assert "TC000008" not in licence_nos
        assert Licensee.objects.get(licence_no="TC000008").status == LicenceStatus.INACTIVE

    def test_search_matches_licence_number_and_either_name(self) -> None:
        assert selectors.search_licensees("tc000002").count() == 1
        assert selectors.search_licensees("FULLYEAR").count() == 1
        assert selectors.search_licensees("富年").count() == 1

    def test_search_still_finds_a_deregistered_licensee(self) -> None:
        # The record does not vanish from the platform when the licence
        # vanishes from the register: someone checking a company they were
        # about to hire has to be able to find out that it is gone.
        found = selectors.search_licensees("TC000008")

        assert [licensee.licence_no for licensee in found] == ["TC000008"]
        assert found[0].is_on_register is False

    def test_search_ranks_licensees_still_on_the_register_first(self) -> None:
        results = list(selectors.search_licensees("LIMITED"))

        on_register = [licensee.is_on_register for licensee in results]
        assert on_register.count(False) == 1
        assert on_register[-1] is False

    def test_blank_search_returns_the_whole_directory(self) -> None:
        assert selectors.search_licensees("   ").count() == selectors.listed_licensees().count()
        assert selectors.listed_licensees().count() > selectors.active_licensees().count()

    def test_deregistered_licensees_are_listed_most_recent_first(self) -> None:
        assert [licensee.licence_no for licensee in selectors.deregistered_licensees()] == [
            "TC000008"
        ]

    def test_removal_change_cites_the_run_that_detected_it(self) -> None:
        licensee = Licensee.objects.get(licence_no="TC000008")
        change = selectors.removal_change(licensee)

        assert change is not None
        assert change.before["name_en"] == licensee.name_en
        assert selectors.removal_change(Licensee.objects.get(licence_no="TC000002")) is None

    def test_last_synced_at_comes_from_the_last_successful_run(self) -> None:
        # COMPLIANCE section 1: the UI must show this.
        run = selectors.last_successful_sync()
        assert run is not None
        assert selectors.registry_last_synced_at() == run.finished_at

    def test_last_synced_at_ignores_dry_runs(self, baseline_csv: Path) -> None:
        before = selectors.registry_last_synced_at()
        sync_registry(file_path=baseline_csv, dry_run=True)

        assert selectors.registry_last_synced_at() == before

    def test_critical_changes_surface_the_disappearances(self) -> None:
        critical = list(selectors.critical_changes())

        assert [change.licence_no for change in critical] == ["TC000008"]

    def test_registry_last_synced_at_is_none_before_any_sync(self) -> None:
        SyncRun.objects.all().delete()
        assert selectors.registry_last_synced_at() is None


class TestTask:
    def test_delegates_to_the_service(
        self, baseline_csv: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The task downloads from TCSP_CSV_URL; point the service at the fixture
        # instead, so this checks the wiring rather than the network.
        monkeypatch.setattr(
            "apps.registry.tasks.sync_registry",
            lambda **kwargs: sync_registry(file_path=baseline_csv, **kwargs),
        )

        result = sync_tcsp_registry.apply().get()

        assert result["status"] == SyncStatus.SUCCESS
        assert result["row_count"] == 6
        assert Licensee.objects.count() == 6

    def test_the_daily_schedule_is_declared(self) -> None:
        from django.conf import settings

        entry = settings.CELERY_BEAT_SCHEDULE["sync-tcsp-registry-daily"]
        assert entry["task"] == "registry.sync_tcsp_registry"
        # 06:00 in CELERY_TIMEZONE, which is Asia/Hong_Kong (ARCHITECTURE 5).
        assert entry["schedule"].hour == {6}
        assert entry["schedule"].minute == {0}
        assert settings.CELERY_TIMEZONE == "Asia/Hong_Kong"


class TestMarketSnapshot:
    """The counts the public home page prints.

    Every field here ends up on the landing page as a bare number, so what
    each one means has to be pinned down: 本平台新收录 is measured by when this
    platform first saw the licence, not by a grant date the open data does not
    publish (COMPLIANCE section 1).
    """

    @staticmethod
    def _make(licence_no: str, *, district: str = "Central and Western", **overrides: Any) -> None:
        now = timezone.now()
        fields: dict[str, Any] = {
            "licence_no": licence_no,
            "name_en": f"{licence_no} Limited",
            "business_address": "1/F, Test Building",
            "district": district,
            "status": LicenceStatus.ACTIVE,
            "first_seen_at": now,
            "last_seen_at": now,
            "last_synced_at": now,
            "raw": {},
        }
        fields.update(overrides)
        with allow_registry_writes():
            Licensee.objects.create(**fields)

    def test_it_counts_rows_rather_than_estimating(self) -> None:
        self._make("TC900001")
        self._make("TC900002", district="Sha Tin")
        self._make("TC900003", status=LicenceStatus.INACTIVE)

        snapshot = selectors.market_snapshot()

        assert snapshot.total_on_register == 2
        assert snapshot.deregistered == 1
        assert snapshot.districts == 2

    def test_a_blank_district_is_not_a_district(self) -> None:
        self._make("TC900004", district="")

        assert selectors.market_snapshot().districts == 0

    def test_the_recent_window_is_the_window_it_reports(self) -> None:
        now = timezone.now()
        self._make("TC900005", first_seen_at=now - timedelta(days=5))
        self._make("TC900006", first_seen_at=now - timedelta(days=90))
        self._make("TC900007", status=LicenceStatus.INACTIVE, last_seen_at=now - timedelta(days=5))
        self._make("TC900008", status=LicenceStatus.INACTIVE, last_seen_at=now - timedelta(days=90))

        snapshot = selectors.market_snapshot(window_days=30, now=now)

        assert snapshot.window_days == 30
        assert snapshot.added_recently == 1
        assert snapshot.removed_recently == 1

    def test_it_carries_the_sync_time_beside_the_counts(self, baseline_csv: Path) -> None:
        # A count with no date is not checkable, so the timestamp travels with
        # the numbers instead of being fetched separately by the template.
        assert selectors.market_snapshot().last_synced_at is None

        sync_registry(file_path=baseline_csv)

        assert selectors.market_snapshot().last_synced_at is not None


class TestTopDistricts:
    def test_biggest_first_and_deregistered_licensees_excluded(self) -> None:
        for index in range(3):
            TestMarketSnapshot._make(f"TC910{index:03d}", district="Kwun Tong")
        TestMarketSnapshot._make("TC911000", district="Sham Shui Po")
        TestMarketSnapshot._make("TC911001", district="Sham Shui Po", status=LicenceStatus.INACTIVE)
        TestMarketSnapshot._make("TC911002", district="")

        assert selectors.top_districts() == [("Kwun Tong", 3), ("Sham Shui Po", 1)]

    def test_it_respects_the_limit(self) -> None:
        for index in range(4):
            TestMarketSnapshot._make(f"TC912{index:03d}", district=f"District {index}")

        assert len(selectors.top_districts(limit=2)) == 2
