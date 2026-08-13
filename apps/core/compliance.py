"""Banned-phrase screening.

COMPLIANCE.md section 2 requires this check to be applied to three surfaces:
LLM output, provider-authored copy, and platform marketing copy.

This is a *screen*, not a decision: a ``blocking`` hit must stop publication,
while a ``warn`` hit routes the text to a human. Phrases are listed in both
Simplified and Traditional Chinese because UI copy is Simplified while much
provider-supplied copy is Traditional.

Phase 0 note: the rule set below is transcribed from COMPLIANCE.md section 2
and is deliberately conservative. It is not a substitute for legal review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class Severity(StrEnum):
    BLOCKING = "blocking"
    WARN = "warn"


class RuleCode(StrEnum):
    GUARANTEED_BANK_SUCCESS = "guaranteed_bank_success"
    BANK_SUCCESS_RATE = "bank_success_rate"
    FALSE_OFFICIAL_STATUS = "false_official_status"
    ABSOLUTE_RANKING = "absolute_ranking"
    PLATFORM_AS_SERVICE_PROVIDER = "platform_as_service_provider"


@dataclass(frozen=True, slots=True)
class Rule:
    code: RuleCode
    severity: Severity
    pattern: re.Pattern[str]
    explanation: str


@dataclass(frozen=True, slots=True)
class Violation:
    code: RuleCode
    severity: Severity
    matched_text: str
    start: int
    end: int
    explanation: str


def _rule(code: RuleCode, severity: Severity, alternatives: list[str], explanation: str) -> Rule:
    return Rule(
        code=code,
        severity=severity,
        pattern=re.compile("|".join(alternatives), re.IGNORECASE),
        explanation=explanation,
    )


RULES: Final[list[Rule]] = [
    _rule(
        RuleCode.GUARANTEED_BANK_SUCCESS,
        Severity.BLOCKING,
        [
            r"保[證证][開开]?[户戶]?成功",
            r"[開开]?[户戶]包成功",
            r"100\s*%\s*[開开]?[户戶]",
            r"包[過过]",
            r"必定批核",
            r"一定[能可][開开]?[户戶]",
        ],
        "COMPLIANCE section 2: the platform must never promise a bank-account outcome.",
    ),
    _rule(
        RuleCode.BANK_SUCCESS_RATE,
        Severity.BLOCKING,
        [
            r"[開开]?[户戶]成功率\s*[:：]?\s*\d",
            r"\d+\s*%\s*(的)?[開开]?[户戶]成功",
        ],
        "COMPLIANCE section 2: no quantified bank-account success rate may be published.",
    ),
    _rule(
        RuleCode.FALSE_OFFICIAL_STATUS,
        Severity.BLOCKING,
        [
            r"官方[認认][證证]",
            r"官方授[權权]",
            r"政府推[薦荐]",
            r"[註注][冊册][處处]指定",
            r"政府指定",
        ],
        "COMPLIANCE section 1/2: the platform is not official and is not "
        "endorsed by any authority.",
    ),
    _rule(
        RuleCode.PLATFORM_AS_SERVICE_PROVIDER,
        Severity.BLOCKING,
        [
            r"本平台.{0,8}提供.{0,8}(公司秘[書书]|[註注][冊册])服[務务]",
            r"我[們们].{0,8}代[辦办].{0,4}[註注][冊册]",
            r"本站.{0,8}代[辦办]",
        ],
        "COMPLIANCE section 6: the platform is an information platform, not a TCSP provider.",
    ),
    _rule(
        RuleCode.ABSOLUTE_RANKING,
        Severity.WARN,
        [
            r"全[港球國国]第一",
            r"排名第一",
            r"[業业][內内]第一",
            r"[唯惟]一(的)?選[擇择]",
            r"最佳選[擇择]",
            r"全[港球]最(好|佳|強强)",
        ],
        "COMPLIANCE section 2 / PRC advertising law: absolute superlatives need verifiable proof.",
    ),
]


def check_banned_phrases(text: str) -> list[Violation]:
    """Return every banned-phrase hit in ``text``, ordered by position.

    An empty list means the text passed the screen. Callers decide what to do:
    ``blocking`` violations must prevent publication, ``warn`` violations route
    to human review.
    """
    if not text:
        return []

    violations: list[Violation] = []
    for rule in RULES:
        for match in rule.pattern.finditer(text):
            violations.append(
                Violation(
                    code=rule.code,
                    severity=rule.severity,
                    matched_text=match.group(0),
                    start=match.start(),
                    end=match.end(),
                    explanation=rule.explanation,
                )
            )
    violations.sort(key=lambda v: (v.start, v.code))
    return violations


def has_blocking_violation(text: str) -> bool:
    """True when ``text`` contains at least one publication-blocking phrase."""
    return any(v.severity is Severity.BLOCKING for v in check_banned_phrases(text))
