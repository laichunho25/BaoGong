"""Reads over the agent log. No writes (ARCHITECTURE section 3).

These exist because the ``AgentRun`` table is only worth its cost if somebody
looks at it. Three questions get asked of it in practice: what did an agent say
about this row, what are we spending today, and how often is the model not
answering at all.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, NamedTuple

from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.agents.models import AgentRun, AgentStatus

if TYPE_CHECKING:
    from django.db.models import QuerySet


def object_key(obj: Any) -> tuple[str, str]:
    """The ``(object_type, object_id)`` convention, in one place."""
    return f"{obj._meta.app_label}.{obj._meta.object_name}", str(obj.pk)


def runs_for(obj: Any) -> QuerySet[AgentRun]:
    """Every run recorded against one row, newest first (``Meta.ordering``)."""
    object_type, object_id = object_key(obj)
    return AgentRun.objects.filter(object_type=object_type, object_id=object_id)


def latest_run_for(obj: Any) -> AgentRun | None:
    return runs_for(obj).first()


def spend_since(since: Any) -> Decimal:
    """Total agent cost since a moment. The budget guard's read, reusable."""
    total = AgentRun.objects.filter(created_at__gte=since).aggregate(t=Sum("cost_usd"))["t"]
    return total or Decimal("0")


def spend_today() -> Decimal:
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return spend_since(start)


class AgentHealth(NamedTuple):
    """One agent's recent behaviour, for an operations screen."""

    agent_name: str
    runs: int
    fallbacks: int
    cost_usd: Decimal

    @property
    def fallback_rate(self) -> float:
        """The number to watch.

        A fallback is a normal outcome; a *rising* fallback rate means the
        platform is quietly running on rules while everyone assumes otherwise.
        """
        return self.fallbacks / self.runs if self.runs else 0.0


def health(*, days: int = 7) -> list[AgentHealth]:
    """Per-agent run count, fallback count and spend over a recent window."""
    since = timezone.now() - timedelta(days=days)
    rows = (
        AgentRun.objects.filter(created_at__gte=since)
        .values("agent_name")
        .annotate(
            runs=Count("pk"),
            fallbacks=Count("pk", filter=Q(status=AgentStatus.FALLBACK)),
            cost=Sum("cost_usd"),
        )
        .order_by("agent_name")
    )
    return [
        AgentHealth(
            agent_name=row["agent_name"],
            runs=row["runs"],
            fallbacks=row["fallbacks"],
            cost_usd=row["cost"] or Decimal("0"),
        )
        for row in rows
    ]
