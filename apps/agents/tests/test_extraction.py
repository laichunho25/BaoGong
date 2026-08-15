"""A3: reading an NNC1, and the limits on what that reading may do.

AI_AGENTS A3's red line is the subject of most of these: extraction produces
advice next to the rule-based match, and nothing it returns can pass or fail a
verification. P4-2 already made ``decide_verification`` the only writer of the
outcome; these tests are what keeps that true once a model is in the loop.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

from apps.agents.nnc1_extraction import Nnc1ExtractionAgent, UnsupportedDocument
from apps.agents.schemas import Nnc1Out

CTX: dict[str, Any] = {
    "verification_id": "v1",
    "media_type": "application/pdf",
    "content": b"%PDF-1.7 pretend this is a form",
}


@pytest.fixture
def agent() -> Nnc1ExtractionAgent:
    return Nnc1ExtractionAgent()


def test_a_pdf_is_sent_as_a_document_block(agent: Nnc1ExtractionAgent) -> None:
    blocks = agent.build_user_prompt(CTX)

    assert blocks[0]["type"] == "document"
    assert blocks[0]["source"]["media_type"] == "application/pdf"
    assert base64.standard_b64decode(blocks[0]["source"]["data"]) == CTX["content"]


def test_a_photograph_is_sent_as_an_image_block(agent: Nnc1ExtractionAgent) -> None:
    """Most uploads are phone photographs of a printed form."""
    blocks = agent.build_user_prompt(
        {**CTX, "media_type": "image/jpeg", "content": b"\xff\xd8\xff"}
    )

    assert blocks[0]["type"] == "image"


def test_the_declared_name_is_never_shown_to_the_model(agent: Nnc1ExtractionAgent) -> None:
    """Showing the model the answer would turn transcription into confirmation,
    and the whole value of this step is that it is an independent reading."""
    blocks = agent.build_user_prompt({**CTX, "declared_secretary_name": "Example Secretaries Ltd"})

    text = " ".join(block.get("text", "") for block in blocks)
    assert "Example Secretaries" not in text


def test_an_unsupported_file_type_is_refused(agent: Nnc1ExtractionAgent) -> None:
    with pytest.raises(UnsupportedDocument):
        agent.build_user_prompt({**CTX, "media_type": "application/zip"})


def test_the_fallback_claims_nothing(agent: Nnc1ExtractionAgent) -> None:
    """Not "guess from what the uploader typed" - that would put the claim being
    checked into the column that is supposed to be independent of it."""
    out = agent.fallback(CTX, "disabled")

    assert isinstance(out, Nnc1Out)
    assert out.secretary_name is None
    assert out.company_number is None
    assert out.confidence == 0.0
    assert "not_read" in out.quality_issues


def test_the_fallback_does_not_call_the_document_fake(agent: Nnc1ExtractionAgent) -> None:
    """A3 never fails anything. An unread document is unread, not forged."""
    assert agent.fallback(CTX, "api_error").document_looks_authentic is True


def test_the_agent_uses_the_cheap_model(agent: Nnc1ExtractionAgent) -> None:
    """CLAUDE.md section 5: extraction is the high-frequency, low-cost lane."""
    assert agent.model == "claude-haiku-4-5-20251001"
