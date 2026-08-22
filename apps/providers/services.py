"""Writes to the provider layer. Views never touch the ORM directly.

Three jobs live here:

1. Keeping a ``Provider`` in existence for every licensee, so the directory has
   a stable URL for each company before anyone claims it.
2. Recomputing the cached inputs the ranking reads, because RATING_SYSTEM
   section 5 mixes data from four apps and a list page cannot join across all
   of them per request.
3. The claim lifecycle: submit, attach evidence, scan, prove the website,
   approve or reject. Approval is the only path that grants a membership and
   the ``tcsp_licence`` badge, so every precondition is enforced here rather
   than in the admin - the admin is one caller among several.
"""

from __future__ import annotations

import hashlib
import logging
import math
import secrets
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import MemberRole, ProviderMember, Role
from apps.accounts.permissions import is_provider_member
from apps.core.compliance import Severity, check_banned_phrases
from apps.core.notifications import absolute_url, notify
from apps.core.scanning import READABLE_STATUSES, ScanStatus, scan_file, scanning_available
from apps.providers.models import (
    Certification,
    CertificationType,
    ClaimDecision,
    ClaimEvidence,
    ClaimStatus,
    EvidenceKind,
    LogoReviewStatus,
    ProfileEditStatus,
    Provider,
    ProviderClaim,
    ProviderLogoUpload,
    ProviderProfileEdit,
    ServiceOffering,
    Tier,
)
from apps.providers.verification import verify_website
from apps.registry.models import Licensee

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from django.core.files.uploadedfile import UploadedFile

    from apps.accounts.models import User
    from apps.core.uploads import InspectedUpload

logger = logging.getLogger(__name__)

# RATING_SYSTEM section 5. The weights are the product definition; the inputs
# they read are defined in this module.
WEIGHT_RATING = Decimal("0.45")
WEIGHT_REVIEW_VOLUME = Decimal("0.20")
WEIGHT_CERTIFICATION = Decimal("0.15")
WEIGHT_COMPLETENESS = Decimal("0.12")
WEIGHT_RESPONSIVENESS = Decimal("0.08")

# "log10(1+n) normalised, capped at n=50" (RATING_SYSTEM section 5).
REVIEW_VOLUME_CAP = 50

TIER_CERTIFICATION_LEVEL = {
    Tier.FREE: Decimal("0"),
    Tier.VERIFIED: Decimal("0.6"),
    Tier.PREMIUM: Decimal("1.0"),
}

# Fields a company can fill in about itself. Completeness is the share of them
# that carry information - a plain, explainable definition, because this number
# feeds a public ranking and has to be defensible when a provider asks why it
# ranks where it does.
COMPLETENESS_FIELDS = (
    "website",
    "founded_year",
    "team_size",
    "languages",
    "industry_specialties",
    "bank_types",
    "logo",
    "office_photos",
)
# ``description`` is deliberately absent, and must stay absent. Only paying
# tiers may write one (PRD section 3.7); counting it here would make the
# ranking rise with the subscription, and the home page promises the opposite -
# placement is for sale, the score is not.
#
# The same reasoning applies to the rest of this tuple, which is why the
# denominator is not ``len(COMPLETENESS_FIELDS)``: most of these fields are
# ones only a paying tier can fill in, so a fixed denominator would cap a free
# page's completeness at a fraction no amount of effort could raise, and a
# subscription would buy a place in the natural ranking. ``completeness_fields``
# narrows the set to what this company's own tier can reach, so the number
# answers "did they fill in what they can" rather than "what do they pay".
# ``office_photos`` has no self-service path at any tier and therefore drops out
# of every denominator until one exists.

#: Fields open to every tier, outside the profile form. A logo goes through
#: ``upload_logo``, and it is open to the free tier on purpose: it is how a
#: buyer recognises a company in a list, not a promotion slot.
ALL_TIER_FIELDS: Final[frozenset[str]] = frozenset({"logo"})

SLUG_MAX_LENGTH = 140


@dataclass(frozen=True, slots=True)
class BackfillReport:
    created: int
    skipped: int

    @property
    def total(self) -> int:
        return self.created + self.skipped


def build_slug(licensee: Licensee) -> str:
    """A stable, readable URL for a licensee.

    The licence number is appended rather than a counter: two companies do
    share a name, and a counter would depend on insertion order, so the same
    register could produce different URLs on two machines.
    """
    base = slugify(licensee.name_en) or "provider"
    suffix = slugify(licensee.licence_no)
    room = SLUG_MAX_LENGTH - len(suffix) - 1
    return f"{base[:room].rstrip('-')}-{suffix}"


