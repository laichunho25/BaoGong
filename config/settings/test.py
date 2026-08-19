"""Test settings: fast, hermetic, no outbound network."""

from .base import *
from .base import LOGGING, env

DEBUG = False
ALLOWED_HOSTS = ["*", "testserver"]
SECRET_KEY = "test-only-key"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# A non-default prefix, so the suite exercises the same arrangement production
# runs: the URLs are built at import time and override_settings cannot re-mount
# them, which is exactly why this is pinned here rather than per-test.
ADMIN_URL = "baogong-ops-console/"

DATABASES = {
    "default": env.db(
        "DATABASE_URL", default="postgres://baogong:baogong@localhost:5432/baogong_test"
    )
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Left on: the point of most notification tests is that a decision produces a
# mail with the moderator's reason in it, and a suite that ships with them
# muted would pass on the day someone deletes the dispatch.
NOTIFICATIONS_ENABLED = True
SITE_URL = "https://testserver.example"

# Pinned: the free allowance is the product's pricing, and the tests that prove
# the fourth quote of the day is refused would otherwise pass or fail depending
# on a developer's .env.
RFQ_FREE_QUOTES_PER_DAY = 3
RFQ_OPEN_DAYS = 14

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "private": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Agents must never reach the real API from a test run. The key is a decoy: it
# is the switch below that stops the call, so that a test which forgets to
# patch the client falls back to rules instead of opening a socket. Tests that
# exercise the model path turn AGENTS_ENABLED on and patch `_client`.
ANTHROPIC_API_KEY = "test-key-not-real"
AGENTS_ENABLED = False

# The gate is off by default so the suite does not depend on whether the
# developer's .env happens to carry a Cloudflare key; the tests that exercise
# it switch it on explicitly.
TURNSTILE_SITE_KEY = ""
TURNSTILE_SECRET = ""

LOGGING["root"]["level"] = "WARNING"
