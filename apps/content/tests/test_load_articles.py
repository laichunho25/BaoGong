"""The loader's job is to fill an empty library and then stay out of the way.

The interesting tests are not "does it create rows". They are the two ways a
content loader ruins somebody's afternoon: overwriting an edit a person made in
the admin, and putting an unreviewed page in front of readers because the front
matter said nothing about status.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.content.models import Article, ArticleStatus, Chunk

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.django_db

ARTICLE = """---
slug: a-page
category: banking
status: published
title_zh_hans: 开户流程
title_en: Opening an account
summary: 一句话摘要。
---

## 第一步

正文一段。

## 第二步

正文两段。
"""


def _write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def test_a_file_becomes_a_published_page_with_citable_passages(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", ARTICLE)

    call_command("load_articles", path=str(tmp_path))

    article = Article.objects.get(slug="a-page")
    assert article.status == ArticleStatus.PUBLISHED
    assert article.published_at is not None
    assert article.title_zh_hans == "开户流程"
    # Published means the Advisor may quote it, which means chunks exist.
    assert Chunk.objects.filter(article=article).count() == 2


def test_an_article_without_a_status_stays_a_draft(tmp_path: Path) -> None:
    """Silence is not consent to publish. A file that forgets the line gets a
    draft, and a draft has no chunks - so the agent cannot quote it either."""
    _write(tmp_path, "a.md", ARTICLE.replace("status: published\n", ""))

    call_command("load_articles", path=str(tmp_path))

    article = Article.objects.get(slug="a-page")
    assert article.status == ArticleStatus.DRAFT
    assert not Chunk.objects.filter(article=article).exists()


def test_running_again_leaves_an_edited_article_alone(tmp_path: Path) -> None:
    """The file seeds the library; the person editing in the admin owns it
    afterwards. A loader that quietly restores its own copy is a loader nobody
    can safely run twice."""
    _write(tmp_path, "a.md", ARTICLE)
    call_command("load_articles", path=str(tmp_path))
    article = Article.objects.get(slug="a-page")
    article.title_zh_hans = "编辑改过的标题"
    article.save(update_fields=["title_zh_hans", "updated_at"])

    call_command("load_articles", path=str(tmp_path))

    article.refresh_from_db()
    assert article.title_zh_hans == "编辑改过的标题"


def test_update_overwrites_and_rebuilds_the_passages(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", ARTICLE)
    call_command("load_articles", path=str(tmp_path))
    _write(tmp_path, "a.md", ARTICLE.replace("## 第二步\n\n正文两段。\n", ""))

    call_command("load_articles", path=str(tmp_path), update=True)

    article = Article.objects.get(slug="a-page")
    assert Chunk.objects.filter(article=article).count() == 1


def test_a_dry_run_writes_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "a.md", ARTICLE)

    call_command("load_articles", path=str(tmp_path), dry_run=True)

    assert not Article.objects.exists()


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (ARTICLE.replace("---\nslug", "slug"), "no front matter"),
        (ARTICLE.replace("category: banking", "category: nonsense"), "unknown category"),
        (ARTICLE.replace("summary: 一句话摘要。", "summary:"), "missing summary"),
        (
            ARTICLE.replace("title_en: Opening an account", "author: someone"),
            "unknown front matter",
        ),
    ],
)
def test_a_broken_file_is_named_in_the_error(tmp_path: Path, text: str, message: str) -> None:
    """The file name is in every message on purpose: twelve files in, "unknown
    category" without one is a hunt."""
    _write(tmp_path, "broken.md", text)

    with pytest.raises(CommandError, match=message):
        call_command("load_articles", path=str(tmp_path))

    assert not Article.objects.exists()


def test_an_empty_directory_is_an_error_not_a_silent_success(tmp_path: Path) -> None:
    with pytest.raises(CommandError, match=r"No \.md files"):
        call_command("load_articles", path=str(tmp_path))


def test_the_shipped_library_loads() -> None:
    """The twelve files that ship with the app are parsed and loaded as they
    are, so a typo in front matter fails in CI rather than on a server."""
    call_command("load_articles")

    assert Article.objects.count() >= 12
    # Every published article must be citable, and every draft must not be.
    for article in Article.objects.all():
        has_chunks = article.chunks.exists()
        assert has_chunks == article.is_published, article.slug
