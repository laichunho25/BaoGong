"""The review moderation queue.

Same shape as the claim queue (providers/admin.py) and for the same reason: the
rules live in ``services``, so this screen is one caller among several and a
decision made here carries the same reason and the same attribution as one made
anywhere else.

What is customised is what a moderator has to see before deciding: the full
text, the sub-scores, whether NNC1 verification has happened, and the AI
moderation labels - shown as *advice with its own confidence*, never as a
verdict. CLAUDE.md rule 3: the agent's output is not what changes the row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django import forms
from django.contrib import admin, messages
from django.shortcuts import render
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

from apps.reviews import services
from apps.reviews.models import Review, ReviewReply, ReviewScore

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.db.models import QuerySet
    from django.http import HttpRequest, HttpResponse


class ReasonForm(forms.Form):
    """Why this review was published, hidden or removed."""

    reason = forms.CharField(
        label=_("Reason (recorded on the review)"),
        widget=forms.Textarea(attrs={"rows": 4, "cols": 80}),
    )


class ReviewScoreInline(admin.TabularInline):  # type: ignore[type-arg]
    model = ReviewScore
    extra = 0
    can_delete = False

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("provider", "author", "overall", "is_verified", "status", "created_at")
    list_filter = ("status", "is_verified", "created_at")
    search_fields = ("provider__slug", "author__email", "body")
    date_hierarchy = "created_at"
    inlines = (ReviewScoreInline,)
    actions = ("publish_reviews", "hide_reviews", "remove_reviews")
    readonly_fields = (
        "provider",
        "author",
        "overall",
        "service_used",
        "engagement_year",
        "is_verified",
        "status",
        "published_at",
        "moderated_by",
        "moderated_at",
        "moderation_note",
        "helpful_count",
        "ai_assessment",
    )
    exclude = ("moderation",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Reviews come from buyers, never from staff typing one in."""
        return False

    def get_queryset(self, request: HttpRequest) -> QuerySet[Review]:
        return super().get_queryset(request).select_related("provider__licensee", "author", "score")

    @admin.display(description=_("AI assessment (advice only)"))
    def ai_assessment(self, obj: Review) -> str:
        """The moderation agent's output, labelled as advice.

        Rendered as a plain list rather than a badge, deliberately: it should
        not look like a decision the moderator is confirming.
        """
        data = obj.moderation or {}
        if not data:
            return str(_("No AI assessment (rule-based queue)."))
        rows = format_html_join(
            "",
            "<li><strong>{}</strong>: {}</li>",
            ((key, str(value)) for key, value in sorted(data.items())),
        )
        return format_html(
            "<ul>{}</ul><p>{}</p>",
            rows,
            _("Advisory only. The decision and its reason are yours."),
        )

    def _decide(
        self,
        request: HttpRequest,
        queryset: QuerySet[Review],
        *,
        action: str,
        title: Any,
        apply: Callable[..., Review],
    ) -> HttpResponse | None:
        form = ReasonForm(request.POST if "apply_reason" in request.POST else None)
        if form.is_valid():
            reason = form.cleaned_data["reason"]
            decided = 0
            for review in queryset:
                try:
                    apply(review=review, moderator=request.user, note=reason)
                except services.ReviewError as exc:
                    self.message_user(request, f"{review.pk}: {exc}", messages.ERROR)
                else:
                    decided += 1
            if decided:
                self.message_user(
                    request,
                    _("%(count)s review(s) updated.") % {"count": decided},
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

    @admin.action(description=_("Publish selected reviews (reason required)"))
    def publish_reviews(
        self, request: HttpRequest, queryset: QuerySet[Review]
    ) -> HttpResponse | None:
        return self._decide(
            request,
            queryset,
            action="publish_reviews",
            title=_("Publish reviews"),
            apply=services.publish_review,
        )

    @admin.action(description=_("Hide selected reviews (reason required)"))
    def hide_reviews(self, request: HttpRequest, queryset: QuerySet[Review]) -> HttpResponse | None:
        return self._decide(
            request,
            queryset,
            action="hide_reviews",
            title=_("Hide reviews"),
            apply=services.hide_review,
        )

    @admin.action(description=_("Remove selected reviews (reason required)"))
    def remove_reviews(
        self, request: HttpRequest, queryset: QuerySet[Review]
    ) -> HttpResponse | None:
        return self._decide(
            request,
            queryset,
            action="remove_reviews",
            title=_("Remove reviews"),
            apply=services.remove_review,
        )


@admin.register(ReviewReply)
class ReviewReplyAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Company replies, for takedown only - staff do not write them."""

    list_display = ("provider", "review", "published_at")
    search_fields = ("provider__slug", "body")
    readonly_fields = ("review", "provider", "author", "body", "published_at")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False
