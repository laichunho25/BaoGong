"""The agent log, read-only, plus the one thing a moderator writes here.

This screen answers CLAUDE.md rule 4's real purpose: the log is only worth its
storage if somebody can look at what an agent said, what it cost, and how often
it is not answering at all. Runs are immutable - editing one would destroy the
record it exists to be - so the only write available is a verdict on a run,
which is also the platform's only source of non-synthetic eval data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.contrib import admin, messages
from django.shortcuts import render
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

from apps.agents import services
from apps.agents.models import AgentFeedback, AgentRun, FeedbackVerdict

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import HttpRequest, HttpResponse

    from apps.accounts.models import User


class FeedbackForm(forms.Form):
    """Optional note beside a verdict.

    Optional, unlike the mandatory reason on a moderation decision: this
    changes nothing about a user's review or a company's page. It is a note to
    whoever next tunes the prompt.
    """

    reason = forms.CharField(
        label=_("Note (optional - what the agent got right or wrong)"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 4, "cols": 80}),
    )


class AgentFeedbackInline(admin.TabularInline):  # type: ignore[type-arg]
    model = AgentFeedback
    extra = 0
    can_delete = False
    readonly_fields = ("reviewer", "verdict", "notes", "created_at")

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Added through the action, so the reviewer is always the signed-in user."""
        return False


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "agent_name",
        "status",
        "fallback_reason",
        "confidence",
        "cost_usd",
        "latency_ms",
        "created_at",
    )
    list_filter = ("agent_name", "status", "fallback_reason", "model", "created_at")
    search_fields = ("input_hash", "object_id", "error")
    date_hierarchy = "created_at"
    inlines = (AgentFeedbackInline,)
    actions = ("mark_correct", "mark_partially_correct", "mark_wrong")
    readonly_fields = (
        "agent_name",
        "model",
        "prompt_version",
        "input_hash",
        "input_ref",
        "pretty_output",
        "status",
        "fallback_reason",
        "confidence",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "latency_ms",
        "attempts",
        "error",
        "object_type",
        "object_id",
        "created_at",
    )
    exclude = ("output", "updated_at")

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Runs are written by agents. A hand-typed one would be a forged record."""
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Kept even for staff: this is the audit trail rule 4 asks for."""
        return False

    @admin.display(description=_("Output"))
    def pretty_output(self, obj: AgentRun) -> str:
        data = obj.output or {}
        if not data:
            return str(_("No output recorded."))
        rows = format_html_join(
            "",
            "<li><strong>{}</strong>: {}</li>",
            ((key, str(value)) for key, value in sorted(data.items())),
        )
        return format_html(
            "<ul>{}</ul><p>{}</p>",
            rows,
            _("What the agent said at the time. Advice, and already acted on or not."),
        )

    # ------------------------------------------------------------------ actions

    def _record(
        self,
        request: HttpRequest,
        queryset: QuerySet[AgentRun],
        *,
        action: str,
        verdict: str,
        title: Any,
    ) -> HttpResponse | None:
        form = FeedbackForm(request.POST if "apply_reason" in request.POST else None)
        if form.is_valid():
            notes = form.cleaned_data["reason"]
            for run in queryset:
                services.record_feedback(
                    agent_run=run,
                    reviewer=cast("User", request.user),
                    verdict=verdict,
                    notes=notes,
                )
            self.message_user(
                request,
                _("%(count)s verdict(s) recorded.") % {"count": queryset.count()},
                messages.SUCCESS,
            )
            return None

        return render(
            request,
            "admin/decision_reason.html",
            {
                **self.admin_site.each_context(request),
                "title": title,
                "objects": queryset,
                "form": form,
                "action": action,
                "opts": self.model._meta,
            },
        )

    @admin.action(description=_("Mark as correct"))
    def mark_correct(
        self, request: HttpRequest, queryset: QuerySet[AgentRun]
    ) -> HttpResponse | None:
        return self._record(
            request,
            queryset,
            action="mark_correct",
            verdict=FeedbackVerdict.CORRECT,
            title=_("Agent was correct"),
        )

    @admin.action(description=_("Mark as partially correct"))
    def mark_partially_correct(
        self, request: HttpRequest, queryset: QuerySet[AgentRun]
    ) -> HttpResponse | None:
        return self._record(
            request,
            queryset,
            action="mark_partially_correct",
            verdict=FeedbackVerdict.PARTIALLY,
            title=_("Agent was partially correct"),
        )

    @admin.action(description=_("Mark as wrong"))
    def mark_wrong(self, request: HttpRequest, queryset: QuerySet[AgentRun]) -> HttpResponse | None:
        return self._record(
            request,
            queryset,
            action="mark_wrong",
            verdict=FeedbackVerdict.WRONG,
            title=_("Agent was wrong"),
        )


@admin.register(AgentFeedback)
class AgentFeedbackAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Listed on its own so "which agent are we disagreeing with" is one filter."""

    list_display = ("agent_run", "reviewer", "verdict", "created_at")
    list_filter = ("verdict", "agent_run__agent_name", "created_at")
    search_fields = ("notes", "agent_run__input_hash")
    readonly_fields = ("agent_run", "reviewer", "verdict", "notes")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def get_queryset(self, request: HttpRequest) -> QuerySet[AgentFeedback]:
        return super().get_queryset(request).select_related("agent_run", "reviewer")
