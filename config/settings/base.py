"""Shared settings. Environment-specific modules import * from here.

Never put secrets or environment-dependent defaults in this file; read them
from the environment via ``env`` so that ``prod.py`` can fail loudly when a
required value is missing.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any

import environ
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

# ---------------------------------------------------------------- core

SECRET_KEY = env("SECRET_KEY", default="insecure-dev-key-do-not-use-in-prod")
DEBUG = False
ALLOWED_HOSTS: list[str] = env.list("ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "django.contrib.sitemaps",
    # third party
    "rest_framework",
    "django_celery_beat",
    # local
    "apps.core",
    "apps.accounts",
    "apps.registry",
    "apps.providers",
    "apps.reviews",
    "apps.rfq",
    "apps.agents",
    "apps.billing",
    "apps.content",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "apps.core.context_processors.compliance",
            ],
        },
    },
]

# ---------------------------------------------------------------- database

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://qs:qs@localhost:5432/qs",
    )
}
DATABASES["default"]["ATOMIC_REQUESTS"] = False
DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------- i18n
# UI copy targets mainland-China buyers -> Simplified Chinese is the default.
# See CLAUDE.md section 6.

LANGUAGE_CODE = "zh-hans"
LANGUAGES = [
    ("zh-hans", "简体中文"),
    ("zh-hant", "繁體中文"),
    ("en", "English"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Asia/Hong_Kong"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------- static / media

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# ---------------------------------------------------------------- celery

CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

REDIS_URL = CELERY_BROKER_URL

# DatabaseScheduler syncs these into django_celery_beat on startup, so the
# schedule stays in version control while remaining editable from the admin.
# CELERY_TIMEZONE is Asia/Hong_Kong, so this is 06:00 HKT (ARCHITECTURE 5).
CELERY_BEAT_SCHEDULE = {
    "sync-tcsp-registry-daily": {
        "task": "registry.sync_tcsp_registry",
        "schedule": crontab(hour=6, minute=0),
    },
}

# ---------------------------------------------------------------- object storage

S3_ENDPOINT_URL = env("S3_ENDPOINT_URL", default="")
S3_BUCKET = env("S3_BUCKET", default="qs-dev")
S3_ACCESS_KEY = env("S3_ACCESS_KEY", default="")
S3_SECRET_KEY = env("S3_SECRET_KEY", default="")
S3_REGION = env("S3_REGION", default="ap-east-1")

# ---------------------------------------------------------------- external services

ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default="")
# CLAUDE.md rule 6: money is Decimal, never float - django-environ has no
# decimal caster, so parse it explicitly.
AGENT_BUDGET_DAILY_USD = Decimal(env("AGENT_BUDGET_DAILY_USD", default="20"))

TCSP_CSV_URL = env(
    "TCSP_CSV_URL",
    default="https://www.tcsp.cr.gov.hk/open-data/licensees.csv",
)

SENTRY_DSN = env("SENTRY_DSN", default="")
TURNSTILE_SITE_KEY = env("TURNSTILE_SITE_KEY", default="")
TURNSTILE_SECRET = env("TURNSTILE_SECRET", default="")

# ---------------------------------------------------------------- domain constants

# COMPLIANCE.md section 1: every page showing registry data must link the source.
REGISTRY_SOURCE_NAME = "香港公司註冊處《信託或公司服務持牌人登記冊》／data.gov.hk"
REGISTRY_SOURCE_URL = "https://www.tcsp.cr.gov.hk/"

# ---------------------------------------------------------------- rest framework

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

# ---------------------------------------------------------------- logging

LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}
