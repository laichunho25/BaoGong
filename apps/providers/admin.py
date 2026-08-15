"""Internal admin for the platform-side profile.

Unlike ``registry``, these rows are editable: everything here is platform data
that staff or the provider itself supplies. The licensee link is read-only
though - repointing a profile at a different licence would silently move every
review and quote attached to it onto another company.

The claim queue also lives here rather than in a bespoke moderator UI. P3 sees
single digits of applications a day; the cost of a purpose-built screen is
worth paying in P4, where review moderation needs batch actions and AI labels.
What the admin does add is the three things a decision actually needs: the
application and the official register side by side, evidence that can only be
opened through the permission-checked download view, and a decision that cannot
be recorded without a reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django import forms
from django.contrib import admin, messages
from django.shortcuts import render
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

from apps.providers import services
from apps.providers.models import (
    Certification,
    ClaimEvidence,
    PriceItem,
    Provider,
    ProviderClaim,
    ServiceOffering,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.db.models import QuerySet
    from django.http import HttpRequest, HttpResponse


class ServiceOfferingInline(admin.TabularInline):  # type: ignore[type-arg]
    model = ServiceOffering
    extra = 0
    show_change_link = True


class PriceItemInline(admin.TabularInline):  # type: ignore[type-arg]
    model = PriceItem
    extra = 0


class CertificationInline(admin.TabularInline):  # type: ignore[type-arg]
    model = Certification
    extra = 0


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "slug",
        "claim_status",
        "tier",
        "rating_cached",
        "ranking_score",
        "is_published",
    )
    list_filter = ("claim_status", "tier", "is_published", "commission_agreement")
    search_fields = ("slug", "licensee__licence_no", "licensee__name_en", "licensee__name_zh")
    autocomplete_fields = ("licensee",)
    readonly_fields = (
        "licensee",
        "slug",
        # Denormalised by services.py and reviews; editing them by hand would
        # be overwritten by the next recompute and would look like tampering.
        "rating_cached",
        "rating_count",
        "verified_review_count",
        "profile_completeness",
        "responsiveness_score",
        "ranking_score",
    )
    inlines = [ServiceOfferingInline, CertificationInline]


@admin.register(ServiceOffering)
class ServiceOfferingAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("provider", "category", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("provider__slug",)
    inlines = [PriceItemInline]


class ReasonForm(forms.Form):
    """The reason every decision and every scan override has to carry.

    An approval nobody wrote a reason for is unreviewable six months later,
    when a competitor asks why this company got the badge - so the field is
    required here and required again in ``services``.
    """

    reason = forms.CharField(
        label=_("Reason (recorded on the claim)"),
        widget=forms.Textarea(attrs={"rows": 4, "cols": 80}),
    )


def _evidence_link(evidence: ClaimEvidence) -> str:
    """A link through the download view, never a direct storage URL.

    The view re-checks who is asking and whether the file has been scanned;
    a raw ``.url`` would hand out bytes that no scanner has cleared, and on S3
    it would be a signed link that keeps working after the file is purged.
    """
    if not evidence.is_readable:
        return ""
    return reverse("providers:claim_evidence", args=[evidence.pk])


class ClaimEvidenceInline(admin.TabularInline):  # type: ignore[type-arg]
    """Evidence is read-only here: files arrive through the claim form only."""

    model = ClaimEvidence
    extra = 0
    can_delete = False
    fields = ("kind", "original_filename", "size_bytes", "scan_state", "download", "sha256")
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    @admin.display(description=_("Scan"))
    def scan_state(self, obj: ClaimEvidence) -> str:
        if obj.purged_at is not None:
            return str(_("Purged (retention)"))
        detail = f" - {obj.scan_detail}" if obj.scan_detail else ""
        return f"{obj.get_scan_status_display()}{detail}"

    @admin.display(description=_("File"))
    def download(self, obj: ClaimEvidence) -> str:
        url = _evidence_link(obj)
        if not url:
            # Deliberately not a link: an unscanned file is not opened in a
            # moderator's browser either.
            return str(_("Not available - not cleared by a scanner"))
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', url, _("Open"))


@admin.register(ProviderClaim)
class ProviderClaimAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """The moderator queue."""

    list_display = (
        "provider",
        "submitted_by",
        "status",
        "website_state",
        "evidence_state",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = (
        "provider__slug",
        "provider__licensee__licence_no",
        "provider__licensee__name_en",
        "submitted_by__email",
    )
    date_hierarchy = "created_at"
    actions = ("approve_claims", "reject_claims")
    inlines = [ClaimEvidenceInline]

    # Everything except the internal notes: the application is the applicant's
    # statement, and a moderator editing it before deciding on it would destroy
    # the only record of what was actually claimed.
    readonly_fields = (
        "provider",
        "submitted_by",
        "register_comparison",
        "contact_name",
        "contact_role",
        "contact_phone",
        "applicant_note",
        "website",
        "website_state",
        "verification_trail",
        "status",
        "reviewer",
        "reviewed_at",
        "decision_reason",
        "ai_risk_flags",
        "created_at",
    )
    fields = (
        "provider",
        "submitted_by",
        "register_comparison",
        "contact_name",
        "contact_role",
        "contact_phone",
        "applicant_note",
        "website",
        "website_state",
        "verification_trail",
        "status",
        "reviewer",
        "reviewed_at",
        "decision_reason",
        "ai_risk_flags",
        "notes",
        "created_at",
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[ProviderClaim]:
        queryset: QuerySet[ProviderClaim] = super().get_queryset(request)
        return queryset.select_related("provider__licensee", "submitted_by").prefetch_related(
            "evidence"
        )

    def has_add_permission(self, request: HttpRequest) -> bool:
        # Claims are opened by applicants, through the claim form.
        return False

    @admin.display(description=_("Website"), boolean=True)
    def website_state(self, obj: ProviderClaim) -> bool:
        return obj.is_website_verified

    @admin.display(description=_("Evidence"))
    def evidence_state(self, obj: ProviderClaim) -> str:
        files = list(obj.evidence.all())
        blocked = len([item for item in files if not item.is_readable])
        if blocked:
            return str(_("%(total)s file(s), %(blocked)s not cleared")) % {
                "total": len(files),
                "blocked": blocked,
            }
        return str(_("%(total)s file(s)")) % {"total": len(files)}

    @admin.display(description=_("Application vs official register"))
    def register_comparison(self, obj: ProviderClaim) -> str:
        """What the applicant says, beside what the register says.

        The register column is the mirror of the official file (CLAUDE.md rule
        1) and is never edited here; it is the thing the applicant's statement
        has to be judged against. The BR number has no counterpart - the
        register does not publish one - and that is stated rather than left as
        an empty cell a reviewer might read as a mismatch.
        """
        licensee = obj.provider.licensee
        rows = [
            (_("Company"), obj.contact_name, licensee.name_en if licensee else ""),
            (_("Chinese name"), "", licensee.name_zh if licensee else ""),
            (_("Licence no."), "", licensee.licence_no if licensee else ""),
            (_("Licence status"), "", licensee.get_status_display() if licensee else ""),
            (_("Address"), "", licensee.business_address if licensee else ""),
            (
                _("BR number"),
                obj.business_registration_no,
                str(_("not published in the register")),
            ),
            (_("Website"), obj.website, obj.provider.website),
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
            "<tfoot><tr><td colspan='3' style='padding-top:6px'>{} {}</td></tr></tfoot></table>",
            _("Applicant"),
            _("Official register"),
            body,
            _("Register data last synced:"),
            licensee.last_synced_at if licensee else "-",
        )

    @admin.display(description=_("Website verification trail"))
    def verification_trail(self, obj: ProviderClaim) -> str:
        if not obj.website_verification_log:
            return str(_("Never attempted"))
        return format_html(
            "<div>{}<ul>{}</ul></div>",
            format_html("<code>{}</code>", obj.expected_token_value),
            format_html_join(
                "",
                "<li>{}: {} - {}</li>",
                (
                    (entry.get("method", ""), entry.get("ok", False), entry.get("detail", ""))
                    for entry in obj.website_verification_log
                ),
            ),
        )

    def _decide(
        self,
        request: HttpRequest,
        queryset: QuerySet[ProviderClaim],
        *,
        action: str,
        title: Any,
        apply: Callable[..., ProviderClaim],
    ) -> HttpResponse | None:
        """Ask for the reason, then let ``services`` decide whether it may happen.

        Nothing is validated here beyond the reason being present. Who may
        decide, and whether the evidence has been cleared, are enforced in
        ``services`` - the admin is one caller among several, and the rules
        must not depend on which door the decision came through.
        """
        form = ReasonForm(request.POST if "apply_reason" in request.POST else None)
        if form.is_valid():
            reason = form.cleaned_data["reason"]
            decided = 0
            for claim in queryset:
                try:
                    apply(claim=claim, reviewer=request.user, reason=reason)
                except services.ClaimError as exc:
                    self.message_user(request, f"{claim.provider.slug}: {exc}", messages.ERROR)
                else:
                    decided += 1
            if decided:
                self.message_user(
                    request,
                    _("%(count)s claim(s) updated.") % {"count": decided},
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

    @admin.action(description=_("Approve selected claims (reason required)"))
    def approve_claims(
        self, request: HttpRequest, queryset: QuerySet[ProviderClaim]
    ) -> HttpResponse | None:
        return self._decide(
            request,
            queryset,
            action="approve_claims",
            title=_("Approve claims"),
            apply=services.approve_claim,
        )

    @admin.action(description=_("Reject selected claims (reason required)"))
    def reject_claims(
        self, request: HttpRequest, queryset: QuerySet[ProviderClaim]
    ) -> HttpResponse | None:
        return self._decide(
            request,
            queryset,
            action="reject_claims",
            title=_("Reject claims"),
            apply=services.reject_claim,
        )


@admin.register(ClaimEvidence)
class ClaimEvidenceAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Files, mostly so a stuck scan can be released deliberately.

    Read-only apart from that one action: these rows describe bytes that were
    uploaded, and editing the description of a file after the fact would make
    the audit trail worthless.
    """

    list_display = ("claim", "kind", "scan_status", "size_bytes", "purge_at", "purged_at")
    list_filter = ("scan_status", "kind")
    search_fields = ("claim__provider__slug", "sha256")
    actions = ("override_scan",)
    readonly_fields = (*(f.name for f in ClaimEvidence._meta.fields), "download")
    fields = readonly_fields

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    @admin.display(description=_("File"))
    def download(self, obj: ClaimEvidence) -> str:
        url = _evidence_link(obj)
        if not url:
            return str(_("Not available - not cleared by a scanner"))
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', url, _("Open"))

    @admin.action(description=_("Release without a scan (reason required)"))
    def override_scan(
        self, request: HttpRequest, queryset: QuerySet[ClaimEvidence]
    ) -> HttpResponse | None:
        """The valve for the case where no scanner is configured yet.

        It refuses files a scanner actually reported as infected - that check
        is in ``services`` - so this releases the unknown, never the known bad,
        and the row afterwards names who accepted the risk.
        """
        form = ReasonForm(request.POST if "apply_reason" in request.POST else None)
        if form.is_valid():
            released = 0
            for evidence in queryset:
                try:
                    services.override_scan(
                        evidence=evidence,
                        reviewer=request.user,  # type: ignore[arg-type]
                        reason=form.cleaned_data["reason"],
                    )
                except services.ClaimError as exc:
                    self.message_user(request, f"{evidence.pk}: {exc}", messages.ERROR)
                else:
                    released += 1
            if released:
                self.message_user(
                    request,
                    _("%(count)s file(s) released.") % {"count": released},
                    messages.SUCCESS,
                )
            return None

        return render(
            request,
            "admin/decision_reason.html",
            {
                **self.admin_site.each_context(request),
                "title": _("Release files without a scan"),
                "objects": queryset,
                "form": form,
                "action": "override_scan",
                "opts": self.model._meta,
            },
        )
