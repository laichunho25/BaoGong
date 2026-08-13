from django.test import RequestFactory

from apps.core.context_processors import compliance


class TestComplianceContext:
    def test_exposes_registry_attribution_required_by_compliance_section_1(self, settings):
        context = compliance(RequestFactory().get("/"))
        assert context["registry_source_name"] == settings.REGISTRY_SOURCE_NAME
        assert context["registry_source_url"] == settings.REGISTRY_SOURCE_URL

    def test_source_name_credits_the_companies_registry(self):
        context = compliance(RequestFactory().get("/"))
        assert "公司註冊處" in context["registry_source_name"]
