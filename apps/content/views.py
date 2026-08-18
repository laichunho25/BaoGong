# Thin HTMX/HTML views. Never write to the ORM directly (CLAUDE.md section 3).
"""The education library, as a reader sees it.

Public and unauthenticated: these pages are how somebody who has never
registered a Hong Kong company finds out what the questions are, and most of
them arrive from a search engine rather than from the site (PRD section 3.6).

A draft is a 404 rather than a 403. Whether an unpublished page exists is not
information a visitor is owed, and a "not authorised" answer says it does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.agents import services as agent_services
from apps.content import selectors
from apps.content.forms import AskForm
from apps.content.models import ArticleCategory
from apps.content.rendering import render_markdown

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

#: Questions one account may ask in an hour. A reader working through a page
#: asks a handful; a script asks thousands, and each one is money.
ASKS_PER_HOUR = 12
ASK_WINDOW_SECONDS = 3600


def article_index(request: HttpRequest) -> HttpResponse:
    category = request.GET.get("category") or ""
    if category and category not in ArticleCategory.values:
        category = ""
    paginator = Paginator(selectors.articles_in_category(category or None), selectors.PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page", "1"))
    return render(
        request,
        "content/article_list.html",
        {
            "page_obj": page,
            "categories": ArticleCategory.choices,
            "active_category": category,
            "ask_form": AskForm(),
        },
    )


@require_POST
@login_required
def ask(request: HttpRequest) -> HttpResponse:
    """Answer a reader's question out of our own guides (AI_AGENTS A6).

    Behind a login, and rate limited per account. Every question is a paid call
    to a vendor, and an open box on a public page is somebody else's budget:
    the daily cap in ``BaseAgent`` protects the account, but it protects it by
    switching every agent off, which is not a way to find out that a page was
    being scraped.

    Nothing is written. The answer is rendered and forgotten; only the
    ``AgentRun`` remains, and its input is a hash (COMPLIANCE section 4).
    """
    form = AskForm(request.POST)
    if not form.is_valid():
        return render(request, "content/_advisor_answer.html", {"form": form}, status=422)

    if _over_the_limit(request):
        return render(
            request,
            "content/_advisor_answer.html",
            {"form": AskForm(), "throttled": True},
            status=429,
        )

    question = form.cleaned_data["question"]
    answer = agent_services.answer_question(question=question)
    return render(
        request,
        "content/_advisor_answer.html",
        {
            "form": AskForm(),
            "question": question,
            "answer": answer.data,
            "sources": answer.sources,
            "used_fallback": answer.used_fallback,
        },
    )


def _over_the_limit(request: HttpRequest) -> bool:
    """Count this account's questions this hour, and say whether it is enough.

    A cache counter rather than a table: this is a spending control, not a
    record of what somebody asked, and the version of it that survives a
    restart would be a log of readers' questions.
    """
    key = f"advisor:asks:{request.user.pk}:{timezone.now():%Y%m%d%H}"
    asked = cache.get_or_set(key, 0, ASK_WINDOW_SECONDS)
    cache.set(key, int(asked or 0) + 1, ASK_WINDOW_SECONDS)
    return int(asked or 0) >= ASKS_PER_HOUR


def article_detail(request: HttpRequest, slug: str) -> HttpResponse:
    article = selectors.article_by_slug(slug)
    if article is None:
        raise Http404
    return render(
        request,
        "content/article_detail.html",
        {
            "article": article,
            "body_html": render_markdown(article.body_md),
            "related": selectors.related_articles(article),
        },
    )
