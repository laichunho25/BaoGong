"""Reviews, their sub-scores, and the company's right of reply.

Fields, constraints, ``__str__`` and properties only - the scoring itself is in
``services.py`` (ARCHITECTURE section 3).

Two things here are load-bearing and easy to undo by accident:

* ``is_verified`` is set only by NNC1 verification (P4-2), never by the author
  and never by a moderator's approval of the text. RATING_SYSTEM section 2 gives
  an unverified review weight 0, so this flag is what a public score is made of.
* a review starts in ``pending_moderation`` and nothing renders it until it is
  ``published``. The default is deliberately the closed one: a defamation claim
  is answered by "it was never public", not by how quickly it came down.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel
from apps.providers.models import ServiceCategory

#: RATING_SYSTEM section 3. Ordered, because the radar chart and the form both
#: show them in this sequence and the order is part of the product definition.
SCORE_FIELDS = (
    "price_transparency",
    "responsiveness",
    "bank_support",
    "professionalism",
    "after_sales",
)

SCORE_MIN = Decimal("1")
SCORE_MAX = Decimal("5")
#: Scores move in halves; anything else is a UI bug, so the DB rejects it.
SCORE_STEP = Decimal("0.5")


class ReviewStatus(models.TextChoices):
    PENDING_MODERATION = "pending_moderation", _("Pending moderation")
    PUBLISHED = "published", _("Published")
    HIDDEN = "hidden", _("Hidden")
    REMOVED = "removed", _("Removed")


class Review(BaseModel):
    """One buyer's account of working with one company."""

    provider = models.ForeignKey(
        "providers.Provider", on_delete=models.CASCADE, related_name="reviews"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews"
    )

    body = models.TextField()
    service_used = ArrayField(
        models.CharField(max_length=32, choices=ServiceCategory.choices),
        default=list,
        blank=True,
    )
    engagement_year = models.PositiveSmallIntegerField(null=True, blank=True)

    # Derived from ReviewScore by services.score_overall; stored because the
    # list page sorts and filters on it and cannot recompute per row.
    overall = models.DecimalField(max_digits=2, decimal_places=1)

    is_verified = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Set only by NNC1 verification. Unverified reviews carry weight 0.",
    )
    status = models.CharField(
        max_length=24,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING_MODERATION,
        db_index=True,
    )
    published_at = models.DateTimeField(null=True, blank=True)

    # AI advice, never fact (CLAUDE.md rule 3): {labels, severity, reasons,
    # model, run_id}. A moderator's decision is what changes ``status``.
    moderation = models.JSONField(default=dict, blank=True)
    moderation_note = models.TextField(
        blank=True, help_text="Why a moderator published, hid or removed this. Internal."
    )
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    moderated_at = models.DateTimeField(null=True, blank=True)

    helpful_count = models.PositiveIntegerField(default=0)

    class Meta(BaseModel.Meta):
        verbose_name = _("review")
        verbose_name_plural = _("reviews")
        constraints = [
            # RATING_SYSTEM section 6: one account, one company, one review.
            # Without it a single motivated account can move a company's score
            # as far as it likes.
            models.UniqueConstraint(
                fields=["provider", "author"], name="reviews_one_review_per_author_per_provider"
            ),
            models.CheckConstraint(
                condition=models.Q(overall__gte=SCORE_MIN, overall__lte=SCORE_MAX),
                name="reviews_overall_within_range",
            ),
        ]
        indexes = [
            # The detail page's list: this company, published, newest first.
            models.Index(fields=["provider", "status", "-published_at"]),
            # The moderation queue.
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.provider_id} {self.overall} ({self.status})"

    @property
    def is_public(self) -> bool:
        return self.status == ReviewStatus.PUBLISHED

    @property
    def counts_towards_rating(self) -> bool:
        """RATING_SYSTEM section 2 v1: verified and published, or weight 0."""
        return self.is_public and self.is_verified


class ReviewScore(BaseModel):
    """The five sub-scores behind one review.

    ``bank_support`` is nullable because "I did not use the bank account
    service" is a real answer, and scoring it anyway would punish companies for
    something the reviewer never bought (RATING_SYSTEM section 3).
    """

    review = models.OneToOneField(Review, on_delete=models.CASCADE, related_name="score")
    price_transparency = models.DecimalField(max_digits=2, decimal_places=1)
    responsiveness = models.DecimalField(max_digits=2, decimal_places=1)
    bank_support = models.DecimalField(max_digits=2, decimal_places=1, null=True, blank=True)
    professionalism = models.DecimalField(max_digits=2, decimal_places=1)
    after_sales = models.DecimalField(max_digits=2, decimal_places=1)

    class Meta(BaseModel.Meta):
        verbose_name = _("review score")
        verbose_name_plural = _("review scores")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    price_transparency__gte=SCORE_MIN, price_transparency__lte=SCORE_MAX
                )
                & models.Q(responsiveness__gte=SCORE_MIN, responsiveness__lte=SCORE_MAX)
                & models.Q(professionalism__gte=SCORE_MIN, professionalism__lte=SCORE_MAX)
                & models.Q(after_sales__gte=SCORE_MIN, after_sales__lte=SCORE_MAX)
                & (
                    models.Q(bank_support__isnull=True)
                    | models.Q(bank_support__gte=SCORE_MIN, bank_support__lte=SCORE_MAX)
                ),
                name="reviews_scores_within_range",
            ),
        ]

    def __str__(self) -> str:
        return f"scores for {self.review_id}"

    def as_dict(self) -> dict[str, Decimal | None]:
        """The five dimensions, in the product's order, for the radar chart."""
        return {field: getattr(self, field) for field in SCORE_FIELDS}


class ReviewReply(BaseModel):
    """The company's answer. One per review (COMPLIANCE section 3).

    ``provider`` is stored beside ``review`` rather than reached through it:
    the reply belongs to the company, and a member who has since left must not
    take the company's published statement with them when their account goes.
    """

    review = models.OneToOneField(Review, on_delete=models.CASCADE, related_name="reply")
    provider = models.ForeignKey(
        "providers.Provider", on_delete=models.CASCADE, related_name="review_replies"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    body = models.TextField()
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = _("review reply")
        verbose_name_plural = _("review replies")

    def __str__(self) -> str:
        return f"reply to {self.review_id}"

    @property
    def is_public(self) -> bool:
        return self.published_at is not None
