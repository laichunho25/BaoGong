"""What production needs from the environment, in one place.

Deliberately importable on its own: no Django, no settings, no third-party
imports. The question this answers - "which variables are missing?" - has to be
answerable on a box where ``config.settings.prod`` cannot even be imported,
which is exactly the situation the answer is needed in.

``prod.py`` reads this to fail once with the whole list instead of one variable
per deploy, and ``scripts/check_env.py`` reads it to print the same list before
anything boots.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

# The dev fallback in base.py. Set explicitly in prod it would be worse than
# unset: every session cookie in the world is forgeable with a value from git.
DEV_SECRET_KEY = "insecure-dev-key-do-not-use-in-prod"


@dataclass(frozen=True)
class EnvVar:
    """One environment variable and the reason production insists on it."""

    name: str
    why: str
    required: bool = True
    #: Returns a complaint when the value is present but unusable, else None.
    validate: Callable[[str], str | None] | None = None


def _check_secret_key(value: str) -> str | None:
    if value == DEV_SECRET_KEY:
        return "is the development fallback key, which is public in the repository"
    if len(value) < 32:
        return "is shorter than 32 characters"
    return None


def _check_admin_url(value: str) -> str | None:
    if value.strip("/") == "admin":
        return "is the default console path, which scanners find within hours"
    return None


def _check_from_email(value: str) -> str | None:
    if "example.com" in value:
        return "still points at example.com, so every message would bounce"
    return None


SPEC: tuple[EnvVar, ...] = (
    EnvVar(
        "SECRET_KEY",
        "signs sessions, CSRF tokens and password reset links",
        validate=_check_secret_key,
    ),
    EnvVar("DATABASE_URL", "the Postgres connection string"),
    EnvVar("REDIS_URL", "Celery broker and result backend"),
    EnvVar(
        "ANTHROPIC_API_KEY",
        "every AI agent call; no key means no matching, moderation or extraction",
    ),
    EnvVar("ADMIN_URL", "secret prefix for the admin console", validate=_check_admin_url),
    EnvVar("S3_BUCKET", "public object storage (logos, article images)"),
    EnvVar("S3_PRIVATE_BUCKET", "encrypted storage for NNC1 uploads and claim evidence"),
    EnvVar("S3_ACCESS_KEY", "object storage credential"),
    EnvVar("S3_SECRET_KEY", "object storage credential"),
    EnvVar("EMAIL_HOST_PASSWORD", "SMTP credential; without it no account can be verified"),
    EnvVar(
        "DEFAULT_FROM_EMAIL",
        "sender on a domain the mail provider has verified",
        validate=_check_from_email,
    ),
    # Optional: the app boots without these, in a reduced state worth naming.
    EnvVar(
        "SENTRY_DSN", "error reporting; unset means failures are only in the logs", required=False
    ),
    EnvVar("TURNSTILE_SITE_KEY", "bot protection on public forms", required=False),
    EnvVar("TURNSTILE_SECRET", "bot protection on public forms", required=False),
    EnvVar("S3_ENDPOINT_URL", "non-AWS object storage endpoint; unset means AWS", required=False),
    EnvVar(
        "FILE_SCANNER_BACKEND",
        "virus scanning; unset means every upload stays unapprovable by design",
        required=False,
    ),
)

REQUIRED: tuple[EnvVar, ...] = tuple(v for v in SPEC if v.required)
OPTIONAL: tuple[EnvVar, ...] = tuple(v for v in SPEC if not v.required)


def missing(values: Mapping[str, str], *, include_optional: bool = False) -> list[EnvVar]:
    """Variables that are absent or blank, in declaration order."""
    pool = SPEC if include_optional else REQUIRED
    return [v for v in pool if not values.get(v.name, "").strip()]


def invalid(values: Mapping[str, str]) -> list[tuple[EnvVar, str]]:
    """Variables that are set but hold a value production refuses."""
    found: list[tuple[EnvVar, str]] = []
    for var in SPEC:
        value = values.get(var.name, "").strip()
        if not value or var.validate is None:
            continue
        complaint = var.validate(value)
        if complaint is not None:
            found.append((var, complaint))
    return found


def problems(values: Mapping[str, str]) -> list[str]:
    """Every reason production would refuse to boot, as readable lines.

    Cross-field rules live here too: a host list is only missing if Render did
    not hand us one either.
    """
    lines = [f"{var.name} is not set - {var.why}" for var in missing(values)]
    lines += [f"{var.name} {complaint}" for var, complaint in invalid(values)]

    hosts = values.get("ALLOWED_HOSTS", "").strip()
    render_host = values.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
    if not hosts and not render_host:
        lines.append(
            "ALLOWED_HOSTS is empty and RENDER_EXTERNAL_HOSTNAME is unset - "
            "Django would answer 400 to every request"
        )
    return lines
