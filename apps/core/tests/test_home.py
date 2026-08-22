from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from django.test import Client

from apps.providers.models import ServiceOffering
from apps.registry.models import LicenceStatus, allow_registry_writes
from apps.reviews.models import ReviewStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.accounts.models import User
    from apps.providers.models import Provider
    from apps.rfq.models import Rfq

# The page now reads the last sync time, because it claims the register is
# synced daily and that claim has to be checkable on the page making it.
pytestmark = pytest.mark.django_db


class TestHomePage:
    def test_renders(self):
        response = Client().get("/")
        assert response.status_code == 200
        assert response.templates[0].name == "pages/home.html"

    def test_includes_the_compliance_disclaimer(self):
        # COMPLIANCE.md section 7 - the disclaimer is site-wide, not opt-in.
        # The msgid is the authoritative Traditional text; what a reader gets is
        # its Simplified translation, because the site's language is Simplified.
        content = Client().get("/").content.decode()
        assert "并非香港公司注册处或任何政府机构" in content
        assert "不构成法律、税务、会计或任何专业意见" in content

    def test_credits_the_data_source(self):
        # COMPLIANCE.md section 1.
        content = Client().get("/").content.decode()
        assert "data.gov.hk" in content

    def test_uses_self_hosted_assets_only(self):
        # PRD section 4 - no Google Fonts / reCAPTCHA / GA: unreliable or
        # blocked for mainland-China visitors.
        content = Client().get("/").content.decode()
        for blocked in ("fonts.googleapis.com", "google-analytics.com", "recaptcha"):
            assert blocked not in content

    def test_copy_passes_the_banned_phrase_screen(self):
        from apps.core.compliance import check_banned_phrases

        content = Client().get("/").content.decode()
        assert check_banned_phrases(content) == []


class TestBrandMeaning:
    """The name is a promise, so the page has to say what it promises.

    "包公" carries two readings - 包拯's impartiality and 包羅香港「公」司服務 -
    and one liability: 包拯 was a magistrate. A platform borrowing his name has
    to state that it is neither an authority nor a judge, or the name itself
    starts claiming official standing (COMPLIANCE sections 1 and 2,
    docs/BRAND.md section 2).
    """

    def test_the_home_page_explains_both_readings_of_the_name(self) -> None:
        content = Client().get("/").content.decode()
        assert "包罗香港公司服务" in content
        assert "包拯" in content

    def test_each_of_the_three_lines_is_named_on_the_page(self) -> None:
        content = Client().get("/").content.decode()
        for image in ("明镜高悬", "铁面无私", "一把尺量到底"):
            assert image in content

    def test_the_page_says_the_platform_is_not_an_authority(self) -> None:
        content = Client().get("/").content.decode()
        assert "包公是个名字，不是身分" in content
        assert "不是政府机构" in content
        assert "不替任何人裁决" in content

    def test_the_hero_promises_evidence_rather_than_a_recommendation(self) -> None:
        content = Client().get("/").content.decode()
        assert "包公不替你选，只把证据摆齐" in content

    def test_the_name_is_explained_on_every_page_not_only_the_home_page(self) -> None:
        # The explanation lives in the site-wide footer on purpose: an
        # About page nobody opens is not an explanation.
        content = Client().get("/providers/").content.decode()
        assert "取「包罗香港公司服务」之意" in content

    def test_the_brand_copy_passes_the_banned_phrase_screen(self) -> None:
        # "包" reads as a guarantee in "包过" / "包成功"; the screen is what
        # keeps the name from drifting there.
        from apps.core.compliance import check_banned_phrases

        for path in ("/", "/providers/", "/compare/", "/guides/"):
            assert check_banned_phrases(Client().get(path).content.decode()) == []