def ensure_providers(*, licence_nos: Iterable[str] | None = None) -> BackfillReport:
    """Create an unclaimed ``Provider`` for every licensee that lacks one.

    Idempotent, and safe to run after every sync: a licensee that already has
    a provider is left completely alone, so this can never overwrite what a
    company has told us about itself.
    """
    pending = Licensee.objects.filter(provider__isnull=True)
    if licence_nos is not None:
        pending = pending.filter(licence_no__in=list(licence_nos))

    new_providers = [Provider(licensee=licensee, slug=build_slug(licensee)) for licensee in pending]
    with transaction.atomic():
        before = Provider.objects.count()
        # ignore_conflicts absorbs the slug collision two workers would hit if
        # the backfill somehow ran twice at once; the licence number in every
        # slug means a collision is always the same company, never two.
        # bulk_create returns the objects it was given either way, so the row
        # count is the only honest measure of what landed.
        Provider.objects.bulk_create(new_providers, ignore_conflicts=True)
        created = Provider.objects.count() - before

    return BackfillReport(created=created, skipped=len(new_providers) - created)


def completeness_fields(provider: Provider) -> tuple[str, ...]:
    """The fields ``provider``'s own tier lets it fill in.

    The denominator of ``compute_profile_completeness``. Read from the tier's
    editable set rather than listed again, so that moving a field between tiers
    moves it here too - the alternative is a second list that drifts, and the
    drift would be a paid ranking boost nobody wrote down.
    """
    reachable = tier_editable_fields(provider.effective_tier) | ALL_TIER_FIELDS
    return tuple(field for field in COMPLETENESS_FIELDS if field in reachable)


def compute_profile_completeness(provider: Provider) -> Decimal:
    """Share of the fields this company could fill in that carry information, 0-1.

    Deliberately counts only fields a provider controls, and only those its own
    tier can reach (COMPLIANCE section 6: a subscription may buy a placement,
    never a position in the natural ranking). Rating and review count are
    excluded from both halves: they are earned, not filled in, and they already
    carry their own weight.
    """
    fields = completeness_fields(provider)
    if not fields:
        # Only reachable if a tier is given no completeness field at all. Zero
        # is the honest answer then; dividing would raise.
        return Decimal("0")

    filled = 0
    for field in fields:
        value = getattr(provider, field)
        if isinstance(value, list):
            filled += 1 if value else 0
        elif value:
            filled += 1
    return (Decimal(filled) / Decimal(len(fields))).quantize(Decimal("0.001"))


def compute_review_volume_score(verified_review_count: int) -> Decimal:
    """log10(1+n) normalised against the cap, 0-1."""
    if verified_review_count <= 0:
        return Decimal("0")
    capped = min(verified_review_count, REVIEW_VOLUME_CAP)
    ratio = math.log10(1 + capped) / math.log10(1 + REVIEW_VOLUME_CAP)
    return Decimal(str(round(ratio, 3)))


def compute_ranking_score(provider: Provider) -> Decimal:
    """The default sort key for search results (RATING_SYSTEM section 5).

    A provider with no verified reviews contributes zero from both rating
    terms rather than a default score. RATING_SYSTEM section 4 refuses to show
    an unearned 5.00, and it would be incoherent to refuse to show it while
    still ranking on it.
    """
    if provider.has_verified_reviews and provider.rating_cached is not None:
        normalised_rating = provider.rating_cached / Decimal("5")
    else:
        normalised_rating = Decimal("0")

    score = (
        WEIGHT_RATING * normalised_rating
        + WEIGHT_REVIEW_VOLUME * compute_review_volume_score(provider.verified_review_count)
        + WEIGHT_CERTIFICATION * TIER_CERTIFICATION_LEVEL[Tier(provider.effective_tier)]
        + WEIGHT_COMPLETENESS * provider.profile_completeness
        + WEIGHT_RESPONSIVENESS * provider.responsiveness_score
    )
    return score.quantize(Decimal("0.0001"))


def recompute_ranking_inputs(*, provider_ids: list[str] | None = None) -> int:
    """Refresh ``profile_completeness`` and ``ranking_score``; return how many changed.

    ``responsiveness_score`` stays untouched: it is owned by the RFQ app (P5)
    and there is no data for it yet. Writing a placeholder would make an empty
    signal look like a measured one.
    """
    queryset = Provider.objects.all()
    if provider_ids is not None:
        queryset = queryset.filter(pk__in=provider_ids)

    now = timezone.now()
    changed = []
    for provider in queryset.iterator(chunk_size=500):
        completeness = compute_profile_completeness(provider)
        was = (provider.profile_completeness, provider.ranking_score)
        provider.profile_completeness = completeness
        provider.ranking_score = compute_ranking_score(provider)
        if (provider.profile_completeness, provider.ranking_score) != was:
            # bulk_update skips auto_now, so updated_at is set by hand.
            provider.updated_at = now
            changed.append(provider)

    Provider.objects.bulk_update(
        changed, ["profile_completeness", "ranking_score", "updated_at"], batch_size=500
    )
    return len(changed)


#: The tiers somebody is paying for. A licence leaving the register matters on
#: every page, but only these are being actively promoted by the platform.
PAID_TIERS: Final[frozenset[str]] = frozenset({Tier.VERIFIED, Tier.PREMIUM})


