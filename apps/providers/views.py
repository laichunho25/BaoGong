"""Public directory. Reads through selectors only; no ORM writes here."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.utils.safestring import SafeString, mark_safe
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.accounts.permissions import is_moderator, verified_email_required
from apps.core.storage import signed_url
from apps.providers import selectors, services
from apps.providers.forms import ClaimSubmissionForm
from apps.providers.models import (
    BankType,
    ClaimEvidence,
    Language,
    Provider,
    ProviderClaim,
    Tier,
)
from apps.providers.tasks import scan_claim_evidence
from apps.providers.verification import WELL_KNOWN_PATH
from apps.registry.selectors import registry_last_synced_at

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest, HttpResponse
    from django.http.response import HttpResponseBase

    from apps.accounts.models import User

PAGE_SIZE = 20

# Labels live here, beside the options they name, rather than as an if-chain in
# the template. SortOption itself stays free of UI copy so selectors.py has no
# translation dependency.
SORT_LABELS = (
    (selectors.SortOption.RECOMMENDED, _("推荐排序")),
    (selectors.SortOption.RATING, _("评分由高到低")),
    (selectors.SortOption.NAME, _("按名称")),
    (selectors.SortOption.NEWEST, _("最新登记")),
)


def _shared_context(request: HttpRequest) -> dict[str, Any]:
    """Attribution COMPLIANCE section 1 requires wherever registry data shows."""
    return {"registry_last_synced_at": registry_last_synced_at()}


def provider_list(request: HttpRequest) -> HttpResponse:
    """The directory.

    HTMX swaps only the results block, so the same view serves both the full
    page and the fragment. The querystring is the whole state - it has to be
    shareable, and hx-push-url keeps the address bar honest.
    """
    filters = selectors.DirectoryFilters.from_request(request.GET)
    page_number = request.GET.get("page", "1")
    paginator = Paginator(selectors.filter_directory(filters), PAGE_SIZE)
    page = paginator.get_page(page_number)

    context: dict[str, Any] = {
        "page": page,
        "filters": filters,
        "districts": selectors.available_districts(),
        "languages": Language.choices,
        "bank_types": BankType.choices,
        "tiers": Tier.choices,
        "sort_options": SORT_LABELS,
        "total": paginator.count,
        "max_compare": selectors.MAX_COMPARE,
        **_shared_context(request),
    }

    if request.headers.get("HX-Request"):
        return render(request, "providers/_results.html", context)
    return render(request, "providers/list.html", context)


def _organization_jsonld(request: HttpRequest, provider: Provider) -> SafeString:
    """schema.org Organization for the detail page.

    ``aggregateRating`` is emitted only when verified reviews exist. Search
    results are a page the platform does not control, so an unearned 5.00 must
    not leak into them any more than it may appear on the page itself
    (RATING_SYSTEM section 4).
    """
    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": provider.display_name,
        "url": request.build_absolute_uri(provider.get_absolute_url()),
        "identifier": provider.licensee.licence_no if provider.licensee else provider.slug,
    }
    if provider.licensee and provider.licensee.business_address:
        data["address"] = {
            "@type": "PostalAddress",
            "streetAddress": provider.licensee.business_address,
            "addressCountry": "HK",
        }
    if provider.website:
        data["sameAs"] = [provider.website]
    if provider.has_verified_reviews and provider.rating_cached is not None:
        data["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": str(provider.rating_cached),
            "reviewCount": provider.verified_review_count,
            "bestRating": "5",
        }

    # "<" is escaped so that a name containing "</script>" cannot break out of
    # the tag; json.dumps does not do this on its own.
    return mark_safe(json.dumps(data, ensure_ascii=False).replace("<", "\\u003c"))


def provider_detail(request: HttpRequest, slug: str) -> HttpResponse:
    provider = selectors.get_provider_detail(slug)
    if provider is None:
        raise Http404("No such provider")

    return render(
        request,
        "providers/detail.html",
        {
            "provider": provider,
            "licensee": provider.licensee,
            "offerings": provider.offerings.all(),
            "certifications": [cert for cert in provider.certifications.all() if cert.is_current],
            "jsonld": _organization_jsonld(request, provider),
            **_shared_context(request),
        },
    )


def _scan_later(evidence_id: str) -> Callable[[], None]:
    """Bind one evidence id for ``on_commit``.

    A closure over the loop variable would queue the last file several times;
    binding it here also keeps the callback out of the loop body, where its
    late-binding bug is easy to reintroduce.
    """
    return lambda: scan_claim_evidence.delay(evidence_id)


@login_required
@verified_email_required
def claim_start(request: HttpRequest, slug: str) -> HttpResponse:
    """Submit a claim for one provider page.

    The form and the files are validated here; every write - the claim row, the
    stored evidence, the scan job - happens in ``services``.
    """
    provider = selectors.claimable_provider(slug)
    if provider is None:
        raise Http404("This page cannot be claimed")

    if request.method == "POST":
        form = ClaimSubmissionForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                claim = services.submit_claim(
                    provider=provider,
                    user=cast("User", request.user),
                    contact_name=form.cleaned_data["contact_name"],
                    contact_phone=form.cleaned_data["contact_phone"],
                    contact_role=form.cleaned_data["contact_role"],
                    business_registration_no=form.cleaned_data["business_registration_no"],
                    website=form.cleaned_data["website"],
                    applicant_note=form.cleaned_data["applicant_note"],
                )
            except services.ClaimError as exc:
                form.add_error(None, str(exc))
            else:
                for upload, inspected in zip(
                    form.cleaned_data["evidence"], form.inspected, strict=True
                ):
                    evidence = services.attach_evidence(
                        claim=claim,
                        upload=upload,
                        inspected=inspected,
                        kind=form.cleaned_data["evidence_kind"],
                    )
                    # Queued after the row exists, and only after commit: an
                    # eager worker would otherwise look for a claim the open
                    # transaction has not written yet.
                    transaction.on_commit(_scan_later(str(evidence.pk)))
                messages.success(request, _("认领申请已提交，我们会在 3 个工作日内回复。"))
                return redirect("providers:claim_detail", claim_id=claim.pk)
    else:
        form = ClaimSubmissionForm(initial={"website": provider.website})

    return render(
        request,
        "providers/claim_start.html",
        {
            "provider": provider,
            "licensee": provider.licensee,
            "form": form,
            **_shared_context(request),
        },
    )


def _claim_for_applicant(request: HttpRequest, claim_id: str) -> ProviderClaim:
    """The claim, if this account is entitled to see it - 404 otherwise.

    Not 403: whether a claim exists for a given company is itself information.
    """
    claim = selectors.get_claim(claim_id)
    if claim is None:
        raise Http404("No such claim")
    if claim.submitted_by_id != request.user.pk and not is_moderator(cast("User", request.user)):
        raise Http404("No such claim")
    return claim


@login_required
def claim_detail(request: HttpRequest, claim_id: str) -> HttpResponse:
    """Status of one application, plus the token the applicant has to publish."""
    claim = _claim_for_applicant(request, claim_id)
    return render(
        request,
        "providers/claim_detail.html",
        {
            "claim": claim,
            "provider": claim.provider,
            "evidence": list(claim.evidence.all()),
            "site_verification_key": settings.CLAIM_SITE_VERIFICATION_KEY,
            "well_known_path": WELL_KNOWN_PATH,
            **_shared_context(request),
        },
    )


@login_required
@require_POST
def claim_verify_website(request: HttpRequest, claim_id: str) -> HttpResponse:
    """Run the website check now, at the applicant's request."""
    claim = _claim_for_applicant(request, claim_id)
    services.verify_claim_website(claim)
    if claim.is_website_verified:
        messages.success(request, _("网站所有权验证成功。"))
    else:
        messages.warning(request, _("尚未找到验证记录。DNS 生效可能需要数小时，请稍后重试。"))
    return redirect("providers:claim_detail", claim_id=claim.pk)


