"""Local development settings."""

from .base import *
from .base import INSTALLED_APPS, MIDDLEWARE, env

DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [*INSTALLED_APPS, "debug_toolbar"]
MIDDLEWARE = ["debug_toolbar.middleware.DebugToolbarMiddleware", *MIDDLEWARE]

# django-debug-toolbar inside docker: the request IP is the gateway, not 127.0.0.1.
INTERNAL_IPS = ["127.0.0.1", "localhost"]
DEBUG_TOOLBAR_CONFIG = {"SHOW_TOOLBAR_CALLBACK": lambda request: DEBUG}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