def suspend_paid_placement(provider: Provider) -> bool:
    """Stop promoting one page. Returns whether anything changed.

    Called by A7 when the licence behind a paying page stops appearing in the
    official register. Automating this direction is allowed because it is the
    conservative one: the platform stops advertising a company until a human
    has looked, and the page itself stays up carrying the deregistration notice
    (``registry.notices``) so a buyer who searches for the company still learns
    the truth. Nothing here is reversed automatically - restoring a placement is
    somebody's decision, and it needs the official register open in front of
    them.
    """
    if provider.paid_placement_suspended_at is not None:
        return False

    provider.paid_placement_suspended_at = timezone.now()
    provider.ranking_score = compute_ranking_score(provider)
    provider.save(update_fields=["paid_placement_suspended_at", "ranking_score", "updated_at"])
    logger.warning(
        "Paid placement suspended for provider %s: licence no longer on the register", provider.pk
    )
    return True


# ---------------------------------------------------------------- claims

TOKEN_BYTES = 24
SHA256_CHUNK = 64 * 1024


class ClaimError(Exception):
    """A claim cannot be created or decided as asked."""


def _new_site_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)[:64]


@transaction.atomic
def submit_claim(
    *,
    provider: Provider,
    user: User,
    contact_name: str,
    contact_phone: str = "",
    contact_role: str = "",
    business_registration_no: str = "",
    website: str = "",
    applicant_note: str = "",
) -> ProviderClaim:
    """Open an application for ``provider``.

    Three refusals, all of which would otherwise become a moderator's problem
    or a wrong badge:

    * an unverified email address - the decision has to be deliverable;
    * a page that is already claimed, or already has an open application;
    * a profile with no licence behind it. A claim grants the ``tcsp_licence``
      badge, and there is nothing to grant if the company is not on the
      register.
    """
    if not user.is_email_verified:
        raise ClaimError("The applicant's email address is not verified.")
    if provider.licensee_id is None:
        raise ClaimError("This profile is not backed by a licence and cannot be claimed.")
    if provider.claim_status == ClaimStatus.CLAIMED:
        raise ClaimError("This page has already been claimed.")
    if ProviderClaim.objects.filter(provider=provider, status=ClaimDecision.PENDING).exists():
        raise ClaimError("A claim for this page is already under review.")

    claim = ProviderClaim.objects.create(
        provider=provider,
        submitted_by=user,
        contact_name=contact_name,
        contact_phone=contact_phone,
        contact_role=contact_role,
        business_registration_no=business_registration_no,
        website=website or provider.website,
        applicant_note=applicant_note,
        website_verification_token=_new_site_token(),
    )
    # The page says "claim pending" from this moment: a visitor comparing
    # companies should see that somebody is answering for this one.
    Provider.objects.filter(pk=provider.pk, claim_status=ClaimStatus.UNCLAIMED).update(
        claim_status=ClaimStatus.PENDING, updated_at=timezone.now()
    )
    return claim


def _digest(upload: UploadedFile[Any]) -> str:
    digest = hashlib.sha256()
    upload.seek(0)
    for chunk in upload.chunks(SHA256_CHUNK):
        digest.update(chunk)
    upload.seek(0)
    return digest.hexdigest()


@transaction.atomic
def attach_evidence(
    *,
    claim: ProviderClaim,
    upload: UploadedFile[Any],
    inspected: InspectedUpload,
    kind: str = EvidenceKind.BUSINESS_REGISTRATION,
) -> ClaimEvidence:
    """Store one supporting document against ``claim``.

    ``inspected`` comes from ``core.uploads.inspect_upload``, which has already
    rejected anything that is not a PDF or an image by its actual leading
    bytes. The row starts at ``scan_pending``, which means unreadable: the file
    is not served and cannot carry the claim to approval until a scanner says
    otherwise.
    """
    if not claim.is_pending:
        raise ClaimError("Evidence cannot be added to a claim that has been decided.")

    evidence = ClaimEvidence(
        claim=claim,
        kind=kind,
        original_filename=(upload.name or "")[:255],
        content_type=inspected.content_type,
        extension=inspected.extension,
        size_bytes=inspected.size_bytes,
        sha256=_digest(upload),
        scan_status=ScanStatus.PENDING,
    )
    # upload_to reads instance.pk and instance.extension, both set above; the
    # uploader's filename is kept in a column, not in the storage key.
    evidence.file.save(f"evidence.{inspected.extension}", upload, save=False)
    evidence.save()
    return evidence


def scan_evidence(evidence: ClaimEvidence) -> ClaimEvidence:
    """Run the configured scanner over a stored file and record the verdict.

    Called from a Celery task. A file the scanner cannot clear - including
    because no scanner is configured - stays unreadable; nothing here can turn
    a failure into a pass.
    """
    if evidence.purged_at is not None or not evidence.file:
        return evidence

    result = scan_file(evidence.file)

    evidence.scan_status = result.status
    evidence.scan_detail = result.detail[:255]
    evidence.scanner = result.scanner[:32]
    evidence.scanned_at = timezone.now()
    evidence.save(
        update_fields=["scan_status", "scan_detail", "scanner", "scanned_at", "updated_at"]
    )
    return evidence


