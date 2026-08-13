"""Shared model primitives.

Every model in this project inherits :class:`BaseModel` (DATA_MODEL.md).
"""

import uuid

import uuid_utils
from django.db import models


def uuid7() -> uuid.UUID:
    """Return a UUIDv7 as a stdlib ``uuid.UUID``.

    UUIDv7 is time-ordered, which keeps B-tree index locality good for
    insert-heavy tables (AgentRun, LicenseeChange). Neither Python 3.12 nor
    Django ships a v7 generator, so this wraps ``uuid_utils`` and converts to
    the stdlib type that Django's UUIDField expects.
    """
    return uuid.UUID(bytes=uuid_utils.uuid7().bytes)


class BaseModel(models.Model):
    """Abstract base: UUIDv7 primary key plus creation/update timestamps."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]
