# All writes and business logic. Wrap in @transaction.atomic (ARCHITECTURE section 3).
"""Writes to the education library.

Publishing is the only interesting operation here, and what makes it
interesting is that it has two audiences. A published article is a page a buyer
can read *and* the only material the Advisor agent is allowed to answer from
(AI_AGENTS A6), so the passages are rebuilt in the same transaction that
changes the body. An article whose chunks lag behind its body is an agent
quoting a sentence that is no longer on the page.

Unpublishing takes the chunks away with it, for the same reason in reverse: a
page a reader cannot open must not keep answering questions through the agent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from apps.content.models import Article, ArticleStatus, Chunk
from apps.content.rendering import split_into_chunks

if TYPE_CHECKING:
    from apps.accounts.models import User


class ArticleError(Exception):
    """An article operation that the library's own rules refuse."""


@transaction.atomic
def save_article(article: Article, *, author: User | None = None) -> Article:
    """Persist an article and bring its passages back in line with its body.

    The single entry point for writing an article, including from the internal
    console: the admin is one caller among several, and the rule that chunks
    follow the body must not depend on which one it was.
    """
    if author is not None and article.author_id is None:
        article.author = author
    article.full_clean(exclude=["author"], validate_unique=False)
    article.save()
    _rebuild_chunks(article)
    return article


@transaction.atomic
def publish_article(article: Article, *, actor: User | None = None) -> Article:
    """Put an article in front of readers, and in reach of the agent."""
    if not article.summary.strip() or not article.body_md.strip():
        raise ArticleError("An article needs a summary and a body before it can be published.")
    article.status = ArticleStatus.PUBLISHED
    if article.published_at is None:
        article.published_at = timezone.now()
    return save_article(article, author=actor)


@transaction.atomic
def unpublish_article(article: Article) -> Article:
    """Take an article back to draft, and its passages out of retrieval.

    ``published_at`` is kept: it is when this page was first put in front of
    readers, and a page that comes back should not claim to be new.
    """
    article.status = ArticleStatus.DRAFT
    article.save(update_fields=["status", "updated_at"])
    article.chunks.all().delete()
    return article


def _rebuild_chunks(article: Article) -> None:
    """Replace the article's passages with the ones its body now contains.

    Deleted and rewritten rather than updated in place. Ordinals are positions
    in a document that gets edited, so matching old rows to new ones would
    quietly re-point a citation at a different paragraph.
    """
    Chunk.objects.filter(article=article).delete()
    if not article.is_published:
        return
    Chunk.objects.bulk_create(
        Chunk(article=article, ordinal=ordinal, heading=heading, text=text)
        for ordinal, (heading, text) in enumerate(split_into_chunks(article.body_md), start=1)
    )
