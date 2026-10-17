.PHONY: install format format-check lint type test test-unit test-integration migrate smoke compose-config quality outbox-worker outbox-worker-once graph-rebuild graph-verify graph-status resource-cleanup dev-identity-init dev-identity-env dev-identity-serve dev-identity-token

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

graph-rebuild:
	python -m apps.graph_projection.main rebuild --target-version 1

graph-verify:
	python -m apps.graph_projection.main verify --version 1

graph-status:
	python -m apps.graph_projection.main status

resource-cleanup:
	python -m apps.resource_cleanup.main

dev-identity-init:
	python tools/dev_identity.py init

dev-identity-env:
	@python tools/dev_identity.py env

dev-identity-serve:
	python tools/dev_identity.py serve

dev-identity-token:
	@python tools/dev_identity.py token

quality: lint format-check type test smoke