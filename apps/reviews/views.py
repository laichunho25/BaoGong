"""Review pages. Reads through selectors, writes through services.

The provider detail page renders the list; these views own the three actions -
write one, reply to one, and read your own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.accounts.permissions import is_moderator, is_provider_member, verified_email_required
from apps.core import turnstile
from apps.core.storage import signed_url
from apps.providers import selectors as provider_selectors
from apps.registry.selectors import registry_last_synced_at
from apps.reviews import selectors, services
from apps.reviews.forms import DisputeForm, Nnc1UploadForm, ReplyForm, ReviewForm
from apps.reviews.models import SCORE_FIELDS, Nnc1Verification
from apps.reviews.tasks import process_nnc1

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse
    from django.http.response import HttpResponseBase

    from apps.accounts.models import User


@login_required
@verified_email_required
def review_create(request: HttpRequest, slug: str) -> HttpResponse:
    """Write a review of one company.

    Requires a verified email address (RATING_SYSTEM section 6) - a review is a
    public statement about a named business, and an unverified address is not
    an account anyone can be held to.
    """
    provider = provider_selectors.get_provider_detail(slug)
    if provider is None:
        raise Http404("No such provider")

    author = cast("User", request.user)
    existing = selectors.review_by_author(provider=provider, author=author)
    if existing is not None:
        messages.info(request, _("您已评价过这家公司。"))
        return redirect("reviews:my_reviews")

    if request.method == "POST":
        form = ReviewForm(request.POST, remote_ip=request.META.get("REMOTE_ADDR"))
        if form.is_valid():
            try:
                services.submit_review(
                    provider=provider,
                    author=author,
                    body=form.cleaned_data["body"],
                    scores=form.scores(),
                    service_used=form.cleaned_data["service_used"],
                    engagement_year=form.cleaned_data["engagement_year"],
                )
            except services.ReviewError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(
                    request, _("评价已提交，审核通过后会公开显示（通常在 3 个工作日内）。")
                )
                return redirect("reviews:my_reviews")
    else:
        form = ReviewForm()

    return render(
        request,
        "reviews/review_form.html",
        {
            "provider": provider,
            "licensee": provider.licensee,
            "form": form,
            # The five sub-scores render as radio rows and the rest as normal
            # fields, so the template needs to tell them apart.
            "score_field_names": SCORE_FIELDS,
            "turnstile_enabled": turnstile.is_enabled(),
            "turnstile_site_key": settings.TURNSTILE_SITE_KEY,
            "registry_last_synced_at": registry_last_synced_at(),
        },
    )


@login_required
def my_reviews(request: HttpRequest) -> HttpResponse:
    """The author's own reviews, including the ones nobody else can see."""
    return render(
        request,
        "reviews/my_reviews.html",
        {"reviews": selectors.reviews_by_author(cast("User", request.user))},
    )


@login_required
@require_POST
def review_reply(request: HttpRequest, review_id: str) -> HttpResponse:
    """The company's answer to one review.

    Membership is checked in services, so this view only has to decide where to
    send the person afterwards.
    """
    review = selectors.get_review(review_id)
    if review is None or not review.is_public:
        raise Http404("No such review")

    form = ReplyForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("回复内容不能为空。"))
    else:
        try:
            services.reply_to_review(
                review=review, author=cast("User", request.user), body=form.cleaned_data["body"]
            )
        except services.ReviewError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("回复已发布。"))

    return redirect(review.provider.get_absolute_url())


