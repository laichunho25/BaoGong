"""A7 - turn one day's register diff into something an operator can act on.

AI_AGENTS A7. The bold rule in that section is the whole design: **the rules
decide severity, the model only writes the copy.** Whether a licence that
vanished from the official file matters depends on facts this codebase already
holds - is the page claimed, is the company paying us for placement - and none
of them are things a language model should be asked to weigh. So
``severity_for`` decides, ``screen_digest`` throws away any critical item the
model wrote about a change the rules did not call critical, and writes a
template item for any critical change the model failed to mention.

``counts`` is recomputed from the rows after the model answers. It is the part
of a digest somebody forwards, and a wrong number about the official register
is exactly the thing CLAUDE.md rule 1 exists to prevent.

The fallback is a numeric summary from a template. It carries every critical
item, because the automation that runs beside it - suspending a paying
company's placement when its licence leaves the register - happens on the rules
either way, and an operator who is emailed about it must be able to read what
happened whether or not any model was reachable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Final

from django.utils.translation import gettext

from apps.agents.base import BaseAgent
from apps.agents.schemas import CriticalItem, DiffDigestOut
from apps.registry.models import ChangeSeverity, ChangeType

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

#: How many individual rows the prompt carries. Every critical row is included
#: before this applies; the cap only trims the routine tail, which the model is
#: asked to summarise from the counts rather than row by row. A day that
#: renames two thousand companies is a day whose digest must still fit.
MAX_ROWS_IN_PROMPT: Final = 40

#: What ``DiffDigestOut.critical_items`` will hold. A day that delists
#: twenty-five paying companies is a day whose digest is not the problem, but
#: the schema is a hard cap and a validation error inside ``fallback()`` would
#: turn a bad day into no digest at all - so the overflow is counted in the
#: summary instead of dropped silently.
MAX_CRITICAL_ITEMS: Final = 25

#: Change types that say nothing about the licence itself, only about how it is
#: written down. Kept apart from the licence events in ``severity_for``.
_DETAIL_CHANGES: Final = frozenset({ChangeType.RENAMED, ChangeType.ADDRESS_CHANGED})


def severity_for(*, change_type: str, claimed: bool, paid: bool) -> str:
    """How much attention one change needs. Rules only - see the module docstring.

    A licence leaving the register is the same event for every company; what
    differs is what the platform is doing with the page. An unclaimed page
    already carries the deregistration notice automatically
    (``registry.notices``), so nobody has to be woken up for it. A paying
    company's page is being promoted by us, and until somebody looks at it we
    are promoting a company that is no longer on the official list.
    """
    if change_type == ChangeType.REMOVED:
        if paid:
            return ChangeSeverity.CRITICAL
        return ChangeSeverity.WARN if claimed else ChangeSeverity.INFO
    if change_type in _DETAIL_CHANGES or change_type == ChangeType.REACTIVATED:
        return ChangeSeverity.WARN if claimed else ChangeSeverity.INFO
    return ChangeSeverity.INFO


def digest_counts(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Counts by change type, plus the three totals a digest is read for."""
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("change_type", ""))
        counts[key] = counts.get(key, 0) + 1
    counts["total"] = len(rows)
    counts["critical"] = len(critical_rows(rows))
    counts["claimed_affected"] = len([row for row in rows if row.get("claimed")])
    return counts


