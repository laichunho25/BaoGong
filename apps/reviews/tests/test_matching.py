"""The rule-based name comparison.

Written around the asymmetry in ``matching.py``: these tests assert that a
mismatch is detected reliably, and that nothing in the module ever returns a
verdict. If a future refactor makes ``match_against_provider`` return something
that reads as "verified", the last test here is the one that should fail.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from apps.reviews import matching
from apps.reviews.matching import FUZZY_THRESHOLD, MatchMethod

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.registry.models import Licensee

pytestmark = pytest.mark.django_db


class TestNormalisation:
    def test_corporate_suffixes_and_punctuation_are_dropped(self) -> None:
        """The register and a filed form disagree about "Ltd" or "Limited"
        more often than they agree, and it is the same company either way."""
        assert matching.normalise_name("Golden Gate Co., Ltd.") == matching.normalise_name(
            "Golden Gate Company Limited"
        )

    def test_full_width_latin_folds_to_ascii(self) -> None:
        """Forms typed on a Chinese IME carry full-width Latin; a byte
        comparison would call these two different companies."""
        assert matching.normalise_name("ＧＯＬＤＥＮ ＧＡＴＥ") == "golden gate"

    def test_a_name_made_only_of_noise_normalises_to_nothing(self) -> None:
        assert matching.normalise_name("Hong Kong Company Limited") == ""

    def test_similarity_of_an_empty_normal_form_is_zero(self) -> None:
        """Two names that both reduce to noise are not "identical" - they are
        unusable, and 1.0 here would hand a moderator a false agreement."""
        assert matching.similarity("Hong Kong Limited", "The HK Co") == 0.0


class TestMatchAgainstProvider:
    def test_the_same_name_spelled_differently_is_exact(
        self, make_licensee: Callable[..., Licensee]
    ) -> None:
        licensee = make_licensee(name_en="Golden Gate Secretarial Limited")

        match = matching.match_against_provider("Golden Gate Secretarial Ltd.", licensee=licensee)

        assert match.method == MatchMethod.EXACT
        assert match.licence_no == licensee.licence_no
        assert match.is_plausible

    def test_a_near_miss_is_similar_rather_than_equal(
        self, make_licensee: Callable[..., Licensee]
    ) -> None:
        """A typo in a hand-copied name should still reach a moderator as
        "probably this company", not as "no match"."""
        licensee = make_licensee(name_en="Golden Gate Secretarial Limited")

        match = matching.match_against_provider("Golden Gates Secretarial", licensee=licensee)

        assert match.method == MatchMethod.FUZZY
        assert FUZZY_THRESHOLD <= match.score < 1.0

    def test_a_different_company_does_not_match(
        self, make_licensee: Callable[..., Licensee]
    ) -> None:
        licensee = make_licensee(name_en="Golden Gate Secretarial Limited")

        match = matching.match_against_provider("Pearl River Accounting", licensee=licensee)

        assert match.method == MatchMethod.NONE
        assert not match.is_plausible
        # No licence number on a non-match: nothing here may look like it
        # attached the document to a licensed company.
        assert match.licence_no == ""

    def test_the_chinese_name_counts_as_the_same_company(
        self, make_licensee: Callable[..., Licensee]
    ) -> None:
        """The register carries both names and an NNC1 may be filed with
        either; comparing only ``name_en`` would fail every Chinese filing."""
        licensee = make_licensee(
            name_en="Golden Gate Secretarial Limited", name_zh="金門秘書有限公司"
        )

        match = matching.match_against_provider("金門秘書有限公司", licensee=licensee)

        assert match.is_plausible

    def test_a_blank_declaration_matches_nothing(
        self, make_licensee: Callable[..., Licensee]
    ) -> None:
        match = matching.match_against_provider("   ", licensee=make_licensee())

        assert match.method == MatchMethod.NONE
        assert match.score == 0.0


class TestFindLicensee:
    def test_it_names_the_other_licensed_company(
        self, make_licensee: Callable[..., Licensee]
    ) -> None:
        """The case a moderator can close in one look: the document names a
        real TCSP, just not the one being reviewed."""
        make_licensee(name_en="Golden Gate Secretarial Limited")
        other = make_licensee(name_en="Pearl River Corporate Services Limited")

        match = matching.find_licensee("Pearl River Corporate Ltd")

        assert match.is_plausible
        assert match.licence_no == other.licence_no

    def test_a_name_on_nobody_returns_no_match(
        self, make_licensee: Callable[..., Licensee]
    ) -> None:
        """A different and more serious signal than the test above: the
        document names a secretary who holds no licence at all."""
        make_licensee(name_en="Golden Gate Secretarial Limited")

        assert not matching.find_licensee("Unlicensed Backroom Agency").is_plausible

    def test_a_blank_declaration_scans_nothing(self) -> None:
        assert matching.find_licensee("").method == MatchMethod.NONE


def test_nothing_in_this_module_returns_a_verdict(
    make_licensee: Callable[..., Licensee],
) -> None:
    """The guard on the module's premise.

    ``is_plausible`` is the strongest thing a match may express, and it means
    "worth showing a moderator". Nothing here may grow a ``verified`` or
    ``passed`` attribute - that decision belongs to ``decide_verification``.
    """
    match = matching.match_against_provider("Anything", licensee=make_licensee())

    assert not hasattr(match, "verified")
    assert not hasattr(match, "passed")