class TestVisualIdentity:
    """The mark and the Chinese ornament.

    Asserted on more than one page because the identity is carried by base.html
    and the site-wide footer, not by the landing page - a header that only shows
    the mark on "/" is a header nobody outside the home page recognises.

    The last two tests are the compliance half. The seal is the platform
    signing its own work; the moment it appears next to register data it reads
    as "certified by us", which is exactly what COMPLIANCE section 1 forbids
    (docs/BRAND.md section 5.3).
    """

    def test_the_seal_mark_appears_on_every_page(self) -> None:
        for path in ("/", "/providers/", "/guides/"):
            content = Client().get(path).content.decode()
            # The carved character, inside the inline SVG of the seal.
            assert ">包</text>" in content, path
            assert "BaoGong" in content, path

    def test_the_favicon_is_linked(self) -> None:
        content = Client().get("/").content.decode()
        assert 'rel="icon"' in content
        assert "favicon.svg" in content

    def test_the_chinese_ornament_is_present_but_carries_no_text(self) -> None:
        content = Client().get("/").content.decode()
        assert "ornament-meander" in content  # 回紋
        assert "ornament-clouds" in content  # 祥雲
        # Decoration only: every ornament element is empty and hidden from
        # assistive technology, so switching images off loses no information.
        for match in re.finditer(r'<div class="ornament-[a-z]+([^>]*)>(.*?)</div>', content):
            assert "aria-hidden" in match.group(1)
            assert match.group(2).strip() == ""

    def test_headings_use_the_display_serif(self) -> None:
        for path in ("/", "/providers/", "/guides/"):
            content = Client().get(path).content.decode()
            assert "font-display" in content, path

    def test_the_seal_is_never_stamped_on_official_register_data(self) -> None:
        # The directory is the page that shows the register. The seal colour
        # and the cloud band must stay out of it; the mark itself lives in the
        # header and the footer, which are not part of the data block.
        content = Client().get("/providers/").content.decode()
        start = content.index("<main")
        end = content.index("</main>")
        main = content[start:end]
        assert "seal-" not in main
        assert "ornament-clouds" not in main

    def test_the_seal_colour_is_not_used_as_a_status(self) -> None:
        # Two reds in one interface make the reader guess which one means
        # "something is wrong". `danger` owns that job; `seal` is furniture.
        for path in ("/", "/providers/", "/guides/"):
            content = Client().get(path).content.decode()
            for status_class in ("bg-seal-50", "bg-seal-600", "border-seal-200", "text-seal-700"):
                assert status_class not in content, path


