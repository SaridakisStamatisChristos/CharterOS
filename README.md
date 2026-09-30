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
python -m pip install "uv==0.12.21"
uv sync --frozen --extra dev
docker compose up -d postgres
alembic upgrade head
uvicorn apps.api.main:app --reload
```

Then open `http://127.0.0.1:8000/health`.

## Quality gate

```bash
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run mypy apps charteros tests tools
uv run bandit -q -r apps charteros tools
uv run pip-audit --strict --skip-editable
uv run pytest
uv run python tools/app_boot_smoke.py
docker compose config
uv run alembic check
```

The integration test suite expects PostgreSQL to be reachable through `CHARTEROS_DATABASE_URL`. The provided Compose configuration exposes a local development instance on port `5432`.

## Configuration

Runtime settings are environment-driven and use the `CHARTEROS_` prefix. See `.env.example`. The database URL is required explicitly. Staging/production also require a complete OIDC issuer/audience/JWKS configuration and reject known development database passwords. The API defaults to loopback; container deployments explicitly bind `0.0.0.0` inside the container namespace. Secrets must not be committed.

## Architecture

See [`docs/architecture/README.md`](docs/architecture/README.md) and the ADRs in [`docs/adr/`](docs/adr/).

## Scope of PR1

PR1 contains repository and runtime foundations only. It deliberately does **not** implement missions, RFQs, quotes, bookings, matching, graph projection, pricing, or other later-roadmap domain functionality.

## License

**CharterOS is proprietary software. It is not open source.**

Public repository access grants only the limited non-commercial internal evaluation rights stated in
[`LICENSE`](LICENSE). Production use, commercial use, redistribution, derivative works, hosted or
managed-service use, competitive implementation, AI/ML training use, sublicensing, and other broader
rights require a separate written agreement from the copyright holder.

Copyright © 2026 Stamatis-Christos Saridakis. All Rights Reserved.
