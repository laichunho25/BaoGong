"""Internal admin. Official registry data is exposed read-only (CLAUDE.md rule 1)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib import admin

if TYPE_CHECKING:
    from django.http import HttpRequest

from apps.registry.models import Licensee, LicenseeChange, SyncRun


# django-stubs types ModelAdmin as generic, but it is not subscriptable at
# runtime on Django 5.1, so the parameter cannot be supplied here.
class ReadOnlyAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """No add, no edit, no delete - the sync pipeline is the only writer."""

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Licensee)
class LicenseeAdmin(ReadOnlyAdmin):
    list_display = ("licence_no", "name_en", "name_zh", "district", "status", "last_seen_at")
    list_filter = ("status", "district")
    search_fields = ("licence_no", "name_en", "name_zh", "business_address")
    date_hierarchy = "last_seen_at"


@admin.register(SyncRun)
class SyncRunAdmin(ReadOnlyAdmin):
    list_display = (
        "started_at",
        "status",
        "row_count",
        "prev_row_count",
        "is_dry_run",
        "finished_at",
    )
    list_filter = ("status", "is_dry_run")
    search_fields = ("checksum", "source_url")


@admin.register(LicenseeChange)
class LicenseeChangeAdmin(ReadOnlyAdmin):
    list_display = ("licence_no", "change_type", "severity", "created_at", "notified_at")
    list_filter = ("change_type", "severity")
    search_fields = ("licence_no",)
