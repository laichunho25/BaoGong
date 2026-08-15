"""Review pages. Reads through selectors, writes through services.

The provider detail page renders the list; these views own the three actions -
write one, reply to one, and read your own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.accounts.permissions import verified_email_required
from apps.core import turnstile
from apps.providers import selectors as provider_selectors
from apps.registry.selectors import registry_last_synced_at
from apps.reviews import selectors, services
from apps.reviews.forms import ReplyForm, ReviewForm
from apps.reviews.models import SCORE_FIELDS

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

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
