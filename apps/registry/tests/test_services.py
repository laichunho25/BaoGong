"""The sync pipeline: sanity check, idempotency, diff classification, write guard."""

from pathlib import Path

import pytest
import requests
import responses

from apps.registry.models import (
    ChangeSeverity,
    ChangeType,
    LicenceStatus,
    Licensee,
    LicenseeChange,
    RegistryWriteError,
    SyncRun,
    SyncStatus,
)
from apps.registry.services import sync_registry

pytestmark = pytest.mark.django_db


def changes_by_type(sync_run_id: str | None) -> dict[str, list[LicenseeChange]]:
    grouped: dict[str, list[LicenseeChange]] = {}
    for change in LicenseeChange.objects.filter(sync_run_id=sync_run_id):
        grouped.setdefault(change.change_type, []).append(change)
    return grouped


class TestFirstRun:
    def test_loads_every_row(self, baseline_csv: Path) -> None:
        report = sync_registry(file_path=baseline_csv)

        assert report.status == SyncStatus.SUCCESS
        assert report.row_count == 6
        assert report.prev_row_count is None
        assert Licensee.objects.count() == 6
        assert report.changes == {ChangeType.NEW: 6}

    def test_records_the_run(self, baseline_csv: Path) -> None:
        report = sync_registry(file_path=baseline_csv)

        run = SyncRun.objects.get(pk=report.sync_run_id)
        assert run.status == SyncStatus.SUCCESS
        assert run.row_count == 6
        assert len(run.checksum) == 64
        assert run.finished_at is not None
        # ARCHITECTURE section 5 step 2: the untouched download is archived.
        assert run.raw_file_key.startswith("raw/tcsp/")

    def test_stamps_the_timestamps_the_ui_must_display(self, baseline_csv: Path) -> None:
        # COMPLIANCE section 1.
        sync_registry(file_path=baseline_csv)

        licensee = Licensee.objects.get(licence_no="TC000002")
        assert licensee.first_seen_at == licensee.last_seen_at == licensee.last_synced_at

    def test_keeps_the_official_row_verbatim(self, baseline_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)

        licensee = Licensee.objects.get(licence_no="TC000006")
        assert any(chr(0xA0) in value for value in licensee.raw.values())
        assert licensee.name_en == "WINSHIP CONSULTANTS LIMITED"


class TestIdempotency:
    def test_replaying_the_same_file_records_no_change(self, baseline_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)
        second = sync_registry(file_path=baseline_csv)

        assert second.status == SyncStatus.SUCCESS
        assert second.changes == {}
        assert Licensee.objects.count() == 6
        assert LicenseeChange.objects.filter(sync_run_id=second.sync_run_id).count() == 0

    def test_moves_last_seen_at_forward(self, baseline_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)
        before = Licensee.objects.get(licence_no="TC000002").last_seen_at
        sync_registry(file_path=baseline_csv)
        after = Licensee.objects.get(licence_no="TC000002").last_seen_at

        assert after > before


class TestSanityCheck:
    def test_a_collapse_aborts_and_writes_nothing(
        self, baseline_csv: Path, collapsed_csv: Path
    ) -> None:
        # ROADMAP P1 acceptance: >15% drop -> aborted_sanity, DB unchanged.
        sync_registry(file_path=baseline_csv)
        snapshot = {
            licensee.licence_no: (licensee.status, licensee.last_seen_at)
            for licensee in Licensee.objects.all()
        }

        report = sync_registry(file_path=collapsed_csv)

        assert report.status == SyncStatus.ABORTED_SANITY
        assert report.row_count == 2
        assert report.prev_row_count == 6
        assert Licensee.objects.count() == 6
        assert {
            licensee.licence_no: (licensee.status, licensee.last_seen_at)
            for licensee in Licensee.objects.all()
        } == snapshot
        assert LicenseeChange.objects.filter(sync_run_id=report.sync_run_id).count() == 0

    def test_the_aborted_run_is_recorded_for_the_operator(
        self, baseline_csv: Path, collapsed_csv: Path
    ) -> None:
        sync_registry(file_path=baseline_csv)
        report = sync_registry(file_path=collapsed_csv)

        run = SyncRun.objects.get(pk=report.sync_run_id)
        assert run.status == SyncStatus.ABORTED_SANITY
        assert "refusing to write" in run.error

    def test_an_aborted_run_is_not_used_as_the_next_baseline(
        self, baseline_csv: Path, collapsed_csv: Path
    ) -> None:
        sync_registry(file_path=baseline_csv)
        sync_registry(file_path=collapsed_csv)
        report = sync_registry(file_path=collapsed_csv)

        # Still compared against the last *successful* run, so a bad file
        # cannot become the new normal by being served twice.
        assert report.prev_row_count == 6
        assert report.status == SyncStatus.ABORTED_SANITY

    def test_a_small_movement_is_allowed(self, baseline_csv: Path, changed_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)
        report = sync_registry(file_path=changed_csv)

        assert report.status == SyncStatus.SUCCESS


