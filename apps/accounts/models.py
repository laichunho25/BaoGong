"""Accounts: who is using the platform, and in which role.

Fields, constraints, __str__, properties only. No business logic
(ARCHITECTURE section 3) - registration, verification and role changes live in
``services.py``.

Two deliberate choices:

* The login identifier is the email address. Buyers arrive from a search
  engine and will not remember an invented username, and every flow that
  matters (verification, quote notifications, claim decisions) already needs a
  working mailbox.
* Email verification tokens are stored as a SHA-256 hash. A leaked database
  dump must not hand out working account-takeover links.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel

if TYPE_CHECKING:
    from datetime import datetime


class Role(models.TextChoices):
    BUYER = "buyer", _("Buyer")
    PROVIDER_MEMBER = "provider_member", _("Provider member")
    MODERATOR = "moderator", _("Moderator")
    ADMIN = "admin", _("Administrator")


class UserManager(BaseUserManager["User"]):
    """Manager for an email-identified user.

    ``createsuperuser`` and the test helpers both go through here, so the
    normalisation rules live in one place.
    """

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra: Any) -> User:
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra: Any) -> User:
        extra.setdefault("role", Role.BUYER)
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra: Any) -> User:
        extra.setdefault("role", Role.ADMIN)
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        # A superuser created from the command line has proved control of the
        # server, which is a stronger proof than the email loop.
        extra.setdefault("email_verified_at", timezone.now())
        if not extra["is_staff"] or not extra["is_superuser"]:
            raise ValueError("A superuser must have is_staff and is_superuser set.")
        return self._create_user(email, password, **extra)


class User(BaseModel, AbstractUser):
    """Platform account.

    ``username`` is dropped rather than kept unused: leaving it in place would
    let a second, unnormalised identifier accumulate rows that nothing checks.
    """

    username = None  # type: ignore[assignment]
    email = models.EmailField(_("email address"), unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.BUYER, db_index=True)
    # Kept optional: mainland buyers often prefer WeChat or a mainland number
    # that we cannot verify by SMS yet (see ROADMAP tech debt).
    phone = models.CharField(max_length=32, blank=True, default="")
    preferred_language = models.CharField(max_length=10, blank=True, default="")
    email_verified_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    # django-stubs types AbstractUser.objects as the username-based manager;
    # this one is email-based by design, so the override is deliberate.
    objects: ClassVar[UserManager] = UserManager()  # type: ignore[assignment]

    class Meta(BaseModel.Meta):
        verbose_name = _("user")
        verbose_name_plural = _("users")

    def __str__(self) -> str:
        return self.email

    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def is_moderator(self) -> bool:
        """Whether this account may act on the moderation queue.

        Superusers are included so that the first deployment has someone who
        can approve the first claim.
        """
        return self.is_superuser or self.role in {Role.MODERATOR, Role.ADMIN}


class MemberRole(models.TextChoices):
    OWNER = "owner", _("Owner")
    STAFF = "staff", _("Staff")


class ProviderMember(BaseModel):
    """A user's membership of one provider - the whole per-object permission model.

    There is no ACL table. The only object-level question the platform asks is
    "is this user a member of this provider?", and this row answers it in one
    indexed lookup. django-guardian would answer the same question through a
    generic permission table that has to be maintained, migrated and backed up,
    in exchange for a granularity nothing here uses. See ``permissions.py``.

    ``is_active`` rather than deletion: a company that removes a colleague
    still needs the record of who approved what, and reviews and quotes point
    at the acting member.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="provider_memberships")
    # A string reference, not an import: providers already imports accounts
    # indirectly through settings.AUTH_USER_MODEL, and a real import here would
    # close the loop.
    provider = models.ForeignKey(
        "providers.Provider", on_delete=models.CASCADE, related_name="members"
    )
    member_role = models.CharField(
        max_length=16, choices=MemberRole.choices, default=MemberRole.OWNER
    )
    is_active = models.BooleanField(default=True, db_index=True)
    # Which approved claim created this membership. Null for a member added by
    # staff, which is why it is nullable rather than required.
    claim = models.ForeignKey(
        "providers.ProviderClaim",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="granted_memberships",
    )

    class Meta(BaseModel.Meta):
        verbose_name = _("provider member")
        verbose_name_plural = _("provider members")
        constraints = [
            models.UniqueConstraint(
                fields=["user", "provider"], name="accounts_one_membership_per_provider"
            )
        ]
        indexes = [models.Index(fields=["provider", "is_active"])]

    def __str__(self) -> str:
        return f"{self.user.email} @ {self.provider_id}"


class EmailVerification(BaseModel):
    """A single-use email verification link.

    The raw token is returned once, by ``services.issue_email_verification``,
    and never stored. ``used_at`` and ``expires_at`` are separate so that an
    expired-but-unused token can be told apart from a replayed one - they need
    different messages on screen.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="email_verifications")
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    email = models.EmailField(help_text="The address this token was sent to.")
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = _("email verification")
        verbose_name_plural = _("email verifications")

    def __str__(self) -> str:
        return f"{self.email} ({'used' if self.used_at else 'pending'})"

    def is_usable(self, *, now: datetime | None = None) -> bool:
        return self.used_at is None and self.expires_at > (now or timezone.now())


class ProviderMemberInvite(BaseModel):
    """An offer of access to one company's page, sent to one mailbox.

    A company cannot simply attach a colleague's account to itself: the person
    has to sign in and accept. That is not politeness. A membership carries the
    right to publish text on a licensed company's public page and to answer
    reviews in its name, and nobody should acquire that because somebody typed
    their address into a form.

    The token is stored as a SHA-256 hash for the same reason
    ``EmailVerification`` does it: a database dump must not be a bundle of
    working access links.

    ``revoked_at`` rather than deletion - who offered access to whom, and who
    took it back, is exactly the history a disputed page needs.
    """

    provider = models.ForeignKey(
        "providers.Provider", on_delete=models.CASCADE, related_name="member_invites"
    )
    email = models.EmailField(help_text="The address this invitation was sent to.")
    member_role = models.CharField(
        max_length=16, choices=MemberRole.choices, default=MemberRole.STAFF
    )
    invited_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="sent_member_invites"
    )
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accepted_member_invites",
    )
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = _("provider member invitation")
        verbose_name_plural = _("provider member invitations")
        constraints = [
            # One open offer per mailbox per company. Re-inviting revokes the
            # previous offer first, so an address never has two live links.
            models.UniqueConstraint(
                fields=["provider", "email"],
                condition=models.Q(accepted_at__isnull=True, revoked_at__isnull=True),
                name="accounts_one_open_invite_per_email",
            )
        ]
        indexes = [models.Index(fields=["provider", "accepted_at"])]

    def __str__(self) -> str:
        return f"{self.email} -> {self.provider_id} ({self.state})"

    @property
    def state(self) -> str:
        if self.accepted_at:
            return "accepted"
        if self.revoked_at:
            return "revoked"
        return "open" if self.expires_at > timezone.now() else "expired"

    @property
    def is_open(self) -> bool:
        """Still waiting on the invitee, and still within its lifetime."""
        return self.state == "open"
