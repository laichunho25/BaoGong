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
    # Not "django.contrib.admin": this config swaps in the hardened AdminSite
    # (apps.core.admin_site) as the default site, so every @admin.register in
    # the project lands on it without each app importing a custom site.
    "apps.core.admin_site.HardenedAdminConfig",
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
    # After AuthenticationMiddleware: the gate reads request.user.
    "apps.core.middleware.AdminAccessMiddleware",
]

# ---------------------------------------------------------------- admin
# The console is mounted on a secret prefix rather than /admin/ so that a
# scanner probing the default path gets an ordinary 404. prod.py refuses to
# boot while this is still the default. See apps/core/admin_site.py.
ADMIN_ENABLED = env.bool("ADMIN_ENABLED", default=True)
# Falls back rather than accepting "": an empty prefix would mount the console
# at the site root and make the middleware gate every request.
ADMIN_URL = (env("ADMIN_URL", default="admin/").strip("/") or "admin") + "/"
# Optional second lock: when set, only these addresses may reach the console.
ADMIN_IP_ALLOWLIST: list[str] = env.list("ADMIN_IP_ALLOWLIST", default=[])
# Off by default: X-Forwarded-For is client-controlled unless a proxy we own
# appends to it. prod.py turns it on because Render always fronts the app.
ADMIN_TRUST_PROXY_IP = env.bool("ADMIN_TRUST_PROXY_IP", default=False)

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
        default="postgres://baogong:baogong@localhost:5432/baogong",
    )
}
DATABASES["default"]["ATOMIC_REQUESTS"] = False
DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------- auth
# The login identifier is the email address: buyers arrive from a search
# engine and would not remember an invented username, and every flow that
# matters already needs a working mailbox.
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:dashboard"
LOGOUT_REDIRECT_URL = "/"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="包公 BaoGong <no-reply@example.com>")

# Notification mail is sent from workers, which have no request to build links
# from. A relative link in a mail is a dead link.
SITE_URL = env("SITE_URL", default="http://localhost:8000")

# The switch is for data work, not for production: a backfill that re-decides
# a thousand rows should not mail a thousand people about decisions that were
# already communicated months ago.
NOTIFICATIONS_ENABLED = env.bool("NOTIFICATIONS_ENABLED", default=True)

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
    # Uploaded evidence (claim documents now, NNC1 in P4). Separate from
    # "default" because COMPLIANCE section 4 makes it personal data: it must
    # never be reachable by URL alone. See apps/core/storage.py.
    "private": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": str(BASE_DIR / "private_media")},
    },
}

# Lifetime of a signed evidence URL. Long enough to open a PDF, short enough
# that a link pasted into a chat has expired by the time anyone reads it.
PRIVATE_FILE_URL_TTL = env.int("PRIVATE_FILE_URL_TTL", default=300)

# Virus scanning for uploads. The default reports "pending", which keeps files
# unreadable and blocks claim approval - a scanner that is not configured must
# fail closed, not silently pass everything. See apps/core/scanning.py.
FILE_SCANNER_BACKEND = env("FILE_SCANNER_BACKEND", default="apps.core.scanning.UnavailableScanner")
CLAMAV_HOST = env("CLAMAV_HOST", default="clamav")
CLAMAV_PORT = env.int("CLAMAV_PORT", default=3310)

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
    # Half an hour after the sync: long enough for a 7,457-row run to finish,
    # and deliberately a separate task so a failure here cannot roll back the
    # mirror of the official file.
    "backfill-provider-pages-daily": {
        "task": "providers.backfill_providers",
        "schedule": crontab(hour=6, minute=30),
    },
    # COMPLIANCE section 4: uploaded evidence is personal data with a retention
    # limit, and a retention promise nobody executes is worse than none.
    "purge-claim-evidence-daily": {
        "task": "providers.purge_claim_evidence",
        "schedule": crontab(hour=3, minute=30),
    },
    # Same promise, different documents. An NNC1 carries more personal data
    # than a BR certificate does, so if either purge is going to be forgotten
    # it must not be this one.
    "purge-nnc1-documents-daily": {
        "task": "reviews.purge_nnc1_documents",
        "schedule": crontab(hour=3, minute=45),
    },
    # Hourly, not daily: companies pay for the right to answer requests, so a
    # request that died at 09:00 must not still be costing anybody a quote at
    # 23:00. The sweep is a bulk update over an indexed column.
    "expire-open-rfqs-hourly": {
        "task": "rfq.expire_open_rfqs",
        "schedule": crontab(minute=5),
    },
    # A quote states its own validity period. Once it passes, leaving the offer
    # on the buyer's screen as a live option misrepresents the company that
    # made it.
    "expire-stale-quotes-hourly": {
        "task": "rfq.expire_stale_quotes",
        "schedule": crontab(minute=10),
    },
}

