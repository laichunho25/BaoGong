"""Markdown in, safe HTML and citable passages out.

Two jobs that have to agree with each other: what the reader sees and what the
Advisor agent is allowed to quote. Both are derived from the same body text
here, so a passage the agent cites is a passage on the page.

The HTML is sanitised even though only staff can author articles. An editor
account is one stolen password away from being an attacker, and "we trust the
authors" is the assumption every stored-XSS post-mortem starts with.
"""

from __future__ import annotations

import re

import markdown
import nh3

#: Long enough to hold an argument, short enough that a citation points at a
#: passage rather than at a page. Chunks are split on paragraph boundaries, so
#: this is a ceiling and not a target.
MAX_CHUNK_CHARS = 600

#: Inline formatting is kept; anything that loads or executes is not. No
#: ``script``, ``iframe``, ``style``, ``img`` (an external image is a request
#: to a third party from a page that must stay reachable from the mainland).
ALLOWED_TAGS = {
    "p",
    "h2",
    "h3",
    "h4",
    "ul",
    "ol",
    "li",
    "strong",
    "em",
    "code",
    "pre",
    "blockquote",
    "a",
    "hr",
    "br",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
}
ALLOWED_ATTRIBUTES = {"a": {"href", "title"}}

_HEADING = re.compile(r"^(#{1,4})\s+(?P<text>.+?)\s*#*$")


def render_markdown(body_md: str) -> str:
    """Render an article body to HTML that is safe to insert into a page."""
    html = markdown.markdown(body_md, extensions=["extra", "sane_lists"], output_format="html")
    return nh3.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)


def split_into_chunks(body_md: str) -> list[tuple[str, str]]:
    """Split a body into ``(heading, text)`` passages, in reading order.

    Headings are carried onto every passage under them rather than left as
    their own row: a citation reading "费用" is worth more to a buyer than the
    same sentence with no idea which section it came from.
    """
    chunks: list[tuple[str, str]] = []
    heading = ""
    buffer: list[str] = []

    def flush() -> None:
        text = "\n\n".join(buffer).strip()
        buffer.clear()
        if text:
            chunks.append((heading, text))

    for block in re.split(r"\n\s*\n", body_md.strip()):
        block = block.strip()
        if not block:
            continue
        match = _HEADING.match(block)
        if match:
            flush()
            heading = match.group("text").strip()
            continue
        # A paragraph longer than the ceiling is kept whole: cutting mid
        # sentence would produce a quote nobody wrote.
        if buffer and sum(len(part) for part in buffer) + len(block) > MAX_CHUNK_CHARS:
            flush()
        buffer.append(block)

    flush()
    return chunks
