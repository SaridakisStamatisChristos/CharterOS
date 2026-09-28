# CharterOS

CharterOS is the transaction and operational control foundation for B2B aviation charter procurement and execution.

This repository is intentionally starting as a **modular monolith**. The first milestone establishes reproducible development, strict configuration, structured logging, PostgreSQL/Alembic plumbing, health checks, CI, and architectural boundaries without implementing business-domain behavior early.

## Requirements

- Python 3.13+
- Docker + Docker Compose (recommended for PostgreSQL)
- GNU Make (optional convenience)

## Quick start

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
docker compose up -d postgres
alembic upgrade head
uvicorn apps.api.main:app --reload
```

Then open `http://127.0.0.1:8000/health`.

## Quality gate

```bash
ruff check .
ruff format --check .
mypy apps charteros tests tools
pytest
python tools/app_boot_smoke.py
docker compose config
```

The integration test suite expects PostgreSQL to be reachable through `CHARTEROS_DATABASE_URL`. The provided Compose configuration exposes a local development instance on port `5432`.

## Configuration

Runtime settings are environment-driven and use the `CHARTEROS_` prefix. See `.env.example`. Secrets must not be committed.

## Architecture

See [`docs/architecture/README.md`](docs/architecture/README.md) and the ADRs in [`docs/adr/`](docs/adr/).

## Scope of PR1

PR1 contains repository and runtime foundations only. It deliberately does **not** implement missions, RFQs, quotes, bookings, matching, graph projection, pricing, or other later-roadmap domain functionality.
