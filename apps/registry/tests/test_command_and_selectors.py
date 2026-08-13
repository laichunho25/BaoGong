"""The operator-facing surfaces: management command, selectors, Celery task."""

from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.registry import selectors
from apps.registry.models import LicenceStatus, Licensee, SyncRun, SyncStatus
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

    def test_search_never_returns_a_deregistered_licensee(self) -> None:
        # COMPLIANCE: an inactive licence must not surface as a live listing.
        assert selectors.search_licensees("TC000008").count() == 0

    def test_blank_search_returns_the_active_register(self) -> None:
        assert selectors.search_licensees("   ").count() == selectors.active_licensees().count()

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