@transaction.atomic
def override_scan(*, evidence: ClaimEvidence, reviewer: User, reason: str) -> ClaimEvidence:
    """Record that a reviewer accepted a file the scanner never cleared.

    This exists so that the absence of a scanner does not silently block every
    claim forever, and it is deliberately an explicit, attributed action rather
    than a setting: the row afterwards says who accepted the risk and why.
    """
    if not reviewer.is_moderator:
        raise ClaimError("Only a moderator may override a scan result.")
    if not reason.strip():
        raise ClaimError("An override needs a reason.")
    if evidence.scan_status == ScanStatus.INFECTED:
        raise ClaimError("A file the scanner reported as infected may not be released.")

    evidence.scan_status = ScanStatus.SKIPPED
    evidence.scan_detail = f"Override: {reason.strip()}"[:255]
    evidence.scan_override_by = reviewer
    evidence.scanned_at = timezone.now()
    evidence.save(
        update_fields=["scan_status", "scan_detail", "scan_override_by", "scanned_at", "updated_at"]
    )
    return evidence


@transaction.atomic
def verify_claim_website(claim: ProviderClaim) -> ProviderClaim:
    """Look for the token on the claimed website and record what was found.

    Both outcomes are stored: a failed attempt is what the applicant needs in
    order to fix their DNS record, and the trail is what the moderator reads
    instead of taking "verified" on trust.
    """
    outcome = verify_website(claim.website, claim.website_verification_token)

    claim.website_verification_log = outcome.as_log()
    if outcome.verified:
        claim.website_verified_at = timezone.now()
        claim.website_verification_method = outcome.method
    claim.save(
        update_fields=[
            "website_verification_log",
            "website_verified_at",
            "website_verification_method",
            "updated_at",
        ]
    )
    return claim


def unreadable_evidence(claim: ProviderClaim) -> list[ClaimEvidence]:
    """Evidence a moderator has not been able to open, and so has not judged."""
    return [
        item
        for item in claim.evidence.all()
        if item.purged_at is None and item.scan_status not in READABLE_STATUSES
    ]


@transaction.atomic
def approve_claim(*, claim: ProviderClaim, reviewer: User, reason: str) -> ProviderClaim:
    """Grant control of the page, plus the licence badge.

    The preconditions are the whole point of putting this in a service:

    * only a moderator decides;
    * a reason is always required - an approval with no note is unreviewable
      six months later, when a rival asks why this company got the badge;
    * every piece of evidence must be readable. Approving while a file is
      still ``scan_pending`` means approving on evidence nobody has seen.

    A verified website is deliberately **not** required: controlling a domain
    does not prove the domain belongs to the licensee, and plenty of small
    licensees have no website at all. It is evidence for the reviewer, not a
    gate.
    """
    if not reviewer.is_moderator:
        raise ClaimError("Only a moderator may decide a claim.")
    if not claim.is_pending:
        raise ClaimError("This claim has already been decided.")
    if not reason.strip():
        raise ClaimError("A decision needs a reason.")
    if unreadable_evidence(claim):
        raise ClaimError(
            "Every uploaded file must be scanned (or explicitly released) before approval."
        )

    now = timezone.now()
    claim.status = ClaimDecision.APPROVED
    claim.reviewer = reviewer
    claim.reviewed_at = now
    claim.decision_reason = reason.strip()
    claim.save(update_fields=["status", "reviewer", "reviewed_at", "decision_reason", "updated_at"])

    provider = claim.provider
    provider.claim_status = ClaimStatus.CLAIMED
    provider.save(update_fields=["claim_status", "updated_at"])

    ProviderMember.objects.update_or_create(
        user=claim.submitted_by,
        provider=provider,
        defaults={"member_role": MemberRole.OWNER, "is_active": True, "claim": claim},
    )

    applicant = claim.submitted_by
    if applicant.role == Role.BUYER:
        applicant.role = Role.PROVIDER_MEMBER
        applicant.save(update_fields=["role", "updated_at"])

    # The badge states a fact from the official register - that this company
    # holds a TCSP licence - now that a human has connected the account to it.
    # evidence_ref points at the claim so the badge is always traceable to the
    # decision that granted it.
    Certification.objects.update_or_create(
        provider=provider,
        type=CertificationType.TCSP_LICENCE,
        defaults={
            "verified_at": now,
            "verified_by": reviewer,
            "evidence_ref": f"claim:{claim.pk}",
            # No expiry date: the register carries none, and inventing one
            # would put a platform-made date on an official fact. Licence
            # status is re-derived by the daily sync instead.
            "expires_at": None,
        },
    )

    # The claim fills in profile fields that feed the ranking.
    recompute_ranking_inputs(provider_ids=[str(provider.pk)])
    schedule_evidence_purge(claim)
    _announce_claim_decision(claim, approved=True)
    return claim


def _announce_claim_decision(claim: ProviderClaim, *, approved: bool) -> None:
    """Send the applicant the decision and the reason behind it.

    Only the applicant: a rejected claim proves nothing about who the person
    is, so telling the company's other members that someone tried to claim
    their page would leak a stranger's application to them.
    """
    notify(
        template="claim_decided",
        recipients=[claim.submitted_by.email],
        context={
            "provider_name": claim.provider.display_name,
            "approved": approved,
            "reason": claim.decision_reason,
            "url": absolute_url(claim.provider.get_absolute_url()),
        },
    )


