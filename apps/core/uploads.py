"""Validation for files the public uploads.

A claim's evidence arrives from an unauthenticated-until-yesterday account and
is opened later by a moderator, so two things are checked before the bytes are
stored:

* the size, because an unbounded upload is a free denial of service;
* the actual content type, sniffed from the leading bytes rather than trusted
  from the filename or the browser's ``Content-Type``. Both of those are
  attacker-controlled; a magic number is at least evidence about the file.

Sniffing is done here with a small signature table rather than python-magic:
the accepted set is three formats, and a C library plus its Windows binaries is
a large amount of machinery for that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    from collections.abc import Collection

    from django.core.files.uploadedfile import UploadedFile

#: 10 MB. A scan of a phone photo of a BR certificate fits comfortably; a
#: multi-hundred-page PDF does not, and does not need to.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

CONTENT_TYPE_PDF = "application/pdf"
CONTENT_TYPE_JPEG = "image/jpeg"
CONTENT_TYPE_PNG = "image/png"

#: Leading bytes -> content type. Deliberately short: every extra format is
#: another parser a moderator's browser has to open safely.
MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", CONTENT_TYPE_PDF),
    (b"\xff\xd8\xff", CONTENT_TYPE_JPEG),
    (b"\x89PNG\r\n\x1a\n", CONTENT_TYPE_PNG),
)

ALLOWED_CONTENT_TYPES = frozenset({CONTENT_TYPE_PDF, CONTENT_TYPE_JPEG, CONTENT_TYPE_PNG})

#: A logo is displayed in an ``<img>``; a PDF in that position is not a logo,
#: it is a document somebody uploaded into the wrong field.
IMAGE_CONTENT_TYPES = frozenset({CONTENT_TYPE_JPEG, CONTENT_TYPE_PNG})

#: 2 MB. A logo is rendered at 96px on the widest page here, so anything larger
#: is bytes every visitor downloads and nobody sees.
MAX_LOGO_BYTES = 2 * 1024 * 1024

#: What each accepted type is called in the refusal message. Built from the
#: allowed set rather than hard-coded, so a caller that narrows the set is not
#: told "PDF, JPG or PNG" by a message that no longer applies to it.
_TYPE_LABELS = {
    CONTENT_TYPE_PDF: "PDF",
    CONTENT_TYPE_JPEG: "JPG",
    CONTENT_TYPE_PNG: "PNG",
}

EXTENSION_BY_CONTENT_TYPE = {
    CONTENT_TYPE_PDF: "pdf",
    CONTENT_TYPE_JPEG: "jpg",
    CONTENT_TYPE_PNG: "png",
}

#: Label order for the refusal message, so it does not depend on set ordering.
MAGIC_SIGNATURES_ORDER = (CONTENT_TYPE_PDF, CONTENT_TYPE_JPEG, CONTENT_TYPE_PNG)

_SNIFF_BYTES = 16


@dataclass(frozen=True, slots=True)
class InspectedUpload:
    """What was actually uploaded, as opposed to what it claimed to be."""

    content_type: str
    size_bytes: int
    extension: str


def sniff_content_type(head: bytes) -> str | None:
    for signature, content_type in MAGIC_SIGNATURES:
        if head.startswith(signature):
            return content_type
    return None


def inspect_upload(
    upload: UploadedFile[Any],
    *,
    allowed: Collection[str] = ALLOWED_CONTENT_TYPES,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> InspectedUpload:
    """Check size and real content type, or raise ``ValidationError``.

    ``allowed`` and ``max_bytes`` are per-caller because the two things this
    platform accepts are not alike: evidence is a scan of a document a
    moderator opens once, a logo is an image every visitor downloads. Both go
    through this function so that the sniffing has one implementation.

    Leaves the file's read position at the start so the caller can store it.
    """
    size = upload.size or 0
    if size == 0:
        raise ValidationError(_("文件为空，请重新选择。"))
    if size > max_bytes:
        raise ValidationError(
            _("文件不得大于 %(limit)s MB。") % {"limit": max_bytes // (1024 * 1024)}
        )

    upload.seek(0)
    head = upload.read(_SNIFF_BYTES)
    upload.seek(0)

    content_type = sniff_content_type(head)
    if content_type is None or content_type not in allowed:
        # The message names the formats rather than what was detected: telling
        # an uploader precisely how their file was classified is a free oracle
        # for probing the sniffer.
        names = "、".join(_TYPE_LABELS[t] for t in MAGIC_SIGNATURES_ORDER if t in allowed)
        raise ValidationError(_("仅支持 %(formats)s 文件。") % {"formats": names})

    return InspectedUpload(
        content_type=content_type,
        size_bytes=size,
        extension=EXTENSION_BY_CONTENT_TYPE[content_type],
    )
