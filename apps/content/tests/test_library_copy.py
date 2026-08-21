"""The shipped library is copy the platform publishes in its own name.

Everything else on the site that speaks to a buyer is screened - provider
blurbs on the write path, Advisor answers before they are returned. These
twenty-two files were the exception, and they are the only pages where the
platform is the author rather than the host.
"""

from pathlib import Path

import pytest

from apps.content.management.commands.load_articles import DEFAULT_PATH, parse_article_file
from apps.core.compliance import check_banned_phrases

LIBRARY = sorted(DEFAULT_PATH.glob("*.md"))


def test_the_library_is_not_empty() -> None:
    """Guards the parametrisation below: an empty glob would pass vacuously."""
    assert len(LIBRARY) >= 22


@pytest.mark.parametrize("path", LIBRARY, ids=lambda p: p.stem)
def test_no_article_makes_a_banned_claim(path: Path) -> None:
    fields, body = parse_article_file(path)
    screened = "\n".join([fields["title_zh_hans"], fields["summary"], body])
    violations = check_banned_phrases(screened)
    assert violations == [], [(v.code, v.matched_text) for v in violations]


@pytest.mark.parametrize("path", LIBRARY, ids=lambda p: p.stem)
def test_no_article_carries_an_unresolved_editorial_note(path: Path) -> None:
    """A [pending verification] block means the piece is not fit to publish.

    Three AML articles shipped with one because the specific circular they were
    framed around could not be verified. They were rewritten to the standing
    framework instead; a block reappearing next to `status: published` means
    somebody published one back.
    """
    fields, body = parse_article_file(path)
    if fields.get("status") != "published":
        return
    assert "待人工核实" not in body, path.name
