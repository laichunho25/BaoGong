"""Registration and email verification rules."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import EmailVerification, Role, User

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.django_db


class TestUserManager:
    def test_the_email_is_normalised_and_lowercased(self) -> None:
        # Two people typing the same address in different case are one person.
        user = User.objects.create_user(email="Someone@Example.COM", password="x")

        assert user.email == "someone@example.com"

    def test_an_account_without_an_email_is_refused(self) -> None:
        with pytest.raises(ValueError, match="email address is required"):
            User.objects.create_user(email="", password="x")

    def test_a_superuser_is_created_verified_and_as_admin(self) -> None:
        # Whoever can run createsuperuser controls the server, which is better
        # proof than the mail loop.
        user = User.objects.create_superuser(email="root@example.com", password="x")

        assert user.role == Role.ADMIN
        assert user.is_email_verified
        assert user.is_moderator


class TestRegisterUser:
    # The mail is queued with transaction.on_commit so that a rolled-back
    # registration cannot send one; the fixture runs those callbacks.
    def test_it_creates_an_unverified_account_and_sends_one_mail(
        self, django_capture_on_commit_callbacks: Callable[..., Any]
    ) -> None:
        with django_capture_on_commit_callbacks(execute=True):
            user = services.register_user(email="new@example.com", password="pw-long-enough")

        assert not user.is_email_verified
        assert len(mail.outbox) == 1
        assert user.email in mail.outbox[0].to

    def test_nothing_is_mailed_before_the_transaction_commits(self) -> None:
        services.register_user(email="new@example.com", password="pw-long-enough")

        assert not mail.outbox

    def test_the_raw_token_is_never_stored(
        self, django_capture_on_commit_callbacks: Callable[..., Any]
    ) -> None:
        with django_capture_on_commit_callbacks(execute=True):
            services.register_user(email="new@example.com", password="pw-long-enough")

        verification = EmailVerification.objects.get()
        assert len(verification.token_hash) == 64
        assert verification.token_hash not in mail.outbox[0].body


class TestVerifyEmail:
    def test_a_fresh_token_verifies_the_address(self, make_user: Callable[..., User]) -> None:
        user = make_user()
        issued = services.issue_email_verification(user)

        services.verify_email(issued.token)

        user.refresh_from_db()
        assert user.is_email_verified

    def test_a_token_cannot_be_used_twice(self, make_user: Callable[..., User]) -> None:
        issued = services.issue_email_verification(make_user())
        services.verify_email(issued.token)

        with pytest.raises(services.VerificationError):
            services.verify_email(issued.token)

    def test_an_expired_token_is_refused(self, make_user: Callable[..., User]) -> None:
        issued = services.issue_email_verification(make_user())
        EmailVerification.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

        with pytest.raises(services.VerificationError):
            services.verify_email(issued.token)

    def test_an_unknown_token_is_refused(self) -> None:
        with pytest.raises(services.VerificationError):
            services.verify_email("not-a-real-token")

    def test_issuing_a_new_token_kills_the_old_one(self, make_user: Callable[..., User]) -> None:
        # Otherwise a forwarded old mail stays a working login link forever.
        user = make_user()
        first = services.issue_email_verification(user)
        second = services.issue_email_verification(user)

        with pytest.raises(services.VerificationError):
            services.verify_email(first.token)
        services.verify_email(second.token)
        user.refresh_from_db()
        assert user.is_email_verified

    def test_a_token_does_not_verify_an_address_that_has_since_changed(
        self, make_user: Callable[..., User]
    ) -> None:
        user = make_user()
        issued = services.issue_email_verification(user)
        user.email = "moved@example.com"
        user.save(update_fields=["email"])

        services.verify_email(issued.token)

        user.refresh_from_db()
        assert not user.is_email_verified


class TestSetRole:
    def test_a_moderator_may_change_a_role(self, make_user: Callable[..., User]) -> None:
        moderator = make_user(role=Role.MODERATOR)
        user = make_user()

        services.set_role(user, Role.PROVIDER_MEMBER, changed_by=moderator)

        user.refresh_from_db()
        assert user.role == Role.PROVIDER_MEMBER

    def test_an_ordinary_account_may_not(self, make_user: Callable[..., User]) -> None:
        with pytest.raises(PermissionError):
            services.set_role(make_user(), Role.MODERATOR, changed_by=make_user())

    def test_an_unknown_role_is_refused(self, make_user: Callable[..., User]) -> None:
        with pytest.raises(ValueError, match="Unknown role"):
            services.set_role(make_user(), "superadmin", changed_by=make_user(role=Role.ADMIN))
