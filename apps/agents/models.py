# Fields, constraints, __str__, properties only. No business logic (ARCHITECTURE section 3).
"""The log every LLM call writes, and the human verdict on it.

CLAUDE.md rule 4 makes ``AgentRun`` mandatory rather than nice to have, and the
reason is that an agent is the one part of this system that cannot be read. A
service can be reviewed by reading it; a prompt plus a model can only be
understood from what it actually did. So every call - including the ones that
never reached the API - lands here with its inputs hashed, its cost, and what
came back.

``AgentFeedback`` closes the loop the other way: a moderator who disagrees with
an agent is the only source of eval data this platform will ever have that is
not synthetic.
"""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel


class AgentStatus(models.TextChoices):
    """How a call ended.

    ``FALLBACK`` is not an error state. It is the normal outcome whenever the
    agent is switched off, the daily budget is spent, or the API misbehaved -
    and the rule-based path ran instead. A fallback rate that is *not* being
    watched is the failure; the status exists so it can be.
    """

    OK = "ok", _("OK")
    INVALID_SCHEMA = "invalid_schema", _("Invalid schema")
    TIMEOUT = "timeout", _("Timeout")
    ERROR = "error", _("Error")
    FALLBACK = "fallback", _("Fell back to rules")


class FallbackReason(models.TextChoices):
    """Why the rule-based path ran. Blank when the model answered."""

    DISABLED = "disabled", _("Agent switched off")
    NO_API_KEY = "no_api_key", _("No API key configured")
    BUDGET = "budget", _("Daily budget spent")
    API_ERROR = "api_error", _("API error after retries")
    INVALID_SCHEMA = "invalid_schema", _("Model returned unusable output")
    TIMEOUT = "timeout", _("Timed out")


class AgentRun(BaseModel):
    """One call to one agent, model-backed or not.

    ``input_ref`` is deliberately not the input. It is a redacted, summarised
    reference to it (``redaction.py``), because this table is queried by
    operators looking at cost and latency, and a review body or an NNC1's
    contents have no business being in a screen used for that.
    """

    agent_name = models.CharField(max_length=64, db_index=True)
    model = models.CharField(max_length=64)
    prompt_version = models.CharField(max_length=16, blank=True)

    #: The same input twice is the same hash: how a duplicate call is spotted,
    #: and how a golden-set row is matched to the run it came from.
    input_hash = models.CharField(max_length=64, db_index=True)
    input_ref = models.JSONField(default=dict, blank=True)
    output = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=16, choices=AgentStatus.choices, default=AgentStatus.OK, db_index=True
    )
    fallback_reason = models.CharField(
        max_length=16, choices=FallbackReason.choices, blank=True, db_index=True
    )
    confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)

    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    # CLAUDE.md rule 6. Six decimal places because a Haiku call costs a fraction
    # of a cent and a day's spend is the sum of thousands of them.
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True)

    # A generic link rather than a FK: agents fire at reviews, NNC1s, RFQs and
    # quotes, and this table must not grow a column per app that ever uses one.
    object_type = models.CharField(max_length=64, blank=True, db_index=True)
    object_id = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta(BaseModel.Meta):
        verbose_name = _("agent run")
        verbose_name_plural = _("agent runs")
        indexes = [
            # The observability screen: one agent's recent calls.
            models.Index(fields=["agent_name", "-created_at"]),
            # "What did this review's agent say" - the moderator's question.
            models.Index(fields=["object_type", "object_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.agent_name} {self.status} ({self.created_at:%Y-%m-%d %H:%M})"

    @property
    def used_fallback(self) -> bool:
        return self.status == AgentStatus.FALLBACK


class FeedbackVerdict(models.TextChoices):
    CORRECT = "correct", _("Correct")
    PARTIALLY = "partially", _("Partially correct")
    WRONG = "wrong", _("Wrong")


class AgentFeedback(BaseModel):
    """A human's verdict on one run, and the eval set's only honest input.

    Recorded against the run rather than the reviewed object so that changing
    the object later does not rewrite the history of what the agent said about
    it at the time.
    """

    agent_run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="feedback")
    reviewer = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    verdict = models.CharField(max_length=16, choices=FeedbackVerdict.choices)
    notes = models.TextField(blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = _("agent feedback")
        verbose_name_plural = _("agent feedback")
        constraints = [
            # One verdict per reviewer per run: two rows would make "how often
            # is this agent right" unanswerable.
            models.UniqueConstraint(
                fields=["agent_run", "reviewer"], name="agents_one_feedback_per_reviewer"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.agent_run_id}: {self.verdict}"
