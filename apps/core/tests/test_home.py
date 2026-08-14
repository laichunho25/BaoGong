import pytest
from django.test import Client

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
        content = Client().get("/").content.decode()
        assert "並非香港公司註冊處或任何政府機構" in content
        assert "不構成法律、稅務、會計或任何專業意見" in content

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
