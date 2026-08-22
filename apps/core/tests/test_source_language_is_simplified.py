"""A Simplified page must not print Traditional Chinese.

CLAUDE.md section 6 makes Simplified the language of user-facing copy, so
nearly every msgid in this codebase is already Simplified and needs no
catalogue entry. Three strings are not: the COMPLIANCE section 7 disclaimer
and the data.gov.hk link in the footer, and the register's own title in
``REGISTRY_SOURCE_NAME``. Those msgids are authoritative Traditional text
reproduced verbatim, so the fix is a translation rather than an edit - and an
untranslated one falls straight through to the page, which is how the footer
shipped three Traditional paragraphs under a Simplified site.

This guards the translation, not the wording: if somebody adds a fourth
Traditional msgid, or drops one of these three entries from the catalogue,
the footer starts leaking again and nothing else would say so.
"""

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.utils import translation

CATALOGUE = Path(settings.BASE_DIR) / "locale" / "zh_Hans" / "LC_MESSAGES" / "django.mo"

# Characters that exist only in Traditional Chinese and appear in this project's
# own copy. Not a complete set - it does not need to be, because it only has to
# catch the strings the platform actually writes.
TRADITIONAL_ONLY = set(
    "們這個說對於業產發應會麼樣點續費證註冊處務網頁條隱與語轉換執專標籤實際權義參歷錄單據價聯絡幫觀覽選擇關資訊營結構線職開戶銀灣華國準備風險確題內從當"
)

pytestmark = pytest.mark.skipif(
    not CATALOGUE.exists(),
    reason="locale/zh_Hans is compiled by the Docker build; run compilemessages first.",
)


def _traditional(text: str) -> set[str]:
    return set(text) & TRADITIONAL_ONLY


@pytest.mark.parametrize(
    "msgid",
    [
        "data.gov.hk 開放數據條款",
        "香港公司註冊處《信託或公司服務持牌人登記冊》／data.gov.hk",
    ],
)
def test_the_traditional_msgids_have_a_simplified_translation(msgid: str) -> None:
    with translation.override("zh-hans"):
        rendered = translation.gettext(msgid)
    assert rendered != msgid, f"{msgid!r} has no zh_Hans entry"
    assert not _traditional(rendered), sorted(_traditional(rendered))


def test_the_site_wide_disclaimer_is_translated() -> None:
    """The one paragraph every page carries. COMPLIANCE section 7."""
    with translation.override("zh-hans"):
        rendered = translation.gettext(
            "本平台為獨立資訊比較平台，並非香港公司註冊處或任何政府機構，"
            "亦非信託或公司服務持牌人。 "
            "平台所載資料僅供一般參考，不構成法律、稅務、會計或任何專業意見，"
            "亦不構成對任何服務商的推薦或保證。 "
            "銀行開戶與否由銀行全權決定，本平台不對開戶結果作任何承諾。 "
            "用戶應自行向官方登記冊核實牌照狀態，並在需要時諮詢持牌專業人士。"
        )
    assert not _traditional(rendered), sorted(_traditional(rendered))
    # The four sentences are load-bearing; a translation that quietly drops one
    # publishes a shorter disclaimer than the one COMPLIANCE section 7 requires.
    assert rendered.count("。") == 4


def test_every_traditional_msgid_in_the_catalogue_is_translated() -> None:
    """Catches the fourth one before a reader does."""
    text = (CATALOGUE.parent / "django.po").read_text(encoding="utf-8")
    untranslated: list[str] = []
    for block in text.split("\n\n"):
        match = re.search(r'^msgid "(.+)"$', block, re.M)
        if match and _traditional(match.group(1)) and re.search(r'^msgstr ""\s*$', block, re.M):
            untranslated.append(match.group(1))
    assert untranslated == []


@pytest.mark.django_db
def test_the_footer_renders_simplified(client) -> None:
    """End to end: the string reaches the page, not just the catalogue."""
    with translation.override("zh-hans"):
        response = client.get("/")
    assert response.status_code == 200
    body = response.content.decode()
    footer = body[body.rindex("<footer") :]
    assert "本平台为独立资讯比较平台" in footer
    assert not _traditional(re.sub(r"<[^>]+>", "", footer))
