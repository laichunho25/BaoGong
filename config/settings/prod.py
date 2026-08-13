"""Production settings.

Required secrets have no defaults: a missing value raises ImproperlyConfigured
at import time rather than silently booting with an insecure fallback.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *
from .base import BASE_DIR, env

DEBUG = False


def _required(name: str) -> str:
    value = env(name, default="")
    if not value:
        raise ImproperlyConfigured(f"Missing required environment variable: {name}")
    return value


SECRET_KEY = _required("SECRET_KEY")

# Render injects RENDER_EXTERNAL_HOSTNAME for every web service. Trusting it
# automatically means a fresh deploy or a renamed service does not 400 on every
# request before someone remembers to update ALLOWED_HOSTS by hand. Custom
# domains still have to be listed explicitly.
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

RENDER_EXTERNAL_HOSTNAME = env("RENDER_EXTERNAL_HOSTNAME", default="")
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS = [*ALLOWED_HOSTS, RENDER_EXTERNAL_HOSTNAME]
    CSRF_TRUSTED_ORIGINS = [*CSRF_TRUSTED_ORIGINS, f"https://{RENDER_EXTERNAL_HOSTNAME}"]

if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS is empty and RENDER_EXTERNAL_HOSTNAME is unset")

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = 60
# Render Postgres terminates TLS; refuse to connect in the clear.
DATABASES["default"].setdefault("OPTIONS", {})
DATABASES["default"]["OPTIONS"]["sslmode"] = env("DATABASE_SSLMODE", default="require")

for _name in ("REDIS_URL", "ANTHROPIC_API_KEY", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
    _required(_name)

# ---------------------------------------------------------------- security

SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# ---------------------------------------------------------------- storage

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env("S3_BUCKET"),
            "endpoint_url": env("S3_ENDPOINT_URL", default=None),
            "access_key": env("S3_ACCESS_KEY"),
            "secret_key": env("S3_SECRET_KEY"),
            "region_name": env("S3_REGION", default="ap-east-1"),
            "default_acl": "private",
            "querystring_auth": True,
            "file_overwrite": False,
        },
    },
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------------------------------------------------------- observability

if env("SENTRY_DSN", default=""):
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=env("SENTRY_DSN"),
        integrations=[DjangoIntegration(), CeleryIntegration()],
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.1),
        send_default_pii=False,  # PDPO: never ship user PII to a third party.
    )