@transaction.atomic
def reject_claim(*, claim: ProviderClaim, reviewer: User, reason: str) -> ProviderClaim:
    """Refuse an application, with a reason the applicant can be told."""
    if not reviewer.is_moderator:
        raise ClaimError("Only a moderator may decide a claim.")
    if not claim.is_pending:
        raise ClaimError("This claim has already been decided.")
    if not reason.strip():
        raise ClaimError("A decision needs a reason.")

    claim.status = ClaimDecision.REJECTED
    claim.reviewer = reviewer
    claim.reviewed_at = timezone.now()
    claim.decision_reason = reason.strip()
    claim.save(update_fields=["status", "reviewer", "reviewed_at", "decision_reason", "updated_at"])

    _reset_provider_claim_status(claim.provider, ClaimStatus.REJECTED)
    schedule_evidence_purge(claim)
    _announce_claim_decision(claim, approved=False)
    return claim


@transaction.atomic
def withdraw_claim(*, claim: ProviderClaim, user: User) -> ProviderClaim:
    """Let the applicant take their own application back."""
    if claim.submitted_by_id != user.pk:
        raise ClaimError("Only the applicant may withdraw a claim.")
    if not claim.is_pending:
        raise ClaimError("This claim has already been decided.")

    claim.status = ClaimDecision.WITHDRAWN
    claim.reviewed_at = timezone.now()
    claim.save(update_fields=["status", "reviewed_at", "updated_at"])

    _reset_provider_claim_status(claim.provider, ClaimStatus.UNCLAIMED)
    schedule_evidence_purge(claim)
    return claim


def _reset_provider_claim_status(provider: Provider, status: str) -> None:
    """Return a page to an unclaimed state after a claim ends without approval.

    Guarded on ``pending`` so that a late rejection of a stale claim cannot
    unclaim a page that a different, approved claim has since taken over.
    """
    Provider.objects.filter(pk=provider.pk, claim_status=ClaimStatus.PENDING).update(
        claim_status=status, updated_at=timezone.now()
    )


def schedule_evidence_purge(claim: ProviderClaim) -> int:
    """Start the retention clock on a decided claim's evidence.

    COMPLIANCE section 4: uploaded documents are personal data kept for a
    limited time. The clock starts at the decision, not at upload - deleting
    the evidence while the claim is still open would leave a decision nobody
    could audit.
    """
    purge_at = timezone.now() + timedelta(days=settings.CLAIM_EVIDENCE_RETENTION_DAYS)
    return ClaimEvidence.objects.filter(claim=claim, purged_at__isnull=True).update(
        purge_at=purge_at, updated_at=timezone.now()
    )


def purge_expired_evidence(*, now: datetime | None = None) -> int:
    """Delete the stored bytes of evidence whose retention window has passed.

    The row survives with its hash, size and scan verdict: what the platform
    must not keep is the document itself, while the record that a decision was
    made on it is exactly what an audit needs.
    """
    moment = timezone.now() if now is None else now
    purged = 0
    queryset = ClaimEvidence.objects.filter(
        purge_at__isnull=False, purge_at__lte=moment, purged_at__isnull=True
    )
    for evidence in queryset.iterator(chunk_size=200):
        if evidence.file:
            evidence.file.delete(save=False)
        evidence.purged_at = moment
        evidence.save(update_fields=["file", "purged_at", "updated_at"])
        purged += 1
    return purged


# --- Self-service profile editing (PRD section 3.7) -------------------------

# What each tier may change about itself. The free tier gets the fields a buyer
# needs in order to make contact at all; everything that shapes how the company
# is presented and compared comes with a subscription. Membership of these sets
# is the whole tier gate - there is no second list in a form or a template.
FREE_EDITABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {"contact_email", "contact_phone", "contact_wechat", "website", "service_categories"}
)
PAID_EDITABLE_FIELDS: Final[frozenset[str]] = FREE_EDITABLE_FIELDS | frozenset(
    {
        "description",
        "founded_year",
        "team_size",
        "languages",
        "supports_simplified",
        "remote_onboarding",
        "bank_account_support",
        "bank_types",
        "non_resident_shareholder_experience",
        "industry_specialties",
    }
)

# Fields a correction may touch: things that can be wrong in a way that costs
# the buyer rather than the company. A company cannot rewrite its own
# description under the heading "typo" and skip the queue.
CORRECTABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {"contact_email", "contact_phone", "contact_wechat"}
)

# How long the free tier waits between updates (PRD section 3.7).
FREE_EDIT_INTERVAL_DAYS: Final[int] = 365


class ProfileEditError(Exception):
    """A self-service edit that must not be applied. The message is shown."""


@dataclass(frozen=True, slots=True)
class EditPermission:
    """Whether this company may edit itself right now, and what it may touch."""

    allowed: bool
    fields: frozenset[str]
    next_allowed_at: datetime | None = None
    reason: str = ""


