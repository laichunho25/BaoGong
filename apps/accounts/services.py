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

from apps.accounts.models import (
    EmailVerification,
    MemberRole,
    ProviderMember,
    ProviderMemberInvite,
    Role,
)

if TYPE_CHECKING:
    from django.http import HttpRequest

    from apps.accounts.models import User
    from apps.providers.models import Provider

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


def resend_verification_email(email: str, *, request: HttpRequest | None = None) -> bool:
    """Send a fresh verification link to ``email``, if it needs one.

    Reachable while signed out, because being unable to sign in is the whole
    reason somebody asks for this. The return value is for the caller's logs
    and tests only - the page says the same thing either way, so that this
    cannot be used to find out which addresses hold an account.

    Nothing is created for an unknown address: the mail is only ever sent to a
    mailbox that already registered.
    """
    user_model = get_user_model()
    user = user_model.objects.filter(email__iexact=email.strip().lower()).first()
    if user is None or user.is_email_verified:
        return False
    issued = issue_email_verification(user)
    send_verification_email(user, issued.token, request=request)
    return True


def mark_email_verified(user: User) -> User:
    """Record that ``user`` proved control of their mailbox by other means.

    Called after a password reset. The reset link went to the address and came
    back used, which is the same proof the verification link asks for and a
    stronger one than a click: it also changed the credential. Making the
    person then hunt for a second mail to confirm the address they just
    demonstrably read would be theatre.
    """
    if user.is_email_verified:
        return user
    user.email_verified_at = timezone.now()
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


# --- Team management -------------------------------------------------------
#
# Who may work on a company's page. Three rules hold on every path below, and
# each is enforced here rather than in the view, because the admin and the
# shell are callers too:
#
# * only an active owner changes the team;
# * nobody joins a company without accepting an invitation sent to their own
#   mailbox, so a membership always has a consenting person behind it;
# * a claimed page always keeps at least one active owner, or it becomes a
#   page nobody can manage and only a moderator could rescue.

#: Shorter than a verification link's 48 hours would be unhelpful - the invitee
#: may not have an account yet and has to register first - and longer than a
#: week leaves stale keys lying in mailboxes.
INVITE_TTL = timedelta(days=7)


class MembershipError(Exception):
    """A change to a company's team that must not proceed. The message is shown."""


@dataclass(frozen=True)
class IssuedInvite:
    invite: ProviderMemberInvite
    token: str


def _require_owner(actor: User, provider: Provider) -> None:
    from apps.accounts.permissions import is_provider_owner

    if not is_provider_owner(actor, provider):
        raise MembershipError(_("只有该公司的拥有者可以管理成员。"))


def _active_owners(provider: Provider, *, exclude_pk: str | None = None) -> int:
    rows = ProviderMember.objects.filter(
        provider=provider, is_active=True, member_role=MemberRole.OWNER
    )
    if exclude_pk is not None:
        rows = rows.exclude(pk=exclude_pk)
    return rows.count()


@transaction.atomic
def invite_member(
    *, provider: Provider, actor: User, email: str, member_role: str = MemberRole.STAFF
) -> IssuedInvite:
    """Offer someone access to a company's page.

    Nothing is granted here. The row created is an offer; the membership only
    exists once ``accept_invite`` runs against an account that owns the address
    the offer was sent to.
    """
    _require_owner(actor, provider)
    if not provider.is_on_register:
        raise MembershipError(_("该公司已不在官方持牌名单上，页面已锁定。"))
    if member_role not in MemberRole.values:
        raise MembershipError(_("未知的成员角色。"))

    address = email.strip().lower()
    if not address:
        raise MembershipError(_("请填写邮箱地址。"))
    if ProviderMember.objects.filter(
        provider=provider, is_active=True, user__email__iexact=address
    ).exists():
        raise MembershipError(_("该邮箱已经是本公司的成员。"))

    now = timezone.now()
    # Revoking the outstanding offer first keeps the partial unique constraint
    # satisfied, and means a resend leaves exactly one working link.
    ProviderMemberInvite.objects.filter(
        provider=provider, email=address, accepted_at__isnull=True, revoked_at__isnull=True
    ).update(revoked_at=now, updated_at=now)

    token = secrets.token_urlsafe(TOKEN_BYTES)
    invite = ProviderMemberInvite.objects.create(
        provider=provider,
        email=address,
        member_role=member_role,
        invited_by=actor,
        token_hash=hash_token(token),
        expires_at=now + INVITE_TTL,
    )
    _announce_invite(invite, token)
    return IssuedInvite(invite=invite, token=token)


