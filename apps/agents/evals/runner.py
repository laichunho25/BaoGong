"""Golden-set loading and scoring.

AI_AGENTS principle 5 asks every agent for an eval with a stated threshold.
ARCHITECTURE section 7 puts the run itself outside CI - it costs money and it
talks to the real API - so this module is split from the test that uses it:
the loading and the scoring are cheap and *are* checked in CI, and only the
part that calls a model carries the ``eval`` marker.

Each agent is scored on the one failure that costs somebody something:

* A4 moderation - escalation recall. Missing a defamatory review is the
  expensive failure; flagging an ordinary one costs a moderator a minute.
* A1 intake - services F1, plus a hard zero on budgets nobody stated. A
  fabricated budget travels to licensed companies as if the buyer wrote it.
* A5 quote analysis - ``missing_govt_fee`` precision. A flag that cries wolf
  teaches buyers to ignore the one that matters.
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


# --------------------------------------------------------------------- A1 intake

#: AI_AGENTS A1. Micro-averaged over services, because the buyer feels a missed
#: service as a missing column in every quote they receive.
INTAKE_SERVICES_F1_THRESHOLD = 0.85
#: Not a threshold anybody may relax. A budget the buyer never wrote is a
#: fabricated fact about their money, sitting in a form field they may not
#: re-read before licensed companies see it.
MAX_HALLUCINATED_BUDGET_RATE = 0.0


@dataclass(frozen=True, slots=True)
class IntakeCase:
    id: str
    raw_input: str
    expect_services: tuple[str, ...]
    #: Whether the buyer stated a budget at all. The eval does not check the
    #: number - it checks that a number appears only when one was written.
    expect_budget: bool


def load_intake_golden() -> list[IntakeCase]:
    path = EVAL_DIR / "rfq_intake" / "golden.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"no golden set for rfq_intake: {path}")
    return [
        IntakeCase(
            id=row["id"],
            raw_input=row["raw_input"],
            expect_services=tuple(row.get("expect_services", ())),
            expect_budget=bool(row.get("expect_budget", False)),
        )
        for row in _rows(path)
    ]


@dataclass(frozen=True, slots=True)
class IntakeScore:
    true_positives: int
    false_positives: int
    false_negatives: int
    #: Cases where the buyer named no budget at all - the only ones where a
    #: number in the output can be judged invented.
    budget_silent: int
    budget_invented: int

    @property
    def precision(self) -> float:
        found = self.true_positives + self.false_positives
        return self.true_positives / found if found else 1.0

    @property
    def recall(self) -> float:
        wanted = self.true_positives + self.false_negatives
        return self.true_positives / wanted if wanted else 1.0

    @property
    def services_f1(self) -> float:
        if not (self.precision + self.recall):
            return 0.0
        return 2 * self.precision * self.recall / (self.precision + self.recall)

    @property
    def hallucinated_budget_rate(self) -> float:
        """Share of cases with no stated budget that came back with one."""
        return self.budget_invented / self.budget_silent if self.budget_silent else 0.0

    def __str__(self) -> str:
        return (
            f"services F1 {self.services_f1:.2f} "
            f"(P {self.precision:.2f} / R {self.recall:.2f}), "
            f"hallucinated budgets {self.budget_invented}"
        )


def score_intake(
    cases: list[IntakeCase], produced: dict[str, tuple[tuple[str, ...], bool]]
) -> IntakeScore:
    """Compare A1's readings to the golden set.

    ``produced`` maps a case id to (services it found, whether it filled in a
    budget). Cases with no answer count as having found nothing, which is the
    honest reading of a run that fell over.
    """
    true_positives = false_positives = false_negatives = 0
    budget_silent = budget_invented = 0
    for case in cases:
        found, has_budget = produced.get(case.id, ((), False))
        expected = set(case.expect_services)
        actual = set(found)
        true_positives += len(expected & actual)
        false_positives += len(actual - expected)
        false_negatives += len(expected - actual)
        if not case.expect_budget:
            budget_silent += 1
            budget_invented += int(has_budget)
    return IntakeScore(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        budget_silent=budget_silent,
        budget_invented=budget_invented,
    )


# -------------------------------------------------------------- A5 quote analysis

#: AI_AGENTS A5. Precision rather than recall: a wrongly raised
#: ``missing_govt_fee`` tells a buyer to doubt a quote that was fine, and a
#: flag buyers learn to ignore protects nobody.
MISSING_GOVT_FEE_PRECISION_THRESHOLD = 0.9


@dataclass(frozen=True, slots=True)
class QuoteCase:
    id: str
    #: The agent context for one quote: totals, line items, validity, and what
    #: the buyer asked for. Held as a dict because it is the agent's input
    #: shape, and a second typed copy of it here would drift from that one.
    quote: dict[str, Any]
    expect_missing_govt_fee: bool


def load_quote_golden() -> list[QuoteCase]:
    path = EVAL_DIR / "quote_analysis" / "golden.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"no golden set for quote_analysis: {path}")
    return [
        QuoteCase(
            id=row["id"],
            quote=row["quote"],
            expect_missing_govt_fee=bool(row["expect_missing_govt_fee"]),
        )
        for row in _rows(path)
    ]


@dataclass(frozen=True, slots=True)
class QuoteScore:
    total: int
    should_flag: int
    caught: int
    false_flags: int

    @property
    def precision(self) -> float:
        raised = self.caught + self.false_flags
        return self.caught / raised if raised else 1.0

    @property
    def recall(self) -> float:
        return self.caught / self.should_flag if self.should_flag else 1.0

    def __str__(self) -> str:
        return (
            f"{self.total} quotes, missing_govt_fee precision {self.precision:.2f}, "
            f"recall {self.recall:.2f}"
        )


def score_quote(cases: list[QuoteCase], flagged: dict[str, bool]) -> QuoteScore:
    """Compare A5's ``missing_govt_fee`` calls to the golden set."""
    should = [case for case in cases if case.expect_missing_govt_fee]
    ordinary = [case for case in cases if not case.expect_missing_govt_fee]
    return QuoteScore(
        total=len(cases),
        should_flag=len(should),
        caught=sum(1 for case in should if flagged.get(case.id)),
        false_flags=sum(1 for case in ordinary if flagged.get(case.id)),
    )
