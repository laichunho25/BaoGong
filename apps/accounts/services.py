"""Account writes: registration, email verification, role changes.

Views never touch the ORM directly (ARCHITECTURE section 3). Everything that
creates or mutates an account goes through this module so that the rules below
hold on every path, including the admin and management commands:

* an address is only ever marked verified by a token that was mailed to it;
* a token is single-use and expires;
* issuing a new token invalidates the outstanding ones, so a forwarded old
  mail cannot be replayed after the user asked for a fresh link.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import EmailVerification, Role

if TYPE_CHECKING:
    from django.http import HttpRequest

    from apps.accounts.models import User

#: Long enough to survive a mail that lands in a spam folder overnight, short
#: enough that a leaked mailbox is not a permanent key to the account.
TOKEN_TTL = timedelta(hours=48)
TOKEN_BYTES = 32


class VerificationError(Exception):
    """A verification token cannot be accepted."""


@dataclass(frozen=True)
class IssuedToken:
    verification: EmailVerification
    token: str


def hash_token(token: str) -> str:
    """Hash a token for storage.

    Plain SHA-256 without a salt: the token is 32 random bytes, so there is no
    dictionary to defend against, and an unsalted digest is what lets the
    lookup be a single indexed query.
    """
    return hashlib.sha256(token.encode()).hexdigest()


@transaction.atomic
def issue_email_verification(user: User) -> IssuedToken:
    """Create a verification token for ``user`` and invalidate the older ones."""
    now = timezone.now()
    EmailVerification.objects.filter(user=user, used_at__isnull=True).update(expires_at=now)

    token = secrets.token_urlsafe(TOKEN_BYTES)
    verification = EmailVerification.objects.create(
        user=user,
        token_hash=hash_token(token),
        email=user.email,
        expires_at=now + TOKEN_TTL,
    )
    return IssuedToken(verification=verification, token=token)


def send_verification_email(user: User, token: str, *, request: HttpRequest | None = None) -> None:
    """Mail the verification link. Never logs or returns the raw token."""
    from django.urls import reverse

    path = reverse("accounts:verify_email", kwargs={"token": token})
    url = request.build_absolute_uri(path) if request is not None else path
    body = render_to_string("accounts/email/verify_email.txt", {"user": user, "url": url})
    send_mail(
        subject=_("请验证您的邮箱 - 包公 BaoGong"),
        message=body,
        from_email=None,
        recipient_list=[user.email],
    )


@transaction.atomic
def register_user(
    *,
    email: str,
    password: str,
    role: str = Role.BUYER,
    phone: str = "",
    request: HttpRequest | None = None,
) -> User:
    """Create an unverified account and send the verification mail.

    The account is usable for browsing straight away; anything that speaks to
    a real company - claiming a page, posting a review, sending an RFQ - is
    gated on ``is_email_verified`` by the views that own those flows.
    """
    user_model = get_user_model()
    user = user_model.objects.create_user(
        email=email,
        password=password,
        role=role,
        phone=phone,
    )
    issued = issue_email_verification(user)
    # Only after the transaction commits: a mail cannot be unsent if the
    # surrounding block rolls back.
    transaction.on_commit(lambda: send_verification_email(user, issued.token, request=request))
    return user


@transaction.atomic
def verify_email(token: str) -> User:
    """Consume ``token`` and mark its address verified.

    Raises ``VerificationError`` for an unknown, expired or already-used
    token. The three cases are deliberately not distinguished in the exception
    message shown to visitors, but they are distinguishable here for logging.
    """
    try:
        verification = EmailVerification.objects.select_for_update().get(
            token_hash=hash_token(token)
        )
    except EmailVerification.DoesNotExist as exc:
        raise VerificationError("unknown token") from exc

    now = timezone.now()
    if not verification.is_usable(now=now):
        raise VerificationError("used" if verification.used_at else "expired")

    verification.used_at = now
    verification.save(update_fields=["used_at", "updated_at"])

    user = verification.user
    # The address is only trusted if it is still the one the token was sent
    # to: a change of email between issue and click must re-verify.
    if user.email == verification.email and not user.is_email_verified:
        user.email_verified_at = now
        user.save(update_fields=["email_verified_at", "updated_at"])
    return user


def set_role(user: User, role: str, *, changed_by: User) -> User:
    """Change a user's role. Only a moderator or above may do so."""
    if not changed_by.is_moderator:
        raise PermissionError("Only a moderator may change a user's role.")
    if role not in Role.values:
        raise ValueError(f"Unknown role: {role}")
    user.role = role
    user.save(update_fields=["role", "updated_at"])
    return user