def critical_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows the rules called critical, in register order."""
    return [row for row in rows if row.get("severity") == ChangeSeverity.CRITICAL]


def describe(row: dict[str, Any]) -> str:
    """One line of English for the prompt.

    Official data and platform status only: no member, no claimant, nothing
    about who is behind the page (COMPLIANCE section 4).
    """
    parts = [
        f"{row.get('licence_no', '?')} [{row.get('change_type', '?')}]",
        f"severity={row.get('severity', ChangeSeverity.INFO)}",
        f"claimed={bool(row.get('claimed'))}",
        f"paid={bool(row.get('paid'))}",
        f"name={row.get('provider_name') or '(unknown)'}",
    ]
    if row.get("change_type") in _DETAIL_CHANGES:
        parts.append(f"before={_short(row.get('before'))} after={_short(row.get('after'))}")
    return " ".join(parts)


def _short(value: Any, *, limit: int = 120) -> str:
    if not value:
        return "-"
    text = ", ".join(f"{key}={val}" for key, val in dict(value).items())
    return text[:limit]


class RegistryDiffAgent(BaseAgent):
    name: ClassVar[str] = "registry_diff"
    model: ClassVar[str] = "claude-haiku-4-5-20251001"
    prompt_file: ClassVar[str] = "registry_diff_v1.md"
    output_schema: ClassVar[type[DiffDigestOut]] = DiffDigestOut
    max_tokens: ClassVar[int] = 1536
    object_type: ClassVar[str] = "registry.SyncRun"

    def build_user_prompt(self, ctx: dict[str, Any]) -> str:
        rows: list[dict[str, Any]] = list(ctx.get("rows", []))
        critical = critical_rows(rows)
        routine = [row for row in rows if row.get("severity") != ChangeSeverity.CRITICAL]
        shown = critical + routine[: max(MAX_ROWS_IN_PROMPT - len(critical), 0)]

        lines = [
            f"Sync date (Hong Kong time): {ctx.get('sync_date', 'unknown')}",
            f"Rows in the official file today: {ctx.get('row_count', 0)} "
            f"(previous run: {ctx.get('prev_row_count', 'unknown')})",
            "",
            "--- counts, already computed; do not add them up yourself ---",
        ]
        lines += [f"- {key}: {value}" for key, value in digest_counts(rows).items()]

        lines += ["", "--- changes (severity is already decided; do not change it) ---"]
        if not shown:
            lines.append("(none)")
        lines += [f"- {describe(row)}" for row in shown]
        if len(rows) > len(shown):
            lines.append(
                f"...and {len(rows) - len(shown)} more routine rows not listed. "
                "Describe those from the counts above."
            )
        return "\n".join(lines)

    def fallback(self, ctx: dict[str, Any], reason: str) -> DiffDigestOut:
        return template_digest(list(ctx.get("rows", [])))


def template_digest(rows: Sequence[dict[str, Any]]) -> DiffDigestOut:
    """The digest with no prose in it: numbers, and one line per critical row.

    Confidence 0 because nothing here was judged - which is also why it is
    safe. The counts are the same counts a screened answer carries.
    """
    counts = digest_counts(rows)
    critical = critical_rows(rows)
    return DiffDigestOut(
        headline=gettext("官方名单今日有 %(total)s 项变动，其中 %(critical)s 项需要立即处理。")
        % {"total": counts["total"], "critical": counts["critical"]},
        critical_items=[template_item(row) for row in critical[:MAX_CRITICAL_ITEMS]],
        routine_summary=_with_overflow(routine_summary(counts), len(critical)),
        counts=counts,
        confidence=0.0,
    )


def _with_overflow(summary: str, critical_count: int) -> str:
    """Say out loud that the list of alarms was cut short."""
    if critical_count <= MAX_CRITICAL_ITEMS:
        return summary
    return summary + gettext("另有 %(count)s 项严重变动未在本邮件列出，请到后台查看。") % {
        "count": critical_count - MAX_CRITICAL_ITEMS
    }


def template_item(row: dict[str, Any]) -> CriticalItem:
    """A critical item written by a rule.

    Used as the fallback's copy, and to replace anything the model left out of
    an otherwise usable digest.
    """
    licence_no = str(row.get("licence_no", ""))[:32]
    return CriticalItem(
        licence_no=licence_no,
        provider_name=str(row.get("provider_name") or "")[:255],
        what=gettext("牌照 %(licence)s 已不在官方名单内。") % {"licence": licence_no},
        why_it_matters=gettext("该公司在本平台仍有付费曝光，需要立即人工复核。"),
        action=gettext("付费曝光已自动暂停，请核对官方名单后决定是否恢复。"),
    )


def routine_summary(counts: dict[str, int]) -> str:
    """One line of numbers for everything that is not critical."""
    labels = {
        ChangeType.NEW: gettext("新增"),
        ChangeType.REMOVED: gettext("移除"),
        ChangeType.REACTIVATED: gettext("重新上榜"),
        ChangeType.RENAMED: gettext("名称变更"),
        ChangeType.ADDRESS_CHANGED: gettext("地址变更"),
    }
    parts = [f"{label} {counts[key]}" for key, label in labels.items() if counts.get(key)]
    if not parts:
        return gettext("今日无变动。")
    return "；".join(parts) + "。"


def screen_digest(data: DiffDigestOut, rows: Sequence[dict[str, Any]]) -> DiffDigestOut:
    """Keep the model's words, keep the rules' facts.

    Three things happen here and each one prevents a specific failure:

    * an item about a licence the rules did not call critical is dropped -
      otherwise the model decides who gets an alert;
    * a critical row the model did not write about gets a template item -
      otherwise a quiet omission removes an alert;
    * ``counts`` is replaced with the computed counts.
    """
    critical = critical_rows(rows)
    allowed = {str(row.get("licence_no")): row for row in critical}

    kept: list[CriticalItem] = []
    seen: set[str] = set()
    for item in data.critical_items:
        row = allowed.get(item.licence_no)
        if row is None or item.licence_no in seen:
            continue
        seen.add(item.licence_no)
        # The register's own name wins over whatever the model typed: this is
        # official data and the model is not a source of it (CLAUDE.md rule 1).
        kept.append(item.model_copy(update={"provider_name": str(row.get("provider_name") or "")}))

    kept += [template_item(row) for row in critical if str(row.get("licence_no")) not in seen]

    counts = digest_counts(rows)
    return data.model_copy(
        update={
            "headline": data.headline.strip() or template_digest(rows).headline,
            "critical_items": kept[:MAX_CRITICAL_ITEMS],
            "routine_summary": _with_overflow(
                data.routine_summary.strip() or routine_summary(counts), len(kept)
            ),
            "counts": counts,
        }
    )
