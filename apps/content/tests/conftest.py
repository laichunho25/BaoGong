"""Fixtures for the education library.

``make_article`` writes through ``services.save_article`` rather than through
the ORM. Chunks are derived, so a fixture that created rows directly would give
every retrieval test an article with no passages - and the tests would pass by
agreeing with each other instead of with the code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from apps.content import services
from apps.content.models import Article, ArticleCategory, ArticleStatus

if TYPE_CHECKING:
    from collections.abc import Callable

BODY_MD = """## 政府费用

在香港注册一家私人有限公司，公司注册处收取的注册费与商业登记费是固定的。

## 服务费

秘书公司的报价里，服务费才是会浮动的一项。问清楚第一年与续期分别包含什么。
"""


@pytest.fixture
def make_article() -> Callable[..., Article]:
    counter = {"n": 0}

    def _make(*, published: bool = True, **overrides: Any) -> Article:
        counter["n"] += 1
        defaults: dict[str, Any] = {
            "slug": f"guide-{counter['n']}",
            "category": ArticleCategory.FEES,
            "title_zh_hans": f"香港公司注册费用说明 {counter['n']}",
            "summary": "注册费、商业登记费与服务费分别是多少，以及哪些项目容易被漏报。",
            "body_md": BODY_MD,
        }
        defaults.update(overrides)
        article = Article(**defaults)
        if published:
            return services.publish_article(article)
        article.status = ArticleStatus.DRAFT
        return services.save_article(article)

    return _make
