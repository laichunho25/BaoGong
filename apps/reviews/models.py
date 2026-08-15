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

``Nnc1Verification`` at the bottom is the thing that sets ``is_verified``, and
it does not set it by itself either - see its docstring.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel
from apps.core.scanning import READABLE_STATUSES, ScanStatus
from apps.core.storage import private_storage
from apps.providers.models import ServiceCategory
from apps.reviews.matching import MatchMethod

if TYPE_CHECKING:
    from datetime import datetime

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


class DisputeGround(models.TextChoices):
    """Why the company says the review should not stand.

    Asked as a closed list because the grounds are not equally serious and the
    queue has to be sortable: "this person was never our client" is checkable
    against the register and the NNC1, while "we disagree with their opinion" is
    not a ground at all. ``OTHER`` exists so the list does not silently push a
    real complaint into the nearest wrong box.
    """

    NOT_A_CUSTOMER = "not_a_customer", _("The reviewer was never our client")
    FACTUALLY_WRONG = "factually_wrong", _("Contains a factual error")
    PERSONAL_DATA = "personal_data", _("Discloses personal data")
    DEFAMATORY = "defamatory", _("Defamatory or abusive")
    COMPETITOR = "competitor", _("Posted by a competitor")
    OTHER = "other", _("Other")


class DisputeDecision(models.TextChoices):
    KEEP = "keep", _("Keep the review as it is")
    AMEND = "amend", _("Hidden pending amendment by the author")
    HIDE = "hide", _("Hide the review")
    REMOVE = "remove", _("Remove the review")


