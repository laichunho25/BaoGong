# 包公 BaoGong

Hong Kong TCSP (Trust or Company Service Provider) licensee comparison, verified
reviews and RFQ matching.

The name carries both halves of what the platform does. 包公 is the popular name
of Bao Zheng, the byword in Chinese culture for impartiality - a magistrate who
decided on evidence rather than connections. Read literally, the same two
characters say 包羅香港「公」司服務: everything you need to open a Hong Kong
company, in one place. The platform borrows the fairness, never the authority:
it is not a government body and it does not decide for anyone. See
`docs/BRAND.md`.

Production runs at **www.baogong.com.hk** (Render, Singapore region - see
`docs/DEPLOY_RENDER.md`).

See `CLAUDE.md` for the project constitution and `docs/` for the authoritative
specs.

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

To see the scheduled work happen - the daily register sync above all - run the
worker and the scheduler alongside `runserver`:

```bash
uv run python manage.py runserver
uv run celery -A config worker -l info --pool=threads --concurrency 2
uv run celery -A config beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

`--pool=threads` is not optional on Windows. Celery's default prefork pool uses
POSIX semaphores through billiard, which raises `PermissionError: [WinError 5]`
on every child; the worker then respawns them forever and looks alive in the
log while completing no tasks at all. Linux and the Docker image are unaffected,
which is why `render.yaml` leaves the pool at its default.

Exactly one beat process, ever. Two schedulers means two register syncs a day.

## Demo data

A freshly synced database holds 7,457 real licensees and nothing else, so every
page built after the directory renders correctly and shows nothing. To look at
the rest of the product:

```bash
uv run python manage.py sync_tcsp     # the real register, if not already loaded
uv run python manage.py seed_demo     # invented prices, reviews, RFQs, quotes
uv run python manage.py seed_demo --reset
```

Sign in as `buyer@seed.local` or `moderator@seed.local`, password
`seed-demo-1234`. The command refuses to run unless `DEBUG` is on: it writes
fabricated reviews and prices under the names of real licensed companies, which
must never reach a production database. Agents are off by default, so the
shortlist and the quote analysis it produces come from the rule fallback.

## CSS

Tailwind is built with the standalone CLI (no npm tree in the repo):

```powershell
./scripts/tailwind.ps1          # one-off
./scripts/tailwind.ps1 -Watch   # during development
```

## Translations

Most user-facing copy is written in Simplified Chinese at the source. Model
choice labels are not: they are English msgids, and an untranslated one reaches
the page, so `locale/zh_Hans/LC_MESSAGES/django.po` translates every label a
buyer or a company can see. Internal-console strings are left blank on purpose.

`.mo` files are build output and are not committed. After changing a `.po` (and
once, on a fresh checkout) run:

```bash
uv run python manage.py makemessages -l zh_Hans   # after adding new strings
uv run django-admin compilemessages --locale zh_Hans
```

Both need GNU gettext on `PATH` (Windows: `gettext-iconv/bin`). The Docker image
and CI compile the catalogue themselves.

## Definition of done

Every task must satisfy `CLAUDE.md` section 7 before it counts as finished:
ruff, mypy, pytest with >=80% coverage on new code, no pending migrations,
updated docs, and a conventional commit explaining *why*.
