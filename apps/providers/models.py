"""Platform-side enrichment of the official register.

CLAUDE.md rule 1 splits the world in two: ``apps.registry`` mirrors the
official file and nothing may edit it, while everything the platform learns,
is told, or verifies lives here and points at a ``Licensee`` by foreign key.

A ``Provider`` exists for every licensee, created unclaimed by the backfill in
``services.py``, so that the directory has a stable URL for each company from
day one - long before anybody claims it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel
from apps.core.money import Money
from apps.core.scanning import READABLE_STATUSES, ScanStatus
from apps.core.storage import private_storage
from apps.registry.models import Licensee

if TYPE_CHECKING:
    from django_stubs_ext import StrOrPromise


class ClaimStatus(models.TextChoices):
    UNCLAIMED = "unclaimed", _("Unclaimed")
    PENDING = "pending", _("Claim pending")
    CLAIMED = "claimed", _("Claimed")
    REJECTED = "rejected", _("Claim rejected")


class Tier(models.TextChoices):
    FREE = "free", _("Free")
    VERIFIED = "verified", _("Verified")
    PREMIUM = "premium", _("Premium")


class Language(models.TextChoices):
    MANDARIN = "mandarin", _("Mandarin")
    CANTONESE = "cantonese", _("Cantonese")
    ENGLISH = "english", _("English")


class BankType(models.TextChoices):
    TRADITIONAL = "traditional", _("Traditional bank")
    VIRTUAL = "virtual", _("Virtual bank")
    EMI = "emi", _("EMI / payment institution")


class Provider(BaseModel):
    """A company as the platform presents it, backed by an official licence.

    Every field here is platform-side: self-declared by the company once it
    claims the page, or filled in by staff. None of it may be presented as
    official (COMPLIANCE section 1), which is why the templates keep official
    and platform data in visibly separate blocks.
    """

    licensee = models.OneToOneField(
        Licensee,
        on_delete=models.PROTECT,
        related_name="provider",
        null=True,
        blank=True,
        help_text="Null only for a candidate that is not on the register; never published.",
    )
    slug = models.SlugField(max_length=140, unique=True)
    claim_status = models.CharField(
        max_length=16, choices=ClaimStatus.choices, default=ClaimStatus.UNCLAIMED, db_index=True
    )
    tier = models.CharField(max_length=16, choices=Tier.choices, default=Tier.FREE, db_index=True)

    # FileField, not ImageField: ImageField needs Pillow purely to validate
    # dimensions, which nothing here uses. Upload validation (MIME, size,
    # virus scan) is specified for P3 and belongs there, not in a field type.
    logo = models.FileField(upload_to="providers/logos/", blank=True)
    website = models.URLField(blank=True)

    # Public contact details, supplied by the company itself. Publishing them
    # lets a buyer go straight to the company without an RFQ, which is the
    # point: a comparison site that hides the phone number is a lead broker
    # (COMPLIANCE section 6 draws exactly that line). WeChat is listed because
    # the buyers this platform is for do not use email to start a conversation.
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=32, blank=True)
    contact_wechat = models.CharField(max_length=64, blank=True)

    # The company's own words. Only ever written by
    # ``services.decide_profile_edit`` after a human has read them: free text
    # published under our layout is the platform lending its voice, and
    # ``check_banned_phrases`` alone cannot tell whether a sentence is true.
    description = models.TextField(
        blank=True, help_text="Published self-introduction. Set by moderation, never by a form."
    )
    founded_year = models.PositiveSmallIntegerField(null=True, blank=True)
    team_size = models.PositiveIntegerField(null=True, blank=True)
    office_photos = models.JSONField(default=list, blank=True)

    languages = ArrayField(
        models.CharField(max_length=16, choices=Language.choices), default=list, blank=True
    )
    supports_simplified = models.BooleanField(default=False)
    remote_onboarding = models.BooleanField(default=False)
    bank_account_support = models.BooleanField(default=False)
    bank_types = ArrayField(
        models.CharField(max_length=16, choices=BankType.choices), default=list, blank=True
    )
    non_resident_shareholder_experience = models.BooleanField(default=False)
    industry_specialties = ArrayField(models.CharField(max_length=64), default=list, blank=True)

    # Denormalised by reviews.services.recompute_provider_rating (P4). Null
    # rather than zero: "no verified reviews yet" is not a score of nothing,
    # and RATING_SYSTEM section 4 forbids showing a number in that case.
    rating_cached = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)
    rating_count = models.PositiveIntegerField(
        default=0, help_text="Published reviews, verified or not - what the review tab lists."
    )
    verified_review_count = models.PositiveIntegerField(
        default=0, help_text="NNC1-verified reviews only - the number shown beside the score."
    )
    # Cached ranking inputs, recomputed by services.recompute_ranking_inputs.
    profile_completeness = models.DecimalField(
        max_digits=4, decimal_places=3, default=0, help_text="0-1, see services.py."
    )
    responsiveness_score = models.DecimalField(
        max_digits=4, decimal_places=3, default=0, help_text="0-1, from RFQ reply times (P5)."
    )
    # Denormalised because RATING_SYSTEM section 5 mixes inputs from four apps
    # and the list page has to sort and paginate in the database.
    ranking_score = models.DecimalField(
        max_digits=6, decimal_places=4, default=0, db_index=True, help_text="See services.py."
    )

    # Set by apps.agents.services.summarise_registry_diff when the licence
    # behind a paying page leaves the official register (AI_AGENTS A7). Not a
    # tier change: the subscription still exists and billing still owns it -
    # this only stops the platform from promoting a page whose licence it can
    # no longer see. Cleared by hand once somebody has checked the register.
    paid_placement_suspended_at = models.DateTimeField(null=True, blank=True)

    is_published = models.BooleanField(default=True, db_index=True)
    commission_agreement = models.BooleanField(
        default=False, help_text="If true the page MUST render the disclosure (COMPLIANCE 6)."
    )

    class Meta:
        ordering = ["slug"]
        indexes = [
            GinIndex(fields=["industry_specialties"], name="providers_specialties_gin"),
            models.Index(fields=["is_published", "tier"]),
        ]

    def __str__(self) -> str:
        return self.display_name

    def get_absolute_url(self) -> str:
        return reverse("providers:detail", kwargs={"slug": self.slug})

    @property
    def display_name(self) -> str:
        """Official name, always. A claimed page may not rename the company."""
        if self.licensee is None:
            return self.slug
        return self.licensee.name_en

    @property
    def has_verified_reviews(self) -> bool:
        """RATING_SYSTEM section 4: gate for showing a score at all."""
        return self.verified_review_count > 0

    @property
    def effective_tier(self) -> str:
        """The tier the platform will actually act on.

        A suspended page keeps its paid tier on the row and ranks as if it were
        free, so that restoring it is one field and not a billing question.
        """
        return Tier.FREE if self.paid_placement_suspended_at else self.tier

    @property
    def is_on_register(self) -> bool:
        return self.licensee is not None and self.licensee.is_on_register

    @property
    def language_labels(self) -> list[str]:
        """Human labels for the ArrayField. ``get_FOO_display`` does not apply
        to an ArrayField of choices, so the mapping is done here rather than
        with an if-chain in the template."""
        labels = dict(Language.choices)
        return [str(labels[value]) for value in self.languages if value in labels]

    @property
    def bank_type_labels(self) -> list[str]:
        labels = dict(BankType.choices)
        return [str(labels[value]) for value in self.bank_types if value in labels]


class ClaimDecision(models.TextChoices):
    PENDING = "pending", _("Pending review")
    APPROVED = "approved", _("Approved")
    REJECTED = "rejected", _("Rejected")
    WITHDRAWN = "withdrawn", _("Withdrawn by the applicant")


class ProviderClaim(BaseModel):
    """A company's application to take control of its own page.

    Approving one binds a real person to a licensed company and grants the
    ``tcsp_licence`` badge, so the row keeps the whole evidence trail: what was
    submitted, what the official register says, whether the website was proved,
    who decided, and why. ``ai_risk_flags`` is advisory only - CLAUDE.md rule 3
    forbids an agent's output from becoming a fact on its own.
    """

    provider = models.ForeignKey(Provider, on_delete=models.CASCADE, related_name="claims")
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="provider_claims"
    )

    # Applicant-declared, never presented as official: the register carries no
    # BR number, so this cannot be checked against it automatically.
    business_registration_no = models.CharField(max_length=32, blank=True)
    contact_name = models.CharField(max_length=120)
    contact_phone = models.CharField(max_length=32, blank=True)
    contact_role = models.CharField(max_length=120, blank=True)
    applicant_note = models.TextField(blank=True)

    website = models.URLField(
        blank=True, help_text="The site the token is published on; may differ from the profile."
    )
    website_verification_token = models.CharField(max_length=64, db_index=True)
    website_verified_at = models.DateTimeField(null=True, blank=True)
    website_verification_method = models.CharField(max_length=16, blank=True)
    website_verification_log = models.JSONField(default=list, blank=True)

    status = models.CharField(
        max_length=16, choices=ClaimDecision.choices, default=ClaimDecision.PENDING, db_index=True
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_claims",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    # Required by services on any decision: a rejection the applicant cannot be
    # told the reason for is not a reviewable decision.
    decision_reason = models.TextField(blank=True)
    notes = models.TextField(blank=True, help_text="Internal; never shown to the applicant.")
    ai_risk_flags = models.JSONField(
        default=dict, blank=True, help_text="Advisory only (CLAUDE.md rule 3). Never decides."
    )

    class Meta(BaseModel.Meta):
        verbose_name = _("provider claim")
        verbose_name_plural = _("provider claims")
        constraints = [
            # One open application per company. Without it, a page could be
            # claimed twice in parallel and the second approval would silently
            # hand a second company control of the same profile.
            models.UniqueConstraint(
                fields=["provider"],
                condition=models.Q(status="pending"),
                name="providers_one_pending_claim_per_provider",
            ),
        ]
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self) -> str:
        return f"{self.provider.slug} <- {self.submitted_by_id} ({self.status})"

    @property
    def is_pending(self) -> bool:
        return self.status == ClaimDecision.PENDING

    @property
    def is_website_verified(self) -> bool:
        return self.website_verified_at is not None

    @property
    def expected_token_value(self) -> str:
        """What the applicant publishes in DNS or in the page's meta tag."""
        return f"{settings.CLAIM_SITE_VERIFICATION_KEY}={self.website_verification_token}"


