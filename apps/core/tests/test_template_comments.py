"""A comment that spans lines has to be a ``{% comment %}`` block.

Django's short form is single-line only: an unclosed ``{#`` is not a comment,
it is text, and the rest of the line renders to the page. This shipped once -
the note explaining the language picker appeared in the masthead of every page,
in English, next to the navigation.

Nothing else catches it. The template still compiles, the page still returns
200, and the leaked text is only visible to somebody looking at the rendered
page rather than the diff.
"""

from pathlib import Path

import pytest
from django.conf import settings

TEMPLATES = sorted((Path(settings.BASE_DIR) / "templates").rglob("*.html"))


def test_there_are_templates_to_check() -> None:
    """Guards the parametrisation: an empty glob would pass vacuously."""
    assert len(TEMPLATES) >= 20


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_no_short_comment_runs_past_its_line(path: Path) -> None:
    leaked = [
        (number, line.strip())
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "{#" in line and "#}" not in line[line.index("{#") :]
    ]
    assert leaked == [], f"{path.name}: use {{% comment %}} for these"
