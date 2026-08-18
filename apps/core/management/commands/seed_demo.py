"""Fill a development database with enough life to look at.

``sync_tcsp`` gives a local machine 7,457 real licensees and nothing else: no
prices, no reviews, no requirements, no quotes. Every page built after P2
therefore renders correctly and shows nothing, which is honest in production
and useless when the point is to open a browser and see whether the work of
the last four phases hangs together.

This command writes the platform-side half. Three rules it keeps:

* **Development only.** It refuses to run unless ``DEBUG`` is on, and every
  account it creates lives at ``@seed.local`` so ``--reset`` can find its own
  rows and nothing else.
* **Through the services.** Reviews are submitted, moderated, verified and
  rated by the same functions the site calls, and quotes are charged against
  the same daily quota. A fixture that reaches past them proves nothing about
  the code it is meant to demonstrate.
* **Made up, and saying so.** Company names come from the official register
  because the pages are keyed on it; every price, review and quote attached to
  them is invented. Nothing here may be loaded into production - it would put
  fabricated statements next to the names of real licensed businesses.

The one place it steps around a service is the virus scanner. No scanner runs
locally (``UnavailableScanner``, fail-closed), so a seeded NNC1 would stay
unreadable forever and no review could ever be verified. The command marks its
own uploads clean, in one clearly-marked place, and then lets
``decide_verification`` do the deciding.
"""

from __future__ import annotations

import random
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import ProviderMember, Role, User
from apps.agents.tasks import analyse_quote, match_rfq, moderate_review
from apps.core.scanning import ScanStatus
from apps.core.uploads import inspect_upload
from apps.providers import services as provider_services
from apps.providers.models import (
    BankType,
    Certification,
    ClaimStatus,
    Language,
    PriceItem,
    PriceUnit,
    Provider,
    ProviderClaim,
    ServiceCategory,
    ServiceOffering,
)
from apps.registry.models import LicenceStatus
from apps.reviews import services as review_services
from apps.reviews.models import SCORE_FIELDS, Nnc1Verification, Review
from apps.rfq import services as rfq_services
from apps.rfq.models import CompanyType, LineItemLabel, Quote, Rfq, Timeline

if TYPE_CHECKING:
    from argparse import ArgumentParser

#: Every account this command creates. The domain is reserved for documentation
#: (RFC 6761 keeps ``.local`` off the public DNS), so a stray mail from a
#: misconfigured dev box cannot reach a real person.
SEED_DOMAIN = "seed.local"
SEED_PASSWORD = "seed-demo-1234"  # Development fixture; DEBUG only.

#: Fixed so two runs on two machines produce the same pages, which is what
#: makes "does this look right?" a question with one answer.
SEED = 20260818

_SERVICE_SETS: tuple[tuple[str, ...], ...] = (
    (
        ServiceCategory.INCORPORATION,
        ServiceCategory.COMPANY_SECRETARY,
        ServiceCategory.REGISTERED_ADDRESS,
        ServiceCategory.BANK_ACCOUNT_ASSIST,
        ServiceCategory.ACCOUNTING,
    ),
    (
        ServiceCategory.INCORPORATION,
        ServiceCategory.COMPANY_SECRETARY,
        ServiceCategory.REGISTERED_ADDRESS,
    ),
    (
        ServiceCategory.INCORPORATION,
        ServiceCategory.COMPANY_SECRETARY,
        ServiceCategory.ACCOUNTING,
        ServiceCategory.AUDIT_LIAISON,
        ServiceCategory.TAX_FILING,
    ),
    (
        ServiceCategory.COMPANY_SECRETARY,
        ServiceCategory.REGISTERED_ADDRESS,
        ServiceCategory.BANK_ACCOUNT_ASSIST,
        ServiceCategory.WORK_VISA,
    ),
)

