from __future__ import annotations

import os
import re
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text

import apps.api.main as api_main
from charteros.security.auth import AuthenticatedPrincipal, Permission, PrincipalType
from charteros.shared.config import Settings, get_settings

_TEST_ADMIN = AuthenticatedPrincipal(
    subject="pytest-admin",
    principal_type=PrincipalType.ADMINISTRATOR,
    permissions=frozenset(Permission),
    buyer_ids=frozenset(),
    operator_ids=frozenset(),
    issuer="pytest",
    key_id="pytest",
)
_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class _TrustedTestAuthenticationBackend:
    """Injected only by pytest; production authentication remains fail-closed."""

    def authenticate(self, authorization_header: str | None) -> AuthenticatedPrincipal:
        del authorization_header
        return _TEST_ADMIN


def _build_test_auth_backend(_settings: Settings) -> _TrustedTestAuthenticationBackend:
    return _TrustedTestAuthenticationBackend()


@pytest.fixture(autouse=True)
def _clear_settings_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    get_settings.cache_clear()
    monkeypatch.setattr(api_main, "build_auth_backend", _build_test_auth_backend)
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _isolate_integration_database(request: pytest.FixtureRequest) -> Iterator[None]:
    """Keep PostgreSQL-backed tests independent of collection order.

    Integration tests exercise committed transactions rather than a rollback-only test
    session, so each test starts from the migrated schema with application tables empty.
    The Alembic revision table is deliberately preserved.
    """

    if request.node.get_closest_marker("integration") is None:
        yield
        return

    database_url = os.environ.get("CHARTEROS_DATABASE_URL")
    if not database_url:
        yield
        return

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            table_names: list[str] = list(
                connection.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' AND tablename <> 'alembic_version' "
                        "ORDER BY tablename"
                    )
                ).scalars()
            )
            unsafe = [name for name in table_names if _TABLE_NAME.fullmatch(name) is None]
            if unsafe:
                raise RuntimeError(f"unexpected PostgreSQL table names in test schema: {unsafe}")
            if table_names:
                quoted = ", ".join(f'"{name}"' for name in table_names)
                connection.exec_driver_sql(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")
        yield
    finally:
        engine.dispose()
