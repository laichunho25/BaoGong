"""The account pages."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from django.core import mail
from django.urls import reverse

from apps.accounts import services, views
from apps.accounts.models import Role, User
from apps.accounts.tests.conftest import PASSWORD

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

pytestmark = pytest.mark.django_db


def body(response: object) -> str:
    return response.content.decode()  # type: ignore[attr-defined]


class TestRegister:
    def test_a_valid_form_creates_an_account_and_mails_it_a_link(
        self, client: Client, django_capture_on_commit_callbacks: Callable[..., Any]
    ) -> None:
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(
                reverse("accounts:register"),
                {
                    "email": "Buyer@Example.com",
                    "password": PASSWORD,
                    "role": Role.BUYER,
                    "phone": "",
                },
            )

        assert response.status_code == 302
        user = User.objects.get()
        assert user.email == "buyer@example.com"
        assert len(mail.outbox) == 1

    def test_registering_does_not_sign_the_account_in(
        self, client: Client, django_capture_on_commit_callbacks: Callable[..., Any]
    ) -> None:
        """Verify first, then sign in. A session handed out on an unconfirmed
        address is a session that can post in a licensed company's direction."""
        with django_capture_on_commit_callbacks(execute=True):
            client.post(
                reverse("accounts:register"),
                {"email": "buyer@example.com", "password": PASSWORD, "role": Role.BUYER},
            )

        assert "_auth_user_id" not in client.session

    def test_a_signed_in_visitor_cannot_open_the_registration_page(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        client.force_login(make_user(verified=True))

        response = client.get(reverse("accounts:register"))

        assert response.status_code == 302
        assert reverse("accounts:dashboard") in response.headers["Location"]

    def test_letters_in_the_phone_box_are_refused(self, client: Client) -> None:
        response = client.post(
            reverse("accounts:register"),
            {
                "email": "buyer@example.com",
                "password": PASSWORD,
                "role": Role.BUYER,
                "phone": "call me maybe",
            },
        )

        assert response.status_code == 200
        assert not User.objects.exists()

    def test_a_phone_number_is_stored_without_its_punctuation(
        self, client: Client, django_capture_on_commit_callbacks: Callable[..., Any]
    ) -> None:
        with django_capture_on_commit_callbacks(execute=True):
            client.post(
                reverse("accounts:register"),
                {
                    "email": "buyer@example.com",
                    "password": PASSWORD,
                    "role": Role.BUYER,
                    "phone": "+852 9123-4567",
                },
            )

        assert User.objects.get().phone == "+85291234567"

    def test_a_password_without_a_symbol_is_refused(self, client: Client) -> None:
        response = client.post(
            reverse("accounts:register"),
            {"email": "weak@example.com", "password": "Horsebattery9", "role": Role.BUYER},
        )

        assert response.status_code == 200
        assert not User.objects.exists()

    def test_a_duplicate_email_is_rejected_with_a_message(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        make_user(email="taken@example.com")

        response = client.post(
            reverse("accounts:register"),
            {"email": "taken@example.com", "password": PASSWORD, "role": Role.BUYER},
        )

        assert response.status_code == 200
        assert "该邮箱已注册" in body(response)
        assert User.objects.count() == 1

    def test_a_weak_password_is_rejected(self, client: Client) -> None:
        response = client.post(
            reverse("accounts:register"),
            {"email": "weak@example.com", "password": "12345678", "role": Role.BUYER},
        )

        assert response.status_code == 200
        assert not User.objects.exists()

    def test_the_role_choice_is_limited_to_buyer_and_provider(self, client: Client) -> None:
        # Nobody may hand themselves a moderator account by editing the form.
        response = client.post(
            reverse("accounts:register"),
            {"email": "sneaky@example.com", "password": PASSWORD, "role": Role.MODERATOR},
        )

        assert response.status_code == 200
        assert not User.objects.exists()


class TestVerificationViews:
    def test_the_link_verifies_and_redirects(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        user = make_user()
        issued = services.issue_email_verification(user)

        response = client.get(reverse("accounts:verify_email", kwargs={"token": issued.token}))

        assert response.status_code == 302
        user.refresh_from_db()
        assert user.is_email_verified

    def test_a_dead_link_says_so_without_saying_which_kind(self, client: Client) -> None:
        response = client.get(reverse("accounts:verify_email", kwargs={"token": "nope"}))

        assert response.status_code == 400
        assert "该链接已失效或已被使用" in body(response)

    def test_resending_needs_a_post(self, client: Client) -> None:
        """A GET here would be triggerable by an ``<img>`` on any page."""
        assert client.get(reverse("accounts:resend_verification")).status_code == 405

    def test_an_unverified_account_can_ask_for_a_new_link_without_signing_in(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        user = make_user()

        client.post(reverse("accounts:resend_verification"), {"email": user.email})

        assert len(mail.outbox) == 1

    def test_an_unknown_address_gets_the_same_answer_and_no_mail(self, client: Client) -> None:
        """The page must not double as a way of testing which addresses are
        registered here."""
        registered = client.post(
            reverse("accounts:resend_verification"), {"email": "nobody@example.com"}, follow=True
        )

        assert "如果该邮箱已注册" in body(registered)
        assert not mail.outbox

    def test_asking_repeatedly_stops_producing_mail(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        user = make_user()
        url = reverse("accounts:resend_verification")

        for _unused in range(6):
            client.post(url, {"email": user.email})

        assert len(mail.outbox) == views.MAIL_REQUESTS


class TestLoginAndDashboard:
    def test_an_account_signs_in_with_its_email(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        user = make_user(email="person@example.com", verified=True)

        response = client.post(
            reverse("accounts:login"), {"username": "Person@example.com", "password": PASSWORD}
        )

        assert response.status_code == 302
        assert client.session.get("_auth_user_id") == str(user.pk)

    def test_the_dashboard_is_private(self, client: Client) -> None:
        response = client.get(reverse("accounts:dashboard"))

        assert response.status_code == 302
        assert reverse("accounts:login") in response.headers["Location"]

    def test_an_unverified_account_is_told_what_it_cannot_do_yet(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        client.force_login(make_user())

        content = body(client.get(reverse("accounts:dashboard")))

        assert "邮箱尚未验证" in content

    def test_a_verified_account_is_not_nagged(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        user = make_user()
        services.verify_email(services.issue_email_verification(user).token)
        client.force_login(user)

        assert "邮箱尚未验证" not in body(client.get(reverse("accounts:dashboard")))

    def test_signing_out_needs_a_post(self, client: Client, make_user: Callable[..., User]) -> None:
        client.force_login(make_user())

        assert client.get(reverse("accounts:logout")).status_code == 405

        client.post(reverse("accounts:logout"))
        assert "_auth_user_id" not in client.session


class TestVerifiedSignInOnly:
    """Verification is a gate on signing in, not a nag after it."""

    def test_an_unverified_account_cannot_sign_in(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        make_user(email="person@example.com")

        response = client.post(
            reverse("accounts:login"),
            {"username": "person@example.com", "password": PASSWORD},
            follow=True,
        )

        assert "_auth_user_id" not in client.session
        assert "请先完成邮箱验证" in body(response)

    def test_a_refused_sign_in_lands_where_a_new_link_can_be_asked_for(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        """The 48-hour mail is usually gone by the time this happens, and the
        resend button used to sit behind the sign-in they cannot complete."""
        make_user(email="person@example.com")

        response = client.post(
            reverse("accounts:login"), {"username": "person@example.com", "password": PASSWORD}
        )

        assert response.headers["Location"] == reverse("accounts:verification_sent")
        assert client.session[views.PENDING_EMAIL_KEY] == "person@example.com"

    def test_verifying_then_signing_in_works(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        user = make_user(email="person@example.com")
        issued = services.issue_email_verification(user)

        client.get(reverse("accounts:verify_email", kwargs={"token": issued.token}))
        response = client.post(
            reverse("accounts:login"), {"username": "person@example.com", "password": PASSWORD}
        )

        assert response.status_code == 302
        assert client.session.get("_auth_user_id") == str(user.pk)

    def test_a_signed_in_visitor_cannot_open_the_sign_in_page(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        client.force_login(make_user(verified=True))

        response = client.get(reverse("accounts:login"))

        assert response.status_code == 302
        assert reverse("accounts:dashboard") in response.headers["Location"]


class TestSignInThrottle:
    def test_guessing_is_refused_after_a_few_attempts(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        make_user(email="person@example.com", verified=True)
        url = reverse("accounts:login")

        for _unused in range(views.LOGIN_ATTEMPTS):
            client.post(url, {"username": "person@example.com", "password": "Wrong-Guess9!"})
        blocked = client.post(url, {"username": "person@example.com", "password": PASSWORD})

        assert blocked.status_code == 429
        assert "_auth_user_id" not in client.session

    def test_a_different_address_from_the_same_caller_is_unaffected(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        """Keyed on caller *and* address: otherwise one wrong guess per company
        would be enough to lock a floor of a shared office out."""
        make_user(email="other@example.com", verified=True)
        url = reverse("accounts:login")
        for _unused in range(views.LOGIN_ATTEMPTS + 1):
            client.post(url, {"username": "person@example.com", "password": "Wrong-Guess9!"})

        response = client.post(url, {"username": "other@example.com", "password": PASSWORD})

        assert response.status_code == 302
        assert client.session.get("_auth_user_id")


class TestPasswordReset:
    def test_the_form_mails_a_link_that_sets_a_new_password(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        user = make_user(email="person@example.com", verified=True)

        client.post(reverse("accounts:password_reset"), {"email": "Person@example.com"})
        assert len(mail.outbox) == 1
        link = next(
            line.strip()
            for line in mail.outbox[0].body.splitlines()
            if "/accounts/password/reset/" in line
        )

        # Django moves the token from the URL into the session, then serves the
        # form from a fixed path - so the GET comes first.
        client.get(link)
        response = client.post(
            link.rsplit("/", 2)[0] + "/set-password/",
            {"new_password1": "Brand-New9!", "new_password2": "Brand-New9!"},
        )

        assert response.status_code == 302
        user.refresh_from_db()
        assert user.check_password("Brand-New9!")

    def test_an_unknown_address_is_answered_the_same_way(self, client: Client) -> None:
        response = client.post(
            reverse("accounts:password_reset"), {"email": "nobody@example.com"}, follow=True
        )

        assert "如果该邮箱已注册" in body(response)
        assert not mail.outbox

    def test_asking_repeatedly_stops_producing_mail(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        user = make_user(verified=True)

        for _unused in range(views.MAIL_REQUESTS + 3):
            client.post(reverse("accounts:password_reset"), {"email": user.email})

        assert len(mail.outbox) == views.MAIL_REQUESTS

    def test_a_weak_new_password_is_refused(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        user = make_user(email="person@example.com", verified=True)
        client.post(reverse("accounts:password_reset"), {"email": user.email})
        link = next(
            line.strip()
            for line in mail.outbox[0].body.splitlines()
            if "/accounts/password/reset/" in line
        )
        client.get(link)

        response = client.post(
            link.rsplit("/", 2)[0] + "/set-password/",
            {"new_password1": "password1234", "new_password2": "password1234"},
        )

        assert response.status_code == 200
        user.refresh_from_db()
        assert user.check_password(PASSWORD)

    def test_completing_a_reset_also_confirms_the_address(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        """The link went to the mailbox and came back used - the same proof the
        verification mail asks for, and it changed the credential as well."""
        user = make_user(email="person@example.com")
        client.post(reverse("accounts:password_reset"), {"email": user.email})
        link = next(
            line.strip()
            for line in mail.outbox[0].body.splitlines()
            if "/accounts/password/reset/" in line
        )
        client.get(link)

        client.post(
            link.rsplit("/", 2)[0] + "/set-password/",
            {"new_password1": "Brand-New9!", "new_password2": "Brand-New9!"},
        )

        user.refresh_from_db()
        assert user.is_email_verified

    def test_a_signed_in_visitor_is_sent_away(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        client.force_login(make_user(verified=True))

        response = client.get(reverse("accounts:password_reset"))

        assert response.status_code == 302
        assert reverse("accounts:dashboard") in response.headers["Location"]
