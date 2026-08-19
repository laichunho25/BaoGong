"""Load the education library from markdown files on disk.

The articles are written as files rather than typed into the admin because
they are reviewable: a change to what the platform tells a buyer about a bank
account should show up in a diff, next to the code that renders it. They are
also the only material the Advisor agent may answer from (AI_AGENTS A6), so
"who changed that sentence, and when" is a question somebody will ask.

The files are a starting point, not the source of truth. Once an article
exists in the database it belongs to whoever edits it in the admin, and this
command **skips it** - re-running the loader must never quietly overwrite a
person's rewrite. ``--update`` says to do it anyway, and says it out loud.

Front matter is a few ``key: value`` lines between ``---`` markers. Deliberately
not YAML: a dependency that can execute what it parses has no business reading
files that end up on a public page.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.content import services
from apps.content.models import Article, ArticleCategory, ArticleStatus

if TYPE_CHECKING:
    from argparse import ArgumentParser

#: Where the shipped articles live. A caller may point somewhere else, which is
#: how the tests run without touching the real library.
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "library"

REQUIRED_KEYS = ("slug", "category", "title_zh_hans", "summary")
OPTIONAL_KEYS = ("title_zh_hant", "title_en", "status")


class Command(BaseCommand):
    help = "Load or refresh education articles from markdown files."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--path", default=str(DEFAULT_PATH), help="Directory of .md files.")
        parser.add_argument(
            "--update",
            action="store_true",
            help="Overwrite articles that already exist, discarding edits made in the admin.",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Parse and report, write nothing."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        directory = Path(options["path"])
        if not directory.is_dir():
            raise CommandError(f"No such directory: {directory}")

        files = sorted(directory.glob("*.md"))
        if not files:
            raise CommandError(f"No .md files in {directory}")

        created = updated = skipped = 0
        for path in files:
            fields, body = parse_article_file(path)
            slug = fields["slug"]
            existing = Article.objects.filter(slug=slug).first()

            if existing is not None and not options["update"]:
                skipped += 1
                self.stdout.write(f"skip     {slug} (already in the database)")
                continue
            if options["dry_run"]:
                self.stdout.write(f"would {'update' if existing else 'create'}   {slug}")
                continue

            article = existing or Article(slug=slug)
            _apply(article, fields, body)
            with transaction.atomic():
                if fields.get("status", ArticleStatus.DRAFT) == ArticleStatus.PUBLISHED:
                    services.publish_article(article)
                else:
                    article.status = ArticleStatus.DRAFT
                    services.save_article(article)

            if existing:
                updated += 1
                self.stdout.write(f"updated  {slug}")
            else:
                created += 1
                self.stdout.write(f"created  {slug}")

        self.stdout.write(
            self.style.SUCCESS(
                f"{created} created, {updated} updated, {skipped} left alone "
                f"({len(files)} file(s) read)."
            )
        )


def _apply(article: Article, fields: dict[str, str], body: str) -> None:
    article.category = fields["category"]
    article.title_zh_hans = fields["title_zh_hans"]
    article.title_zh_hant = fields.get("title_zh_hant", "")
    article.title_en = fields.get("title_en", "")
    article.summary = fields["summary"]
    article.body_md = body


def parse_article_file(path: Path) -> tuple[dict[str, str], str]:
    """Split one file into its front matter and its body.

    Raises ``CommandError`` rather than returning something half-built: a file
    with a category the model does not know would otherwise fail later, in a
    stack trace that says nothing about which file was wrong.
    """
    text = path.read_text(encoding="utf-8").lstrip()
    if not text.startswith("---"):
        raise CommandError(f"{path.name}: no front matter (expected a line of ---)")

    _, _, rest = text.partition("---")
    front, marker, body = rest.partition("\n---")
    if not marker:
        raise CommandError(f"{path.name}: front matter is not closed")

    fields: dict[str, str] = {}
    for number, line in enumerate(front.splitlines(), start=2):
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise CommandError(f"{path.name} line {number}: expected 'key: value'")
        key = key.strip()
        if key not in REQUIRED_KEYS + OPTIONAL_KEYS:
            raise CommandError(f"{path.name}: unknown front matter key {key!r}")
        fields[key] = value.strip()

    missing = [key for key in REQUIRED_KEYS if not fields.get(key)]
    if missing:
        raise CommandError(f"{path.name}: missing {', '.join(missing)}")
    if fields["category"] not in ArticleCategory.values:
        raise CommandError(f"{path.name}: unknown category {fields['category']!r}")
    if fields.get("status", ArticleStatus.DRAFT) not in ArticleStatus.values:
        raise CommandError(f"{path.name}: unknown status {fields['status']!r}")

    return fields, body.lstrip("\n")
