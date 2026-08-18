# Read queries only. Never writes (ARCHITECTURE section 3).
"""Reads from the education library.

Every function here starts from :func:`published_articles`. A draft is a page
that has not been checked yet, and this platform's whole claim is that what it
publishes has been checked - so "published" is a filter the callers cannot
forget rather than one they have to remember.

Search is substring matching, not Postgres full-text search. The corpus is
Simplified Chinese and this database has no Chinese tokeniser (``zhparser`` is
not installed anywhere the project deploys), so ``to_tsvector`` would treat a
whole sentence as one token and match almost nothing. A few dozen articles are
small enough that a scan is honest and fast; when the library outgrows that,
the fix is a tokeniser, not a bigger regular expression.

For the same reason a question is not matched whole: :func:`query_terms` cuts
it into overlapping bigrams before :func:`search_chunks` looks for them, and
ranks the passages in Python. That function is the seam where vector search
lands once embeddings are populated - see ``Chunk.embedding``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from django.db.models import Q

from apps.content.models import Article, ArticleStatus, Chunk

if TYPE_CHECKING:
    from django.db.models import QuerySet

#: How many articles a list page shows before paginating.
PAGE_SIZE = 12


def published_articles() -> QuerySet[Article]:
    return Article.objects.filter(status=ArticleStatus.PUBLISHED, published_at__isnull=False)


def articles_in_category(category: str | None) -> QuerySet[Article]:
    queryset = published_articles()
    return queryset.filter(category=category) if category else queryset


def article_by_slug(slug: str) -> Article | None:
    return published_articles().filter(slug=slug).select_related("author").first()


def latest_articles(limit: int = 3) -> QuerySet[Article]:
    """The newest few, for the home page and for the foot of an article."""
    return published_articles()[:limit]


def related_articles(article: Article, limit: int = 3) -> QuerySet[Article]:
    """Other pages in the same category. Same question, different angle."""
    return published_articles().filter(category=article.category).exclude(pk=article.pk)[:limit]


def search_articles(query: str, limit: int = 10) -> QuerySet[Article]:
    term = query.strip()
    if not term:
        return published_articles().none()
    return published_articles().filter(
        Q(title_zh_hans__icontains=term)
        | Q(title_zh_hant__icontains=term)
        | Q(title_en__icontains=term)
        | Q(summary__icontains=term)
        | Q(body_md__icontains=term)
    )[:limit]


#: Characters that carry no topic on their own. A bigram made only of these is
#: grammar, and matching on grammar retrieves the whole corpus.
_STOP_CHARS = set("的了吗呢吧是在有和与我你他她它们要会能就都很也还对把被给这那个之不没")

#: Whole words that are how a question is asked rather than what it is about.
_STOP_TERMS = frozenset(
    {
        "什么",
        "怎么",
        "怎樣",
        "怎样",
        "可以",
        "需要",
        "如果",
        "请问",
        "請問",
        "知道",
        "一下",
        "哪些",
        "多少",
        "为什么",
        "為什麼",
    }
)

_CJK_RUN = re.compile(r"[一-鿿]+")
_ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]{1,}")

#: How many terms of a question are used. A long question is mostly context;
#: past a dozen terms the extra ones only pull in passages about nothing.
MAX_QUERY_TERMS = 12
#: How many rows are scored in Python before the top ones are returned.
_CANDIDATE_POOL = 80


def query_terms(query: str) -> list[str]:
    """Break a question into things worth matching on.

    Chinese has no spaces and this database has no tokeniser, so a Chinese run
    becomes its overlapping bigrams: 「商业登记费」 gives 商业/业登/登记/记费, and a
    passage about 商业登记费 matches four of them while a passage about 记账 matches
    none. It is crude, it is also why a whole-sentence ``icontains`` - which
    matches nothing at all - is not what runs here.
    """
    terms: list[str] = []
    for word in _ASCII_WORD.findall(query):
        terms.append(word.lower())
    for run in _CJK_RUN.findall(query):
        if len(run) == 1:
            continue
        for start in range(len(run) - 1):
            bigram = run[start : start + 2]
            if bigram in _STOP_TERMS or set(bigram) <= _STOP_CHARS:
                continue
            terms.append(bigram)
    for term in _STOP_TERMS:
        while term in terms:
            terms.remove(term)
    return list(dict.fromkeys(terms))[:MAX_QUERY_TERMS]


def search_chunks(query: str, limit: int = 8) -> list[Chunk]:
    """Passages a question touches, for the Advisor agent to cite.

    Only chunks of published articles: the agent may not quote a page a reader
    cannot open (AI_AGENTS A6).

    Ranked by how many of the question's terms a passage contains, then by
    article recency. This is the seam where the vector search goes once an
    embedding provider is configured - the signature stays, so A6 does not
    change when the ranking does.
    """
    terms = query_terms(query)
    if not terms:
        return []
    matches = Q()
    for term in terms:
        matches |= Q(text__icontains=term)
    candidates = list(
        Chunk.objects.filter(
            matches,
            article__status=ArticleStatus.PUBLISHED,
            article__published_at__isnull=False,
        )
        .select_related("article")
        .order_by("-article__published_at", "ordinal")[:_CANDIDATE_POOL]
    )
    scored = sorted(
        candidates,
        key=lambda chunk: -sum(1 for term in terms if term in chunk.text.lower()),
    )
    return scored[:limit]