@login_required
def dispute_create(request: HttpRequest, review_id: str) -> HttpResponse:
    """The company's appeal against one published review.

    Membership and the rest of the rules are checked in ``services``; this view
    decides only what the person sees. The page is deliberately explicit that
    filing changes nothing about the review yet, and that the answer comes with
    a reason inside five working days - a right of appeal that arrives as
    silence is not one.
    """
    member = cast("User", request.user)
    review = selectors.get_review(review_id)
    if review is None or not review.is_public:
        raise Http404("No such review")
    if not is_provider_member(member, review.provider):
        # 404 rather than 403: which reviews a company has is not something an
        # unrelated account learns by trying.
        raise Http404("No such review")

    existing = selectors.open_dispute_for(review)
    if existing is not None:
        messages.info(request, _("该评价已有一宗待处理的申诉，我们会尽快回复。"))
        return redirect(review.provider.get_absolute_url())

    if request.method == "POST":
        form = DisputeForm(request.POST)
        if form.is_valid():
            try:
                services.raise_dispute(
                    review=review,
                    raised_by=member,
                    ground=form.cleaned_data["ground"],
                    reason=form.cleaned_data["reason"],
                    evidence=form.evidence(),
                )
            except services.ReviewError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(
                    request,
                    _("申诉已提交。审核人员会在 %(days)s 个工作日内处理，并附上处理理由。")
                    % {"days": settings.DISPUTE_SLA_BUSINESS_DAYS},
                )
                return redirect(review.provider.get_absolute_url())
    else:
        form = DisputeForm()

    return render(
        request,
        "reviews/dispute_form.html",
        {
            "review": review,
            "provider": review.provider,
            "form": form,
            "sla_days": settings.DISPUTE_SLA_BUSINESS_DAYS,
        },
    )


@login_required
@verified_email_required
def nnc1_upload(request: HttpRequest, review_id: str) -> HttpResponse:
    """Upload the NNC1 that turns a review into a verified one.

    Only the review's author, because the badge asserts that *this reviewer*
    was a client. Everything else the page has to say is about what happens to
    the document afterwards: private storage, a moderator, 90 days, then the
    bytes are gone and the conclusion stays.
    """
    author = cast("User", request.user)
    review = selectors.get_review(review_id)
    if review is None or review.author_id != author.pk:
        # 404 rather than 403: whether a review exists is not something an
        # unrelated account gets to learn by probing.
        raise Http404("No such review")

    existing = selectors.verification_for(review)
    if existing is not None and existing.is_decided:
        messages.info(request, _("该评价的核验已有结论。"))
        return redirect("reviews:my_reviews")

    if request.method == "POST":
        form = Nnc1UploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                verification = services.submit_nnc1(
                    review=review,
                    uploader=author,
                    upload=form.cleaned_data["document"],
                    inspected=form.inspected,
                    declared_company_name=form.cleaned_data["declared_company_name"],
                    declared_company_no=form.cleaned_data["declared_company_no"],
                    declared_secretary_name=form.cleaned_data["declared_secretary_name"],
                )
            except services.ReviewError as exc:
                form.add_error(None, str(exc))
            else:
                # Scanning and matching happen in a worker; until the scan
                # finishes the file is unreadable, including to its uploader.
                transaction.on_commit(lambda: process_nnc1.delay(str(verification.pk)))
                messages.success(request, _("文件已上传，审核人员核验后会更新评价状态。"))
                return redirect("reviews:my_reviews")
    else:
        form = Nnc1UploadForm()

    return render(
        request,
        "reviews/nnc1_form.html",
        {"review": review, "provider": review.provider, "form": form, "verification": existing},
    )


@login_required
def nnc1_download(request: HttpRequest, verification_id: str) -> HttpResponseBase:
    """Hand out one NNC1, if it may be handed out at all.

    Same three gates as claim evidence, in the same order: the requester is the
    uploader or a moderator; the file has been scanned and not purged; and only
    then a short-lived signed URL or a streamed response. The uploader gets no
    exception on the scan - their own file is no safer to open than anyone
    else's, and it is their browser that would open it.
    """
    verification = (
        Nnc1Verification.objects.select_related("review").filter(pk=verification_id).first()
    )
    if verification is None:
        raise Http404("No such file")
    requester = cast("User", request.user)
    if verification.review.author_id != requester.pk and not is_moderator(requester):
        raise Http404("No such file")
    if not verification.is_readable:
        raise Http404("This file is not available")

    url = signed_url(verification.file.name or "")
    if url:
        return redirect(url)
    response = FileResponse(verification.file.open("rb"), content_type=verification.content_type)
    response["Content-Disposition"] = f'attachment; filename="nnc1.{verification.extension}"'
    # Personal data: no shared cache may keep a copy.
    response["Cache-Control"] = "private, no-store"
    return response