@login_required
@require_POST
def claim_withdraw(request: HttpRequest, claim_id: str) -> HttpResponse:
    claim = _claim_for_applicant(request, claim_id)
    try:
        services.withdraw_claim(claim=claim, user=cast("User", request.user))
    except services.ClaimError as exc:
        messages.error(request, str(exc))
    else:
        messages.info(request, _("认领申请已撤回。"))
    return redirect("providers:claim_detail", claim_id=claim.pk)


@login_required
def claim_evidence_download(request: HttpRequest, evidence_id: str) -> HttpResponseBase:
    """Hand out one evidence file, if it may be handed out at all.

    Three gates, in this order: the requester is the applicant or a moderator;
    the file has been scanned and not purged; and only then a short-lived
    signed URL (S3) or a streamed response (local disk). Nothing about this
    path is guessable from the storage key, which is why templates never call
    ``.url`` on these files.
    """
    evidence = ClaimEvidence.objects.select_related("claim").filter(pk=evidence_id).first()
    if evidence is None:
        raise Http404("No such file")
    requester = cast("User", request.user)
    if evidence.claim.submitted_by_id != requester.pk and not is_moderator(requester):
        raise Http404("No such file")
    if not evidence.is_readable:
        # Includes scan_pending: an unscanned file is never opened in someone's
        # browser, not even the uploader's.
        raise Http404("This file is not available")

    url = signed_url(evidence.file.name or "")
    if url:
        return redirect(url)
    response = FileResponse(evidence.file.open("rb"), content_type=evidence.content_type)
    response["Content-Disposition"] = f'attachment; filename="evidence.{evidence.extension}"'
    # Personal data: no shared cache may keep a copy.
    response["Cache-Control"] = "private, no-store"
    return response


def provider_compare(request: HttpRequest) -> HttpResponse:
    """Side-by-side comparison of up to three providers.

    Over the limit the extras are dropped rather than rejected: a shared link
    with four ids should still show something useful.
    """
    raw = request.GET.get("ids", "")
    providers = selectors.get_providers_for_compare([part.strip() for part in raw.split(",")])

    return render(
        request,
        "providers/compare.html",
        {
            "providers": providers,
            "max_compare": selectors.MAX_COMPARE,
            **_shared_context(request),
        },
    )
