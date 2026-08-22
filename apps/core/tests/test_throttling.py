"""The shared attempt counter.

The flows that use it are tested where they live (sign-in, password reset,
verification mail). What is tested here is the counter itself, and the two
properties the callers rely on: that a refused attempt still counts, and that
the key separates callers rather than lumping them together.
"""

from __future__ import annotations

from django.test import RequestFactory

from apps.core import throttling


def test_the_allowance_is_spent_then_refused() -> None:
    key = "test:allowance"

    verdicts = [throttling.too_many(key, limit=3, window_seconds=60) for _ in range(5)]

    assert verdicts == [False, False, False, True, True]


def test_a_blocked_key_stays_blocked_while_it_is_hammered() -> None:
    """Attempts are counted even when refused, so a caller cannot drain the
    window by continuing to try."""
    key = "test:hammered"
    for _unused in range(10):
        throttling.too_many(key, limit=2, window_seconds=60)

    assert throttling.too_many(key, limit=2, window_seconds=60)


def test_forgetting_a_key_restores_the_allowance() -> None:
    """Called after a successful sign-in: the typos before it are not evidence
    of anything once the right password arrives."""
    key = "test:forgotten"
    for _unused in range(5):
        throttling.too_many(key, limit=2, window_seconds=60)

    throttling.forget(key)

    assert not throttling.too_many(key, limit=2, window_seconds=60)


def test_the_key_separates_callers_and_subjects() -> None:
    factory = RequestFactory()
    one = factory.post("/", REMOTE_ADDR="198.51.100.1")
    two = factory.post("/", REMOTE_ADDR="198.51.100.2")

    keys = {
        throttling.client_key(one, scope="login", subject="a@example.com"),
        throttling.client_key(one, scope="login", subject="b@example.com"),
        throttling.client_key(two, scope="login", subject="a@example.com"),
        throttling.client_key(one, scope="password-reset", subject="a@example.com"),
    }

    assert len(keys) == 4


def test_the_key_does_not_carry_the_address_in_clear() -> None:
    """The cache is a shared service and its keys are readable by anything that
    can reach it; a counter should not double as a list of who tried to sign in
    as whom (COMPLIANCE section 3)."""
    request = RequestFactory().post("/", REMOTE_ADDR="198.51.100.1")

    key = throttling.client_key(request, scope="login", subject="person@example.com")

    assert "person@example.com" not in key
    assert "198.51.100.1" not in key


def test_the_same_caller_and_subject_share_one_counter() -> None:
    factory = RequestFactory()
    first = factory.post("/", REMOTE_ADDR="198.51.100.1")
    second = factory.post("/", REMOTE_ADDR="198.51.100.1")

    assert throttling.client_key(first, scope="login", subject="Person@Example.com") == (
        throttling.client_key(second, scope="login", subject="person@example.com ")
    )
