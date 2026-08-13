from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from apps.core.views import healthz, home
from apps.registry.views import registry_healthz

urlpatterns = [
    path("", home, name="home"),
    path("healthz", healthz, name="healthz"),
    # Separate from /healthz on purpose: the process can be perfectly alive
    # while the daily register sync has silently stopped running.
    path("healthz/registry", registry_healthz, name="registry_healthz"),
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
]

if settings.DEBUG:
    urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]
