# Fields, constraints, __str__, properties only. No business logic (ARCHITECTURE section 3).
"""The education library.

Two models, and the second one exists for a machine. An ``Article`` is what a
buyer reads; a ``Chunk`` is one passage of it, kept as its own row so the
Advisor agent can cite a passage rather than a whole page (AI_AGENTS A6). The
agent may only answer out of this table, so a chunk that does not exist is an
answer the platform will not give.

``embedding`` is nullable on purpose: no embedding provider is configured yet,
and retrieval falls back to text search until one is. A null embedding must
therefore never mean "skip this passage" - it means "found some other way".
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from pgvector.django import VectorField

from apps.core.models import BaseModel

#: Voyage/OpenAI-class embedding width. Fixed here rather than in settings: the
#: column type carries it, so changing it is a migration and a re-embed, not a
#: configuration change somebody can make by accident.
EMBEDDING_DIMENSIONS = 1536


class ArticleCategory(models.TextChoices):
    INCORPORATION = "incorporation", _("Setting up a company")
    BANKING = "banking", _("Opening a bank account")
    LICENSING = "licensing", _("Licences and how to check them")
    FEES = "fees", _("Fees and hidden costs")
    COMPLIANCE = "compliance", _("Staying compliant")


class ArticleStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    PUBLISHED = "published", _("Published")


class Article(BaseModel):
    """One education page.

    Titles are per language and the body is not. Translating a title is cheap
    and makes the page findable; translating a body is a job for a person, and
    a half-translated body would be worse than one honest language.
    """

    slug = models.SlugField(max_length=80, unique=True)
    category = models.CharField(max_length=32, choices=ArticleCategory.choices, db_index=True)
    title_zh_hans = models.CharField(max_length=160)
    title_zh_hant = models.CharField(max_length=160, blank=True)
    title_en = models.CharField(max_length=160, blank=True)
    summary = models.TextField(max_length=400)
    body_md = models.TextField()
    status = models.CharField(
        max_length=16, choices=ArticleStatus.choices, default=ArticleStatus.DRAFT, db_index=True
    )
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="articles",
    )
    seo = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]
        verbose_name = _("article")
        verbose_name_plural = _("articles")
        constraints = [
            models.CheckConstraint(
                # A published page with no date cannot be sorted, cited or
                # submitted to a sitemap, so the database refuses to hold one.
                condition=models.Q(status="draft") | models.Q(published_at__isnull=False),
                name="content_published_article_has_a_date",
            )
        ]

    def __str__(self) -> str:
        return self.title_zh_hans

    @property
    def title(self) -> str:
        """The title in the reader's language, falling back to Simplified.

        Falling back rather than showing an empty heading: the audience reads
        Simplified Chinese, and an untranslated title is a page that still
        answers the question.
        """
        code = (get_language() or "").lower()
        if code.startswith("zh-hant") and self.title_zh_hant:
            return self.title_zh_hant
        if code.startswith("en") and self.title_en:
            return self.title_en
        return self.title_zh_hans

    @property
    def is_published(self) -> bool:
        return self.status == ArticleStatus.PUBLISHED

    @property
    def meta_title(self) -> str:
        value = self.seo.get("meta_title") if isinstance(self.seo, dict) else None
        return value or self.title

    @property
    def meta_description(self) -> str:
        value = self.seo.get("meta_description") if isinstance(self.seo, dict) else None
        return value or self.summary

    def get_absolute_url(self) -> str:
        return reverse("content:detail", args=[self.slug])


class Chunk(BaseModel):
    """One citable passage of an article.

    Rebuilt from the body whenever the body changes rather than edited: a
    passage that no longer appears in the article is a citation to something
    the reader cannot find.
    """

    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="chunks")
    ordinal = models.PositiveIntegerField()
    heading = models.CharField(max_length=200, blank=True)
    text = models.TextField()
    embedding = VectorField(dimensions=EMBEDDING_DIMENSIONS, null=True, blank=True)

    class Meta:
        ordering = ["article", "ordinal"]
        verbose_name = _("article chunk")
        verbose_name_plural = _("article chunks")
        constraints = [
            models.UniqueConstraint(
                fields=["article", "ordinal"], name="content_one_chunk_per_ordinal"
            )
        ]

    def __str__(self) -> str:
        return f"{self.article.slug}#{self.ordinal}"
