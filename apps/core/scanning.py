"""Virus scanning for uploaded files, as a pluggable backend.

The scanner is deliberately an interface with a *refusing* default. Until a
real scanner is wired up, ``UnavailableScanner`` reports ``pending``, and the
callers treat pending exactly like unscanned: the file is not previewed, not
served, and cannot carry a claim to approval. The failure mode is a stuck
queue, which someone notices, rather than a moderator's browser opening an
unscanned PDF, which nobody notices until it matters.

``ClamAvScanner`` speaks clamd's INSTREAM protocol directly over a socket. That
is a small, stable wire format, and it avoids a dependency whose only job is to
frame four-byte lengths.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from django.conf import settings
from django.db import models
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.db.models.fields.files import FieldFile

CHUNK_SIZE = 64 * 1024
CLAMAV_TIMEOUT = 30


class ScanStatus(models.TextChoices):
    """State of one uploaded file.

    ``SKIPPED`` exists for the case where a moderator has verified a file by
    other means and records that decision; it is an audited override, never a
    default. See ``providers.services.approve_claim``.
    """

    PENDING = "pending", _("Scan pending")
    CLEAN = "clean", _("Clean")
    INFECTED = "infected", _("Infected")
    ERROR = "error", _("Scan error")
    SKIPPED = "skipped", _("Scan skipped by a reviewer")


#: The only states in which a stored file may be opened or served.
READABLE_STATUSES = frozenset({ScanStatus.CLEAN, ScanStatus.SKIPPED})


@dataclass(frozen=True, slots=True)
class ScanResult:
    status: str
    detail: str = ""
    scanner: str = ""


class Scanner(Protocol):
    """What ``services`` needs from a scanner. Backends implement only this."""

    name: str

    def scan(self, chunks: Iterator[bytes]) -> ScanResult: ...


class UnavailableScanner:
    """The default: reports every file as still waiting for a scan.

    Not a no-op that says "clean" - that would silently turn the security
    requirement off the moment the setting was forgotten.
    """

    name = "unavailable"

    def scan(self, chunks: Iterator[bytes]) -> ScanResult:
        for _chunk in chunks:  # Drain, so callers can rely on the file being read.
            pass
        return ScanResult(
            status=ScanStatus.PENDING,
            detail="No scanner configured (FILE_SCANNER_BACKEND).",
            scanner=self.name,
        )


class ClamAvScanner:
    """clamd over TCP, INSTREAM."""

    name = "clamav"

    def __init__(self, host: str | None = None, port: int | None = None) -> None:
        self.host = host or settings.CLAMAV_HOST
        self.port = port or settings.CLAMAV_PORT

    def scan(self, chunks: Iterator[bytes]) -> ScanResult:
        try:
            reply = self._instream(chunks)
        except OSError as exc:
            # An unreachable scanner is an error, never a pass: the file stays
            # unreadable and the claim stays blocked.
            return ScanResult(status=ScanStatus.ERROR, detail=str(exc), scanner=self.name)

        if reply.endswith("OK"):
            return ScanResult(status=ScanStatus.CLEAN, detail=reply, scanner=self.name)
        if reply.endswith("FOUND"):
            return ScanResult(status=ScanStatus.INFECTED, detail=reply, scanner=self.name)
        return ScanResult(status=ScanStatus.ERROR, detail=reply, scanner=self.name)

    def _instream(self, chunks: Iterator[bytes]) -> str:
        with socket.create_connection((self.host, self.port), timeout=CLAMAV_TIMEOUT) as sock:
            sock.sendall(b"zINSTREAM\0")
            for chunk in chunks:
                if not chunk:
                    continue
                sock.sendall(struct.pack("!L", len(chunk)) + chunk)
            sock.sendall(struct.pack("!L", 0))  # Zero-length frame ends the stream.
            return sock.recv(4096).decode("utf-8", "replace").strip("\0\n ")


def get_scanner() -> Scanner:
    """Instantiate the configured backend."""
    scanner: Scanner = import_string(settings.FILE_SCANNER_BACKEND)()
    return scanner


def scan_file(file: FieldFile) -> ScanResult:
    """Run the configured scanner over a stored file.

    Streamed in chunks rather than read whole: an uploaded file is attacker
    sized, and the worker that scans it should not be the thing that decides
    how much memory that is worth.
    """
    with file.open("rb") as handle:
        return get_scanner().scan(iter(lambda: handle.read(CHUNK_SIZE), b""))
