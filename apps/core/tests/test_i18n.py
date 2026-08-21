"""A language the site cannot render must not be offered, or negotiated into."""

import pytest
from django.conf import settings
from django.test import Client

from apps.core.i18n import languages_with_catalogues

INTENT = [("zh-hans", "简体中文"), ("zh-hant", "繁體中文"), ("en", "English")]


def _catalogue(root, code_dir: str) -> None:
    (root / code_dir / "LC_MESSAGES").mkdir(parents=True)
    (root / code_dir / "LC_MESSAGES" / "django.mo").write_bytes(b"")


def test_the_source_language_needs_no_catalogue(tmp_path):
    """Its msgids are its copy; requiring a file would switch the site off."""
    assert languages_with_catalogues(INTENT, [tmp_path], "zh-hans") == [("zh-hans", "简体中文")]


def test_a_language_appears_once_its_catalogue_lands(tmp_path):
    _catalogue(tmp_path, "zh_Hant")
    assert languages_with_catalogues(INTENT, [tmp_path], "zh-hans") == [
        ("zh-hans", "简体中文"),
        ("zh-hant", "繁體中文"),
    ]


def test_any_locale_path_may_supply_it(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    _catalogue(second, "en")
    codes = [code for code, _ in languages_with_catalogues(INTENT, [first, second], "zh-hans")]
    assert codes == ["zh-hans", "en"]


def test_declared_order_is_kept(tmp_path):
    _catalogue(tmp_path, "en")
    _catalogue(tmp_path, "zh_Hant")
    codes = [code for code, _ in languages_with_catalogues(INTENT, [tmp_path], "zh-hans")]
    assert codes == ["zh-hans", "zh-hant", "en"]


def test_settings_only_advertise_what_is_compiled():
    """Guards the deployed state: zh-hant and en have no catalogue yet."""
    assert [code for code, _ in settings.LANGUAGES] == ["zh-hans"]


@pytest.mark.django_db
def test_the_picker_is_hidden_while_there_is_nothing_to_pick():
    response = Client().get("/")
    assert response.status_code == 200
    assert b"language-picker" not in response.content


@pytest.mark.django_db
def test_an_english_browser_is_not_served_a_mislabelled_page():
    """Accept-Language must not win a language the site cannot render."""
    response = Client().get("/", headers={"accept-language": "en-US,en;q=0.9"})
    assert response.status_code == 200
    assert b'<html lang="zh-hans">' in response.content