class EvidenceKind(models.TextChoices):
    BUSINESS_REGISTRATION = "business_registration", _("Business registration certificate")
    ADDRESS_PROOF = "address_proof", _("Proof of business address")
    AUTHORISATION = "authorisation", _("Letter of authorisation")
    OTHER = "other", _("Other supporting document")


def claim_evidence_path(instance: ClaimEvidence, filename: str) -> str:
    """Storage key: opaque, per claim, with the sniffed extension only.

    The uploader's filename is kept in a column instead of in the path - it is
    attacker-controlled text and often carries a person's name, which does not
    belong in a storage key that appears in logs.
    """
    return f"claims/{instance.claim_id}/{instance.pk}.{instance.extension}"


class ClaimEvidence(BaseModel):
    """One uploaded document supporting a claim.

    A row per file rather than a JSON list on the claim: each file carries its
    own scan state and its own retention clock, and both have to be queryable -
    the purge task and the "may this be opened?" check are per file.
    """

    claim = models.ForeignKey(ProviderClaim, on_delete=models.CASCADE, related_name="evidence")
    kind = models.CharField(
        max_length=32, choices=EvidenceKind.choices, default=EvidenceKind.BUSINESS_REGISTRATION
    )
    file = models.FileField(storage=private_storage, upload_to=claim_evidence_path)
    original_filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=64)
    extension = models.CharField(max_length=8)
    size_bytes = models.PositiveIntegerField(default=0)
    # Lets a moderator recognise the same document submitted twice without
    # opening either, and proves the stored bytes were not altered afterwards.
    sha256 = models.CharField(max_length=64, db_index=True)

    scan_status = models.CharField(
        max_length=16, choices=ScanStatus.choices, default=ScanStatus.PENDING, db_index=True
    )
    scan_detail = models.CharField(max_length=255, blank=True)
    scanner = models.CharField(max_length=32, blank=True)
    scanned_at = models.DateTimeField(null=True, blank=True)
    scan_override_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Set only when a reviewer accepted a file the scanner did not clear.",
    )

    # COMPLIANCE section 4: uploaded personal data has a retention limit.
    purge_at = models.DateTimeField(null=True, blank=True, db_index=True)
    purged_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = _("claim evidence")
        verbose_name_plural = _("claim evidence")

    def __str__(self) -> str:
        return f"{self.kind} ({self.scan_status})"

    @property
    def is_readable(self) -> bool:
        """Whether these bytes may be opened or served.

        Pending is treated exactly like unscanned: a moderator's browser must
        not be the thing that finds out a file was malicious.
        """
        return bool(self.file) and self.purged_at is None and self.scan_status in READABLE_STATUSES


