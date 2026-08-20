"""Read queries for the public directory.

Every queryset here starts from ``directory_queryset`` so that the joins the
list page needs are declared once. A missing ``select_related`` on this page
is 20 extra queries per screen, not one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote

from django.db.models import (
    Case,
    Count,
    Exists,
    F,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    QuerySet,
    When,
)
from django.utils.translation import gettext_lazy as _

from apps.providers.models import (
    BankType,
    ClaimDecision,
    ClaimStatus,
    Language,
    LogoReviewStatus,
    PriceItem,
    ProfileEditStatus,
    Provider,
    ProviderClaim,
    ProviderLogoUpload,
    ProviderProfileEdit,
    ServiceCategory,
    ServiceOffering,
    Tier,
)
from apps.registry.models import LicenceStatus

if TYPE_CHECKING:
    from collections.abc import Sequence

    from django.http import QueryDict

MAX_COMPARE = 3


class SortOption:
    RECOMMENDED = "recommended"
    RATING = "rating"
    NAME = "name"
    NEWEST = "newest"

    CHOICES = (RECOMMENDED, RATING, NAME, NEWEST)
    DEFAULT = RECOMMENDED


def directory_queryset() -> QuerySet[Provider]:
    """Published providers with the licensee row every card and page reads.

    ``select_related`` on the licensee is not an optimisation here but a
    correctness requirement: the card shows the official name, district and
    licence status, and COMPLIANCE section 1 requires the source and sync time
    alongside them.
    """
    return Provider.objects.filter(is_published=True).select_related("licensee")


@dataclass(frozen=True, slots=True)
class DirectoryFilters:
    """The filter state of one list-page request, parsed from the query string."""

    query: str = ""
    district: str = ""
    language: str = ""
    service: str = ""
    bank_support: bool = False
    remote_onboarding: bool = False
    tier: str = ""
    bank_type: str = ""
    include_deregistered: bool = False
    sort: str = SortOption.DEFAULT

    @classmethod
    def from_request(cls, params: QueryDict) -> DirectoryFilters:
        """Build from ``request.GET``. Unknown values fall back to the default.

        A bad filter value returns the unfiltered page rather than an error:
        these URLs get shared and edited by hand, and a 400 helps nobody.
        """
        language = params.get("language", "")
        service = params.get("service", "")
        tier = params.get("tier", "")
        bank_type = params.get("bank_type", "")
        sort = params.get("sort", "")
        return cls(
            query=params.get("q", "").strip(),
            district=params.get("district", "").strip(),
            language=language if language in Language.values else "",
            service=service if service in ServiceCategory.values else "",
            bank_support=params.get("bank_support") == "1",
            remote_onboarding=params.get("remote_onboarding") == "1",
            tier=tier if tier in Tier.values else "",
            bank_type=bank_type if bank_type in BankType.values else "",
            include_deregistered=params.get("include_deregistered") == "1",
            sort=sort if sort in SortOption.CHOICES else SortOption.DEFAULT,
        )

    @property
    def is_active(self) -> bool:
        """Whether anything is narrowing the list, for the 'clear filters' UI."""
        return any(
            [
                self.query,
                self.district,
                self.language,
                self.service,
                self.bank_support,
                self.remote_onboarding,
                self.tier,
                self.bank_type,
                self.include_deregistered,
            ]
        )


def _apply_sort(queryset: QuerySet[Provider], sort: str) -> QuerySet[Provider]:
    # Providers still on the register always sort ahead of those removed from
    # it, whatever the chosen sort - a deregistered company is findable, but it
    # is never the first thing a buyer sees.
    on_register_first = Case(
        When(licensee__status=LicenceStatus.ACTIVE, then=0), default=1, output_field=IntegerField()
    )
    if sort == SortOption.NAME:
        return queryset.order_by(on_register_first, "licensee__name_en")
    if sort == SortOption.RATING:
        # nulls_last: a provider with no verified reviews has no score, and
        # must not outrank one that earned a low score.
        return queryset.order_by(
            on_register_first, F("rating_cached").desc(nulls_last=True), "licensee__name_en"
        )
    if sort == SortOption.NEWEST:
        return queryset.order_by(on_register_first, "-licensee__first_seen_at")
    return queryset.order_by(on_register_first, "-ranking_score", "licensee__name_en")


def filter_directory(filters: DirectoryFilters) -> QuerySet[Provider]:
    """Apply one request's filters. Read-only; no side effects."""
    queryset = directory_queryset()

    if not filters.include_deregistered:
        queryset = queryset.filter(licensee__status=LicenceStatus.ACTIVE)

    if filters.query:
        queryset = queryset.filter(
            Q(licensee__licence_no__iexact=filters.query)
            | Q(licensee__name_en__icontains=filters.query)
            | Q(licensee__name_zh__icontains=filters.query)
        )
    if filters.district:
        queryset = queryset.filter(licensee__district=filters.district)
    if filters.language:
        queryset = queryset.filter(languages__contains=[filters.language])
    if filters.service:
        # ``distinct`` because the join can match a provider once per offering
        # row; without it a company would appear twice for one filter.
        queryset = queryset.filter(
            offerings__category=filters.service, offerings__is_active=True
        ).distinct()
    if filters.bank_support:
        queryset = queryset.filter(bank_account_support=True)
    if filters.remote_onboarding:
        queryset = queryset.filter(remote_onboarding=True)
    if filters.tier:
        # A suspended paid page is not in its paid tier for anything a buyer
        # sees; it stays findable, just not under the badge it is not showing.
        queryset = queryset.filter(tier=filters.tier)
        if filters.tier != Tier.FREE:
            queryset = queryset.filter(paid_placement_suspended_at__isnull=True)
    if filters.bank_type:
        queryset = queryset.filter(bank_types__contains=[filters.bank_type])

    return _apply_sort(queryset, filters.sort)


