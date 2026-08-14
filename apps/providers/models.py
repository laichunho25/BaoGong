"""Platform-side enrichment of the official register.

CLAUDE.md rule 1 splits the world in two: ``apps.registry`` mirrors the
official file and nothing may edit it, while everything the platform learns,
is told, or verifies lives here and points at a ``Licensee`` by foreign key.

A ``Provider`` exists for every licensee, created unclaimed by the backfill in
``services.py``, so that the directory has a stable URL for each company from
day one - long before anybody claims it.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel
from apps.core.money import Money
from apps.registry.models import Licensee


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

    def __str__(self) -> str:
        return f"{self.provider.slug} {self.type}"

    @property
    def is_current(self) -> bool:
        return self.expires_at is None or self.expires_at > timezone.now()
