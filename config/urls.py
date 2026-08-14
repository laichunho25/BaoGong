from django.conf import settings
from django.contrib import admin
from django.contrib.sitemaps.views import index as sitemap_index
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path
from django.views.generic import TemplateView

from apps.core.views import healthz, home
from apps.providers.sitemaps import ProviderSitemap, StaticViewSitemap
from apps.providers.views import provider_compare
from apps.registry.views import registry_healthz

# Split into an index plus per-section files: the directory alone is over
# 7,000 URLs, and a single sitemap that grows past the 50,000/50MB limit fails
# silently at the search engine rather than at deploy time.
SITEMAPS = {"static": StaticViewSitemap, "providers": ProviderSitemap}

urlpatterns = [
    path("", home, name="home"),
    path("providers/", include("apps.providers.urls")),
    # Top level rather than under /providers/: the comparison is of providers
    # but it is its own shareable page, and the URL is short enough to retype.
    path("compare/", provider_compare, name="compare"),
    path("healthz", healthz, name="healthz"),
    # Separate from /healthz on purpose: the process can be perfectly alive
    # while the daily register sync has silently stopped running.
    path("healthz/registry", registry_healthz, name="registry_healthz"),
    path(
        "sitemap.xml",
        sitemap_index,
        {"sitemaps": SITEMAPS, "sitemap_url_name": "sitemap_section"},
    ),
    path("sitemap-<section>.xml", sitemap, {"sitemaps": SITEMAPS}, name="sitemap_section"),
    path(
        "robots.txt",
        TemplateView.as_view(template_name="robots.txt", content_type="text/plain"),
        name="robots",
    ),
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
]

if settings.DEBUG:
    urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]
