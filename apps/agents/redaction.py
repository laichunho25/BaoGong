"""Strip direct identifiers before text leaves the process.

COMPLIANCE.md section 4: personal data is redacted before it is sent to an LLM,
with NNC1 extraction the one documented exception - reading the secretary's
name off the document *is* the task there, so that path sends the extracted
text and relies on the retention clock instead.

Two callers, two different needs:

* ``redact`` runs over prompt text on its way to the API.
* ``summarise_for_log`` builds ``AgentRun.input_ref``, which is not the input
  at all - it is a length and a hash and a couple of ids, because that table is
  read by whoever is looking at cost and latency, and a review body has no
  business being on that screen.

The patterns below catch the identifiers that actually appear in Hong Kong
review text. They are a reduction of risk, not a guarantee, and nothing here
excuses sending a field that did not need sending in the first place.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Final

_HKID = re.compile(r"\b[A-Z]{1,2}\d{6}\(?[0-9A]\)?", re.IGNORECASE)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Hong Kong numbers are eight digits, optionally +852 and optionally spaced;
# mainland mobiles are eleven starting with 1. Both appear in review text.
_PHONE = re.compile(r"(?:\+?852[\s-]?)?\b(?:\d[\s-]?){7,10}\d\b")
_WECHAT = re.compile(r"(?:微信|weixin|wechat|whatsapp|WhatsApp|QQ)\s*[:：]?\s*[\w.-]{4,}", re.I)
_URL = re.compile(r"https?://\S+")

_REPLACEMENTS: Final[list[tuple[re.Pattern[str], str]]] = [
    (_EMAIL, "[EMAIL]"),
    (_URL, "[URL]"),
    (_WECHAT, "[IM_HANDLE]"),
    (_HKID, "[ID_NUMBER]"),
    (_PHONE, "[PHONE]"),
]


def redact(text: str) -> str:
    """Replace direct identifiers with typed placeholders.

    Placeholders rather than deletion: a moderation agent asked whether a
    review leaks personal data needs to see that *something* was there, and
    ``[PHONE]`` says so without carrying the number.
    """
    if not text:
        return ""
    for pattern, placeholder in _REPLACEMENTS:
        text = pattern.sub(placeholder, text)
    return text


def hash_input(payload: Any) -> str:
    """Stable sha256 over an agent's input.

    ``sort_keys`` so that two structurally identical inputs hash the same
    regardless of dict ordering - otherwise the duplicate-call check and the
    golden-set link both quietly stop working.
    """
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def summarise_for_log(payload: dict[str, Any]) -> dict[str, Any]:
    """What ``AgentRun.input_ref`` may hold: shape, never content.

    Strings collapse to their length. Anything else is kept only if it is a
    number or a bool - identifiers belong in ``object_id``, which is indexed
    and which a reader expects to be an id.
    """
    summary: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            summary[f"{key}_chars"] = len(value)
        elif isinstance(value, bool | int | float):
            summary[key] = value
        elif isinstance(value, list | tuple):
            summary[f"{key}_count"] = len(value)
    return summary
