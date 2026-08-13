from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from apps.core.views import healthz

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
]

if settings.DEBUG:
    urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]
