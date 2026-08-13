"""The daily 'did it sync, and is anything waiting on me?' check, and the
notice shown for a licence that has left the official register."""

import json
from datetime import timedelta
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.utils import timezone

from apps.registry import notices, selectors
from apps.registry.models import ChangeSeverity, Licensee, LicenseeChange, SyncRun, SyncStatus
from apps.registry.services import sync_registry

pytestmark = pytest.mark.django_db


def run_health(*args: str) -> str:
    out = StringIO()
    call_command("registry_health", *args, stdout=out)
    return out.getvalue()


def age_the_last_sync(hours: int) -> None:
    """Push the last successful run into the past.

    SyncRun is not guarded like Licensee, so this is an ordinary update - the
    read-only rule covers official data, not our own audit trail.
    """
    run = selectors.last_successful_sync()
    assert run is not None
    SyncRun.objects.filter(pk=run.pk).update(finished_at=timezone.now() - timedelta(hours=hours))


class TestRegistryHealth:
    def test_a_fresh_sync_is_healthy(self, baseline_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)
        health = selectors.registry_health()

        assert health.is_healthy
        assert health.reason == ""
        assert health.row_count == 6
        assert health.age_hours is not None and health.age_hours < 1

    def test_no_sync_at_all_is_stale(self) -> None:
        # The failure mode this whole command exists for: beat never fired, so
        # there is no failed run to find.
        health = selectors.registry_health()

        assert health.is_stale
        assert health.last_success_at is None
        assert "no successful sync" in health.reason

    def test_an_old_sync_is_stale(self, baseline_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)
        age_the_last_sync(30)

        assert selectors.registry_health().is_stale

    def test_the_age_limit_is_configurable(self, baseline_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)
        age_the_last_sync(30)

        assert selectors.registry_health(max_age_hours=48).is_healthy

    def test_a_dry_run_does_not_refresh_the_clock(self, baseline_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)
        age_the_last_sync(30)
        sync_registry(file_path=baseline_csv, dry_run=True)

        assert selectors.registry_health().is_stale

    def test_an_aborted_run_does_not_refresh_the_clock(
        self, baseline_csv: Path, collapsed_csv: Path
    ) -> None:
        sync_registry(file_path=baseline_csv)
        age_the_last_sync(30)
        sync_registry(file_path=collapsed_csv)

        health = selectors.registry_health()
        assert health.is_stale
        assert health.last_run_status == SyncStatus.ABORTED_SANITY

    def test_counts_unacknowledged_disappearances(
        self, baseline_csv: Path, changed_csv: Path
    ) -> None:
        sync_registry(file_path=baseline_csv)
        sync_registry(file_path=changed_csv)

        assert selectors.registry_health().unnotified_critical == 1

    def test_an_acknowledged_disappearance_stops_counting(
        self, baseline_csv: Path, changed_csv: Path
    ) -> None:
        sync_registry(file_path=baseline_csv)
        sync_registry(file_path=changed_csv)
        LicenseeChange.objects.filter(severity=ChangeSeverity.CRITICAL).update(
            notified_at=timezone.now()
        )

        assert selectors.registry_health().unnotified_critical == 0


class TestRegistryHealthzEndpoint:
    """``/healthz/registry`` - what an external uptime monitor polls."""

    url = "/healthz/registry"

    def test_200_when_fresh(self, client: Client, baseline_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)

        response = client.get(self.url)

        assert response.status_code == 200
        assert response.json()["healthy"] is True
        assert response.json()["row_count"] == 6

    def test_503_when_stale(self, client: Client, baseline_csv: Path) -> None:
        # The status code is the whole point: a monitor reads that, not the body.
        sync_registry(file_path=baseline_csv)
        age_the_last_sync(30)

        response = client.get(self.url)

        assert response.status_code == 503
        assert response.json()["stale"] is True
        assert "over the 26h limit" in response.json()["reason"]

    def test_503_when_nothing_has_ever_synced(self, client: Client) -> None:
        response = client.get(self.url)

        assert response.status_code == 503
        assert response.json()["last_success_at"] is None

    def test_the_tolerance_can_be_set_per_monitor(self, client: Client, baseline_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)
        age_the_last_sync(30)

        assert client.get(self.url, {"max_age_hours": "48"}).status_code == 200
        assert client.get(self.url, {"max_age_hours": "12"}).status_code == 503

    def test_a_junk_tolerance_falls_back_to_the_default(
        self, client: Client, baseline_csv: Path
    ) -> None:
        # A monitor cannot act on a 400, so bad input must not change the
        # meaning of the status code.
        sync_registry(file_path=baseline_csv)

        for junk in ("abc", "-5", "0", ""):
            response = client.get(self.url, {"max_age_hours": junk})
            assert response.status_code == 200
            assert response.json()["max_age_hours"] == selectors.DEFAULT_MAX_SYNC_AGE_HOURS

    def test_it_is_never_cached(self, client: Client, baseline_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)

        response = client.get(self.url)

        assert "no-cache" in response.headers["Cache-Control"]

    def test_it_needs_no_login(self, client: Client, baseline_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)

        assert client.get(self.url).status_code == 200

    def test_the_endpoint_and_the_command_agree(self, client: Client, baseline_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)

        assert client.get(self.url).json() == json.loads(run_health("--json"))