class Dispute(BaseModel):
    """A company's appeal against a published review (COMPLIANCE section 3).

    The right of reply answers a review in public; this answers it in private,
    to a moderator, when the company's case is that the review should not be
    there at all. Both exist because a platform that publishes accusations
    about named businesses and offers them no route of appeal is not neutral.

    Two properties are the whole design:

    * **raising one changes nothing.** The review stays exactly as public as it
      was until a moderator decides. If filing a dispute hid the review, the
      button would be a one-click takedown of any inconvenient review, and the
      company with the most staff would have the cleanest page.
    * **it has a clock.** ``due_at`` is five working days out, per COMPLIANCE
      section 3. An appeal route with no deadline is a way of saying no slowly,
      so the queue is sorted by it and lateness is visible on the row.

    ``provider`` is stored beside ``review`` for the same reason it is on
    ``ReviewReply``: the dispute is the company's, and the member who filed it
    may have left by the time it is decided (``raised_by`` is SET_NULL).
    """

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="disputes")
    provider = models.ForeignKey(
        "providers.Provider", on_delete=models.CASCADE, related_name="disputes"
    )
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    ground = models.CharField(max_length=24, choices=DisputeGround.choices)
    reason = models.TextField(help_text="The company's case, in its own words.")
    # Structured references the company offers - an engagement number, a date,
    # a case reference. Not files: a document uploaded here would need the same
    # scanning, private storage and retention clock as an NNC1, and that is a
    # feature rather than a field (see ROADMAP).
    evidence = models.JSONField(default=dict, blank=True)

    # CLAUDE.md rule 3: a draft for the moderator to accept, edit or discard.
    # Nothing writes it yet - see docs/AI_AGENTS.md on why arbitration is not
    # the third agent to ship.
    ai_arbitration_draft = models.TextField(blank=True)

    # Empty means open. A separate status field would let "decided" and "has a
    # decision" disagree, and there is no third state.
    decision = models.CharField(
        max_length=16, choices=DisputeDecision.choices, blank=True, default="", db_index=True
    )
    decision_note = models.TextField(
        blank=True, help_text="Why the moderator decided this. Shown to the company."
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(db_index=True)

    class Meta(BaseModel.Meta):
        verbose_name = _("dispute")
        verbose_name_plural = _("disputes")
        constraints = [
            # One open dispute per review. Re-filing while the first is pending
            # is not more evidence, it is a second row for the same complaint,
            # and a company could otherwise flood the queue for one review.
            models.UniqueConstraint(
                fields=["review"],
                condition=models.Q(decision=""),
                name="reviews_one_open_dispute_per_review",
            ),
        ]
        indexes = [
            # The queue: open, most overdue first.
            models.Index(fields=["decision", "due_at"]),
        ]

    def __str__(self) -> str:
        return f"dispute on {self.review_id} ({self.decision or 'open'})"

    @property
    def is_open(self) -> bool:
        return not self.decision

    def is_overdue(self, *, now: datetime | None = None) -> bool:
        """Past its COMPLIANCE section 3 deadline and still undecided."""
        if not self.is_open:
            return False
        return (now or timezone.now()) > self.due_at


def nnc1_path(instance: Nnc1Verification, filename: str) -> str:
    """Storage key: opaque, per review, with the sniffed extension only.

    Same shape as ``providers.claim_evidence_path`` and for the same reason -
    the uploader's filename is attacker-controlled text that usually carries a
    person's name, and storage keys end up in logs.
    """
    return f"nnc1/{instance.review_id}/{instance.pk}.{instance.extension}"


class VerificationResult(models.TextChoices):
    NEEDS_HUMAN = "needs_human", _("Needs a reviewer")
    PASSED = "passed", _("Verified")
    FAILED = "failed", _("Not verified")


class Nnc1Verification(BaseModel):
    """The document behind a "已验证" badge, and what was decided about it.

    An NNC1 (Incorporation Form) names the company's first secretary. If it
    names the company under review, the reviewer was a client - which is the
    single fact the badge asserts and the reason this platform is worth more
    than an anonymous review site.

    Three properties are load-bearing:

    * **it never verifies itself.** ``result`` starts at ``needs_human`` and
      only ``services.decide_verification`` moves it, with a named reviewer and
      a written reason. The rule-based name match in ``matching.py`` is put in
      front of that person as evidence; see the module docstring there for why
      a match cannot be a gate.
    * **the file is unreadable until scanned**, exactly like claim evidence: a
      moderator's browser must not be what discovers a malicious PDF.
    * **the bytes have a deadline.** COMPLIANCE section 4 gives uploaded
      personal data 90 days from the decision. The row and its hash survive the
      purge, because the record that a decision was made is what an audit
      needs and the document is what the platform must not keep.

    CLAUDE.md rule 5 is why the declared fields below are so few: an NNC1 also
    carries directors' names, residential addresses and identity-document
    numbers, and none of that is needed to answer "was this person a client".
    Only the fields that answer that question are transcribed into columns.
    """

    review = models.OneToOneField(Review, on_delete=models.CASCADE, related_name="verification")

    file = models.FileField(storage=private_storage, upload_to=nnc1_path)
    original_filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=64)
    extension = models.CharField(max_length=8)
    size_bytes = models.PositiveIntegerField(default=0)
    sha256 = models.CharField(max_length=64, db_index=True)

    scan_status = models.CharField(
        max_length=16, choices=ScanStatus.choices, default=ScanStatus.PENDING, db_index=True
    )
    scan_detail = models.CharField(max_length=255, blank=True)
    scanner = models.CharField(max_length=32, blank=True)
    scanned_at = models.DateTimeField(null=True, blank=True)

    # What the uploader says the document says. Kept apart from ``extracted``
    # so that "the person typed this" and "a model read this" can never be
    # confused for one another.
    declared_company_name = models.CharField(max_length=255, blank=True)
    declared_company_no = models.CharField(max_length=32, blank=True)
    declared_secretary_name = models.CharField(max_length=255, blank=True)

    # A3's structured output (P4-3), advice and not fact: CLAUDE.md rule 3.
    extracted = models.JSONField(default=dict, blank=True)
    extraction_confidence = models.DecimalField(
        max_digits=3, decimal_places=2, null=True, blank=True
    )
    agent_run_id_ref = models.UUIDField(
        null=True,
        blank=True,
        help_text="AgentRun that produced `extracted`. A plain UUID until P4-3 creates the table.",
    )

    # The rule-based comparison against the official register.
    match_method = models.CharField(
        max_length=16, choices=MatchMethod.choices, default=MatchMethod.NONE
    )
    match_score = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    matched_licence_no = models.CharField(max_length=32, blank=True)
    match_detail = models.CharField(max_length=255, blank=True)

    result = models.CharField(
        max_length=16,
        choices=VerificationResult.choices,
        default=VerificationResult.NEEDS_HUMAN,
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True, help_text="Why the reviewer passed or failed it.")

    # COMPLIANCE section 4: set when the decision is made, not on upload - the
    # clock starts once the document has done its job.
    purge_at = models.DateTimeField(null=True, blank=True, db_index=True)
    purged_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = _("NNC1 verification")
        verbose_name_plural = _("NNC1 verifications")
        indexes = [
            # The verification queue: undecided, oldest first.
            models.Index(fields=["result", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"NNC1 for {self.review_id} ({self.result})"

    @property
    def is_readable(self) -> bool:
        """Whether these bytes may be opened or served.

        Pending counts as unscanned. The uploader does not get an exception:
        their own file is no safer to open than anyone else's.
        """
        return bool(self.file) and self.purged_at is None and self.scan_status in READABLE_STATUSES

    @property
    def is_decided(self) -> bool:
        return self.result != VerificationResult.NEEDS_HUMAN
