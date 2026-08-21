"""The migration wait has to be right when nobody is watching the log.

Driven against a stub ``manage.py`` rather than a real database: what is under
test is the waiting, not Django's migration machinery.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "wait_for_migrations.sh"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh not available")


def run(tmp_path: Path, stub: str, attempts: str = "3") -> subprocess.CompletedProcess[str]:
    (tmp_path / "manage.py").write_text(stub, encoding="utf-8")
    return subprocess.run(
        [shutil.which("sh"), str(SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "PATH": str(Path(sys.executable).parent),
            "PYTHON": sys.executable,
            "MIGRATION_WAIT_ATTEMPTS": attempts,
            "MIGRATION_WAIT_INTERVAL": "0",
            # Windows python refuses to start without it; empty and ignored elsewhere.
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        },
    )


UP_TO_DATE = "raise SystemExit(0)\n"
NEVER_READY = "import sys; print('relation does not exist', file=sys.stderr); raise SystemExit(1)\n"
READY_ON_SECOND_CALL = """
import pathlib, sys
marker = pathlib.Path("attempted")
if marker.exists():
    raise SystemExit(0)
marker.touch()
print("no migrations applied yet", file=sys.stderr)
raise SystemExit(1)
"""


def test_passes_straight_through_when_the_schema_is_current(tmp_path):
    result = run(tmp_path, UP_TO_DATE)
    assert result.returncode == 0, result.stderr
    assert "after 1 check(s)" in result.stdout


def test_waits_and_then_proceeds_once_web_has_migrated(tmp_path):
    result = run(tmp_path, READY_ON_SECOND_CALL)
    assert result.returncode == 0, result.stderr
    assert "waiting for the web service to migrate (1/3)" in result.stdout
    assert "after 2 check(s)" in result.stdout


def test_an_unreachable_database_fails_loudly_instead_of_hanging(tmp_path):
    """A bounded wait: the point is to stop crying wolf, not to swallow a fault."""
    result = run(tmp_path, NEVER_READY, attempts="2")
    assert result.returncode == 1
    assert "gave up after 2 attempts" in result.stderr
    assert "relation does not exist" in result.stderr
