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
import math
import secrets
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.models import MemberRole, ProviderMember, Role
from apps.core.scanning import READABLE_STATUSES, ScanStatus, get_scanner
from apps.providers.models import (
    Certification,
    CertificationType,
    ClaimDecision,
    ClaimEvidence,
    ClaimStatus,
    EvidenceKind,
    Provider,
    ProviderClaim,
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


def compute_profile_completeness(provider: Provider) -> Decimal:
    """Share of the self-declared fields that carry information, 0-1.

    Deliberately counts only fields a provider controls. Rating and review
    count are excluded: they are earned, not filled in, and they already carry
    their own weight in the ranking.
    """
    filled = 0
    for field in COMPLETENESS_FIELDS:
        value = getattr(provider, field)
        if isinstance(value, list):
            filled += 1 if value else 0
        elif value:
            filled += 1
    return (Decimal(filled) / Decimal(len(COMPLETENESS_FIELDS))).quantize(Decimal("0.001"))


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
        + WEIGHT_CERTIFICATION * TIER_CERTIFICATION_LEVEL[Tier(provider.tier)]
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

    scanner = get_scanner()
    with evidence.file.open("rb") as handle:
        result = scanner.scan(iter(lambda: handle.read(SHA256_CHUNK), b""))

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
    return claim


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
