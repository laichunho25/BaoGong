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

from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.contrib import admin, messages
from django.shortcuts import render
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

from apps.reviews import services
from apps.reviews.models import Nnc1Verification, Review, ReviewReply, ReviewScore

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.db.models import QuerySet
    from django.http import HttpRequest, HttpResponse

    from apps.accounts.models import User


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


class VerificationReasonForm(forms.Form):
    """Why this NNC1 passed or failed.

    Separate from ``ReasonForm`` only in its label: the reason ends up on a
    different row and answers a different question ("why does this reviewer
    carry a badge"), and a moderator writing it should be told so.
    """

    reason = forms.CharField(
        label=_("Reason (recorded on the verification)"),
        widget=forms.Textarea(attrs={"rows": 4, "cols": 80}),
    )


@admin.register(Nnc1Verification)
class Nnc1VerificationAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """The NNC1 queue.

    Built around one comparison: what the uploader typed, beside what the
    official register holds for the company being reviewed. A moderator should
    be able to reject the obvious mismatches from this screen without opening
    anything, and should have to open the document for everything else -
    ``matching.py`` explains why agreement is not evidence.
    """

    list_display = ("review", "declared_secretary_name", "match_state", "scan_state", "result")
    list_filter = ("result", "scan_status", "match_method")
    search_fields = (
        "review__provider__slug",
        "review__author__email",
        "declared_company_name",
        "declared_secretary_name",
        "sha256",
    )
    date_hierarchy = "created_at"
    actions = ("pass_verifications", "fail_verifications")

    # Everything the uploader supplied is read-only: it is their statement, and
    # a moderator editing it before deciding on it destroys the only record of
    # what was actually submitted.
    readonly_fields = (
        "review",
        "register_comparison",
        "declared_company_name",
        "declared_company_no",
        "declared_secretary_name",
        "match_state",
        "match_detail",
        "extracted_state",
        "scan_state",
        "document",
        "original_filename",
        "size_bytes",
        "sha256",
        "result",
        "reviewed_by",
        "reviewed_at",
        "review_note",
        "purge_at",
        "purged_at",
        "created_at",
    )
    fields = readonly_fields

    def has_add_permission(self, request: HttpRequest) -> bool:
        """NNC1s arrive from reviewers, through the upload form."""
        return False

    def get_queryset(self, request: HttpRequest) -> QuerySet[Nnc1Verification]:
        queryset: QuerySet[Nnc1Verification] = super().get_queryset(request)
        return queryset.select_related("review__provider__licensee", "review__author")

    @admin.display(description=_("Declared secretary vs official register"))
    def register_comparison(self, obj: Nnc1Verification) -> str:
        """The uploader's transcription beside the register's own record.

        The register column is the read-only mirror of the official file
        (CLAUDE.md rule 1). The footer states the asymmetry in words, because a
        percentage next to two names invites reading a high number as a pass.
        """
        licensee = obj.review.provider.licensee
        rows = [
            (
                _("Secretary named on the NNC1"),
                obj.declared_secretary_name,
                licensee.name_en if licensee else "",
            ),
            (_("Chinese name"), "", licensee.name_zh if licensee else ""),
            (_("Licence no."), "", licensee.licence_no if licensee else ""),
            (_("Licence status"), "", licensee.get_status_display() if licensee else ""),
            (_("Reviewer's own company"), obj.declared_company_name, ""),
            (_("Company no."), obj.declared_company_no, ""),
        ]
        body = format_html_join(
            "",
            "<tr><th style='text-align:left;padding:2px 12px 2px 0'>{}</th>"
            "<td style='padding:2px 12px 2px 0'>{}</td><td>{}</td></tr>",
            rows,
        )
        return format_html(
            "<table><thead><tr><th></th><th style='text-align:left'>{}</th>"
            "<th style='text-align:left'>{}</th></tr></thead><tbody>{}</tbody>"
            "<tfoot><tr><td colspan='3' style='padding-top:6px'>{}</td></tr></tfoot></table>",
            _("Uploader"),
            _("Official register"),
            body,
            _(
                "The uploader typed the left column, so agreement proves nothing on its own "
                "- open the document. Disagreement is the useful signal."
            ),
        )

    @admin.display(description=_("Name match (evidence only)"))
    def match_state(self, obj: Nnc1Verification) -> str:
        score = f"{obj.match_score:.2f}" if obj.match_score is not None else "-"
        return f"{obj.get_match_method_display()} ({score})"

    @admin.display(description=_("Scan"))
    def scan_state(self, obj: Nnc1Verification) -> str:
        if obj.purged_at is not None:
            return str(_("Purged (retention)"))
        detail = f" - {obj.scan_detail}" if obj.scan_detail else ""
        return f"{obj.get_scan_status_display()}{detail}"

    @admin.display(description=_("AI extraction (advice only)"))
    def extracted_state(self, obj: Nnc1Verification) -> str:
        """A3's reading of the document, labelled as advice (CLAUDE.md rule 3)."""
        if not obj.extracted:
            return str(_("No AI extraction (rule-based comparison only)."))
        rows = format_html_join(
            "",
            "<li><strong>{}</strong>: {}</li>",
            ((key, str(value)) for key, value in sorted(obj.extracted.items())),
        )
        return format_html(
            "<ul>{}</ul><p>{} {}</p>",
            rows,
            _("Confidence:"),
            obj.extraction_confidence if obj.extraction_confidence is not None else "-",
        )

    @admin.display(description=_("Document"))
    def document(self, obj: Nnc1Verification) -> str:
        if not obj.is_readable:
            # Deliberately not a link, same as claim evidence: an unscanned or
            # purged file is not opened in a moderator's browser either.
            return str(_("Not available - not cleared by a scanner, or purged"))
        url = reverse("reviews:nnc1_document", args=[obj.pk])
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', url, _("Open"))

    def _decide(
        self,
        request: HttpRequest,
        queryset: QuerySet[Nnc1Verification],
        *,
        action: str,
        title: Any,
        passed: bool,
    ) -> HttpResponse | None:
        form = VerificationReasonForm(request.POST if "apply_reason" in request.POST else None)
        if form.is_valid():
            reason = form.cleaned_data["reason"]
            decided = 0
            for verification in queryset:
                try:
                    services.decide_verification(
                        verification=verification,
                        reviewer=cast("User", request.user),
                        passed=passed,
                        note=reason,
                    )
                except services.ReviewError as exc:
                    self.message_user(request, f"{verification.pk}: {exc}", messages.ERROR)
                else:
                    decided += 1
            if decided:
                self.message_user(
                    request,
                    _("%(count)s verification(s) updated.") % {"count": decided},
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

    @admin.action(description=_("Pass verification - grants the badge (reason required)"))
    def pass_verifications(
        self, request: HttpRequest, queryset: QuerySet[Nnc1Verification]
    ) -> HttpResponse | None:
        return self._decide(
            request,
            queryset,
            action="pass_verifications",
            title=_("Pass NNC1 verifications"),
            passed=True,
        )

    @admin.action(description=_("Fail verification (reason required)"))
    def fail_verifications(
        self, request: HttpRequest, queryset: QuerySet[Nnc1Verification]
    ) -> HttpResponse | None:
        return self._decide(
            request,
            queryset,
            action="fail_verifications",
            title=_("Fail NNC1 verifications"),
            passed=False,
        )


@admin.register(ReviewReply)
class ReviewReplyAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Company replies, for takedown only - staff do not write them."""

    list_display = ("provider", "review", "published_at")
    search_fields = ("provider__slug", "body")
    readonly_fields = ("review", "provider", "author", "body", "published_at")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False
