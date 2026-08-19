"""The internal console must be hard to find and hard to confirm."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, override_settings
from django.urls import clear_url_caches, reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_user():
    return get_user_model().objects.create_superuser(
        email="ops@example.com", password="pw-12345678"
    )


@pytest.fixture
def buyer():
    return get_user_model().objects.create_user(email="buyer@example.com", password="pw-12345678")


def test_default_admin_path_is_not_mounted(client: Client) -> None:
    """The whole point: /admin/ answers like any other unknown URL."""
    assert client.get("/admin/").status_code == 404


def test_console_is_reachable_on_the_configured_prefix(client: Client) -> None:
    response = client.get(reverse("admin:login"))
    assert response.status_code == 200
    assert reverse("admin:login").startswith("/baogong-ops-console/")


def test_signed_in_buyer_gets_404_not_a_login_form(client: Client, buyer) -> None:
    """A 200 here would confirm the prefix to anyone who guessed it."""
    client.force_login(buyer)
    assert client.get(reverse("admin:index")).status_code == 404


def test_staff_reaches_the_index(client: Client, staff_user) -> None:
    client.force_login(staff_user)
    response = client.get(reverse("admin:index"))
    assert response.status_code == 200


def test_console_responses_are_marked_noindex(client: Client, staff_user) -> None:
    client.force_login(staff_user)
    response = client.get(reverse("admin:index"))
    assert "noindex" in response.headers["X-Robots-Tag"]


def test_ip_allowlist_hides_the_console_from_other_addresses(client: Client, staff_user) -> None:
    client.force_login(staff_user)
    with override_settings(ADMIN_IP_ALLOWLIST=["203.0.113.7"]):
        assert client.get(reverse("admin:index")).status_code == 404
        allowed = client.get(reverse("admin:index"), REMOTE_ADDR="203.0.113.7")
        assert allowed.status_code == 200


def test_forwarded_for_is_ignored_unless_the_deployment_trusts_a_proxy(
    client: Client, staff_user
) -> None:
    """Otherwise the allowlist is bypassed by one request header."""
    client.force_login(staff_user)
    with override_settings(ADMIN_IP_ALLOWLIST=["203.0.113.7"], ADMIN_TRUST_PROXY_IP=False):
        spoofed = client.get(reverse("admin:index"), HTTP_X_FORWARDED_FOR="203.0.113.7")
        assert spoofed.status_code == 404
    with override_settings(ADMIN_IP_ALLOWLIST=["203.0.113.7"], ADMIN_TRUST_PROXY_IP=True):
        # Only the entry our own proxy appended counts - the client-supplied
        # left-hand entries must not be able to satisfy the allowlist.
        forged = client.get(
            reverse("admin:index"), HTTP_X_FORWARDED_FOR="203.0.113.7, 198.51.100.4"
        )
        assert forged.status_code == 404
        proxied = client.get(
            reverse("admin:index"), HTTP_X_FORWARDED_FOR="198.51.100.4, 203.0.113.7"
        )
        assert proxied.status_code == 200


def test_console_can_be_switched_off_entirely(client: Client, staff_user) -> None:
    client.force_login(staff_user)
    path = reverse("admin:index")
    with override_settings(ADMIN_ENABLED=False):
        assert client.get(path).status_code == 404


def test_robots_txt_does_not_publish_the_console_path(client: Client) -> None:
    body = client.get("/robots.txt").content.decode()
    assert "console" not in body
    assert "admin" not in body


def teardown_module(module) -> None:
    clear_url_caches()