class ServiceCategory(models.TextChoices):
    INCORPORATION = "incorporation", _("Company incorporation")
    COMPANY_SECRETARY = "company_secretary", _("Company secretary")
    REGISTERED_ADDRESS = "registered_address", _("Registered address")
    ACCOUNTING = "accounting", _("Accounting")
    AUDIT_LIAISON = "audit_liaison", _("Audit liaison")
    BANK_ACCOUNT_ASSIST = "bank_account_assist", _("Bank account assistance")
    TAX_FILING = "tax_filing", _("Tax filing")
    TRADEMARK = "trademark", _("Trademark")
    WORK_VISA = "work_visa", _("Work visa")


class ServiceOffering(BaseModel):
    provider = models.ForeignKey(Provider, on_delete=models.CASCADE, related_name="offerings")
    category = models.CharField(max_length=32, choices=ServiceCategory.choices, db_index=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "category"], name="providers_one_offering_per_category"
            )
        ]

    def __str__(self) -> str:
        return f"{self.provider.slug} {self.category}"


class PriceUnit(models.TextChoices):
    ONE_OFF = "one_off", _("One-off")
    YEARLY = "yearly", _("Per year")
    MONTHLY = "monthly", _("Per month")
    HOURLY = "hourly", _("Per hour")


class PriceSource(models.TextChoices):
    PROVIDER_DECLARED = "provider_declared", _("Declared by the provider")
    QUOTE_DERIVED = "quote_derived", _("Derived from quotes")
    PLATFORM_SURVEY = "platform_survey", _("Platform survey")