def tier_editable_fields(tier: str) -> frozenset[str]:
    """What ``tier`` may edit, ignoring whether the licence is still listed.

    Split out from ``editable_fields`` because the completeness denominator
    needs the tier's reach and not today's permission: a page that has just
    left the register should not have its ranking input redefined on the way
    out, it should simply stop being editable.
    """
    return FREE_EDITABLE_FIELDS if tier == Tier.FREE else PAID_EDITABLE_FIELDS


def editable_fields(provider: Provider) -> frozenset[str]:
    """The fields ``provider``'s tier permits it to set. Empty once delisted."""
    if not provider.is_on_register:
        return frozenset()
    return tier_editable_fields(provider.effective_tier)


def last_allowance_edit(provider: Provider) -> ProviderProfileEdit | None:
    """The most recent edit that started the free tier's twelve-month clock."""
    return (
        provider.profile_edits.filter(is_correction=False)
        .exclude(status=ProfileEditStatus.REJECTED)
        .order_by("-created_at")
        .first()
    )


def edit_permission(
    provider: Provider, *, is_correction: bool = False, now: datetime | None = None
) -> EditPermission:
    """Answer the page's one question: may this company change itself today?

    Three separate refusals, kept apart because they need different words on
    screen: the licence is gone, the page was never claimed, or the free tier's
    once-a-year allowance is already spent.
    """
    fields = editable_fields(provider)
    correction_fields = fields & CORRECTABLE_FIELDS

    if not provider.is_on_register:
        return EditPermission(
            allowed=False,
            fields=frozenset(),
            reason=str(
                _(
                    "该公司已不在官方持牌名单上，页面已锁定，无法编辑。"
                    "如属续期或资料有误，请先向公司注册处更新，我们每日同步。"
                )
            ),
        )
    if provider.claim_status != ClaimStatus.CLAIMED:
        return EditPermission(allowed=False, fields=frozenset(), reason=str(_("请先认领本页面。")))

    if is_correction:
        return EditPermission(allowed=True, fields=correction_fields)
    if provider.effective_tier != Tier.FREE:
        return EditPermission(allowed=True, fields=fields)

    previous = last_allowance_edit(provider)
    if previous is None:
        return EditPermission(allowed=True, fields=fields)

    now = now or timezone.now()
    next_allowed_at = previous.created_at + timedelta(days=FREE_EDIT_INTERVAL_DAYS)
    if now >= next_allowed_at:
        return EditPermission(allowed=True, fields=fields)
    return EditPermission(
        allowed=False,
        fields=correction_fields,
        next_allowed_at=next_allowed_at,
        reason=_(
            "通用模式每 12 个月可更新一次资料，下次可更新时间为 %(date)s。"
            "联络方式填错了可随时提交更正，不占用这个次数。"
        )
        % {"date": next_allowed_at.date().isoformat()},
    )


def screen_provider_text(text: str) -> None:
    """Refuse text that COMPLIANCE section 2 forbids a provider to publish.

    Blocking hits are refused outright rather than queued: a moderator should
    not have to be the one who notices a guaranteed-bank-account promise in the
    fifth paragraph, and telling the company at the keyboard is the only
    feedback that teaches.
    """
    blocking = [v for v in check_banned_phrases(text) if v.severity is Severity.BLOCKING]
    if not blocking:
        return
    quoted = "、".join(sorted({v.matched_text for v in blocking}))
    raise ProfileEditError(
        _("以下说法不能刊登（见平台规则）：%(phrases)s。请改写后再提交。") % {"phrases": quoted}
    )


@transaction.atomic
def apply_profile_edit(
    *,
    provider: Provider,
    actor: User,
    values: dict[str, Any],
    is_correction: bool = False,
) -> ProviderProfileEdit:
    """Apply a company's own changes and record what changed.

    Structured values are written straight through - it is the company's page
    and nothing here is presented as official. ``description`` is not: it goes
    into the returned row as ``submitted_description`` and reaches the page
    only through ``decide_profile_edit``.

    Returns the log row. Raises ``ProfileEditError`` when the tier, the licence
    or the annual allowance forbids the change; the caller shows the message.
    """
    permission = edit_permission(provider, is_correction=is_correction)
    if not permission.allowed:
        raise ProfileEditError(permission.reason)

    rejected = set(values) - permission.fields
    if rejected:
        raise ProfileEditError(
            _("当前方案不能修改：%(fields)s。") % {"fields": "、".join(sorted(rejected))}
        )

    locked = Provider.objects.select_for_update().get(pk=provider.pk)
    description = values.pop("description", None)
    categories = values.pop("service_categories", None)

    changes: dict[str, dict[str, Any]] = {}
    for field, new_value in values.items():
        old_value = getattr(locked, field)
        if old_value == new_value:
            continue
        changes[field] = {"from": _jsonable(old_value), "to": _jsonable(new_value)}
        setattr(locked, field, new_value)

    if categories is not None:
        category_change = _sync_offerings(locked, categories)
        if category_change is not None:
            changes["service_categories"] = category_change

    if description is not None:
        screen_provider_text(description)
        if description.strip() == locked.description.strip():
            description = None

    if not changes and description is None:
        raise ProfileEditError(_("没有任何改动。"))

    applied = [field for field in values if field in changes]
    if applied:
        locked.save(update_fields=[*applied, "updated_at"])
    if changes:
        recompute_ranking_inputs(provider_ids=[str(locked.pk)])

    edit = ProviderProfileEdit.objects.create(
        provider=locked,
        actor=actor,
        status=ProfileEditStatus.PENDING if description is not None else ProfileEditStatus.APPLIED,
        changes=changes,
        submitted_description=description or "",
        is_correction=is_correction,
    )
    logger.info(
        "provider profile edited",
        extra={
            "provider": str(locked.pk),
            "edit": str(edit.pk),
            "fields": edit.changed_fields,
            "pending_description": description is not None,
        },
    )
    return edit