class TestHomeSections:
    """The six sections the landing page is built out of.

    Asserted by their headings rather than by CSS classes: a section can be
    restyled freely, but a home page that silently stops answering "what can
    these companies do for me" is a different page.
    """

    def test_every_section_renders(self) -> None:
        content = Client().get("/").content.decode()
        for heading in ("业务功能", "需要报价", "市场资讯", "用家评语", "热门搜索"):
            assert heading in content

    def test_market_numbers_are_counted_not_illustrated(
        self, make_licensee: Callable[..., object]
    ) -> None:
        # Three on the register and one off it: the page prints both, because
        # a company vanishing from the list is the thing worth knowing.
        for _ in range(3):
            make_licensee()
        gone = make_licensee()
        with allow_registry_writes():
            gone.status = LicenceStatus.INACTIVE  # type: ignore[attr-defined]
            gone.save()  # type: ignore[attr-defined]

        response = Client().get("/")

        assert response.context["market"].total_on_register == 3
        assert response.context["market"].deregistered == 1

    def test_service_tiles_count_published_companies_only(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        listed = make_provider()
        hidden = make_provider(is_published=False)
        for provider in (listed, hidden):
            ServiceOffering.objects.create(provider=provider, category="incorporation")

        response = Client().get("/")

        tiles = {tile.value: tile.provider_count for tile in response.context["services"]}
        assert tiles["incorporation"] == 1
        # A category nobody offers still gets a tile: the visitor came to find
        # out what these companies do, and a zero is an honest answer.
        assert tiles["work_visa"] == 0

    def test_search_chips_never_lead_to_an_empty_result(
        self, make_provider: Callable[..., Provider]
    ) -> None:
        make_provider(bank_account_support=True)

        chips = Client().get("/").context["chips"]

        labels = [chip.label for chip in chips]
        assert "协助银行开户" in labels
        assert "可远程办理" not in labels
        assert all(chip.count > 0 for chip in chips)


class TestHomeRequirementPrivacy:
    """Counts are public; the requirements themselves are not.

    COMPLIANCE section 4 - the request wall sits behind a login, and a preview
    on the public home page would be a hole in the same rule.
    """

    def test_anonymous_visitors_get_counts_without_requirements(self, open_rfq: Rfq) -> None:
        response = Client().get("/")
        content = response.content.decode()

        assert response.context["rfq_previews"] is None
        assert open_rfq.title not in content
        assert response.context["matching"].open_requests == 1

    def test_signed_in_visitors_get_the_previews(self, open_rfq: Rfq, buyer: User) -> None:
        client = Client()
        client.force_login(buyer)

        content = client.get("/").content.decode()

        assert open_rfq.title in content


class TestHomeFeaturedReviews:
    """Only what the page's own claim covers.

    RATING_SYSTEM - a review counts once a moderator has confirmed the NNC1.
    The home page is where the platform states that standard, so a pending or
    unverified review appearing there would advertise a rule it breaks.
    """

    def test_only_published_and_verified_reviews_appear(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_review: Callable[..., object],
    ) -> None:
        provider = make_provider()
        make_review(
            provider=provider,
            author=make_user(email="verified@example.com"),
            body="VERIFIED-BODY",
        )
        make_review(
            provider=make_provider(),
            author=make_user(email="unverified@example.com"),
            is_verified=False,
            body="UNVERIFIED-BODY",
        )
        make_review(
            provider=make_provider(),
            author=make_user(email="pending@example.com"),
            status=ReviewStatus.PENDING_MODERATION,
            body="PENDING-BODY",
        )

        content = Client().get("/").content.decode()

        assert "VERIFIED-BODY" in content
        assert "UNVERIFIED-BODY" not in content
        assert "PENDING-BODY" not in content

    def test_the_section_stays_empty_rather_than_borrowing_content(self) -> None:
        content = Client().get("/").content.decode()

        assert "还没有经核验的评价" in content


class TestReviewInvitation:
    """The one block on the home page that asks the reader for something.

    Asking a shareholder to upload an NNC1 in exchange for something is only
    allowed while two things stay true, and both are checked here: the page
    says what the reward is *and* that it does not depend on the score
    (COMPLIANCE section 3), and every reward it names is one the codebase
    actually gives. Marketing copy is the easiest place in a platform for a
    promise to outrun its implementation.
    """

    def test_the_invitation_is_on_the_page(self) -> None:
        content = Client().get("/").content.decode()

        assert "你踩过的坑，值得让下一个人少踩一次" in content
        assert "NNC1" in content
        assert "NAR1" in content

    def test_it_says_the_reward_does_not_depend_on_the_score(self) -> None:
        """Remove this sentence and the section becomes an offer to buy
        opinions. That is the line COMPLIANCE section 3 draws."""
        content = Client().get("/").content.decode()

        assert "我们不付钱买评价" in content
        assert "给一星的评价，拿到的标记和给五星的完全一样" in content

    def test_it_states_what_happens_to_the_uploaded_document(self) -> None:
        """CLAUDE.md rule 5 - the fields we read and the day the file goes."""
        content = Client().get("/").content.decode()

        assert "90 日内删除" in content
        assert "证件号码一律不入库" in content

    def test_every_reward_it_names_is_one_the_wall_really_gives(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_review: Callable[..., object],
        open_rfq: Rfq,
    ) -> None:
        """The invitation promises a mark and a place in the queue. Both come
        from ``rfq.selectors.open_rfqs``, so the promise is checked against the
        query rather than against itself."""
        from apps.rfq.selectors import open_rfqs

        content = Client().get("/").content.decode()
        assert "已核实用家" in content
        assert "需求单在需求墙上优先展示" in content

        assert open_rfqs().first().buyer_verified is False  # type: ignore[union-attr]
        make_review(provider=make_provider(), author=open_rfq.buyer, is_verified=True)
        assert open_rfqs().first().buyer_verified is True  # type: ignore[union-attr]

    def test_a_visitor_who_already_qualified_is_thanked_instead(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_review: Callable[..., object],
    ) -> None:
        author = make_user(email="already@example.com")
        make_review(provider=make_provider(), author=author, is_verified=True)
        client = Client()
        client.force_login(author)

        response = client.get("/")

        assert response.context["viewer_is_verified_reviewer"] is True
        assert "你已经是已核实用家" in response.content.decode()

    def test_a_signed_out_visitor_still_sees_the_invitation(self) -> None:
        response = Client().get("/")

        assert response.context["viewer_is_verified_reviewer"] is False
        assert "你踩过的坑，值得让下一个人少踩一次" in response.content.decode()
