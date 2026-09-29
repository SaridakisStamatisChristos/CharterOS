from collections.abc import Iterator

import pytest

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