#: Plausible ranges in HKD minor units, per service. A range where the market
#: really does quote a range, a point where it does not.
_PRICE_RANGES: dict[str, tuple[str, int, int]] = {
    ServiceCategory.INCORPORATION: ("成立公司（含政府规费）", 450_000, 1_280_000),
    ServiceCategory.COMPANY_SECRETARY: ("公司秘书（每年）", 180_000, 480_000),
    ServiceCategory.REGISTERED_ADDRESS: ("注册地址（每年）", 120_000, 360_000),
    ServiceCategory.BANK_ACCOUNT_ASSIST: ("银行开户陪同", 300_000, 900_000),
    ServiceCategory.ACCOUNTING: ("会计记账（每年起）", 500_000, 1_800_000),
    ServiceCategory.AUDIT_LIAISON: ("审计对接", 400_000, 1_200_000),
    ServiceCategory.TAX_FILING: ("报税申报", 200_000, 600_000),
    ServiceCategory.WORK_VISA: ("工作签证申请", 1_500_000, 3_800_000),
}

_YEARLY = {
    ServiceCategory.COMPANY_SECRETARY,
    ServiceCategory.REGISTERED_ADDRESS,
    ServiceCategory.ACCOUNTING,
}

_REVIEW_BODIES: tuple[tuple[str, tuple[int, int, int, int, int]], ...] = (
    (
        "去年通过他们开的公司，报价单一开始就写清楚政府规费和服务费分开算，"
        "后面没有再加过钱。开户是他们陪着去的，等了三周才批下来，中间有问必答。",
        (5, 5, 4, 5, 4),
    ),
    (
        "整体还可以。注册很快，一周就拿到证书。缺点是会计报价是后来才给的，"
        "第一次问的时候只说“看工作量”，心里没底。",
        (3, 4, 3, 4, 4),
    ),
    (
        "内地过去的客户他们接得多，普通话沟通没问题，材料清单发得很细。"
        "开户这块他们说得很实在，没有打包票，最后是自己飞过去面签的。",
        (4, 5, 4, 5, 5),
    ),
    (
        "价格在市场中间，服务谈不上惊喜但也没出错。续期前一个月有提醒，"
        "年审的文件是他们准备好我签字的。",
        (4, 4, 3, 4, 3),
    ),
    (
        "沟通效率一般，邮件经常隔天才回。事情最后都办成了，但过程需要自己盯。",
        (3, 2, 3, 3, 3),
    ),
)


