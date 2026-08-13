"""Local development settings."""

from typing import Any

from .base import *
from .base import INSTALLED_APPS, MIDDLEWARE, env

DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [*INSTALLED_APPS, "debug_toolbar"]
MIDDLEWARE = ["debug_toolbar.middleware.DebugToolbarMiddleware", *MIDDLEWARE]

# django-debug-toolbar inside docker: the request IP is the gateway, not 127.0.0.1.
INTERNAL_IPS = ["127.0.0.1", "localhost"]


def _show_toolbar(request: Any) -> bool:
    """Read DEBUG off the live settings.

    A lambda closing over the module-level ``DEBUG`` stays True even when a
    test run turns DEBUG off, which makes the toolbar render into test
    responses and reverse a URL namespace that is not registered.
    """
    from django.conf import settings

    return bool(settings.DEBUG)


DEBUG_TOOLBAR_CONFIG = {"SHOW_TOOLBAR_CALLBACK": _show_toolbar}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