def _jsonable(value: Any) -> Any:
    """JSON-safe copy of a field value for the diff column."""
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _sync_offerings(provider: Provider, categories: list[str]) -> dict[str, Any] | None:
    """Make the active offerings match ``categories``, returning the diff.

    Deactivates rather than deletes: an offering carries the prices the company
    published under it, and a category switched off by accident should come
    back with them intact rather than as an empty row.
    """
    existing = {o.category: o for o in provider.offerings.all()}
    before = sorted(c for c, o in existing.items() if o.is_active)
    wanted = sorted(set(categories))
    if before == wanted:
        return None

    for category in wanted:
        offering = existing.get(category)
        if offering is None:
            ServiceOffering.objects.create(provider=provider, category=category, is_active=True)
        elif not offering.is_active:
            offering.is_active = True
            offering.save(update_fields=["is_active", "updated_at"])
    for category, offering in existing.items():
        if category not in wanted and offering.is_active:
            offering.is_active = False
            offering.save(update_fields=["is_active", "updated_at"])
    return {"from": before, "to": wanted}


@transaction.atomic
def decide_profile_edit(
    *, edit: ProviderProfileEdit, reviewer: User, approve: bool, note: str
) -> ProviderProfileEdit:
    """The only writer of ``Provider.description``.

    A reason is required either way: an approval nobody can account for is
    indistinguishable from an accident, and a rejection without one gives the
    company nothing to fix.
    """
    if edit.status != ProfileEditStatus.PENDING:
        raise ProfileEditError(_("This edit has already been decided."))
    if not note.strip():
        raise ProfileEditError(_("A reason is required."))
    if reviewer.role not in {Role.MODERATOR, Role.ADMIN} and not reviewer.is_superuser:
        raise ProfileEditError(_("Only the moderation team may decide this."))

    if approve:
        screen_provider_text(edit.submitted_description)
        provider = Provider.objects.select_for_update().get(pk=edit.provider_id)
        provider.description = edit.submitted_description
        provider.save(update_fields=["description", "updated_at"])

    edit.status = ProfileEditStatus.APPROVED if approve else ProfileEditStatus.REJECTED
    edit.reviewed_by = reviewer
    edit.reviewed_at = timezone.now()
    edit.review_note = note.strip()
    edit.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note", "updated_at"])
    logger.info(
        "profile description decided",
        extra={"edit": str(edit.pk), "approved": approve, "reviewer": str(reviewer.pk)},
    )
    return edit


# --- Logo upload (PRD section 3.7, open to every tier) ----------------------


class LogoError(Exception):
    """A logo upload that must not proceed. The message is shown."""


@transaction.atomic
def upload_logo(
    *, provider: Provider, actor: User, upload: UploadedFile[Any], inspected: InspectedUpload
) -> ProviderLogoUpload:
    """Queue a logo for scanning and review. Never touches ``Provider.logo``.

    ``inspected`` comes from ``core.uploads.inspect_upload`` restricted to
    images, so a PDF renamed to .png has already been refused by its leading
    bytes. What is stored here is private and unpublished; the only path to the
    public bucket is ``decide_logo``.
    """
    if not scanning_available():
        # Refused here and not only in the template: a form that is missing from
        # the page is still reachable by anyone who kept the URL. Taking the
        # file would build a queue no moderator can clear, because an unscanned
        # logo can never be approved.
        raise LogoError(_("标志上传功能正在准备中，暂时无法提交。"))
    if not provider.is_on_register:
        raise LogoError(_("该公司已不在官方持牌名单上，页面已锁定，无法上传标志。"))
    if provider.claim_status != ClaimStatus.CLAIMED:
        raise LogoError(_("请先认领本页面。"))
    if provider.logo_uploads.filter(status=LogoReviewStatus.PENDING).exists():
        raise LogoError(_("已有一份标志在等待审核，请先撤回后再上传新的。"))

    logo = ProviderLogoUpload(
        provider=provider,
        uploaded_by=actor,
        original_filename=(upload.name or "")[:255],
        content_type=inspected.content_type,
        extension=inspected.extension,
        size_bytes=inspected.size_bytes,
        sha256=_digest(upload),
        scan_status=ScanStatus.PENDING,
    )
    logo.file.save(f"logo.{inspected.extension}", upload, save=False)
    logo.save()
    logger.info(
        "provider logo uploaded",
        extra={"provider": str(provider.pk), "logo": str(logo.pk)},
    )
    return logo