class Command(BaseCommand):
    help = "Create fabricated demo data (providers, reviews, RFQs, quotes) for local browsing."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--providers",
            type=int,
            default=40,
            help="How many licensees get services and prices (default 40).",
        )
        parser.add_argument(
            "--claimed",
            type=int,
            default=6,
            help="How many of those are claimed by a seeded account (default 6).",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete everything an earlier run created, then stop.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG:
            raise CommandError(
                "seed_demo writes invented reviews and prices against the names of real "
                "licensed companies. It runs with DEBUG on and nowhere else."
            )

        if options["reset"]:
            self._reset()
            return

        pool = list(
            Provider.objects.filter(
                is_published=True, licensee__status=LicenceStatus.ACTIVE
            ).select_related("licensee")[: options["providers"]]
        )
        if not pool:
            raise CommandError(
                "No licensees in the database. Run `manage.py sync_tcsp` first - this "
                "command enriches the register, it does not invent companies."
            )

        rng = random.Random(SEED)
        with transaction.atomic():
            moderator = self._account("moderator", Role.MODERATOR)
            buyer = self._account("buyer", Role.BUYER)
            reviewers = [self._account(f"reviewer{n}", Role.BUYER) for n in range(1, 6)]

            self._offerings(pool, rng)
            claimed = self._claims(pool, moderator, options["claimed"])
            self._reviews(claimed, reviewers, moderator, rng)
            provider_services.recompute_ranking_inputs(
                provider_ids=[str(provider.pk) for provider in pool]
            )
            rfqs = self._requirements(buyer)
            self._quotes(rfqs[0], claimed, rng)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(pool)} providers, {len(claimed)} claimed, "
                f"{Review.objects.count()} reviews, {Rfq.objects.count()} requirements, "
                f"{Quote.objects.count()} quotes."
            )
        )
        self.stdout.write(f"Sign in as buyer@{SEED_DOMAIN} / moderator@{SEED_DOMAIN}")
        self.stdout.write(f"Password for every seeded account: {SEED_PASSWORD}")

    # ------------------------------------------------------------------ people

    def _account(self, handle: str, role: str) -> User:
        """An account that is already past the email loop.

        Verified on creation because the mail backend is the console here: a
        seeded buyer who cannot publish a requirement until someone copies a
        token out of the server log is a fixture that does not work.
        """
        user, created = User.objects.get_or_create(
            email=f"{handle}@{SEED_DOMAIN}",
            defaults={
                "role": role,
                "first_name": handle.title(),
                "email_verified_at": timezone.now(),
                "is_active": True,
            },
        )
        if created:
            user.set_password(SEED_PASSWORD)
            user.save(update_fields=["password"])
        return user

    # --------------------------------------------------------------- providers

    def _offerings(self, pool: list[Provider], rng: random.Random) -> None:
        """Services, prices and the profile facts the matcher filters on."""
        for index, provider in enumerate(pool):
            services = _SERVICE_SETS[index % len(_SERVICE_SETS)]
            provider.languages = [Language.CANTONESE, Language.ENGLISH] + (
                [Language.MANDARIN] if index % 3 != 2 else []
            )
            provider.supports_simplified = index % 3 != 2
            provider.remote_onboarding = index % 4 != 3
            provider.bank_account_support = ServiceCategory.BANK_ACCOUNT_ASSIST in services
            provider.bank_types = (
                [BankType.TRADITIONAL, BankType.VIRTUAL] if provider.bank_account_support else []
            )
            provider.non_resident_shareholder_experience = index % 2 == 0
            provider.founded_year = 2005 + (index % 15)
            provider.team_size = 4 + (index % 20)
            provider.website = f"https://example.com/{provider.slug}"
            provider.save()

            for category in services:
                offering, _ = ServiceOffering.objects.update_or_create(
                    provider=provider,
                    category=category,
                    defaults={"is_active": True, "description": ""},
                )
                label, low, high = _PRICE_RANGES[category]
                # A quarter of the pool publishes no price at all, because a
                # quarter of the directory really does not - and the pages that
                # say "价格需要询问" have to be visible on a demo too.
                if index % 4 == 3:
                    offering.prices.all().delete()
                    continue
                spread = rng.randint(0, (high - low) // 2)
                minimum = low + spread
                PriceItem.objects.update_or_create(
                    offering=offering,
                    label=label,
                    defaults={
                        "currency": "HKD",
                        "amount_minor": None,
                        "min_amount_minor": minimum,
                        "max_amount_minor": minimum + rng.randint(50_000, 400_000),
                        "unit": PriceUnit.YEARLY if category in _YEARLY else PriceUnit.ONE_OFF,
                        "includes_govt_fee": category == ServiceCategory.INCORPORATION,
                    },
                )

    def _claims(self, pool: list[Provider], moderator: User, wanted: int) -> list[Provider]:
        """Put a seeded account behind ``wanted`` pages, through the real claim flow.

        Pages a human already claimed on this machine are stepped over rather
        than reused: a quote submitted as somebody else's account would not be
        cleaned up by ``--reset``, and their claim is a state worth keeping.
        """
        claimed: list[Provider] = []
        for provider in pool:
            if len(claimed) >= wanted:
                break
            index = len(claimed) + 1
            member = self._account(f"company{index}", Role.BUYER)
            mine = ProviderMember.objects.filter(provider=provider, user=member).exists()
            if not mine:
                if provider.claim_status != ClaimStatus.UNCLAIMED:
                    continue
                claim = provider_services.submit_claim(
                    provider=provider,
                    user=member,
                    contact_name=f"Seed Contact {index}",
                    contact_role="Director",
                    applicant_note="Seeded claim for local development.",
                )
                provider_services.approve_claim(
                    claim=claim,
                    reviewer=moderator,
                    reason="Seeded fixture: approved without evidence, development only.",
                )
                provider.refresh_from_db()
            claimed.append(provider)
        return claimed

    # ----------------------------------------------------------------- reviews

    def _reviews(
        self,
        providers: list[Provider],
        reviewers: list[User],
        moderator: User,
        rng: random.Random,
    ) -> None:
        """Reviews in every state the tab can show: pending, published, verified."""
        for index, provider in enumerate(providers):
            for offset in range(min(3, len(reviewers))):
                author = reviewers[(index + offset) % len(reviewers)]
                if Review.objects.filter(provider=provider, author=author).exists():
                    continue
                body, scores = _REVIEW_BODIES[(index + offset) % len(_REVIEW_BODIES)]
                review = review_services.submit_review(
                    provider=provider,
                    author=author,
                    body=body,
                    scores=dict(
                        zip(
                            SCORE_FIELDS,
                            [Decimal(value) for value in scores],
                            strict=True,
                        )
                    ),
                    service_used=[ServiceCategory.INCORPORATION],
                    engagement_year=2024 + (offset % 2),
                )
                moderate_review(str(review.pk))
                # One review per company is left in the queue on purpose: the
                # moderation screens are part of what there is to look at.
                if offset == 2:
                    continue
                review_services.publish_review(
                    review=review, moderator=moderator, note="Seeded fixture: published."
                )
                if offset == 0:
                    self._verify(review, moderator, rng)

    def _verify(self, review: Review, moderator: User, rng: random.Random) -> None:
        """Take one review through NNC1 verification.

        The scan verdict is written here rather than by ``scan_nnc1`` because
        no scanner is reachable from a dev box, and a document that stays
        unreadable can never be passed. This is the only line in the file that
        asserts something a service would normally have to prove.
        """
        upload = SimpleUploadedFile(
            "nnc1.pdf",
            b"%PDF-1.4\n% seeded placeholder, not a real NNC1\n",
            content_type="application/pdf",
        )
        verification = review_services.submit_nnc1(
            review=review,
            uploader=review.author,
            upload=upload,
            inspected=inspect_upload(upload),
            declared_company_name=review.provider.display_name,
            declared_company_no=f"{rng.randint(1_000_000, 3_999_999)}",
        )
        Nnc1Verification.objects.filter(pk=verification.pk).update(
            scan_status=ScanStatus.CLEAN,
            scanner="seed_demo",
            scan_detail="Marked clean by seed_demo; no scanner runs in development.",
            scanned_at=timezone.now(),
        )
        verification.refresh_from_db()
        review_services.run_name_match(verification)
        review_services.decide_verification(
            verification=verification,
            reviewer=moderator,
            passed=True,
            note="Seeded fixture: treated as a matching NNC1.",
        )

    # ------------------------------------------------------------ requirements

    def _requirements(self, buyer: User) -> list[Rfq]:
        """Two requirements: one taking quotes, one still a draft."""
        existing = list(Rfq.objects.filter(buyer=buyer).order_by("created_at"))
        if existing:
            return existing

        published = rfq_services.create_rfq(
            buyer=buyer,
            title="内地股东，想在香港开一家贸易公司并办银行户口",
            services_needed=[
                ServiceCategory.INCORPORATION,
                ServiceCategory.COMPANY_SECRETARY,
                ServiceCategory.BANK_ACCOUNT_ASSIST,
            ],
            raw_input="我们在深圳做电子元件出口，想在香港开公司收美金，股东两个人都是内地身份。",
            company_type=CompanyType.HK_PRIVATE_LIMITED,
            business_nature="电子元件出口贸易",
            needs_bank_account=True,
            currency="HKD",
            budget_min_minor=800_000,
            budget_max_minor=2_500_000,
            timeline=Timeline.WITHIN_1_MONTH,
        )
        rfq_services.publish_rfq(rfq=published, buyer=buyer)
        # A2 runs here instead of in a worker: `publish_rfq` queues it after
        # commit, and a dev box usually has no worker consuming the queue.
        # Agents are off by default, so this exercises the rule fallback -
        # which is the path a demo should be showing anyway.
        match_rfq(str(published.pk))
        published.refresh_from_db()

        draft = rfq_services.create_rfq(
            buyer=buyer,
            title="想把现有香港公司的会计和报税转过来",
            services_needed=[ServiceCategory.ACCOUNTING, ServiceCategory.TAX_FILING],
            company_type=CompanyType.HK_PRIVATE_LIMITED,
            business_nature="软件外包",
            timeline=Timeline.FLEXIBLE,
        )
        return [published, draft]

    def _quotes(self, rfq: Rfq, providers: list[Provider], rng: random.Random) -> None:
        """Three answers to the open requirement, deliberately not alike.

        One quote leaves the government fee out and one omits the bank item, so
        the comparison table has a blank cell to name and A5 has something to
        ask about - a demo where every quote is complete demonstrates nothing.
        """
        if Quote.objects.filter(rfq=rfq).exists():
            return

        plans: tuple[dict[str, Any], ...] = (
            {
                "total": 1_180_000,
                "govt": True,
                "items": [
                    (LineItemLabel.GOVT_INCORPORATION_FEE, 172_000),
                    (LineItemLabel.BUSINESS_REGISTRATION_FEE, 215_000),
                    (LineItemLabel.INCORPORATION_SERVICE, 380_000),
                    (LineItemLabel.COMPANY_SECRETARY, 230_000),
                    (LineItemLabel.BANK_ACCOUNT_ASSIST, 183_000),
                ],
                "message": "报价已含政府规费，银行开户陪同一次，不含第二次预约。",
                "delivery": 7,
            },
            {
                "total": 880_000,
                "govt": False,
                "items": [
                    (LineItemLabel.INCORPORATION_SERVICE, 420_000),
                    (LineItemLabel.COMPANY_SECRETARY, 260_000),
                    (LineItemLabel.REGISTERED_ADDRESS, 200_000),
                ],
                "message": "以上为服务费，政府规费另计。",
                "delivery": 5,
            },
            {
                "total": 1_520_000,
                "govt": True,
                "items": [
                    (LineItemLabel.GOVT_INCORPORATION_FEE, 172_000),
                    (LineItemLabel.BUSINESS_REGISTRATION_FEE, 215_000),
                    (LineItemLabel.INCORPORATION_SERVICE, 480_000),
                    (LineItemLabel.COMPANY_SECRETARY, 280_000),
                    (LineItemLabel.REGISTERED_ADDRESS, 190_000),
                    (LineItemLabel.COMPANY_KIT, 183_000),
                ],
                "message": "包含公司套装与首年注册地址，开户可另行报价。",
                "delivery": 10,
            },
        )

        for provider, plan in zip(providers, plans, strict=False):
            member = ProviderMember.objects.filter(provider=provider, is_active=True).first()
            if member is None:
                continue
            quote = rfq_services.submit_quote(
                rfq=rfq,
                provider=provider,
                submitted_by=member.user,
                first_year_total_minor=int(plan["total"]),
                renewal_total_minor=int(plan["total"]) - rng.randint(200_000, 500_000),
                includes_govt_fee=bool(plan["govt"]),
                delivery_days=int(plan["delivery"]),
                message=str(plan["message"]),
                line_items=[
                    {"label": label, "amount_minor": amount, "ordinal": ordinal}
                    for ordinal, (label, amount) in enumerate(plan["items"])
                ],
            )
            analyse_quote(str(quote.pk))

    # ------------------------------------------------------------------- reset

    def _reset(self) -> None:
        """Remove what a previous run wrote, and only that.

        Ordered by foreign key rather than left to cascades: ``Rfq.buyer`` is
        PROTECT, and a half-deleted fixture is worse than none.
        """
        users = User.objects.filter(email__endswith=f"@{SEED_DOMAIN}")
        seeded_ids = list(
            ProviderMember.objects.filter(user__in=users).values_list("provider_id", flat=True)
        )
        providers = Provider.objects.filter(pk__in=seeded_ids)

        Quote.objects.filter(rfq__buyer__in=users).delete()
        Quote.objects.filter(provider__in=providers).delete()
        Rfq.objects.filter(buyer__in=users).delete()
        for verification in Nnc1Verification.objects.filter(review__author__in=users):
            verification.file.delete(save=False)
            verification.delete()
        Review.objects.filter(author__in=users).delete()
        Certification.objects.filter(provider__in=providers).delete()
        ProviderMember.objects.filter(user__in=users).delete()
        ProviderClaim.objects.filter(submitted_by__in=users).delete()
        providers.update(claim_status=ClaimStatus.UNCLAIMED)
        # Offerings and prices carry no marker saying who wrote them, and in
        # development nothing else does. Both tables go, rather than guessing.
        PriceItem.objects.all().delete()
        ServiceOffering.objects.all().delete()
        provider_services.recompute_ranking_inputs()

        deleted = users.count()
        users.delete()
        self.stdout.write(
            self.style.WARNING(
                f"Removed {deleted} seeded accounts, their reviews, requirements and quotes, "
                "and every service offering and price in the database."
            )
        )
