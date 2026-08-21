"""Production settings.

Required secrets have no defaults: a missing value raises ImproperlyConfigured
at import time rather than silently booting with an insecure fallback.

The whole environment is validated up front, in one pass, so a half-configured
deploy reports every missing variable at once. Raising on the first one turned
provisioning into a queue of identical crash-restart cycles, one per secret.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *
from .base import BASE_DIR, env
from .env_spec import SPEC
from .env_spec import problems as _env_problems

DEBUG = False

_values = {var.name: env(var.name, default="") for var in SPEC}
_values["ALLOWED_HOSTS"] = env("ALLOWED_HOSTS", default="")
_values["RENDER_EXTERNAL_HOSTNAME"] = env("RENDER_EXTERNAL_HOSTNAME", default="")

_problems = _env_problems(_values)
if _problems:
    _detail = "\n".join(f"  - {line}" for line in _problems)
    raise ImproperlyConfigured(
        f"Environment is not fit for production, {len(_problems)} problem(s):\n{_detail}"
    )


def _required(name: str) -> str:
    """Read a value already proven present by the check above."""
    return env(name)


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

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = 60
# Render Postgres terminates TLS; refuse to connect in the clear.
DATABASES["default"].setdefault("OPTIONS", {})
DATABASES["default"]["OPTIONS"]["sslmode"] = env("DATABASE_SSLMODE", default="require")

# ---------------------------------------------------------------- admin

# A production console on the default path is found by scanners within hours of
# the first deploy, so the secret prefix is required rather than recommended.
ADMIN_URL = _required("ADMIN_URL").strip("/") + "/"

# Render always fronts the app with its own proxy, so REMOTE_ADDR is the proxy
# and the allowlist has to read the address the proxy appended.
ADMIN_TRUST_PROXY_IP = env.bool("ADMIN_TRUST_PROXY_IP", default=True)

# ---------------------------------------------------------------- mail

# Django's default backend is SMTP on localhost:25, and a Render container has
# no MTA - so leaving this unset does not disable mail, it makes every send
# raise ConnectionRefusedError inside a Celery task where nobody is looking.
# Email is not a side channel here: an account cannot be verified and a
# colleague cannot join a company without a link that arrived in a mailbox.
# So the credential is required at boot like any other, and a deploy that
# would come up unable to send simply does not come up.
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST", default="smtp.resend.com")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="resend")
EMAIL_HOST_PASSWORD = _required("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=20)

if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise ImproperlyConfigured("EMAIL_USE_TLS and EMAIL_USE_SSL are mutually exclusive")

# The sender has to be a domain the provider has verified. The base default
# ends in example.com, which is accepted by nothing and would bounce every
# message, so prod refuses to inherit it.
DEFAULT_FROM_EMAIL = _required("DEFAULT_FROM_EMAIL")
SERVER_EMAIL = env("SERVER_EMAIL", default=DEFAULT_FROM_EMAIL)

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
    # A separate bucket, not a prefix in the public one: personal data should
    # be one bucket policy away from the world, not one path mistake.
    "private": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env("S3_PRIVATE_BUCKET"),
            "endpoint_url": env("S3_ENDPOINT_URL", default=None),
            "access_key": env("S3_ACCESS_KEY"),
            "secret_key": env("S3_SECRET_KEY"),
            "region_name": env("S3_REGION", default="ap-east-1"),
            "default_acl": "private",
            "querystring_auth": True,
            "querystring_expire": env.int("PRIVATE_FILE_URL_TTL", default=300),
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