def available_districts() -> list[str]:
    """Districts that actually have a published provider, for the filter list.

    Blank districts are excluded: the parser leaves them empty when it cannot
    tell, and an empty option in a filter reads as a place.
    """
    return sorted(
        directory_queryset()
        .exclude(licensee__district="")
        .values_list("licensee__district", flat=True)
        .distinct()
    )


# ---------------------------------------------------------------- home page

#: The service categories the home page leads with, in the order a buyer meets
#: them: you incorporate, then you must keep a secretary and an address, then
#: you try to open an account, then the books start. The remaining categories
#: are reachable from the directory filters; this is a doorway, not an index.
FEATURED_SERVICES: tuple[tuple[str, str], ...] = (
    (ServiceCategory.INCORPORATION, "building"),
    (ServiceCategory.COMPANY_SECRETARY, "stamp"),
    (ServiceCategory.REGISTERED_ADDRESS, "doc"),
    (ServiceCategory.BANK_ACCOUNT_ASSIST, "bank"),
    (ServiceCategory.ACCOUNTING, "calculator"),
    (ServiceCategory.TAX_FILING, "coins"),
    (ServiceCategory.AUDIT_LIAISON, "scale"),
    (ServiceCategory.WORK_VISA, "passport"),
)


@dataclass(frozen=True, slots=True)
class ServiceSummary:
    """One service tile on the home page."""

    value: str
    label: str
    icon: str
    provider_count: int

    @property
    def url(self) -> str:
        from django.urls import reverse

        return f"{reverse('providers:list')}?service={self.value}"


def service_summaries() -> list[ServiceSummary]:
    """The featured service categories with how many companies publish each.

    The count is of companies that have actually filled the category in, so a
    tile reading 0 is telling the truth about the directory rather than hiding
    it. Tiles are shown whatever the count: a category with nobody in it yet is
    still the thing the visitor came to find out about.
    """
    counts = dict(
        ServiceOffering.objects.filter(
            is_active=True,
            provider__is_published=True,
            provider__licensee__status=LicenceStatus.ACTIVE,
        )
        .values_list("category")
        .annotate(total=Count("provider", distinct=True))
    )
    labels = dict(ServiceCategory.choices)
    return [
        ServiceSummary(
            value=value, label=str(labels[value]), icon=icon, provider_count=counts.get(value, 0)
        )
        for value, icon in FEATURED_SERVICES
    ]


@dataclass(frozen=True, slots=True)
class SearchChip:
    """A one-click entry into the directory, with the size of what it opens."""

    label: str
    url: str
    count: int


