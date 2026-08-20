"""Admin for accounts.

The user form is Django's, minus ``username``: dropping the field from the
model is not enough, the stock admin forms still reference it.
"""

from typing import Any

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import (
    EmailVerification,
    ProviderMember,
    ProviderMemberInvite,
    User,
)


class EmailUserCreationForm(UserCreationForm):  # type: ignore[type-arg]
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("email",)


class EmailUserChangeForm(UserChangeForm):  # type: ignore[type-arg]
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"


@admin.register(User)
class PlatformUserAdmin(UserAdmin):  # type: ignore[type-arg]
    add_form = EmailUserCreationForm
    form = EmailUserChangeForm
    change_password_form = AdminPasswordChangeForm
    model = User

    list_display = ("email", "role", "is_email_verified", "is_active", "is_staff", "created_at")
    list_filter = ("role", "is_active", "is_staff", "is_superuser")
    search_fields = ("email", "phone")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at", "last_login", "email_verified_at")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            _("Personal info"),
            {"fields": ("first_name", "last_name", "phone", "preferred_language")},
        ),
        (_("Role"), {"fields": ("role",)}),
        (
            _("Permissions"),
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        (_("Dates"), {"fields": ("last_login", "email_verified_at", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "password1", "password2", "role")}),
    )

    @admin.display(boolean=True, description=_("Email verified"))
    def is_email_verified(self, obj: User) -> bool:
        return obj.is_email_verified


@admin.register(EmailVerification)
class EmailVerificationAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Read-only: a token row is evidence of what was sent, not something to edit."""

    list_display = ("email", "user", "expires_at", "used_at", "created_at")
    list_filter = ("used_at",)
    search_fields = ("email",)
    readonly_fields = tuple(f.name for f in EmailVerification._meta.fields)

    def has_add_permission(self, request: Any) -> bool:
        return False

    def has_change_permission(self, request: Any, obj: Any = None) -> bool:
        return False


@admin.register(ProviderMember)
class ProviderMemberAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Who may edit which company page.

    This one table is the platform's entire object-level permission model
    (``permissions.py``), so until it had a screen the only way to answer "who
    can change this page?" - or to cut off a colleague who has left - was a
    psql prompt. Membership is revoked by clearing ``is_active``, never by
    deleting: claims, quotes and decisions point at the member who acted, and
    a deleted row would take that trail with it.
    """

    list_display = (
        "user",
        "provider",
        "member_role",
        "is_active",
        "granted_by_claim",
        "created_at",
    )
    list_filter = ("member_role", "is_active")
    list_select_related = ("user", "provider", "provider__licensee")
    search_fields = ("user__email", "provider__slug", "provider__licensee__name_en")
    autocomplete_fields = ("user", "provider")
    readonly_fields = ("claim", "created_at", "updated_at")
    ordering = ("-created_at",)
    actions = ("deactivate_memberships",)

    @admin.display(description=_("From claim"), boolean=True)
    def granted_by_claim(self, obj: ProviderMember) -> bool:
        """False means a staff member added this by hand - worth seeing in the list."""
        return obj.claim_id is not None

    @admin.action(description=_("Revoke access (keep the record)"))
    def deactivate_memberships(self, request: Any, queryset: Any) -> None:
        updated = queryset.filter(is_active=True).update(is_active=False)
        self.message_user(request, _("Revoked %(count)d membership(s).") % {"count": updated})

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False


@admin.register(ProviderMemberInvite)
class ProviderMemberInviteAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Standing offers of access, read-only.

    Here so that "why does this person have access to that company?" has an
    answer that predates the membership row. Nothing is editable: an invitation
    is a record of what was sent to which mailbox, and the company withdraws
    its own offers from the team page.
    """

    list_display = ("email", "provider", "member_role", "state", "invited_by", "created_at")
    list_filter = ("member_role",)
    list_select_related = ("provider", "invited_by")
    search_fields = ("email", "provider__slug", "provider__licensee__name_en")
    readonly_fields = (*(f.name for f in ProviderMemberInvite._meta.fields), "state")
    ordering = ("-created_at",)

    @admin.display(description=_("State"))
    def state(self, obj: ProviderMemberInvite) -> str:
        return obj.state

    def has_add_permission(self, request: Any) -> bool:
        return False

    def has_change_permission(self, request: Any, obj: Any = None) -> bool:
        return False
