# Internal moderation/admin interfaces.
"""The education library's editing desk.

Writing goes through ``services.save_article`` rather than through the model,
because an article's citable passages are derived from its body and the rule
that they stay in step must not depend on which screen made the edit
(ARCHITECTURE section 3).

Chunks are shown read-only. They are not content anybody types; they are what
the Advisor agent is allowed to quote, and an editable copy of a derived thing
is a copy that will one day disagree with what it was derived from.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from apps.content import services
from apps.content.models import Article, Chunk

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import HttpRequest

    from apps.accounts.models import User


class ChunkInline(admin.TabularInline):  # type: ignore[type-arg]
    model = Chunk
    extra = 0
    can_delete = False
    fields = ("ordinal", "heading", "text")
    readonly_fields = ("ordinal", "heading", "text")

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("title_zh_hans", "category", "status", "published_at", "chunk_count")
    list_filter = ("status", "category")
    search_fields = ("slug", "title_zh_hans", "summary", "body_md")
    prepopulated_fields = {"slug": ("title_en",)}
    readonly_fields = ("created_at", "updated_at")
    inlines = [ChunkInline]
    actions = ["publish", "unpublish"]
    fieldsets = (
        (None, {"fields": ("slug", "category", "status", "published_at", "author")}),
        (
            _("Titles"),
            {
                "fields": ("title_zh_hans", "title_zh_hant", "title_en"),
                "description": _("Simplified Chinese is required; the other two fall back to it."),
            },
        ),
        (_("Body"), {"fields": ("summary", "body_md")}),
        (_("SEO"), {"fields": ("seo",), "classes": ("collapse",)}),
        (_("Timestamps"), {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description=_("Passages"))
    def chunk_count(self, obj: Article) -> int:
        return obj.chunks.count()

    def save_model(self, request: HttpRequest, obj: Article, form: Any, change: bool) -> None:
        services.save_article(obj, author=cast("User", request.user))

    @admin.action(description=_("Publish selected articles"))
    def publish(self, request: HttpRequest, queryset: QuerySet[Article]) -> None:
        published = 0
        for article in queryset:
            try:
                services.publish_article(article, actor=cast("User", request.user))
            except services.ArticleError as exc:
                self.message_user(request, f"{article.slug}: {exc}", level=messages.ERROR)
            else:
                published += 1
        if published:
            self.message_user(request, _("%(count)s article(s) published.") % {"count": published})

    @admin.action(description=_("Move selected articles back to draft"))
    def unpublish(self, request: HttpRequest, queryset: QuerySet[Article]) -> None:
        for article in queryset:
            services.unpublish_article(article)
        self.message_user(
            request, _("%(count)s article(s) back to draft.") % {"count": queryset.count()}
        )
