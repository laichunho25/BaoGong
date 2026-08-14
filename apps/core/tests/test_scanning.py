"""The scanner interface, and the fail-closed default behind it.

The interesting property is negative: nothing in here may turn a file nobody
scanned into a file somebody may open.
"""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING

import pytest
from django.test import override_settings

from apps.core import scanning

if TYPE_CHECKING:
    from collections.abc import Iterator


def _chunks(*parts: bytes) -> Iterator[bytes]:
    return iter(parts)


class TestUnavailableScanner:
    def test_it_reports_pending_and_never_clean(self) -> None:
        result = scanning.UnavailableScanner().scan(_chunks(b"%PDF-1.4"))

        assert result.status == scanning.ScanStatus.PENDING
        assert result.status not in scanning.READABLE_STATUSES

    def test_it_still_reads_the_file(self) -> None:
        # Callers close the handle afterwards and assume it was consumed; a
        # scanner that silently skipped the read would mask a broken backend.
        consumed = []

        def source() -> Iterator[bytes]:
            yield b"one"
            consumed.append(True)
            yield b"two"

        scanning.UnavailableScanner().scan(source())

        assert consumed == [True]


class FakeSocket:
    """Just enough of a socket for the INSTREAM exchange."""

    def __init__(self, reply: bytes) -> None:
        self.reply = reply
        self.sent = b""

    def __enter__(self) -> FakeSocket:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, size: int) -> bytes:
        return self.reply


class TestClamAvScanner:
    @pytest.mark.parametrize(
        ("reply", "status"),
        [
            (b"stream: OK\0", scanning.ScanStatus.CLEAN),
            (b"stream: Eicar-Test-Signature FOUND\0", scanning.ScanStatus.INFECTED),
            (b"stream: something went wrong ERROR\0", scanning.ScanStatus.ERROR),
        ],
    )
    def test_it_maps_clamd_replies(
        self, reply: bytes, status: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeSocket(reply)
        monkeypatch.setattr(socket, "create_connection", lambda *a, **k: fake)

        result = scanning.ClamAvScanner("clamav", 3310).scan(_chunks(b"%PDF-1.4", b""))

        assert result.status == status
        assert fake.sent.startswith(b"zINSTREAM\0")
        # The stream is terminated, or clamd would wait for more data.
        assert fake.sent.endswith(b"\x00\x00\x00\x00")

    def test_an_unreachable_scanner_is_an_error_not_a_pass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(*args: object, **kwargs: object) -> None:
            raise OSError("connection refused")

        monkeypatch.setattr(socket, "create_connection", refuse)

        result = scanning.ClamAvScanner("clamav", 3310).scan(_chunks(b"%PDF"))

        assert result.status == scanning.ScanStatus.ERROR
        assert result.status not in scanning.READABLE_STATUSES


class TestGetScanner:
    def test_the_configured_backend_is_used(self) -> None:
        with override_settings(FILE_SCANNER_BACKEND="apps.core.scanning.ClamAvScanner"):
            assert isinstance(scanning.get_scanner(), scanning.ClamAvScanner)

    def test_the_default_refuses_rather_than_passes(self) -> None:
        assert isinstance(scanning.get_scanner(), scanning.UnavailableScanner)
