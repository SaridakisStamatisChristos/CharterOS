from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from apps.api.main import create_app
from charteros.security.auth import RejectingAuthenticationBackend
from charteros.shared.config import Settings
from charteros.shared.middleware import RequestBodyLimitMiddleware

TEST_DATABASE_URL = "postgresql+psycopg://charteros:test-only@localhost:5432/charteros"


def _settings() -> Settings:
    return Settings(
        environment="test",
        database_url=TEST_DATABASE_URL,
        api_max_request_body_bytes=1024,
        api_max_json_depth=4,
        _env_file=None,
    )


def test_oversized_body_is_rejected_before_authentication() -> None:
    app = create_app(_settings(), auth_backend=RejectingAuthenticationBackend())

    with TestClient(app) as client:
        response = client.post(
            "/v1/fx/rates",
            content=b"x" * 1025,
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json() == {"detail": "request_too_large"}
    assert "x-correlation-id" in response.headers


def test_deeply_nested_json_is_rejected_before_route_work() -> None:
    app = create_app(_settings(), auth_backend=RejectingAuthenticationBackend())
    payload = b'{"a":{"b":{"c":{"d":{"e":1}}}}}'

    with TestClient(app) as client:
        response = client.post(
            "/v1/fx/rates",
            content=payload,
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "request_complexity_exceeded"}


def test_small_request_continues_to_fail_closed_on_authentication() -> None:
    app = create_app(_settings(), auth_backend=RejectingAuthenticationBackend())

    with TestClient(app) as client:
        response = client.post(
            "/v1/fx/rates",
            content=b"{}",
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_slow_request_body_times_out_before_downstream_app() -> None:
    downstream_called = False

    async def downstream(_scope: Scope, _receive: Receive, _send: Send) -> None:
        nonlocal downstream_called
        downstream_called = True

    middleware: ASGIApp = RequestBodyLimitMiddleware(
        downstream,
        max_body_bytes=1024,
        max_json_depth=8,
        body_read_timeout_seconds=0.01,
    )
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/test",
        "raw_path": b"/v1/test",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "client": None,
        "server": None,
        "state": {},
    }
    sent: list[Message] = []

    async def slow_receive() -> Message:
        await asyncio.sleep(0.05)
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def capture_send(message: Message) -> None:
        sent.append(message)

    await middleware(scope, slow_receive, capture_send)

    assert downstream_called is False
    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 408
