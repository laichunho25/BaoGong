"""The environment check has to be right about a box nobody can log into."""

import subprocess
import sys
from pathlib import Path

import pytest

from config.settings.env_spec import (
    DEV_SECRET_KEY,
    OPTIONAL,
    REQUIRED,
    invalid,
    missing,
    problems,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_env.py"


def _good_env() -> dict[str, str]:
    return {
        "SECRET_KEY": "x" * 50,
        "DATABASE_URL": "postgres://u:p@db:5432/baogong",
        "REDIS_URL": "redis://cache:6379/0",
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "ADMIN_URL": "console-7f3a/",
        "S3_BUCKET": "baogong-public",
        "S3_PRIVATE_BUCKET": "baogong-private",
        "S3_ACCESS_KEY": "key",
        "S3_SECRET_KEY": "secret",
        "EMAIL_HOST_PASSWORD": "re_test",
        "DEFAULT_FROM_EMAIL": "no-reply@baogong.com.hk",
        "ALLOWED_HOSTS": "www.baogong.com.hk",
    }


def test_a_complete_environment_has_no_problems():
    assert problems(_good_env()) == []


def test_every_missing_variable_is_reported_in_one_pass():
    """The point of the exercise: not one crash-restart cycle per secret."""
    reported = problems({})
    for var in REQUIRED:
        assert any(line.startswith(f"{var.name} ") for line in reported), var.name
    assert len(reported) == len(REQUIRED) + 1  # + the ALLOWED_HOSTS rule


def test_blank_counts_as_missing():
    env = _good_env() | {"ANTHROPIC_API_KEY": "   "}
    assert [var.name for var in missing(env)] == ["ANTHROPIC_API_KEY"]


def test_optional_variables_never_block_a_boot():
    env = _good_env()
    assert problems(env) == []
    assert {var.name for var in missing(env, include_optional=True)} == {
        var.name for var in OPTIONAL
    }


@pytest.mark.parametrize(
    ("key", "value", "fragment"),
    [
        ("SECRET_KEY", DEV_SECRET_KEY, "development fallback"),
        ("SECRET_KEY", "short", "shorter than 32"),
        ("ADMIN_URL", "/admin/", "default console path"),
        ("DEFAULT_FROM_EMAIL", "no-reply@example.com", "example.com"),
    ],
)
def test_present_but_unusable_values_are_rejected(key, value, fragment):
    env = _good_env() | {key: value}
    found = invalid(env)
    assert [var.name for var, _ in found] == [key]
    assert fragment in found[0][1]


def test_render_hostname_satisfies_the_host_rule_on_its_own():
    env = _good_env() | {"ALLOWED_HOSTS": "", "RENDER_EXTERNAL_HOSTNAME": "baogong.onrender.com"}
    assert problems(env) == []


def test_no_hostname_from_either_source_is_a_problem():
    env = _good_env() | {"ALLOWED_HOSTS": ""}
    assert problems(env) == [
        "ALLOWED_HOSTS is empty and RENDER_EXTERNAL_HOSTNAME is unset - "
        "Django would answer 400 to every request"
    ]


def test_script_exits_zero_on_a_complete_environment(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(f"{k}={v}" for k, v in _good_env().items()) + "\n", encoding="utf-8"
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--env-file", str(env_file)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={"PATH": ""},  # an empty environment: only the file may satisfy the check
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_script_exits_nonzero_and_names_the_gap(tmp_path):
    env_file = tmp_path / ".env"
    values = _good_env()
    del values["ANTHROPIC_API_KEY"]
    env_file.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--env-file", str(env_file)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={"PATH": ""},
    )
    assert result.returncode == 1
    assert "ANTHROPIC_API_KEY is not set" in result.stderr
