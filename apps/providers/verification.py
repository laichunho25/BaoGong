"""Proving that a claimant controls the company's website.

The applicant publishes ``qs-site-verification=<token>`` in one of three
places, and any one of them is accepted:

* a DNS TXT record on the domain - the strongest, since it needs registrar or
  DNS control and cannot be set by someone who merely has a page on the site;
* ``/.well-known/qs-site-verification.txt`` - easy for a company whose site is
  managed by an agency that will upload a file but not edit templates;
* a ``<meta name="qs-site-verification" content="...">`` tag on the homepage.

This is evidence for a human reviewer, not an approval. A verified website says
the applicant controls that domain; it does not say the domain belongs to the
licensee, which is what the moderator is there to judge.

Network I/O only - no ORM writes. ``services.verify_claim_website`` records the
result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import requests
from django.conf import settings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

METHOD_DNS_TXT = "dns_txt"
METHOD_WELL_KNOWN = "well_known"
METHOD_META_TAG = "meta_tag"

WELL_KNOWN_PATH = "/.well-known/qs-site-verification.txt"

# Attribute order varies between site builders, so the tag is matched by name
# and content independently rather than as one fixed string.
_META_TAG = re.compile(
    rb"""<meta[^>]*name\s*=\s*["']qs-site-verification["'][^>]*"""
    rb"""content\s*=\s*["']([^"']+)["'][^>]*>""",
    re.IGNORECASE,
)
_META_TAG_REVERSED = re.compile(
    rb"""<meta[^>]*content\s*=\s*["']([^"']+)["'][^>]*"""
    rb"""name\s*=\s*["']qs-site-verification["'][^>]*>""",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class VerificationAttempt:
    method: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class VerificationOutcome:
    verified: bool
    method: str
    attempts: tuple[VerificationAttempt, ...]

    def as_log(self) -> list[dict[str, object]]:
        """JSON-serialisable trail for the claim, so a reviewer can see what was tried."""
        return [{"method": a.method, "ok": a.ok, "detail": a.detail[:200]} for a in self.attempts]


def domain_of(website: str) -> str:
    """Hostname of ``website``, without ``www.`` or a port."""
    candidate = website.strip()
    if "//" not in candidate:
        candidate = f"https://{candidate}"
    host = (urlparse(candidate).hostname or "").lower()
    return host.removeprefix("www.")


def _check_dns_txt(domain: str, expected: str) -> VerificationAttempt:
    try:
        import dns.resolver
    except ImportError:  # pragma: no cover - dnspython is a declared dependency
        return VerificationAttempt(METHOD_DNS_TXT, False, "dnspython is not installed")

    try:
        answers = dns.resolver.resolve(domain, "TXT", lifetime=settings.CLAIM_VERIFICATION_TIMEOUT)
    except Exception as exc:
        return VerificationAttempt(METHOD_DNS_TXT, False, f"{type(exc).__name__}: {exc}")

    for record in answers:
        # A TXT record is a sequence of strings; long values arrive split.
        value = b"".join(getattr(record, "strings", [])).decode("utf-8", "replace")
        if value.strip() == expected:
            return VerificationAttempt(METHOD_DNS_TXT, True, "TXT record matched")
    return VerificationAttempt(METHOD_DNS_TXT, False, "No matching TXT record")


def _fetch(url: str) -> bytes:
    """GET ``url``, capped. The response is chosen by the applicant, so it is
    read with a byte limit instead of into memory whole."""
    response = requests.get(
        url,
        timeout=settings.CLAIM_VERIFICATION_TIMEOUT,
        headers={"User-Agent": "QSMatchingSiteVerification/1.0"},
        stream=True,
        allow_redirects=True,
    )
    response.raise_for_status()
    body = b""
    for chunk in response.iter_content(8192):
        body += chunk
        if len(body) >= settings.CLAIM_VERIFICATION_MAX_BYTES:
            break
    return body[: settings.CLAIM_VERIFICATION_MAX_BYTES]


def _check_well_known(domain: str, expected: str) -> VerificationAttempt:
    try:
        body = _fetch(f"https://{domain}{WELL_KNOWN_PATH}")
    except Exception as exc:
        return VerificationAttempt(METHOD_WELL_KNOWN, False, f"{type(exc).__name__}: {exc}")
    if expected.encode() in body:
        return VerificationAttempt(METHOD_WELL_KNOWN, True, "File contained the token")
    return VerificationAttempt(METHOD_WELL_KNOWN, False, "File did not contain the token")


def _check_meta_tag(domain: str, token: str) -> VerificationAttempt:
    try:
        body = _fetch(f"https://{domain}/")
    except Exception as exc:
        return VerificationAttempt(METHOD_META_TAG, False, f"{type(exc).__name__}: {exc}")

    for pattern in (_META_TAG, _META_TAG_REVERSED):
        match = pattern.search(body)
        if match and match.group(1).decode("utf-8", "replace").strip() == token:
            return VerificationAttempt(METHOD_META_TAG, True, "Meta tag matched")
    return VerificationAttempt(METHOD_META_TAG, False, "No matching meta tag on the homepage")


def verify_website(website: str, token: str) -> VerificationOutcome:
    """Try each method in order of strength and stop at the first that proves control."""
    domain = domain_of(website)
    if not domain:
        return VerificationOutcome(
            verified=False,
            method="",
            attempts=(VerificationAttempt("", False, "No website on the claim"),),
        )

    expected = f"{settings.CLAIM_SITE_VERIFICATION_KEY}={token}"
    # Callables, not results: a DNS record that already proves control must not
    # cost the applicant's server two more requests.
    checks: Iterable[Callable[[], VerificationAttempt]] = (
        lambda: _check_dns_txt(domain, expected),
        lambda: _check_well_known(domain, expected),
        lambda: _check_meta_tag(domain, token),
    )

    attempts: list[VerificationAttempt] = []
    for check in checks:
        attempt = check()
        attempts.append(attempt)
        if attempt.ok:
            return VerificationOutcome(True, attempt.method, tuple(attempts))
    return VerificationOutcome(False, "", tuple(attempts))
