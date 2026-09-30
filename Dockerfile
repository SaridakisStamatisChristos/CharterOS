ARG PYTHON_VERSION=3.13.15

FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ARG UV_VERSION=0.12.21
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_NO_PROGRESS=1

WORKDIR /build

RUN python -m pip install "uv==${UV_VERSION}"

COPY pyproject.toml uv.lock README.md ./
COPY apps ./apps
COPY charteros ./charteros

RUN uv sync --frozen --no-dev --no-editable

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

RUN addgroup --system charteros \
    && adduser --system --ingroup charteros --no-create-home charteros

COPY --from=builder --chown=charteros:charteros /build/.venv /app/.venv
COPY --chown=charteros:charteros migrations ./migrations
COPY --chown=charteros:charteros alembic.ini ./alembic.ini

USER charteros

EXPOSE 8000

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2).read()"]

CMD ["python", "-m", "apps.api"]
