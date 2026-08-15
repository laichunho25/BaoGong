"""A3 - read the secretary's details off an uploaded NNC1.

This is the one agent COMPLIANCE section 4 lets send personal data to the
model, because reading the document *is* the task. Two things follow from that
and are enforced here rather than left to the caller:

* the file must have been scanned. An unreadable upload is never opened, not
  even to send it somewhere else.
* what comes back is written to ``Nnc1Verification.extracted``, never to
  ``result``. AI_AGENTS A3 is explicit that ``document_looks_authentic=false``
  may only route a case to a human, and P4-2 already made
  ``decide_verification`` the sole writer of the outcome.

The comparison against the official register stays where P4-2 put it - in
``reviews.matching``, as rules. A model that both read the name and judged
whether it matched would be a model that hands out verified badges.
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING, Any, ClassVar

from apps.agents.base import BaseAgent
from apps.agents.schemas import Nnc1Out

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)

#: What the vision API accepts, mapped from what ``core.uploads`` sniffed.
_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
_PDF_TYPE = "application/pdf"
SUPPORTED_MEDIA_TYPES = frozenset({*_IMAGE_TYPES, _PDF_TYPE})


class UnsupportedDocument(ValueError):
    """The upload is not something the model can be shown."""


class Nnc1ExtractionAgent(BaseAgent):
    name: ClassVar[str] = "nnc1_extraction"
    model: ClassVar[str] = "claude-haiku-4-5-20251001"
    prompt_file: ClassVar[str] = "nnc1_extract_v1.md"
    output_schema: ClassVar[type[BaseModel]] = Nnc1Out
    max_tokens: ClassVar[int] = 1024
    # A scan of a form takes longer to read than a paragraph of text.
    timeout_s: ClassVar[int] = 60
    object_type: ClassVar[str] = "reviews.Nnc1Verification"

    def build_user_prompt(self, ctx: dict[str, Any]) -> list[dict[str, Any]]:
        """The document itself, plus nothing that could anchor the reading.

        Deliberately *not* included: what the uploader declared the secretary's
        name to be. Showing the model the answer it is being asked to check
        turns a transcription into a confirmation, and the whole point of this
        step is an independent read.
        """
        media_type = str(ctx.get("media_type", ""))
        content = ctx.get("content")
        if media_type not in SUPPORTED_MEDIA_TYPES or not isinstance(content, bytes | bytearray):
            raise UnsupportedDocument(media_type or "no document")

        encoded = base64.standard_b64encode(bytes(content)).decode("ascii")
        block_type = "document" if media_type == _PDF_TYPE else "image"
        return [
            {
                "type": block_type,
                "source": {"type": "base64", "media_type": media_type, "data": encoded},
            },
            {
                "type": "text",
                "text": (
                    "Read this document and report what it says. If it is not an "
                    "NNC1 or NNC1G, say so in quality_issues and return null for "
                    "the fields you cannot read."
                ),
            },
        ]

    def fallback(self, ctx: dict[str, Any], reason: str) -> Nnc1Out:
        """An empty reading with zero confidence.

        AI_AGENTS A3: the fallback is "send it to a human", and a human is
        already the only thing that can decide a verification. So the honest
        fallback is to claim nothing at all - every field ``null``, confidence
        0 - rather than to guess from the declared fields, which would put the
        uploader's own claim into the column meant to be independent of it.
        """
        logger.info("nnc1 extraction fell back (%s) for %s", reason, self.object_id(ctx))
        return Nnc1Out(document_looks_authentic=True, quality_issues=["not_read"], confidence=0.0)

    def object_id(self, ctx: dict[str, Any]) -> str:
        return str(ctx.get("verification_id", ""))
