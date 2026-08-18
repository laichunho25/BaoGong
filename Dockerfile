# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq5 curl gettext \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependency layer: cached until pyproject/uv.lock change.
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-install-project --group dev

COPY . .

RUN python -c "import compileall,sys; sys.exit(0 if compileall.compile_dir('apps', quiet=1) else 1)"

# The message catalogue is compiled here rather than committed: a `.mo` is a
# build artefact, and a stale one serves the wrong words without saying so.
# Without this step every model choice label falls back to its English msgid.
RUN django-admin compilemessages --locale zh_Hans

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