def popular_searches(*, limit: int = 10) -> list[SearchChip]:
    """Ready-made directory queries, largest first.

    These are the busiest *slices of the register*, not the most-typed search
    terms: nothing here logs what visitors type, so calling them 搜索热度 would
    describe a measurement the platform never took. The page labels them as
    what they are - shortcuts into the directory - and each chip carries its
    count so the reader can see why it is on the list.
    """
    from django.urls import reverse

    base = reverse("providers:list")
    published = directory_queryset().filter(licensee__status=LicenceStatus.ACTIVE)

    chips = [
        SearchChip(
            label=str(_("协助银行开户")),
            url=f"{base}?bank_support=1",
            count=published.filter(bank_account_support=True).count(),
        ),
        SearchChip(
            label=str(_("可远程办理")),
            url=f"{base}?remote_onboarding=1",
            count=published.filter(remote_onboarding=True).count(),
        ),
        SearchChip(
            label=str(_("普通话服务")),
            url=f"{base}?language={Language.MANDARIN}",
            count=published.filter(languages__contains=[Language.MANDARIN]).count(),
        ),
        SearchChip(
            label=str(_("虚拟银行经验")),
            url=f"{base}?bank_type={BankType.VIRTUAL}",
            count=published.filter(bank_types__contains=[BankType.VIRTUAL]).count(),
        ),
    ]
    from apps.registry.selectors import top_districts

    chips += [
        SearchChip(label=district, url=f"{base}?district={quote(district)}", count=count)
        for district, count in top_districts(limit=limit)
    ]
    # Drop the empty ones: a shortcut into an empty result set is a dead end
    # dressed up as a suggestion.
    return sorted((chip for chip in chips if chip.count), key=lambda chip: -chip.count)[:limit]


# ---------------------------------------------------------------- matching (A2)

#: AI_AGENTS A2: the model never sees the whole register. It ranks a shortlist
#: that SQL already screened, and thirty is what fits in a prompt without the
#: summaries having to be shortened into uselessness.
MATCH_CANDIDATE_LIMIT = 30


@dataclass(frozen=True, slots=True)
class CandidateFilters:
    """What a requirement asks for, in the terms the directory can answer.

    Deliberately not an ``Rfq``: this is the providers app, and a read query
    here should not know what a requirement row looks like. The caller in
    ``rfq`` does the translating.
    """

    services: tuple[str, ...] = ()
    needs_bank_account: bool = False
    language: str = ""
    budget_max_minor: int | None = None
    currency: str = "HKD"


def match_candidates(
    filters: CandidateFilters, *, limit: int = MATCH_CANDIDATE_LIMIT
) -> list[Provider]:
    """The shortlist A2 ranks: hard filters in SQL, order by ranking score.

    The filters are the ones from AI_AGENTS A2, and they are hard for a reason -
    a company that does not help with bank accounts is not a *worse* answer to
    "I need help opening an account", it is not an answer. What the model adds
    is the ordering and the explanation, over a pool that is already correct.

    One departure from the spec: candidates are ordered by how many of the
    requested services they actually publish *before* ``ranking_score``. A
    strong company that offers none of what was asked for is not the first row
    a buyer should read, and the score in RATING_SYSTEM section 5 knows nothing
    about this requirement.
    """
    queryset = directory_queryset().filter(licensee__status=LicenceStatus.ACTIVE)

    if filters.needs_bank_account:
        queryset = queryset.filter(bank_account_support=True)
    if filters.language:
        queryset = queryset.filter(languages__contains=[filters.language])
    if filters.budget_max_minor is not None:
        queryset = queryset.filter(_within_budget(filters.budget_max_minor, filters.currency))

    matched = Count(
        "offerings",
        filter=Q(offerings__category__in=filters.services, offerings__is_active=True),
        distinct=True,
    )
    return list(
        queryset.annotate(matched_services=matched)
        .prefetch_related(
            Prefetch(
                "offerings",
                queryset=ServiceOffering.objects.filter(is_active=True).prefetch_related("prices"),
            ),
            "certifications",
        )
        .order_by("-matched_services", "-ranking_score", "licensee__name_en")[:limit]
    )


def _within_budget(budget_max_minor: int, currency: str) -> Q:
    """Priced within the ceiling, or not priced publicly at all.

    The second half matters more than the first: most companies publish no
    prices, and excluding them would turn "I have a budget" into "only show me
    companies that already told the internet what they charge".
    """
    priced = PriceItem.objects.filter(
        offering__provider=OuterRef("pk"), offering__is_active=True, currency=currency
    )
    affordable = priced.filter(
        Q(amount_minor__lte=budget_max_minor) | Q(min_amount_minor__lte=budget_max_minor)
    )
    return Q(Exists(affordable)) | ~Q(Exists(priced))


def get_provider_detail(slug: str) -> Provider | None:
    """One provider with everything its page renders, in three queries."""
    return (
        directory_queryset()
        .prefetch_related(
            Prefetch(
                "offerings",
                queryset=ServiceOffering.objects.filter(is_active=True).prefetch_related("prices"),
            ),
            "certifications",
        )
        .filter(slug=slug)
        .first()
    )