class TestRegistryHealthCommand:
    def test_reports_a_fresh_register(self, baseline_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)

        output = run_health()

        assert "Registry is fresh." in output
        assert "6 rows" in output

    def test_exits_non_zero_when_stale(self, baseline_csv: Path) -> None:
        # This is the signal an uptime monitor watches.
        sync_registry(file_path=baseline_csv)
        age_the_last_sync(30)

        with pytest.raises(CommandError, match="stale"):
            run_health()

    def test_exits_non_zero_when_nothing_has_ever_synced(self) -> None:
        with pytest.raises(CommandError, match="no successful sync"):
            run_health()

    def test_respects_the_age_limit_flag(self, baseline_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)
        age_the_last_sync(30)

        assert "Registry is fresh." in run_health("--max-age-hours", "48")

    def test_disappearances_warn_but_do_not_fail_by_default(
        self, baseline_csv: Path, changed_csv: Path
    ) -> None:
        sync_registry(file_path=baseline_csv)
        sync_registry(file_path=changed_csv)

        output = run_health()

        assert "TC000008" in output
        assert "Registry is fresh." in output

    def test_fail_on_critical_makes_them_block(self, baseline_csv: Path, changed_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)
        sync_registry(file_path=changed_csv)

        with pytest.raises(CommandError, match="not been acknowledged"):
            run_health("--fail-on-critical")

    def test_json_output_is_machine_readable(self, baseline_csv: Path, changed_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)
        sync_registry(file_path=changed_csv)

        payload = json.loads(run_health("--json"))

        assert payload["healthy"] is True
        assert payload["stale"] is False
        assert payload["row_count"] == 6
        assert payload["unnotified_critical"] == 1
        assert payload["age_hours"] < 1


class TestDeregistrationNotice:
    @pytest.fixture(autouse=True)
    def _loaded(self, baseline_csv: Path, changed_csv: Path) -> None:
        sync_registry(file_path=baseline_csv)
        sync_registry(file_path=changed_csv)

    @property
    def removed(self) -> Licensee:
        return Licensee.objects.get(licence_no="TC000008")

    def test_a_licensee_still_listed_gets_no_notice(self) -> None:
        still_listed = Licensee.objects.get(licence_no="TC000002")

        assert still_listed.is_on_register
        assert still_listed.deregistered_since is None
        assert notices.deregistration_notice(still_listed) == ""
        assert notices.deregistration_headline(still_listed) == ""

    def test_a_removed_licensee_keeps_its_record(self) -> None:
        # The point of the whole feature: the company does not disappear from
        # the platform, it gains a notice.
        assert self.removed.pk is not None
        assert self.removed.name_en == "NEW TERRITORIES BUSINESS LIMITED"
        assert self.removed.is_on_register is False

    def test_the_notice_states_the_last_date_it_was_listed(self) -> None:
        licensee = self.removed
        last_seen = timezone.localtime(licensee.last_seen_at).strftime("%Y-%m-%d")

        assert licensee.deregistered_since == licensee.last_seen_at
        assert last_seen in notices.deregistration_notice(licensee)

    def test_the_notice_claims_no_reason_for_the_removal(self) -> None:
        # COMPLIANCE section 3: the register publishes no reason, so the
        # platform must not imply an enforcement action against a named company.
        notice = notices.deregistration_notice(self.removed)

        for forbidden in ("吊销", "吊銷", "撤销", "撤銷", "除牌", "被除名", "违规", "違規"):
            assert forbidden not in notice
        assert "核实" in notice

    def test_the_headline_fits_a_list_row(self) -> None:
        assert notices.deregistration_headline(self.removed) == "已不在官方持牌名单内"
