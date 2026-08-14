"""The account pages."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from django.core import mail
from django.urls import reverse

from apps.accounts import services
from apps.accounts.models import Role, User
from apps.accounts.tests.conftest import PASSWORD

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

pytestmark = pytest.mark.django_db


def body(response: object) -> str:
    return response.content.decode()  # type: ignore[attr-defined]


class TestRegister:
    def test_a_valid_form_creates_an_account_and_signs_it_in(
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
        assert client.session.get("_auth_user_id") == str(user.pk)
        assert len(mail.outbox) == 1

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

    def test_resending_requires_a_post_from_a_signed_in_account(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        user = make_user()

        assert client.post(reverse("accounts:resend_verification")).status_code == 302
        assert not mail.outbox

        client.force_login(user)
        client.post(reverse("accounts:resend_verification"))
        assert len(mail.outbox) == 1


class TestLoginAndDashboard:
    def test_an_account_signs_in_with_its_email(
        self, client: Client, make_user: Callable[..., User]
    ) -> None:
        user = make_user(email="person@example.com")

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