def scan_provider_logo(logo: ProviderLogoUpload) -> ProviderLogoUpload:
    """Record the scanner's verdict on a stored logo. Called from a task."""
    if not logo.file:
        return logo

    result = scan_file(logo.file)
    logo.scan_status = result.status
    logo.scan_detail = result.detail[:255]
    logo.scanner = result.scanner[:32]
    logo.scanned_at = timezone.now()
    logo.save(update_fields=["scan_status", "scan_detail", "scanner", "scanned_at", "updated_at"])
    return logo


@transaction.atomic
def withdraw_logo(*, logo: ProviderLogoUpload, user: User) -> ProviderLogoUpload:
    """Let the company take back an upload that is still waiting.

    The bytes go with it. A withdrawn image is one the company decided not to
    publish, and keeping a private copy of it serves nobody.
    """
    if not logo.is_pending:
        raise LogoError(_("这份标志已经处理过了。"))
    if not is_provider_member(user, str(logo.provider_id)):
        raise LogoError(_("只有该公司的成员可以撤回。"))

    if logo.file:
        logo.file.delete(save=False)
    logo.status = LogoReviewStatus.WITHDRAWN
    logo.save(update_fields=["file", "status", "updated_at"])
    return logo


@transaction.atomic
def public_logo_name(provider: Provider, extension: str) -> str:
    """The filename a published logo is served under.

    The name is part of the page, not an implementation detail: image search
    reads the filename, and this is the only file on the site that an ordinary
    visitor's browser ever fetches by name. ``abc-secretaries-tc000123-logo.png``
    says what the picture is; ``0193f2c8-....png`` says nothing, to a reader or
    to a crawler.

    Built from the slug, so the URL matches the company's own page URL and
    changes only when that does. Nothing else may name this file - a second
    caller with its own convention would mean two spellings of the same logo
    in the bucket.
    """
    return f"{provider.slug}-logo.{extension}"


def decide_logo(
    *, logo: ProviderLogoUpload, reviewer: User, approve: bool, note: str
) -> ProviderLogoUpload:
    """The only writer of ``Provider.logo``.

    An approval copies the bytes out of the private bucket into the public one
    and only then deletes the private copy - in that order, so a failure part
    way through leaves the file that has not been published yet, rather than no
    file at all. A refusal deletes it outright.

    A logo the scanner never cleared cannot be published. There is no override
    here on purpose: unlike claim evidence, which one moderator opens once and
    which blocks a whole application while it waits, a logo is served to every
    visitor of the page, and no reason to accept that risk is good enough.
    """
    if not logo.is_pending:
        raise LogoError(_("This logo has already been decided."))
    if not note.strip():
        raise LogoError(_("A reason is required."))
    if reviewer.role not in {Role.MODERATOR, Role.ADMIN} and not reviewer.is_superuser:
        raise LogoError(_("Only the moderation team may decide this."))
    if approve and not logo.is_readable:
        raise LogoError(_("This file has not been cleared by the scanner."))

    now = timezone.now()
    if approve:
        provider = Provider.objects.select_for_update().get(pk=logo.provider_id)
        with logo.file.open("rb") as handle:
            payload = handle.read()
        provider.logo.save(
            public_logo_name(provider, logo.extension), ContentFile(payload), save=False
        )
        provider.save(update_fields=["logo", "updated_at"])
        logo.published_at = now
        recompute_ranking_inputs(provider_ids=[str(provider.pk)])

    if logo.file:
        logo.file.delete(save=False)
    logo.status = LogoReviewStatus.APPROVED if approve else LogoReviewStatus.REJECTED
    logo.reviewed_by = reviewer
    logo.reviewed_at = now
    logo.review_note = note.strip()
    logo.save(
        update_fields=[
            "file",
            "status",
            "reviewed_by",
            "reviewed_at",
            "review_note",
            "published_at",
            "updated_at",
        ]
    )
    _announce_logo_decision(logo, approved=approve)
    logger.info(
        "provider logo decided",
        extra={"logo": str(logo.pk), "approved": approve, "reviewer": str(reviewer.pk)},
    )
    return logo


def _announce_logo_decision(logo: ProviderLogoUpload, *, approved: bool) -> None:
    """Tell the company what happened, in the reviewer's own words."""
    recipients = [
        email
        for email in ProviderMember.objects.filter(
            provider_id=logo.provider_id, is_active=True
        ).values_list("user__email", flat=True)
        if email
    ]
    notify(
        template="logo_decided",
        recipients=recipients,
        context={
            "provider_name": logo.provider.display_name,
            "approved": approved,
            "reason": logo.review_note,
            "url": absolute_url(reverse("providers:manage", kwargs={"slug": logo.provider.slug})),
        },
    )
