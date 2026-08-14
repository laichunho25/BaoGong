# QS Matching Platform

Hong Kong TCSP (Trust or Company Service Provider) licensee comparison, verified
reviews and RFQ matching. See `CLAUDE.md` for the project constitution and
`docs/` for the authoritative specs.

## Requirements

- Python 3.12 (`uv python install 3.12`)
- Docker Desktop (WSL2 backend on Windows)
- uv

## Quick start

```bash
cp .env.example .env          # then fill in SECRET_KEY and ANTHROPIC_API_KEY
docker compose up --build
```

- App: http://localhost:8000
- Health: http://localhost:8000/healthz
- MinIO console: http://localhost:9001 (`minioadmin` / `minioadmin`)
- Internal console: `/$ADMIN_URL/` — **not** `/admin/`, which returns 404 on
  purpose. Locally `ADMIN_URL` defaults to `admin`; production refuses to boot
  on that value (docs/DEPLOY_RENDER.md section 8).

## Local (no Docker)

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run mypy apps/
```

Tests that do not touch the database run without a Postgres server. Anything
marked `django_db` needs the `db` service from docker-compose.

## CSS

Tailwind is built with the standalone CLI (no npm tree in the repo):

```powershell
./scripts/tailwind.ps1          # one-off
./scripts/tailwind.ps1 -Watch   # during development
```

## Definition of done

Every task must satisfy `CLAUDE.md` section 7 before it counts as finished:
ruff, mypy, pytest with >=80% coverage on new code, no pending migrations,
updated docs, and a conventional commit explaining *why*.
