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

EXTENSION_BY_CONTENT_TYPE = {
    CONTENT_TYPE_PDF: "pdf",
    CONTENT_TYPE_JPEG: "jpg",
    CONTENT_TYPE_PNG: "png",
}

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


def inspect_upload(upload: UploadedFile[Any]) -> InspectedUpload:
    """Check size and real content type, or raise ``ValidationError``.

    Leaves the file's read position at the start so the caller can store it.
    """
    size = upload.size or 0
    if size == 0:
        raise ValidationError(_("文件为空，请重新选择。"))
    if size > MAX_UPLOAD_BYTES:
        raise ValidationError(
            _("文件不得大于 %(limit)s MB。") % {"limit": MAX_UPLOAD_BYTES // (1024 * 1024)}
        )

    upload.seek(0)
    head = upload.read(_SNIFF_BYTES)
    upload.seek(0)

    content_type = sniff_content_type(head)
    if content_type is None or content_type not in ALLOWED_CONTENT_TYPES:
        # The message names the formats rather than what was detected: telling
        # an uploader precisely how their file was classified is a free oracle
        # for probing the sniffer.
        raise ValidationError(_("仅支持 PDF、JPG 或 PNG 文件。"))

    return InspectedUpload(
        content_type=content_type,
        size_bytes=size,
        extension=EXTENSION_BY_CONTENT_TYPE[content_type],
    )