@transaction.atomic
def accept_invite(*, token: str, user: User) -> ProviderMember:
    """Turn an offer into a membership, for the person who was offered it.

    The signed-in account must own the invited address. Without that check the
    link would be a bearer token: forwarded once, and a stranger is publishing
    on a licensed company's page.
    """
    try:
        invite = ProviderMemberInvite.objects.select_for_update().get(token_hash=hash_token(token))
    except ProviderMemberInvite.DoesNotExist as exc:
        raise MembershipError(_("邀请链接无效或已过期。")) from exc

    if not invite.is_open:
        raise MembershipError(_("邀请链接无效或已过期。"))
    if user.email.strip().lower() != invite.email:
        raise MembershipError(_("请用收到邀请的邮箱登录后再接受邀请。"))

    membership, _created = ProviderMember.objects.update_or_create(
        user=user,
        provider_id=invite.provider_id,
        defaults={"member_role": invite.member_role, "is_active": True},
    )
    now = timezone.now()
    invite.accepted_at = now
    invite.accepted_by = user
    invite.save(update_fields=["accepted_at", "accepted_by", "updated_at"])

    # Accepting proves the address: it was the invitation mail that carried the
    # token, and only that mailbox received it.
    if not user.is_email_verified:
        user.email_verified_at = now
        user.save(update_fields=["email_verified_at", "updated_at"])
    if user.role == Role.BUYER:
        user.role = Role.PROVIDER_MEMBER
        user.save(update_fields=["role", "updated_at"])
    return membership


@transaction.atomic
def revoke_invite(*, invite: ProviderMemberInvite, actor: User) -> ProviderMemberInvite:
    """Withdraw an offer that has not been accepted."""
    _require_owner(actor, invite.provider)
    if not invite.is_open:
        raise MembershipError(_("这份邀请已经失效了。"))
    invite.revoked_at = timezone.now()
    invite.save(update_fields=["revoked_at", "updated_at"])
    return invite


@transaction.atomic
def set_member_role(*, membership: ProviderMember, actor: User, member_role: str) -> ProviderMember:
    """Promote a colleague to owner, or step back down to staff.

    This is also how ownership is handed over: promote the successor, then
    demote yourself. Doing it in that order is the point - the last owner
    cannot demote themselves, so no sequence of legal moves ends with a claimed
    page that nobody owns.
    """
    _require_owner(actor, membership.provider)
    if member_role not in MemberRole.values:
        raise MembershipError(_("未知的成员角色。"))
    if not membership.is_active:
        raise MembershipError(_("该成员已停用。"))
    if (
        membership.member_role == MemberRole.OWNER
        and member_role != MemberRole.OWNER
        and _active_owners(membership.provider, exclude_pk=str(membership.pk)) == 0
    ):
        raise MembershipError(_("公司至少要保留一位拥有者，请先指定另一位拥有者。"))

    membership.member_role = member_role
    membership.save(update_fields=["member_role", "updated_at"])
    return membership


@transaction.atomic
def deactivate_member(*, membership: ProviderMember, actor: User) -> ProviderMember:
    """Remove a colleague's access, keeping the record that they had it.

    Deactivation rather than deletion: quotes, replies and profile edits point
    at the member who made them, and a page that loses that trail loses the
    ability to answer "who wrote this".
    """
    _require_owner(actor, membership.provider)
    if not membership.is_active:
        return membership
    if (
        membership.member_role == MemberRole.OWNER
        and _active_owners(membership.provider, exclude_pk=str(membership.pk)) == 0
    ):
        raise MembershipError(_("公司至少要保留一位拥有者，请先指定另一位拥有者。"))

    membership.is_active = False
    membership.save(update_fields=["is_active", "updated_at"])
    _announce_removal(membership)
    return membership


def _announce_invite(invite: ProviderMemberInvite, token: str) -> None:
    """Mail the offer. The raw token is used here and never stored or logged."""
    from django.urls import reverse

    from apps.core.notifications import absolute_url, notify

    notify(
        template="member_invited",
        recipients=[invite.email],
        context={
            "provider_name": invite.provider.display_name,
            "role_label": str(MemberRole(invite.member_role).label),
            "url": absolute_url(reverse("accounts:accept_invite", kwargs={"token": token})),
        },
    )


def _announce_removal(membership: ProviderMember) -> None:
    """Tell the person, not the company - they are the one losing access."""
    from django.urls import reverse

    from apps.core.notifications import absolute_url, notify

    notify(
        template="member_removed",
        recipients=[membership.user.email],
        context={
            "provider_name": membership.provider.display_name,
            "url": absolute_url(reverse("accounts:dashboard")),
        },
    )
