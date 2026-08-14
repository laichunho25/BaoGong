"""Website ownership proof.

No real DNS lookup and no real HTTP request: the point of the tests is the
order the methods are tried in, and that a wrong token is not accepted.
"""

from __future__ import annotations

import pytest
import responses

from apps.providers import verification

TOKEN = "abc123"
EXPECTED = f"qs-site-verification={TOKEN}"


class _Answer:
    """One TXT record, split the way a resolver returns long values."""

    def __init__(self, value: str) -> None:
        self.strings = [value.encode()]


class TestDomainOf:
    @pytest.mark.parametrize(
        ("website", "domain"),
        [
            ("https://www.example.com/about", "example.com"),
            ("example.com", "example.com"),
            ("http://EXAMPLE.com:8080", "example.com"),
            ("", ""),
        ],
    )
    def test_it_reduces_a_url_to_a_hostname(self, website: str, domain: str) -> None:
        assert verification.domain_of(website) == domain


class TestVerifyWebsite:
    def test_a_dns_record_is_accepted_without_touching_the_site(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import dns.resolver

        monkeypatch.setattr(dns.resolver, "resolve", lambda *a, **k: [_Answer(EXPECTED)])

        def fail(url: str) -> bytes:
            raise AssertionError("The site must not be fetched once DNS has proved control")

        monkeypatch.setattr(verification, "_fetch", fail)

        outcome = verification.verify_website("https://example.com", TOKEN)

        assert outcome.verified
        assert outcome.method == verification.METHOD_DNS_TXT

    def test_a_wrong_token_in_dns_is_not_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import dns.resolver

        monkeypatch.setattr(
            dns.resolver,
            "resolve",
            lambda *a, **k: [_Answer("qs-site-verification=somebody-elses-token")],
        )
        monkeypatch.setattr(verification, "_fetch", lambda url: b"")

        outcome = verification.verify_website("https://example.com", TOKEN)

        assert outcome.verified is False
        # Every attempt is recorded, so the applicant can see which one to fix.
        assert len(outcome.attempts) == 3

    @responses.activate
    def test_the_well_known_file_is_accepted_when_dns_says_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import dns.resolver

        monkeypatch.setattr(
            dns.resolver, "resolve", lambda *a, **k: (_ for _ in ()).throw(OSError("no answer"))
        )
        responses.add(
            responses.GET,
            f"https://example.com{verification.WELL_KNOWN_PATH}",
            body=EXPECTED,
            status=200,
        )

        outcome = verification.verify_website("https://example.com", TOKEN)

        assert outcome.verified
        assert outcome.method == verification.METHOD_WELL_KNOWN

    @responses.activate
    def test_a_meta_tag_is_accepted_in_either_attribute_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import dns.resolver

        monkeypatch.setattr(
            dns.resolver, "resolve", lambda *a, **k: (_ for _ in ()).throw(OSError("no answer"))
        )
        responses.add(
            responses.GET,
            f"https://example.com{verification.WELL_KNOWN_PATH}",
            status=404,
        )
        responses.add(
            responses.GET,
            "https://example.com/",
            body=f'<html><head><meta content="{TOKEN}" name="qs-site-verification"></head></html>',
            status=200,
        )

        outcome = verification.verify_website("https://example.com", TOKEN)

        assert outcome.verified
        assert outcome.method == verification.METHOD_META_TAG

    def test_a_claim_with_no_website_fails_without_a_lookup(self) -> None:
        outcome = verification.verify_website("", TOKEN)

        assert outcome.verified is False
        assert len(outcome.attempts) == 1
