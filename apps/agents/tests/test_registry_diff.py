"""A7's rules, its screen, and the one thing it is allowed to do by itself.

The digest is prose and prose is the eval harness's problem. What is asserted
here is everything the prose is not allowed to decide: which change is
critical, who gets mailed, whose paid placement comes down, and what the counts
say. All of that has to keep working with every model switched off, so most of
these tests never go near one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from django.core import mail
from django.utils import timezone

from apps.agents import registry_diff
from apps.agents import services as agent_services
from apps.agents.registry_diff import (
    MAX_ROWS_IN_PROMPT,
    RegistryDiffAgent,
    digest_counts,
    screen_digest,
    severity_for,
    template_digest,
)
from apps.agents.schemas import CriticalItem, DiffDigestOut
from apps.providers.models import ClaimStatus, Provider, Tier
from apps.registry.models import ChangeSeverity, ChangeType, LicenseeChange, SyncRun, SyncStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.accounts.models import User
    from apps.registry.models import Licensee

pytestmark = pytest.mark.django_db


@pytest.fixture
def sync_run() -> SyncRun:
    return SyncRun.objects.create(
        source_url="https://example.test/tcsp.csv",
        started_at=timezone.now(),
        finished_at=timezone.now(),
        status=SyncStatus.SUCCESS,
        row_count=7457,
        prev_row_count=7458,
    )


@pytest.fixture
def make_change(sync_run: SyncRun) -> Callable[..., LicenseeChange]:
    def _make(*, licence_no: str, change_type: str = ChangeType.REMOVED, **overrides: Any):
        return LicenseeChange.objects.create(
            sync_run=sync_run,
            licence_no=licence_no,
            change_type=change_type,
            before=overrides.pop("before", {"name_en": "Before Limited"}),
            after=overrides.pop("after", None),
            severity=overrides.pop("severity", ChangeSeverity.CRITICAL),
            **overrides,
        )

    return _make


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "licence_no": "TC000001",
        "change_type": ChangeType.REMOVED,
        "before": {"name_en": "Before Limited"},
        "after": None,
        "claimed": True,
        "paid": True,
        "provider_name": "Harbour Corporate Services Limited",
        "provider_id": "",
        "severity": ChangeSeverity.CRITICAL,
    }
    row.update(overrides)
    return row


# ------------------------------------------------------------------- severity


@pytest.mark.parametrize(
    ("change_type", "claimed", "paid", "expected"),
    [
        (ChangeType.REMOVED, True, True, ChangeSeverity.CRITICAL),
        (ChangeType.REMOVED, True, False, ChangeSeverity.WARN),
        (ChangeType.REMOVED, False, False, ChangeSeverity.INFO),
        (ChangeType.RENAMED, True, False, ChangeSeverity.WARN),
        (ChangeType.RENAMED, False, False, ChangeSeverity.INFO),
        (ChangeType.ADDRESS_CHANGED, True, False, ChangeSeverity.WARN),
        (ChangeType.REACTIVATED, True, False, ChangeSeverity.WARN),
        (ChangeType.NEW, False, False, ChangeSeverity.INFO),
        # A page cannot be paid without being claimed, but the rule must not
        # depend on the caller having got that right.
        (ChangeType.NEW, True, True, ChangeSeverity.INFO),
    ],
)
def test_severity_is_decided_by_the_rules_and_not_by_prose(
    change_type: str, claimed: bool, paid: bool, expected: str
) -> None:
    assert severity_for(change_type=change_type, claimed=claimed, paid=paid) == expected


def test_the_counts_are_computed_not_reported() -> None:
    rows = [
        _row(licence_no="TC1"),
        _row(
            licence_no="TC2",
            change_type=ChangeType.NEW,
            claimed=False,
            paid=False,
            severity=ChangeSeverity.INFO,
        ),
        _row(
            licence_no="TC3",
            change_type=ChangeType.RENAMED,
            paid=False,
            severity=ChangeSeverity.WARN,
        ),
    ]
    counts = digest_counts(rows)

    assert counts["total"] == 3
    assert counts["critical"] == 1
    assert counts["claimed_affected"] == 2
    assert counts[ChangeType.RENAMED] == 1


# --------------------------------------------------------------------- screen


def _answer(**overrides: Any) -> DiffDigestOut:
    data: dict[str, Any] = {
        "headline": "今日有一家付费公司的牌照不在名单内。",
        "critical_items": [
            CriticalItem(
                licence_no="TC000001",
                provider_name="模型自己写的名字",
                what="该牌照今日未出现在官方名单。",
                why_it_matters="平台仍在推广该公司。",
                action="请核对官方登记册。",
            )
        ],
        "routine_summary": "其余为例行变动。",
        "counts": {"total": 999},
        "confidence": 0.6,
    }
    data.update(overrides)
    return DiffDigestOut(**data)


def test_an_item_about_a_change_the_rules_did_not_flag_is_dropped() -> None:
    """Otherwise the model, not the rules, decides who gets woken up."""
    rows = [_row(licence_no="TC000002", paid=False, severity=ChangeSeverity.WARN)]

    out = screen_digest(_answer(), rows)

    assert out.critical_items == []


def test_a_critical_change_the_model_ignored_still_gets_an_item() -> None:
    """A quiet omission would remove an alert, which is the worse failure."""
    rows = [_row(licence_no="TC000001"), _row(licence_no="TC000009")]

    out = screen_digest(_answer(), rows)

    assert [item.licence_no for item in out.critical_items] == ["TC000001", "TC000009"]
    assert out.critical_items[1].what == template_digest(rows).critical_items[1].what


def test_the_models_wording_survives_when_it_is_about_a_real_critical_row() -> None:
    out = screen_digest(_answer(), [_row()])

    assert out.critical_items[0].action == "请核对官方登记册。"


def test_the_official_name_wins_over_whatever_the_model_typed() -> None:
    """CLAUDE.md rule 1: the model is not a source of official data."""
    out = screen_digest(_answer(), [_row()])

    assert out.critical_items[0].provider_name == "Harbour Corporate Services Limited"


def test_the_counts_the_model_returned_are_replaced() -> None:
    out = screen_digest(_answer(), [_row(), _row(licence_no="TC000002")])

    assert out.counts["total"] == 2
    assert out.counts["critical"] == 2


def test_a_duplicate_item_is_not_mailed_twice() -> None:
    item = _answer().critical_items[0]
    out = screen_digest(_answer(critical_items=[item, item]), [_row()])

    assert len(out.critical_items) == 1


def test_an_empty_headline_falls_back_to_the_template_one() -> None:
    out = screen_digest(_answer(headline="   ", routine_summary=" "), [_row()])

    assert out.headline
    assert out.routine_summary


# ------------------------------------------------------------------- fallback


def test_the_fallback_still_names_every_critical_change() -> None:
    """The suspension happens on the rules; the mail about it must not need a
    model to be readable."""
    rows = [_row(), _row(licence_no="TC000002")]

    out = RegistryDiffAgent().fallback({"rows": rows}, "disabled")

    assert [item.licence_no for item in out.critical_items] == ["TC000001", "TC000002"]
    assert out.confidence == 0.0
    assert out.counts["critical"] == 2


def test_the_fallback_survives_a_day_with_nothing_critical() -> None:
    out = template_digest([_row(change_type=ChangeType.NEW, severity=ChangeSeverity.INFO)])

    assert out.critical_items == []
    assert "1" in out.headline


# --------------------------------------------------------------------- prompt


def test_every_critical_row_reaches_the_prompt_however_long_the_tail_is() -> None:
    rows = [_row(licence_no="TC000001"), _row(licence_no="TC000002")]
    rows += [
        _row(
            licence_no=f"TC{index:06d}",
            change_type=ChangeType.NEW,
            claimed=False,
            paid=False,
            severity=ChangeSeverity.INFO,
        )
        for index in range(10, 10 + MAX_ROWS_IN_PROMPT * 2)
    ]

    prompt = RegistryDiffAgent().build_user_prompt({"rows": rows})

    assert "TC000001" in prompt
    assert "TC000002" in prompt
    assert "more routine rows not listed" in prompt


def test_the_prompt_carries_the_computed_counts() -> None:
    prompt = RegistryDiffAgent().build_user_prompt({"rows": [_row()], "sync_date": "2026-08-18"})

    assert "critical: 1" in prompt
    assert "2026-08-18" in prompt


# -------------------------------------------------------------------- service


def _claimed_paid_provider(
    make_provider: Callable[..., Provider], *, tier: str = Tier.PREMIUM
) -> Provider:
    provider = make_provider()
    provider.claim_status = ClaimStatus.CLAIMED
    provider.tier = tier
    provider.save()
    return provider


def test_a_paying_companys_delisting_suspends_its_placement_and_mails_operations(
    django_capture_on_commit_callbacks: Callable[..., Any],
    sync_run: SyncRun,
    make_change: Callable[..., LicenseeChange],
    make_provider: Callable[..., Provider],
    moderator: User,
) -> None:
    provider = _claimed_paid_provider(make_provider)
    change = make_change(licence_no=provider.licensee.licence_no)

    with django_capture_on_commit_callbacks(execute=True):
        digest = agent_services.summarise_registry_diff(sync_run)

    assert digest is not None
    provider.refresh_from_db()
    change.refresh_from_db()
    assert provider.paid_placement_suspended_at is not None
    assert provider.effective_tier == Tier.FREE
    assert digest.suspended == [provider.licensee.licence_no]
    assert change.severity == ChangeSeverity.CRITICAL
    assert change.notified_at is not None
    assert change.ai_summary
    assert len(mail.outbox) == 1
    assert moderator.email in mail.outbox[0].recipients()
    assert provider.licensee.licence_no in mail.outbox[0].body


def test_an_unclaimed_company_leaving_the_register_wakes_nobody(
    sync_run: SyncRun,
    make_change: Callable[..., LicenseeChange],
    make_licensee: Callable[..., Licensee],
) -> None:
    """The page carries the deregistration notice by itself. There are hundreds
    of these a year and an alert nobody can act on is an alert nobody reads."""
    licensee = make_licensee()
    change = make_change(licence_no=licensee.licence_no)

    digest = agent_services.summarise_registry_diff(sync_run)

    assert digest is not None
    change.refresh_from_db()
    assert change.severity == ChangeSeverity.INFO
    assert change.notified_at is None
    assert digest.critical_count == 0
    assert mail.outbox == []


def test_a_claimed_but_unpaid_delisting_is_a_warning_not_an_alarm(
    sync_run: SyncRun,
    make_change: Callable[..., LicenseeChange],
    make_provider: Callable[..., Provider],
) -> None:
    provider = make_provider()
    provider.claim_status = ClaimStatus.CLAIMED
    provider.save()
    change = make_change(licence_no=provider.licensee.licence_no)

    agent_services.summarise_registry_diff(sync_run)

    change.refresh_from_db()
    provider.refresh_from_db()
    assert change.severity == ChangeSeverity.WARN
    assert provider.paid_placement_suspended_at is None
    assert mail.outbox == []


def test_running_it_twice_does_not_send_a_second_alert(
    django_capture_on_commit_callbacks: Callable[..., Any],
    sync_run: SyncRun,
    make_change: Callable[..., LicenseeChange],
    make_provider: Callable[..., Provider],
    moderator: User,
) -> None:
    """The task retries; the operator should not get the same alarm twice."""
    provider = _claimed_paid_provider(make_provider)
    make_change(licence_no=provider.licensee.licence_no)

    with django_capture_on_commit_callbacks(execute=True):
        agent_services.summarise_registry_diff(sync_run)
        second = agent_services.summarise_registry_diff(sync_run)

    assert second is not None
    assert second.suspended == []
    assert second.notified == 0
    assert len(mail.outbox) == 1


def test_a_sync_that_changed_nothing_produces_no_digest(sync_run: SyncRun) -> None:
    assert agent_services.summarise_registry_diff(sync_run) is None


def test_the_digest_is_written_from_the_rules_when_no_model_answers(
    django_capture_on_commit_callbacks: Callable[..., Any],
    sync_run: SyncRun,
    make_change: Callable[..., LicenseeChange],
    make_provider: Callable[..., Provider],
    moderator: User,
) -> None:
    """The suite runs with ``AGENTS_ENABLED`` off, so this is the path the
    platform takes on any day the vendor is unreachable."""
    provider = _claimed_paid_provider(make_provider, tier=Tier.VERIFIED)
    make_change(licence_no=provider.licensee.licence_no)

    with django_capture_on_commit_callbacks(execute=True):
        digest = agent_services.summarise_registry_diff(sync_run)

    assert digest is not None
    assert digest.used_fallback
    assert digest.critical_count == 1
    assert len(mail.outbox) == 1


def test_the_agent_run_is_logged_against_the_sync_and_not_a_company(
    sync_run: SyncRun,
    make_change: Callable[..., LicenseeChange],
    make_provider: Callable[..., Provider],
) -> None:
    """CLAUDE.md rule 4. The subject of the run is the sync, which is what a
    reader of the log needs to find it by."""
    from apps.agents.models import AgentRun

    provider = _claimed_paid_provider(make_provider)
    make_change(licence_no=provider.licensee.licence_no)

    agent_services.summarise_registry_diff(sync_run)

    run = AgentRun.objects.get(agent_name=registry_diff.RegistryDiffAgent.name)
    assert run.object_type == "registry.SyncRun"
    assert run.object_id == str(sync_run.pk)