def get_providers_for_compare(slugs: Sequence[str]) -> list[Provider]:
    """Up to ``MAX_COMPARE`` providers, in the order the URL asked for them.

    Order matters: the columns should stay where the user put them, and a
    queryset would return them in whatever order the index prefers.
    """
    wanted = list(dict.fromkeys(slug for slug in slugs if slug))[:MAX_COMPARE]
    if not wanted:
        return []
    found = {
        provider.slug: provider
        for provider in directory_queryset()
        .prefetch_related(
            Prefetch(
                "offerings",
                queryset=ServiceOffering.objects.filter(is_active=True).prefetch_related("prices"),
            )
        )
        .filter(slug__in=wanted)
    }
    return [found[slug] for slug in wanted if slug in found]


# ---------------------------------------------------------------- claims


def get_claim(claim_id: str) -> ProviderClaim | None:
    """One claim with everything its page and the review queue render."""
    return (
        ProviderClaim.objects.select_related(
            "provider", "provider__licensee", "submitted_by", "reviewer"
        )
        .prefetch_related("evidence")
        .filter(pk=claim_id)
        .first()
    )


def claims_for_user(user_id: str) -> QuerySet[ProviderClaim]:
    return (
        ProviderClaim.objects.filter(submitted_by__pk=user_id)
        .select_related("provider", "provider__licensee")
        .order_by("-created_at")
    )


def pending_claims() -> QuerySet[ProviderClaim]:
    """The moderation queue, oldest first - the queue is answered in order."""
    return (
        ProviderClaim.objects.filter(status=ClaimDecision.PENDING)
        .select_related("provider", "provider__licensee", "submitted_by")
        .order_by("created_at")
    )


def claimable_provider(slug: str) -> Provider | None:
    """A provider that may still be claimed, or None.

    Unpublished profiles and candidates with no licence behind them are
    excluded here as well as in ``services.submit_claim``: the view needs to
    answer 404 before rendering a form that could never be accepted.
    """
    provider = directory_queryset().filter(slug=slug, licensee__isnull=False).first()
    if provider is None or provider.claim_status == ClaimStatus.CLAIMED:
        return None
    return provider


def pending_description_edits(provider: Provider) -> QuerySet[ProviderProfileEdit]:
    """Self-introductions this company has submitted and nobody has read yet."""
    return provider.profile_edits.filter(status=ProfileEditStatus.PENDING).order_by("created_at")


def recent_profile_edits(provider: Provider, *, limit: int = 10) -> list[ProviderProfileEdit]:
    """The company's own change history, newest first.

    Shown back to the company rather than kept internal: the page is the only
    place it can find out that a colleague changed the price last Tuesday, and
    a log nobody is allowed to read stops anyone from noticing a mistake.
    """
    return list(provider.profile_edits.select_related("actor").order_by("-created_at")[:limit])


def pending_description_queue() -> QuerySet[ProviderProfileEdit]:
    """Every unread self-introduction, oldest first - answered in order."""
    return (
        ProviderProfileEdit.objects.filter(status=ProfileEditStatus.PENDING)
        .select_related("provider", "provider__licensee", "actor")
        .order_by("created_at")
    )


def providers_for_member(user_id: str) -> list[Provider]:
    """Pages this account may edit.

    Read from ``ProviderMember`` rather than from the user's role: the role
    says what kind of account this is, the membership says which company it
    speaks for, and only the second one can answer "which page do I manage?".
    """
    return list(
        directory_queryset()
        .filter(members__user__pk=user_id, members__is_active=True)
        .order_by("slug")
    )


def pending_logo_upload(provider: Provider) -> ProviderLogoUpload | None:
    """The logo waiting on a decision, if there is one. At most one by constraint."""
    return provider.logo_uploads.filter(status=LogoReviewStatus.PENDING).first()


def recent_logo_uploads(provider: Provider, *, limit: int = 5) -> list[ProviderLogoUpload]:
    """What this company has submitted, and what became of it."""
    return list(provider.logo_uploads.order_by("-created_at")[:limit])


def pending_logo_queue() -> QuerySet[ProviderLogoUpload]:
    """Every logo nobody has decided on, oldest first."""
    return (
        ProviderLogoUpload.objects.filter(status=LogoReviewStatus.PENDING)
        .select_related("provider", "provider__licensee", "uploaded_by")
        .order_by("created_at")
    )
