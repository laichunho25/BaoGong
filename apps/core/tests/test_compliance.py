"""COMPLIANCE.md section 2 - banned phrase screening.

These tests encode the compliance rules themselves, so a failure here means a
legal boundary moved, not just a refactor.
"""

import pytest

from apps.core.compliance import (
    RuleCode,
    Severity,
    check_banned_phrases,
    has_blocking_violation,
)


class TestCleanText:
    def test_empty_text_passes(self):
        assert check_banned_phrases("") == []

    @pytest.mark.parametrize(
        "text",
        [
            "我们提供香港持牌秘书公司的比较信息。",
            "银行是否批核开户由银行全权决定。",
            "该服务商支持简体中文与远程办理。",
        ],
    )
    def test_ordinary_copy_passes(self, text):
        assert check_banned_phrases(text) == []
        assert has_blocking_violation(text) is False


class TestBankGuarantees:
    @pytest.mark.parametrize(
        "text",
        ["保证开户成功", "保證開戶成功", "开户包成功", "100% 开户", "包过", "必定批核"],
    )
    def test_guarantee_phrases_are_blocking(self, text):
        violations = check_banned_phrases(text)
        assert violations, f"{text!r} should have been caught"
        assert violations[0].severity is Severity.BLOCKING
        assert has_blocking_violation(text)

    @pytest.mark.parametrize("text", ["开户成功率 95%", "开戶成功率:98", "90% 开户成功"])
    def test_quantified_success_rate_is_blocking(self, text):
        codes = {v.code for v in check_banned_phrases(text)}
        assert RuleCode.BANK_SUCCESS_RATE in codes


class TestFalseOfficialStatus:
    @pytest.mark.parametrize("text", ["官方认证平台", "政府推荐", "注册处指定", "官方授权"])
    def test_official_claims_are_blocking(self, text):
        violations = check_banned_phrases(text)
        assert violations
        assert violations[0].code is RuleCode.FALSE_OFFICIAL_STATUS
        assert violations[0].severity is Severity.BLOCKING


class TestPlatformRole:
    @pytest.mark.parametrize(
        "text",
        ["本平台提供公司秘书服务", "本平台为您提供注册服务", "我们代办注册"],
    )
    def test_platform_posing_as_provider_is_blocking(self, text):
        codes = {v.code for v in check_banned_phrases(text)}
        assert RuleCode.PLATFORM_AS_SERVICE_PROVIDER in codes


class TestAbsoluteRanking:
    @pytest.mark.parametrize("text", ["全港第一", "排名第一的秘书公司", "全港最好"])
    def test_superlatives_warn_but_do_not_block(self, text):
        violations = check_banned_phrases(text)
        assert violations
        assert all(v.severity is Severity.WARN for v in violations)
        assert has_blocking_violation(text) is False


class TestViolationDetail:
    def test_reports_offsets_and_explanation(self):
        text = "我们承诺保证开户成功，请放心。"
        (violation,) = check_banned_phrases(text)
        assert text[violation.start : violation.end] == violation.matched_text
        assert "COMPLIANCE" in violation.explanation

    def test_multiple_violations_are_ordered_by_position(self):
        text = "官方认证，并且保证开户成功。"
        violations = check_banned_phrases(text)
        assert len(violations) >= 2
        assert violations == sorted(violations, key=lambda v: v.start)
