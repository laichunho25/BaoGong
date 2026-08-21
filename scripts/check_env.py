#!/usr/bin/env python
"""Report every environment problem production would refuse to boot on.

Runs before the app, on purpose: it imports nothing but the standard library
and ``config.settings.env_spec``, so it still works on a container whose
settings module cannot be imported - which is the only moment anyone needs it.

    python scripts/check_env.py                 # check the live environment
    python scripts/check_env.py --env-file .env # check a file before deploying
    python scripts/check_env.py --show-optional # list the degraded-mode ones too

Exit code 0 means production would boot; 1 means it would not.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

# Loaded by file path, not by import: ``config/__init__.py`` pulls in Celery,
# and this script has to survive an environment where the app's dependencies
# are missing or broken.
_spec_path = Path(__file__).resolve().parents[1] / "config" / "settings" / "env_spec.py"
_spec = importlib.util.spec_from_file_location("baogong_env_spec", _spec_path)
assert _spec is not None and _spec.loader is not None
env_spec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(env_spec)

OPTIONAL, REQUIRED = env_spec.OPTIONAL, env_spec.REQUIRED
missing, problems = env_spec.missing, env_spec.problems


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a dotenv file well enough to answer "is it set?".

    Deliberately not django-environ: this has to run before the dependencies
    are guaranteed importable.
    """
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        help="check this dotenv file instead of the live environment",
    )
    parser.add_argument(
        "--show-optional",
        action="store_true",
        help="also list unset optional variables and what stays switched off",
    )
    args = parser.parse_args(argv)

    values = dict(os.environ)
    if args.env_file:
        if not args.env_file.exists():
            print(f"check_env: no such file: {args.env_file}", file=sys.stderr)
            return 1
        values.update(read_env_file(args.env_file))

    found = problems(values)

    if args.show_optional:
        unset = [var for var in missing(values, include_optional=True) if not var.required]
        if unset:
            print("Optional, currently switched off:")
            for var in unset:
                print(f"  ~ {var.name} - {var.why}")
            print()

    if not found:
        print(f"check_env: OK - all {len(REQUIRED)} required variables are set and usable.")
        return 0

    print(f"check_env: {len(found)} problem(s) - production will not boot:", file=sys.stderr)
    for line in found:
        print(f"  - {line}", file=sys.stderr)
    print(
        "\nSet these in the Render dashboard under Env Groups -> baogong-shared "
        f"({len(OPTIONAL)} further variables are optional; --show-optional lists them).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
