"""Private file storage.

Two storages exist on purpose. ``default`` holds things meant to be seen -
logos, office photos - and may be served straight off a CDN. ``private`` holds
evidence a company or a buyer uploaded about itself: business registration
certificates now, NNC1 documents in P4. COMPLIANCE section 4 makes those
personal data, so nothing in that bucket may be reachable by URL alone.

The rule that follows from it: no template ever calls ``.url`` on a private
file. Access goes through a view that checks who is asking, and only then hands
back a short-lived signed URL (S3) or streams the bytes (local disk).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.files.storage import storages

if TYPE_CHECKING:
    from django.core.files.storage import Storage

PRIVATE_ALIAS = "private"


def private_storage() -> Storage:
    """The storage backend for uploaded evidence.

    A callable rather than a module-level instance: ``FileField(storage=...)``
    resolves it per access, so the concrete backend never ends up frozen into a
    migration and the test settings can swap it for in-memory storage.
    """
    return storages[PRIVATE_ALIAS]


def signed_url(name: str, *, ttl: int | None = None) -> str | None:
    """A time-limited URL for ``name``, or None if the backend cannot make one.

    Local development uses ``FileSystemStorage``, which has no signing and no
    public base URL; returning None there is the honest answer, and the caller
    falls back to streaming the file through a view that has already checked
    permissions.
    """
    storage = private_storage()
    expire = settings.PRIVATE_FILE_URL_TTL if ttl is None else ttl
    try:
        # Accepting ``expire`` is the capability test: django-storages' S3
        # backend signs and takes an expiry, FileSystemStorage does neither.
        # There is deliberately no fallback to a plain ``storage.url(name)`` -
        # that would be a permanent, unauthenticated link to a private file,
        # which is the one thing this module exists to prevent.
        url: Any = storage.url(name, expire=expire)  # type: ignore[call-arg]
    except (TypeError, ValueError, NotImplementedError):
        return None
    return str(url) if url else None
