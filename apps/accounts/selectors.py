"""Account reads."""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.accounts.models import EmailVerification, Role, User

if TYPE_CHECKING:
    from django.db.models import QuerySet


def moderators() -> QuerySet[User]:
    """Everyone who may act on the moderation queue."""
    return User.objects.filter(role__in=[Role.MODERATOR, Role.ADMIN], is_active=True)


def pending_verifications(user: User) -> QuerySet[EmailVerification]:
    return EmailVerification.objects.filter(user=user, used_at__isnull=True)
