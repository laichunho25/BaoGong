"""``BaseAgent`` - the only place this codebase talks to Anthropic.

CLAUDE.md section 5 forbids constructing a client anywhere else, and the reason
is visible in what this class does around the call rather than in the call
itself: a kill switch, a daily budget, a timeout, bounded retries, schema
validation, a rule-based fallback, and an ``AgentRun`` row written on every
path including the ones that never reach the network.

The shape of the contract is deliberate:

* ``run()`` never raises for an agent-side problem. Every failure becomes
  ``fallback()``. A moderation queue that stops accepting reviews because a
  vendor had a bad afternoon is a worse outcome than one moderating by rules.
* ``fallback()`` is abstract. An agent with no deterministic path is an agent
  this platform cannot run, so the type system says so.
* ``AgentResult.used_fallback`` is on the result, not buried in the log,
  because the caller usually needs to treat rule-based advice differently -
  usually by trusting it less and escalating more.

ARCHITECTURE section 4 is the specification; where it and this file differ,
this file also updates the doc in the same commit.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from django.conf import settings
from pydantic import ValidationError

from apps.agents import pricing, redaction, selectors
from apps.agents.models import AgentRun, AgentStatus, FallbackReason
from apps.agents.schemas import tool_schema

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

#: The tool the model must call. One tool, forced, so the only shape the model
#: can answer in is the schema - there is no free-text branch to parse.
TOOL_NAME = "submit"


class PromptNotFound(RuntimeError):
    """A versioned prompt file is missing.

    Raised rather than fallen back on: this is a deployment error, not a
    runtime condition, and silently running the rule-based path forever would
    hide it for as long as nobody reads the fallback rate.
    """


@lru_cache(maxsize=32)
def load_prompt(filename: str) -> str:
    """Read a versioned prompt from ``apps/agents/prompts``.

    Cached because prompts are immutable at runtime - a change means a new
    ``_v2`` file (CLAUDE.md section 5), never an edit to a live one.
    """
    path = PROMPT_DIR / filename
    if not path.is_file():
        raise PromptNotFound(f"prompt file not found: {filename}")
    return path.read_text(encoding="utf-8")


@dataclass(slots=True)
class AgentResult:
    """What an agent returns, from either path.

    ``data`` is always a validated schema instance, so a caller never has to
    ask which path produced it before reading a field.
    """

    data: BaseModel
    confidence: float
    used_fallback: bool
    run_id: str | None = None
    fallback_reason: str = ""


@dataclass(slots=True)
class _CallOutcome:
    """Internal: what one attempt at the API produced."""

    payload: dict[str, Any] | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""
    status: str = AgentStatus.OK
    attempts: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


class BaseAgent(ABC):
    """Base class for every agent. Subclasses supply the prompt and fallback."""

    name: ClassVar[str]
    model: ClassVar[str]
    prompt_file: ClassVar[str]
    output_schema: ClassVar[type[BaseModel]]
    max_tokens: ClassVar[int] = 2048
    timeout_s: ClassVar[int] = 30
    max_retries: ClassVar[int] = 2
    #: Seconds before the first retry; doubled each attempt.
    backoff_base_s: ClassVar[float] = 0.5
    #: What ``AgentRun.object_type`` records, e.g. "reviews.Review".
    object_type: ClassVar[str] = ""

    # ------------------------------------------------------------------ hooks

    @abstractmethod
    def build_user_prompt(self, ctx: dict[str, Any]) -> str | list[dict[str, Any]]:
        """Render the user turn. Must redact anything not needed (COMPLIANCE 4).

        A list of content blocks rather than a string when the agent sends a
        document - A3 reads an NNC1 the model has to actually look at.
        """

    @abstractmethod
    def fallback(self, ctx: dict[str, Any], reason: str) -> BaseModel:
        """Deterministic, non-LLM answer. Required - see the module docstring."""

    def object_id(self, ctx: dict[str, Any]) -> str:
        """Which row this run is about. Used by the moderator's "what did the
        agent say about *this* review" query."""
        return str(ctx.get("object_id", ""))

    def extract_confidence(self, data: BaseModel) -> float:
        """Schemas all carry ``confidence``; overridden if one ever does not."""
        return float(getattr(data, "confidence", 0.0))

    @property
    def prompt_version(self) -> str:
        """``review_moderation_v1.md`` -> ``v1``."""
        stem = Path(self.prompt_file).stem
        _, _, version = stem.rpartition("_")
        return version

    # ------------------------------------------------------------------- run

    def run(self, ctx: dict[str, Any]) -> AgentResult:
        """Answer ``ctx``, by model if possible and by rules otherwise.

        Always writes exactly one ``AgentRun``.
        """
        started = time.monotonic()
        input_hash = redaction.hash_input(ctx)

        blocked = self._blocking_reason()
        if blocked is not None:
            return self._finish_fallback(
                ctx, reason=blocked, input_hash=input_hash, started=started
            )

        outcome = self._call_with_retries(ctx)
        if outcome.payload is None:
            return self._finish_fallback(
                ctx,
                reason=self._reason_for(outcome.status),
                input_hash=input_hash,
                started=started,
                outcome=outcome,
            )

        try:
            data = self.output_schema.model_validate(outcome.payload)
        except ValidationError as exc:
            # The model answered, and answered something this system cannot
            # use. That is a prompt problem; record it as one rather than as a
            # generic error, because the fix is a new prompt version.
            logger.warning("agent %s returned unusable output: %s", self.name, exc)
            outcome.status = AgentStatus.INVALID_SCHEMA
            outcome.error = str(exc)[:2000]
            return self._finish_fallback(
                ctx,
                reason=FallbackReason.INVALID_SCHEMA,
                input_hash=input_hash,
                started=started,
                outcome=outcome,
            )

        confidence = self.extract_confidence(data)
        run = self._log(
            ctx=ctx,
            input_hash=input_hash,
            status=AgentStatus.OK,
            fallback_reason="",
            output=data.model_dump(mode="json"),
            confidence=confidence,
            started=started,
            outcome=outcome,
        )
        return AgentResult(
            data=data, confidence=confidence, used_fallback=False, run_id=str(run.pk)
        )

    # ------------------------------------------------------------- gatekeeping

    def _blocking_reason(self) -> str | None:
        """Why this call must not reach the API, if it must not.

        Checked before the client is built so that a switched-off agent costs
        nothing at all - not a connection, not an import.
        """
        if not getattr(settings, "AGENTS_ENABLED", True):
            return FallbackReason.DISABLED
        if not getattr(settings, f"AGENT_ENABLED_{self.name.upper()}", True):
            return FallbackReason.DISABLED
        if not settings.ANTHROPIC_API_KEY:
            return FallbackReason.NO_API_KEY
        if self._spent_today() >= settings.AGENT_BUDGET_DAILY_USD:
            logger.error("agent budget for the day is spent; %s is falling back", self.name)
            return FallbackReason.BUDGET
        return None

    def _spent_today(self) -> Decimal:
        """Total agent spend since midnight, Hong Kong time.

        Across all agents, not this one: the budget protects the account, and a
        per-agent allowance would let six agents spend six budgets.
        """
        return selectors.spend_today()

    @staticmethod
    def _reason_for(status: str) -> str:
        if status == AgentStatus.TIMEOUT:
            return FallbackReason.TIMEOUT
        if status == AgentStatus.INVALID_SCHEMA:
            return FallbackReason.INVALID_SCHEMA
        return FallbackReason.API_ERROR

    # ----------------------------------------------------------------- the call

    def _client(self) -> Any:
        """Built per call rather than held on the class.

        Agents run inside Celery workers that fork; a client created at import
        time is a socket shared across processes.
        """
        import anthropic

        return anthropic.Anthropic(
            api_key=settings.ANTHROPIC_API_KEY, timeout=float(self.timeout_s), max_retries=0
        )

    def _call_with_retries(self, ctx: dict[str, Any]) -> _CallOutcome:
        """Try the API up to ``max_retries + 1`` times with exponential backoff.

        Retries are handled here rather than by the SDK so that every attempt
        is counted into the one ``AgentRun`` this call will write.
        """
        import anthropic

        outcome = _CallOutcome()
        for attempt in range(self.max_retries + 1):
            outcome.attempts = attempt + 1
            try:
                return self._call_once(ctx, attempts=attempt + 1)
            except anthropic.APITimeoutError as exc:
                outcome.status = AgentStatus.TIMEOUT
                outcome.error = str(exc)[:2000]
            except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
                outcome.status = AgentStatus.ERROR
                outcome.error = str(exc)[:2000]
                # 4xx other than rate-limiting will fail again identically;
                # spending three attempts on a malformed request is waste.
                status_code = getattr(exc, "status_code", None)
                if isinstance(status_code, int) and 400 <= status_code < 500 and status_code != 429:
                    break
            except Exception as exc:
                outcome.status = AgentStatus.ERROR
                outcome.error = f"{type(exc).__name__}: {exc}"[:2000]
                break

            if attempt < self.max_retries:
                time.sleep(self.backoff_base_s * (2**attempt))

        logger.warning(
            "agent %s failed after %s attempts: %s", self.name, outcome.attempts, outcome.error
        )
        return outcome

    def _call_once(self, ctx: dict[str, Any], *, attempts: int) -> _CallOutcome:
        """One request. Forced tool use, so the reply is the schema or nothing."""
        response = self._client().messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=load_prompt(self.prompt_file),
            messages=[{"role": "user", "content": self.build_user_prompt(ctx)}],
            tools=[
                {
                    "name": TOOL_NAME,
                    "description": f"Return the {self.name} result.",
                    "input_schema": tool_schema(self.output_schema),
                }
            ],
            tool_choice={"type": "tool", "name": TOOL_NAME},
        )
        usage = getattr(response, "usage", None)
        outcome = _CallOutcome(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            attempts=attempts,
        )
        for block in response.content:
            if getattr(block, "type", "") == "tool_use" and getattr(block, "name", "") == TOOL_NAME:
                outcome.payload = dict(block.input)
                return outcome

        # Forced tool use and no tool call: treat as unusable output rather
        # than as an error, because the fix is the prompt, not the transport.
        outcome.status = AgentStatus.INVALID_SCHEMA
        outcome.error = "model returned no tool_use block"
        return outcome

    # ------------------------------------------------------------------ logging

    def _finish_fallback(
        self,
        ctx: dict[str, Any],
        *,
        reason: str,
        input_hash: str,
        started: float,
        outcome: _CallOutcome | None = None,
    ) -> AgentResult:
        data = self.fallback(ctx, reason)
        run = self._log(
            ctx=ctx,
            input_hash=input_hash,
            status=AgentStatus.FALLBACK,
            fallback_reason=reason,
            output=data.model_dump(mode="json"),
            confidence=self.extract_confidence(data),
            started=started,
            outcome=outcome,
        )
        return AgentResult(
            data=data,
            confidence=self.extract_confidence(data),
            used_fallback=True,
            run_id=str(run.pk),
            fallback_reason=reason,
        )

    def _log(
        self,
        *,
        ctx: dict[str, Any],
        input_hash: str,
        status: str,
        fallback_reason: str,
        output: dict[str, Any],
        confidence: float,
        started: float,
        outcome: _CallOutcome | None,
    ) -> AgentRun:
        """Write the row CLAUDE.md rule 4 requires.

        Failing to log must not fail the call: the answer is already computed,
        and losing it because the audit write broke would turn a bookkeeping
        problem into a user-facing one. The lost row is logged loudly instead.
        """
        outcome = outcome or _CallOutcome()
        cost = pricing.estimate_cost(
            self.model,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
        )
        try:
            return AgentRun.objects.create(
                agent_name=self.name,
                model=self.model,
                prompt_version=self.prompt_version,
                input_hash=input_hash,
                input_ref=redaction.summarise_for_log(ctx),
                output=output,
                status=status,
                fallback_reason=fallback_reason,
                confidence=Decimal(str(round(confidence, 3))),
                input_tokens=outcome.input_tokens,
                output_tokens=outcome.output_tokens,
                cost_usd=cost,
                latency_ms=int((time.monotonic() - started) * 1000),
                attempts=outcome.attempts,
                error=outcome.error,
                object_type=self.object_type,
                object_id=self.object_id(ctx),
            )
        except Exception:
            logger.exception("could not record AgentRun for %s", self.name)
            return AgentRun(agent_name=self.name, model=self.model, status=status)