# ---------------------------------------------------------------- claims

# How long a decided claim's evidence is kept. COMPLIANCE section 4 sets 90
# days for NNC1 uploads; the same clock is applied here because the documents
# are the same kind of data.
CLAIM_EVIDENCE_RETENTION_DAYS = env.int("CLAIM_EVIDENCE_RETENTION_DAYS", default=90)
# COMPLIANCE section 4 states 90 days for NNC1 uploads outright. Configurable
# only downwards in practice: a longer window would need a reason in the PICS.
NNC1_RETENTION_DAYS = env.int("NNC1_RETENTION_DAYS", default=90)
# COMPLIANCE section 3: a company's dispute against a review is handled within
# five working days. Settable so the promise and the queue's deadline can never
# drift apart - if the published commitment changes, this is the one place.
DISPUTE_SLA_BUSINESS_DAYS = env.int("DISPUTE_SLA_BUSINESS_DAYS", default=5)
# PRD section 3.7: the free allowance runs on a monthly clock and the paid ones
# on a daily clock - that gap is what a subscription buys. The numbers are the
# product's pricing, so they live in one place rather than in the service that
# spends them; the rule that reads them is ``apps.rfq.allowances``.
RFQ_FREE_QUOTES_PER_MONTH = env.int("RFQ_FREE_QUOTES_PER_MONTH", default=5)
RFQ_QUOTES_PER_DAY_VERIFIED = env.int("RFQ_QUOTES_PER_DAY_VERIFIED", default=5)
RFQ_QUOTES_PER_DAY_PREMIUM = env.int("RFQ_QUOTES_PER_DAY_PREMIUM", default=20)
# How long a request stays on the wall. Companies spend a scarce quota to
# answer, so a request nobody is waiting on any more has to stop costing them.
RFQ_OPEN_DAYS = env.int("RFQ_OPEN_DAYS", default=14)
# Prefix of the DNS TXT record / meta tag value a company publishes to prove it
# controls the website it claims.
CLAIM_SITE_VERIFICATION_KEY = "baogong-site-verification"
# Ceiling on the page fetched during website verification: the response is
# attacker-chosen, so it is read with a limit rather than into memory whole.
CLAIM_VERIFICATION_MAX_BYTES = 512 * 1024
CLAIM_VERIFICATION_TIMEOUT = env.int("CLAIM_VERIFICATION_TIMEOUT", default=10)

# ---------------------------------------------------------------- object storage

S3_ENDPOINT_URL = env("S3_ENDPOINT_URL", default="")
S3_BUCKET = env("S3_BUCKET", default="baogong-dev")
S3_ACCESS_KEY = env("S3_ACCESS_KEY", default="")
S3_SECRET_KEY = env("S3_SECRET_KEY", default="")
S3_REGION = env("S3_REGION", default="ap-east-1")

# ---------------------------------------------------------------- external services

ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default="")
# CLAUDE.md rule 6: money is Decimal, never float - django-environ has no
# decimal caster, so parse it explicitly.
AGENT_BUDGET_DAILY_USD = Decimal(env("AGENT_BUDGET_DAILY_USD", default="20"))

# COMPLIANCE section 8 requires every agent to be switchable off. The global
# one first, then one per agent; a switched-off agent runs its rule-based
# fallback, so turning one off degrades the platform rather than breaking it.
AGENTS_ENABLED = env.bool("AGENTS_ENABLED", default=True)
AGENT_ENABLED_REVIEW_MODERATION = env.bool("AGENT_ENABLED_REVIEW_MODERATION", default=True)
AGENT_ENABLED_NNC1_EXTRACTION = env.bool("AGENT_ENABLED_NNC1_EXTRACTION", default=True)
AGENT_ENABLED_RFQ_INTAKE = env.bool("AGENT_ENABLED_RFQ_INTAKE", default=True)
AGENT_ENABLED_QUOTE_ANALYSIS = env.bool("AGENT_ENABLED_QUOTE_ANALYSIS", default=True)
AGENT_ENABLED_MATCHING = env.bool("AGENT_ENABLED_MATCHING", default=True)
AGENT_ENABLED_ADVISOR = env.bool("AGENT_ENABLED_ADVISOR", default=True)
AGENT_ENABLED_REGISTRY_DIFF = env.bool("AGENT_ENABLED_REGISTRY_DIFF", default=True)

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
