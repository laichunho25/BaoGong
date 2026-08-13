"""Template context available on every page."""

from django.conf import settings
from django.http import HttpRequest


def compliance(request: HttpRequest) -> dict[str, str]:
    """Attribution values required by COMPLIANCE.md section 1 on every page."""
    return {
        "registry_source_name": settings.REGISTRY_SOURCE_NAME,
        "registry_source_url": settings.REGISTRY_SOURCE_URL,
    }
