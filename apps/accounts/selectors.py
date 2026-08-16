"""Account reads."""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.accounts.models import EmailVerification, ProviderMember, Role, User

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from apps.providers.models import Provider


def moderators() -> QuerySet[User]:
    """Everyone who may act on the moderation queue."""
    return User.objects.filter(role__in=[Role.MODERATOR, Role.ADMIN], is_active=True)


def pending_verifications(user: User) -> QuerySet[EmailVerification]:
    return EmailVerification.objects.filter(user=user, used_at__isnull=True)


def provider_member_emails(provider: Provider) -> list[str]:
    """Who to write to when something happens to a company's page.

    Only active members, and only members - the person who submitted a claim
    that was refused is not on this list, because they were never confirmed to
    speak for the company. A company with no members yet returns an empty list,
    which is a normal state for an unclaimed page and not an error.
    """
    return list(
        ProviderMember.objects.filter(provider=provider, is_active=True)
        .exclude(user__email="")
        .values_list("user__email", flat=True)
    )
