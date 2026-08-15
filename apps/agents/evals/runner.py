"""Golden-set loading and scoring.

AI_AGENTS principle 5 asks every agent for an eval with a stated threshold.
ARCHITECTURE section 7 puts the run itself outside CI - it costs money and it
talks to the real API - so this module is split from the test that uses it:
the loading and the scoring are cheap and *are* checked in CI, and only the
part that calls a model carries the ``eval`` marker.

The metric that matters for A4 is escalation recall. Missing a defamatory
review is the expensive failure; flagging an ordinary one costs a moderator a
minute.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

EVAL_DIR = Path(__file__).resolve().parent

#: AI_AGENTS A4. Deliberately not 1.0: a threshold nobody can hit is a
#: threshold that gets deleted the first time it fails.
ESCALATION_RECALL_THRESHOLD = 0.95
#: How many ordinary reviews may be escalated anyway. Loose, because the cost
#: is a moderator's minute, and because P4 has no moderation capacity problem.
MAX_FALSE_ESCALATION_RATE = 0.35


@dataclass(frozen=True, slots=True)
class GoldenCase:
    id: str
    body: str
    expect_escalation: bool
    expect_labels: tuple[str, ...]


def load_golden(agent_name: str) -> list[GoldenCase]:
    """Read one agent's golden set. Raises if it is missing or malformed."""
    path = EVAL_DIR / agent_name / "golden.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"no golden set for {agent_name}: {path}")
    return [
        GoldenCase(
            id=row["id"],
            body=row["body"],
            expect_escalation=bool(row["expect_escalation"]),
            expect_labels=tuple(row.get("expect_labels", ())),
        )
        for row in _rows(path)
    ]


def _rows(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


@dataclass(frozen=True, slots=True)
class EvalScore:
    """What one eval run produced, in the terms the threshold is written in."""

    total: int
    should_escalate: int
    caught: int
    false_escalations: int

    @property
    def escalation_recall(self) -> float:
        return self.caught / self.should_escalate if self.should_escalate else 1.0

    @property
    def false_escalation_rate(self) -> float:
        ordinary = self.total - self.should_escalate
        return self.false_escalations / ordinary if ordinary else 0.0

    def __str__(self) -> str:
        return (
            f"{self.total} cases, escalation recall {self.escalation_recall:.2f}, "
            f"false escalation {self.false_escalation_rate:.2f}"
        )


def score(cases: list[GoldenCase], escalated: dict[str, bool]) -> EvalScore:
    """Compare a run's escalation decisions to the golden set."""
    should = [case for case in cases if case.expect_escalation]
    ordinary = [case for case in cases if not case.expect_escalation]
    return EvalScore(
        total=len(cases),
        should_escalate=len(should),
        caught=sum(1 for case in should if escalated.get(case.id)),
        false_escalations=sum(1 for case in ordinary if escalated.get(case.id)),
    )
