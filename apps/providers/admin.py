"""Internal admin for the platform-side profile.

Unlike ``registry``, these rows are editable: everything here is platform data
that staff or the provider itself supplies. The licensee link is read-only
though - repointing a profile at a different licence would silently move every
review and quote attached to it onto another company.
"""

from __future__ import annotations

from django.contrib import admin

from apps.providers.models import Certification, PriceItem, Provider, ServiceOffering


class ServiceOfferingInline(admin.TabularInline):  # type: ignore[type-arg]
    model = ServiceOffering
    extra = 0
    show_change_link = True


class PriceItemInline(admin.TabularInline):  # type: ignore[type-arg]
    model = PriceItem
    extra = 0


class CertificationInline(admin.TabularInline):  # type: ignore[type-arg]
    model = Certification
    extra = 0


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "slug",
        "claim_status",
        "tier",
        "rating_cached",
        "ranking_score",
        "is_published",
    )
    list_filter = ("claim_status", "tier", "is_published", "commission_agreement")
    search_fields = ("slug", "licensee__licence_no", "licensee__name_en", "licensee__name_zh")
    autocomplete_fields = ("licensee",)
    readonly_fields = (
        "licensee",
        "slug",
        # Denormalised by services.py and reviews; editing them by hand would
        # be overwritten by the next recompute and would look like tampering.
        "rating_cached",
        "rating_count",
        "verified_review_count",
        "profile_completeness",
        "responsiveness_score",
        "ranking_score",
    )
    inlines = [ServiceOfferingInline, CertificationInline]


@admin.register(ServiceOffering)
class ServiceOfferingAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("provider", "category", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("provider__slug",)
    inlines = [PriceItemInline]
