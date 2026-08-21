"""Which of the configured languages the site can actually deliver.

The list in CLAUDE.md section 6 - Simplified Chinese by default, Traditional
Chinese and English as options - is a statement of intent. It is not evidence
that a translation exists, and Django treats it as if it were: an unavailable
language still wins Accept-Language negotiation and still fills the picker. The
visitor then gets the same Simplified Chinese page under an ``<html lang="en">``
that misdescribes it to screen readers and to search engines.

So the configured list is filtered by what is compiled on disk. A language
lights up by itself the moment someone lands a catalogue for it, and until then
the site does not offer what it cannot do.

Imported from settings, so it must stay free of anything that needs the app
registry: ``to_locale`` is pure string handling.
"""

from collections.abc import Iterable, Sequence
from pathlib import Path

from django.utils.translation import to_locale


def languages_with_catalogues(
    languages: Sequence[tuple[str, str]],
    locale_paths: Iterable[Path],
    source_code: str,
) -> list[tuple[str, str]]:
    """Filter (code, name) pairs down to the ones a visitor can actually read.

    The source language needs no catalogue: the msgids *are* its copy.
    """
    paths = list(locale_paths)
    return [
        (code, name)
        for code, name in languages
        if code == source_code
        or any((path / to_locale(code) / "LC_MESSAGES" / "django.mo").exists() for path in paths)
    ]
