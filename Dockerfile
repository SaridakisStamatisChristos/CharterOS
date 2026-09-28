FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system charteros && adduser --system --ingroup charteros charteros

COPY pyproject.toml README.md ./
COPY apps ./apps
COPY charteros ./charteros
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini

RUN python -m pip install --upgrade pip \
    && python -m pip install .

USER charteros

EXPOSE 8000

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