class TestDiff:
    @pytest.fixture(autouse=True)
    def _loaded(self, baseline_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)

    def test_classifies_every_change_type(self, changed_csv: Path) -> None:
        report = sync_registry(file_path=changed_csv)
        grouped = changes_by_type(report.sync_run_id)

        assert set(grouped) == {
            ChangeType.NEW,
            ChangeType.REMOVED,
            ChangeType.RENAMED,
            ChangeType.ADDRESS_CHANGED,
        }

    def test_a_rename_keeps_both_sides(self, changed_csv: Path) -> None:
        report = sync_registry(file_path=changed_csv)
        change = LicenseeChange.objects.get(
            sync_run_id=report.sync_run_id, change_type=ChangeType.RENAMED
        )

        assert change.licence_no == "TC000003"
        assert change.before["name_en"] == "TRILLION CAPITAL CORPORATE SERVICES LIMITED"
        assert change.after["name_en"] == "TRILLION CAPITAL CORPORATE SERVICES (HK) LIMITED"
        # A provider may have been claimed under its former name.
        assert change.severity == ChangeSeverity.WARN

    def test_a_disappearance_is_critical_and_deactivates(self, changed_csv: Path) -> None:
        report = sync_registry(file_path=changed_csv)
        change = LicenseeChange.objects.get(
            sync_run_id=report.sync_run_id, change_type=ChangeType.REMOVED
        )

        assert change.licence_no == "TC000008"
        assert change.severity == ChangeSeverity.CRITICAL
        # The row is retained, not deleted: the platform must keep showing that
        # this company was once licensed and no longer is.
        assert Licensee.objects.get(licence_no="TC000008").status == LicenceStatus.INACTIVE

    def test_a_returning_licensee_is_reactivated(
        self, baseline_csv: Path, changed_csv: Path
    ) -> None:
        sync_registry(file_path=changed_csv)
        assert Licensee.objects.get(licence_no="TC000008").status == LicenceStatus.INACTIVE

        report = sync_registry(file_path=baseline_csv)

        change = LicenseeChange.objects.get(
            sync_run_id=report.sync_run_id, change_type=ChangeType.REACTIVATED
        )
        assert change.licence_no == "TC000008"
        assert change.severity == ChangeSeverity.WARN
        assert Licensee.objects.get(licence_no="TC000008").status == LicenceStatus.ACTIVE

    def test_an_address_change_updates_the_district(self, changed_csv: Path) -> None:
        sync_registry(file_path=changed_csv)

        licensee = Licensee.objects.get(licence_no="TC000005")
        assert "JOHNSTON ROAD" in licensee.business_address
        assert licensee.district == "Wan Chai"


class TestDryRun:
    def test_reports_the_changes_without_writing_them(self, baseline_csv: Path) -> None:
        report = sync_registry(file_path=baseline_csv, dry_run=True)

        assert report.status == SyncStatus.SUCCESS
        assert report.changes == {ChangeType.NEW: 6}
        assert Licensee.objects.count() == 0
        assert LicenseeChange.objects.count() == 0

    def test_does_not_archive_or_become_the_next_baseline(self, baseline_csv: Path) -> None:
        dry = sync_registry(file_path=baseline_csv, dry_run=True)
        assert SyncRun.objects.get(pk=dry.sync_run_id).raw_file_key == ""

        real = sync_registry(file_path=baseline_csv)
        assert real.prev_row_count is None


class TestFailures:
    def test_a_download_error_is_recorded_not_raised(self) -> None:
        with responses.RequestsMock() as mock:
            mock.add(responses.GET, "https://example.test/licensees.csv", status=503)
            report = sync_registry(source_url="https://example.test/licensees.csv")

        assert report.status == SyncStatus.FAILED
        assert SyncRun.objects.get(pk=report.sync_run_id).status == SyncStatus.FAILED
        assert Licensee.objects.count() == 0

    def test_a_malformed_file_fails_without_writing(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.csv"
        broken.write_bytes(b"not,a,register\n1,2,3\n")

        report = sync_registry(file_path=broken)

        assert report.status == SyncStatus.FAILED
        assert "missing required column" in report.error
        assert Licensee.objects.count() == 0

    def test_downloads_from_the_configured_url(self, baseline_csv: Path) -> None:
        with responses.RequestsMock() as mock:
            mock.add(
                responses.GET,
                "https://example.test/licensees.csv",
                body=baseline_csv.read_bytes(),
                content_type="text/csv",
            )
            report = sync_registry(source_url="https://example.test/licensees.csv")

        assert report.status == SyncStatus.SUCCESS
        assert Licensee.objects.count() == 6


class TestWriteGuard:
    """CLAUDE.md rule 1, enforced at the model layer."""

    def test_save_is_refused_outside_the_pipeline(self, baseline_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)
        licensee = Licensee.objects.get(licence_no="TC000002")
        licensee.name_en = "HAND EDITED LIMITED"

        with pytest.raises(RegistryWriteError, match=r"apps\.providers"):
            licensee.save()

    def test_queryset_update_is_refused(self, baseline_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)

        with pytest.raises(RegistryWriteError):
            Licensee.objects.all().update(name_en="HAND EDITED LIMITED")

    def test_delete_is_refused(self, baseline_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)

        with pytest.raises(RegistryWriteError):
            Licensee.objects.get(licence_no="TC000002").delete()
        with pytest.raises(RegistryWriteError):
            Licensee.objects.all().delete()

    def test_bulk_create_is_refused(self) -> None:
        with pytest.raises(RegistryWriteError):
            Licensee.objects.bulk_create([Licensee(licence_no="TC999999")])

    def test_the_gate_closes_again_after_a_sync(self, baseline_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)

        with pytest.raises(RegistryWriteError):
            Licensee.objects.all().update(district="Wan Chai")


def test_requests_exception_is_a_handled_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(requests, "get", boom)
    report = sync_registry(source_url="https://example.test/licensees.csv")

    assert report.status == SyncStatus.FAILED
    assert "no route to host" in report.error