class PriceItem(BaseModel):
    """A published price or price range.

    CLAUDE.md rule 6: money is a currency plus an integer amount in the
    currency's minor unit. No float ever touches a price.
    """

    offering = models.ForeignKey(ServiceOffering, on_delete=models.CASCADE, related_name="prices")
    label = models.CharField(max_length=120)
    currency = models.CharField(max_length=3, default="HKD")
    amount_minor = models.BigIntegerField(null=True, blank=True)
    min_amount_minor = models.BigIntegerField(null=True, blank=True)
    max_amount_minor = models.BigIntegerField(null=True, blank=True)
    unit = models.CharField(max_length=16, choices=PriceUnit.choices, default=PriceUnit.ONE_OFF)
    includes_govt_fee = models.BooleanField(default=False)
    effective_from = models.DateField(null=True, blank=True)
    source = models.CharField(
        max_length=24, choices=PriceSource.choices, default=PriceSource.PROVIDER_DECLARED
    )

    class Meta:
        ordering = ["label"]
        constraints = [
            # A price is either a point or a range; neither being set means the
            # row says nothing and would render as a blank cell in the compare
            # table, which reads as "free".
            models.CheckConstraint(
                condition=models.Q(amount_minor__isnull=False)
                | models.Q(min_amount_minor__isnull=False, max_amount_minor__isnull=False),
                name="providers_price_point_or_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.label} {self.currency}"

    @property
    def display(self) -> str:
        """Formatted price for the page. Empty only if the constraint is bypassed.

        Formatting goes through Money so that the minor-unit exponent comes
        from one table; a template filter doing ``amount_minor / 100`` would be
        wrong for JPY and would reintroduce a float into the money path.
        """
        if self.amount_minor is not None:
            return Money(self.amount_minor, self.currency).format()
        if self.min_amount_minor is not None and self.max_amount_minor is not None:
            low = Money(self.min_amount_minor, self.currency).format()
            high = Money(self.max_amount_minor, self.currency).format()
            return f"{low} - {high.removeprefix(f'{self.currency} ')}"
        return ""


class CertificationType(models.TextChoices):
    TCSP_LICENCE = "tcsp_licence", _("TCSP licence")
    OFFICE_VERIFIED = "office_verified", _("Office verified")
    WEBSITE_VERIFIED = "website_verified", _("Website verified")
    TRACK_RECORD = "track_record", _("Track record")
    PREMIUM_BADGE = "premium_badge", _("Premium badge")


class Certification(BaseModel):
    provider = models.ForeignKey(Provider, on_delete=models.CASCADE, related_name="certifications")
    type = models.CharField(max_length=24, choices=CertificationType.choices)
    verified_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    evidence_ref = models.CharField(max_length=512, blank=True)
    # AUTH_USER_MODEL rather than auth.User: P3 swaps in a custom user, and a
    # hard reference here would have to be migrated at the worst moment.
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["type"]
        constraints = [
            # One badge of each kind per company. Two "tcsp_licence" rows would
            # render the badge twice and leave no answer to "which one is
            # current"; renewals update the row instead.
            models.UniqueConstraint(
                fields=["provider", "type"], name="providers_one_certification_per_type"
            )
        ]

    def __str__(self) -> str:
        return f"{self.provider.slug} {self.type}"

    @property
    def is_current(self) -> bool:
        return self.expires_at is None or self.expires_at > timezone.now()


class ProfileEditStatus(models.TextChoices):
    APPLIED = "applied", _("Applied immediately")
    PENDING = "pending", _("Awaiting review")
    APPROVED = "approved", _("Approved")
    REJECTED = "rejected", _("Rejected")


# What a company sees in its own change log. Keyed by the name the diff stores,
# so a field renamed in the model shows up here as a missing label rather than
# as a wrong one.
PROFILE_FIELD_LABELS: dict[str, StrOrPromise] = {
    "contact_email": _("联系邮箱"),
    "contact_phone": _("联系电话"),
    "contact_wechat": _("微信号"),
    "website": _("公司网站"),
    "service_categories": _("业务范畴"),
    "founded_year": _("成立年份"),
    "team_size": _("团队人数"),
    "languages": _("服务语言"),
    "supports_simplified": _("简体中文服务"),
    "remote_onboarding": _("远程办理"),
    "bank_account_support": _("协助开户"),
    "bank_types": _("合作银行类型"),
    "non_resident_shareholder_experience": _("非本地股东经验"),
    "industry_specialties": _("行业专长"),
}


class ProviderProfileEdit(BaseModel):
    """One self-service change to a company page, and what became of it.

    Append-only. Every edit a company makes lands here before or as it lands on
    ``Provider``, which gives three things nothing else did: an answer to "who
    changed the price on this page and when", the clock the free tier's
    once-a-year allowance is measured from, and a queue for the one field that
    cannot go live unread.

    Structured fields (contact details, languages, services, prices) apply at
    once - it is the company's own page and a wrong phone number hurts nobody
    but itself. ``description`` is free text and does not: published under our
    layout it reads as something the platform stands behind, so it waits in
    ``submitted_description`` until a moderator decides.

    ``is_correction`` marks a fix rather than an update. Corrections do not
    consume the annual allowance, because a company that mistyped its own phone
    number must not be told to live with it for a year - the person that hurts
    is the buyer who dials it.
    """

    provider = models.ForeignKey(Provider, on_delete=models.CASCADE, related_name="profile_edits")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Null once the account is deleted; the row and its diff remain.",
    )
    status = models.CharField(
        max_length=16, choices=ProfileEditStatus.choices, default=ProfileEditStatus.APPLIED
    )
    # {field: {"from": <old>, "to": <new>}} for everything already applied.
    changes = models.JSONField(default=dict, blank=True)
    submitted_description = models.TextField(blank=True)
    is_correction = models.BooleanField(default=False)

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = _("profile edit")
        verbose_name_plural = _("profile edits")
        indexes = [
            models.Index(fields=["provider", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.provider_id} {self.status} {self.created_at:%Y-%m-%d}"

    @property
    def changed_fields(self) -> list[str]:
        return sorted(self.changes)

    @property
    def changed_field_labels(self) -> list[str]:
        """The same list in the words the company used when it typed them."""
        return [str(PROFILE_FIELD_LABELS.get(name, name)) for name in self.changed_fields]

    @property
    def counts_towards_allowance(self) -> bool:
        """Whether this edit starts the free tier's twelve-month clock."""
        return not self.is_correction and self.status != ProfileEditStatus.REJECTED
