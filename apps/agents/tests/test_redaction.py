"""Redaction and the shape of what gets logged.

COMPLIANCE section 4 asks for two different things and they are easy to
conflate: what leaves the process (redacted text) and what is kept afterwards
(a reference, not the text).
"""

from __future__ import annotations

from apps.agents.redaction import hash_input, redact, summarise_for_log


def test_contact_details_are_replaced_with_typed_placeholders() -> None:
    """Placeholders, not deletion: the moderation agent still needs to see that
    something was there in order to label a personal-data leak."""
    text = "Ask for Amy on 9123 4567 or amy@example.com, WeChat: amy_hk_2024"

    cleaned = redact(text)

    assert "9123 4567" not in cleaned
    assert "amy@example.com" not in cleaned
    assert "amy_hk_2024" not in cleaned
    assert "[PHONE]" in cleaned
    assert "[EMAIL]" in cleaned
    assert "[IM_HANDLE]" in cleaned


def test_an_identity_number_is_removed() -> None:
    assert "A123456(7)" not in redact("His HKID is A123456(7).")


def test_ordinary_review_text_survives_untouched() -> None:
    """Over-redaction would strip the detail that makes a review worth reading."""
    text = "They filed the NNC1 in four days and explained the government fee."

    assert redact(text) == text


def test_the_hash_is_stable_across_key_order() -> None:
    a = {"body": "hello", "provider_name": "X"}
    b = {"provider_name": "X", "body": "hello"}

    assert hash_input(a) == hash_input(b)


def test_different_inputs_hash_differently() -> None:
    assert hash_input({"body": "a"}) != hash_input({"body": "b"})


def test_the_log_summary_keeps_shape_and_drops_content() -> None:
    summary = summarise_for_log(
        {
            "body": "a long review body",
            "author_verified": True,
            "services": ["incorporation", "accounting"],
            "content": b"\x00\x01",
        }
    )

    assert summary == {
        "body_chars": len("a long review body"),
        "author_verified": True,
        "services_count": 2,
    }
    # Bytes are dropped entirely: an NNC1's contents have no business in a
    # table read by whoever is looking at cost and latency.
    assert "content" not in summary
