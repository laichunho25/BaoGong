"""A company's logo, from the upload box to the directory listing.

Two gates stand between the bytes and the page and the tests here keep both
shut: the scanner decides whether the file may be served at all, and a
moderator decides whether the platform is willing to print the image. The
second gate is not ceremony. An image is text that no phrase list can read, so
``check_banned_phrases`` is blind to "保证开户成功" drawn inside a PNG and a
person is the only remaining check.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.accounts.models import MemberRole, ProviderMember
from apps.core.uploads import IMAGE_CONTENT_TYPES, MAX_LOGO_BYTES, inspect_upload
from apps.providers import selectors, services
from apps.providers.forms import ProviderLogoForm
from apps.providers.models import (
    ClaimStatus,
    LogoReviewStatus,
    Provider,
    ProviderLogoUpload,
    Tier,
)
from apps.providers.tests.conftest import PNG_BYTES
from apps.registry.models import LicenceStatus, allow_registry_writes

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

    from apps.accounts.models import User

# A scanner has to be configured for the upload box to exist at all: without
# one every file stays `pending`, nothing can ever be approved, and
# ``upload_logo`` refuses the file rather than build a queue nobody can clear.
# What the scanner answers is a separate question - ``clean_scanner`` decides
# that per test. The refusal itself is covered by ``TestWithoutAScanner``.
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _scanner_configured(settings: Any) -> None:
    """Every test in this file runs with a scanner configured, unless it says
    otherwise. Which backend does not matter here - nothing in these tests
    reaches a socket, because ``clean_scanner`` replaces the verdict wherever
    one is needed."""
    settings.FILE_SCANNER_BACKEND = "apps.core.scanning.ClamAvScanner"


@pytest.fixture
def claimed(make_provider: Callable[..., Provider]) -> Callable[..., Provider]:
    def _make(**overrides: Any) -> Provider:
        return make_provider(claim_status=ClaimStatus.CLAIMED, **overrides)

    return _make


def _member(provider: Provider, user: User) -> ProviderMember:
    return ProviderMember.objects.create(user=user, provider=provider, member_role=MemberRole.OWNER)


def _delist(provider: Provider) -> None:
    with allow_registry_writes():
        licensee = provider.licensee
        assert licensee is not None
        licensee.status = LicenceStatus.INACTIVE
        licensee.save(update_fields=["status"])
    provider.refresh_from_db()


def _submit(provider: Provider, user: User, upload: SimpleUploadedFile) -> ProviderLogoUpload:
    inspected = inspect_upload(upload, allowed=IMAGE_CONTENT_TYPES, max_bytes=MAX_LOGO_BYTES)
    return services.upload_logo(provider=provider, actor=user, upload=upload, inspected=inspected)


def _cleared(provider: Provider, user: User, upload: SimpleUploadedFile) -> ProviderLogoUpload:
    return services.scan_provider_logo(_submit(provider, user, upload))


class TestUpload:
    def test_it_never_touches_the_published_logo(
        self,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        make_image_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        provider = claimed()
        logo = _submit(provider, make_user(), make_image_upload())

        provider.refresh_from_db()
        assert not provider.logo
        assert logo.status == LogoReviewStatus.PENDING
        assert logo.is_readable is False

    def test_the_free_tier_may_upload_one(
        self,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        make_image_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        """PRD section 3.7 sells placement, not recognisability: a logo is how a
        buyer tells one row of the directory from the next, so it is not a
        paid field."""
        logo = _submit(claimed(tier=Tier.FREE), make_user(), make_image_upload())

        assert logo.pk is not None

    def test_a_pdf_wearing_a_png_name_is_refused(
        self, make_upload: Callable[..., SimpleUploadedFile]
    ) -> None:
        form = ProviderLogoForm({}, {"logo": make_upload(name="logo.png")})

        assert form.is_valid() is False
        assert form.errors["logo"]

    def test_an_oversized_image_is_refused(
        self, make_image_upload: Callable[..., SimpleUploadedFile]
    ) -> None:
        oversized = make_image_upload(content=PNG_BYTES + b"\x00" * MAX_LOGO_BYTES)
        form = ProviderLogoForm({}, {"logo": oversized})

        assert form.is_valid() is False
        assert form.errors["logo"]

    def test_only_one_may_wait_at_a_time(
        self,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        make_image_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        """Otherwise a company could queue a dozen images and publish whichever
        one the moderator happened to reach first."""
        provider = claimed()
        user = make_user()
        _submit(provider, user, make_image_upload())

        with pytest.raises(services.LogoError):
            _submit(provider, user, make_image_upload())

    def test_a_delisted_page_may_not_upload(
        self,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        make_image_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        provider = claimed()
        _delist(provider)

        with pytest.raises(services.LogoError):
            _submit(provider, make_user(), make_image_upload())

    def test_an_unclaimed_page_may_not_upload(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_image_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        with pytest.raises(services.LogoError):
            _submit(make_provider(), make_user(), make_image_upload())


class TestDecision:
    def test_an_unscanned_logo_cannot_be_published(
        self,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
        make_image_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        """The reason the file waits in a private bucket at all. There is no
        override here, unlike claim evidence: this image goes to every visitor
        of the page, not to one moderator."""
        logo = _submit(claimed(), make_user(), make_image_upload())

        with pytest.raises(services.LogoError):
            services.decide_logo(logo=logo, reviewer=moderator, approve=True, note="looks fine")

    def test_approval_publishes_the_bytes_and_drops_the_private_copy(
        self,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
        make_image_upload: Callable[..., SimpleUploadedFile],
        clean_scanner: None,
    ) -> None:
        provider = claimed()
        logo = _cleared(provider, make_user(), make_image_upload())

        services.decide_logo(logo=logo, reviewer=moderator, approve=True, note="Checked by hand")

        provider.refresh_from_db()
        logo.refresh_from_db()
        assert provider.logo
        assert logo.status == LogoReviewStatus.APPROVED
        assert logo.published_at is not None
        assert not logo.file

    def test_publishing_raises_completeness(
        self,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
        make_image_upload: Callable[..., SimpleUploadedFile],
        clean_scanner: None,
    ) -> None:
        """A free page can reach every field it is allowed to reach - that is
        the whole point of the tier-relative denominator (COMPLIANCE section 6)."""
        provider = claimed(tier=Tier.FREE, website="https://example.com")
        logo = _cleared(provider, make_user(), make_image_upload())

        services.decide_logo(logo=logo, reviewer=moderator, approve=True, note="Checked by hand")

        provider.refresh_from_db()
        assert provider.profile_completeness == Decimal("1.000")

    def test_a_refusal_leaves_the_page_as_it_was_and_keeps_no_copy(
        self,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
        make_image_upload: Callable[..., SimpleUploadedFile],
        clean_scanner: None,
    ) -> None:
        provider = claimed()
        logo = _cleared(provider, make_user(), make_image_upload())

        services.decide_logo(
            logo=logo, reviewer=moderator, approve=False, note="图上有开户成功率字样"
        )

        provider.refresh_from_db()
        logo.refresh_from_db()
        assert not provider.logo
        assert logo.status == LogoReviewStatus.REJECTED
        assert not logo.file

    def test_the_company_is_told_the_decision_and_the_reason(
        self,
        django_capture_on_commit_callbacks: Callable[..., Any],
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
        make_image_upload: Callable[..., SimpleUploadedFile],
        clean_scanner: None,
    ) -> None:
        """A refusal the company only learns about by revisiting a dashboard is
        a refusal without a reason attached to it."""
        provider = claimed()
        owner = make_user(email="owner@example.com")
        _member(provider, owner)
        logo = _cleared(provider, owner, make_image_upload())

        with django_capture_on_commit_callbacks(execute=True):
            services.decide_logo(
                logo=logo, reviewer=moderator, approve=False, note="图上有开户成功率字样"
            )

        [message] = mail.outbox
        assert message.to == ["owner@example.com"]
        assert "图上有开户成功率字样" in message.body

    def test_a_decision_needs_a_moderator_and_a_reason(
        self,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
        make_image_upload: Callable[..., SimpleUploadedFile],
        clean_scanner: None,
    ) -> None:
        logo = _cleared(claimed(), make_user(), make_image_upload())

        with pytest.raises(services.LogoError):
            services.decide_logo(logo=logo, reviewer=make_user(), approve=True, note="Fine")
        with pytest.raises(services.LogoError):
            services.decide_logo(logo=logo, reviewer=moderator, approve=True, note="   ")

    def test_it_cannot_be_decided_twice(
        self,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
        make_image_upload: Callable[..., SimpleUploadedFile],
        clean_scanner: None,
    ) -> None:
        logo = _cleared(claimed(), make_user(), make_image_upload())
        services.decide_logo(logo=logo, reviewer=moderator, approve=True, note="Checked by hand")

        with pytest.raises(services.LogoError):
            services.decide_logo(logo=logo, reviewer=moderator, approve=False, note="Changed mind")


class TestWithdraw:
    def test_a_member_may_take_it_back_and_the_bytes_go_too(
        self,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        make_image_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        provider = claimed()
        owner = make_user()
        _member(provider, owner)
        logo = _submit(provider, owner, make_image_upload())

        services.withdraw_logo(logo=logo, user=owner)

        logo.refresh_from_db()
        assert logo.status == LogoReviewStatus.WITHDRAWN
        assert not logo.file

    def test_a_stranger_may_not(
        self,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        make_image_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        provider = claimed()
        owner = make_user()
        _member(provider, owner)
        logo = _submit(provider, owner, make_image_upload())

        with pytest.raises(services.LogoError):
            services.withdraw_logo(logo=logo, user=make_user())


class TestViews:
    def test_a_member_can_upload_from_the_manage_page(
        self,
        client: Client,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        make_image_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        provider = claimed()
        owner = make_user()
        _member(provider, owner)
        client.force_login(owner)

        response = client.post(
            reverse("providers:logo_upload", kwargs={"slug": provider.slug}),
            {"logo": make_image_upload()},
        )

        assert response.status_code == 302
        assert selectors.pending_logo_upload(provider) is not None

    def test_a_stranger_is_told_the_page_does_not_exist(
        self,
        client: Client,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        make_image_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        """404 rather than 403: who manages which company is not something a
        stranger gets to enumerate."""
        provider = claimed()
        client.force_login(make_user())

        response = client.post(
            reverse("providers:logo_upload", kwargs={"slug": provider.slug}),
            {"logo": make_image_upload()},
        )

        assert response.status_code == 404
        assert selectors.pending_logo_upload(provider) is None

    def test_an_unscanned_logo_is_served_to_nobody(
        self,
        client: Client,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
        make_image_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        logo = _submit(claimed(), make_user(), make_image_upload())
        client.force_login(moderator)

        response = client.get(reverse("providers:logo_preview", kwargs={"logo_id": logo.pk}))

        assert response.status_code == 404

    def test_the_detail_page_shows_a_published_logo(
        self,
        client: Client,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
        make_image_upload: Callable[..., SimpleUploadedFile],
        clean_scanner: None,
    ) -> None:
        provider = claimed()
        logo = _cleared(provider, make_user(), make_image_upload())
        services.decide_logo(logo=logo, reviewer=moderator, approve=True, note="Checked by hand")

        response = client.get(provider.get_absolute_url())

        provider.refresh_from_db()
        assert provider.logo.url in response.content.decode()

    def test_a_delisted_page_falls_back_to_the_initial(
        self,
        client: Client,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
        make_image_upload: Callable[..., SimpleUploadedFile],
        clean_scanner: None,
    ) -> None:
        """Everything the company supplied comes off the page when the licence
        leaves the register, and a logo is company-supplied."""
        provider = claimed()
        logo = _cleared(provider, make_user(), make_image_upload())
        services.decide_logo(logo=logo, reviewer=moderator, approve=True, note="Checked by hand")
        _delist(provider)

        response = client.get(provider.get_absolute_url())

        provider.refresh_from_db()
        assert provider.logo.url not in response.content.decode()

    def test_a_file_that_is_not_an_image_never_reaches_the_service(
        self,
        client: Client,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = claimed()
        owner = make_user()
        _member(provider, owner)
        client.force_login(owner)

        response = client.post(
            reverse("providers:logo_upload", kwargs={"slug": provider.slug}),
            {"logo": SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")},
            follow=True,
        )

        assert selectors.pending_logo_upload(provider) is None
        assert list(response.context["messages"])

    def test_a_second_upload_is_refused_with_a_message(
        self,
        client: Client,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        make_image_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        """The queue holds one upload per company, and the page has to say so -
        the form is still rendered while a decision is outstanding."""
        provider = claimed()
        owner = make_user()
        _member(provider, owner)
        first = _submit(provider, owner, make_image_upload())
        client.force_login(owner)

        response = client.post(
            reverse("providers:logo_upload", kwargs={"slug": provider.slug}),
            {"logo": make_image_upload()},
            follow=True,
        )

        assert selectors.pending_logo_upload(provider) == first
        assert list(response.context["messages"])

    def test_a_member_can_withdraw_from_the_manage_page(
        self,
        client: Client,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        make_image_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        provider = claimed()
        owner = make_user()
        _member(provider, owner)
        logo = _submit(provider, owner, make_image_upload())
        client.force_login(owner)

        response = client.post(reverse("providers:logo_withdraw", kwargs={"logo_id": logo.pk}))

        logo.refresh_from_db()
        assert response.status_code == 302
        assert logo.status == LogoReviewStatus.WITHDRAWN

    def test_a_stranger_cannot_withdraw_another_company_upload(
        self,
        client: Client,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        make_image_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        provider = claimed()
        owner = make_user()
        _member(provider, owner)
        logo = _submit(provider, owner, make_image_upload())
        client.force_login(make_user())

        response = client.post(reverse("providers:logo_withdraw", kwargs={"logo_id": logo.pk}))

        logo.refresh_from_db()
        assert response.status_code == 404
        assert logo.status == LogoReviewStatus.PENDING

    def test_withdrawing_a_decided_upload_is_a_message_not_a_crash(
        self,
        client: Client,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
        make_image_upload: Callable[..., SimpleUploadedFile],
        clean_scanner: None,
    ) -> None:
        provider = claimed()
        owner = make_user()
        _member(provider, owner)
        logo = _cleared(provider, owner, make_image_upload())
        services.decide_logo(logo=logo, reviewer=moderator, approve=True, note="Checked by hand")
        client.force_login(owner)

        response = client.post(
            reverse("providers:logo_withdraw", kwargs={"logo_id": logo.pk}), follow=True
        )

        logo.refresh_from_db()
        assert logo.status == LogoReviewStatus.APPROVED
        assert list(response.context["messages"])

    def test_a_scanned_logo_is_served_to_the_moderator_deciding_on_it(
        self,
        client: Client,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        moderator: User,
        make_image_upload: Callable[..., SimpleUploadedFile],
        clean_scanner: None,
    ) -> None:
        """The private bucket has no public URL, so this view is the only way a
        moderator can look at the image before deciding to print it."""
        logo = _cleared(claimed(), make_user(), make_image_upload())
        client.force_login(moderator)

        response = client.get(reverse("providers:logo_preview", kwargs={"logo_id": logo.pk}))

        assert response.status_code == 200
        assert response["Cache-Control"] == "private, no-store"
        assert b"".join(response.streaming_content) == PNG_BYTES

    def test_a_missing_upload_is_a_404_for_everyone(self, client: Client, moderator: User) -> None:
        client.force_login(moderator)

        response = client.get(
            reverse(
                "providers:logo_preview",
                kwargs={"logo_id": "00000000-0000-0000-0000-000000000000"},
            )
        )

        assert response.status_code == 404


class TestWithoutAScanner:
    """What a company sees before the scanner exists.

    This is the state the platform actually deploys in first: Render has no
    managed ClamAV, and the fail-closed default means every file would sit at
    ``pending`` forever. Accepting uploads into that would be worse than not
    offering the box, because the company hears nothing back and concludes the
    site is broken rather than unfinished.
    """

    @pytest.fixture(autouse=True)
    def _no_scanner(self, settings: Any) -> None:
        settings.FILE_SCANNER_BACKEND = "apps.core.scanning.UnavailableScanner"

    def test_the_upload_is_refused_rather_than_queued(
        self,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        make_image_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        provider = claimed()

        with pytest.raises(services.LogoError):
            _submit(provider, make_user(), make_image_upload())

        assert provider.logo_uploads.count() == 0

    def test_the_manage_page_says_why_instead_of_offering_a_form(
        self,
        client: Client,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
    ) -> None:
        provider = claimed()
        owner = make_user()
        _member(provider, owner)
        client.force_login(owner)

        response = client.get(reverse("providers:manage", kwargs={"slug": provider.slug}))

        assert response.status_code == 200
        assert response.context["logo_form"] is None
        assert response.context["scanning_available"] is False
        assert "正在准备中" in response.content.decode()

    def test_a_kept_url_does_not_get_around_the_missing_form(
        self,
        client: Client,
        claimed: Callable[..., Provider],
        make_user: Callable[..., User],
        make_image_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        """The form is absent from the page, which stops nobody who bookmarked
        the endpoint - so the refusal lives in the service, not the template."""
        provider = claimed()
        owner = make_user()
        _member(provider, owner)
        client.force_login(owner)

        response = client.post(
            reverse("providers:logo_upload", kwargs={"slug": provider.slug}),
            {"logo": make_image_upload()},
            follow=True,
        )

        assert provider.logo_uploads.count() == 0
        assert list(response.context["messages"])
