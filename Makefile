.PHONY: install format format-check lint type test test-unit test-integration migrate smoke compose-config quality outbox-worker outbox-worker-once

install:
	python -m pip install -e '.[dev]'

format:
	ruff format .

format-check:
	ruff format --check .

lint:
	ruff check .

type:
	mypy apps charteros tests tools

test:
	pytest

test-unit:
	pytest -m 'not integration'

test-integration:
	pytest -m integration

migrate:
	alembic upgrade head

smoke:
	python tools/app_boot_smoke.py

compose-config:
	docker compose config

outbox-worker:
	python -m apps.outbox_worker.main

outbox-worker-once:
	python -m apps.outbox_worker.main --once

quality: lint format-check type test smoke
