from __future__ import annotations

import asyncio
from time import perf_counter
from uuid import UUID, uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from charteros.observability import OperationalMetrics
from charteros.shared.context import bind_correlation_id, reset_correlation_id

_HEADER_NAME = b"x-correlation-id"


class RequestBodyLimitMiddleware:
    """Bound request buffering before authentication, validation, or transaction work begins."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int,
        max_json_depth: int,
        body_read_timeout_seconds: float,
        metrics: OperationalMetrics | None = None,
    ) -> None:
        if max_body_bytes < 1:
            raise ValueError("max_body_bytes must be positive")
        if max_json_depth < 1:
            raise ValueError("max_json_depth must be positive")
        if body_read_timeout_seconds <= 0:
            raise ValueError("body_read_timeout_seconds must be positive")
        self.app = app
        self._max_body_bytes = max_body_bytes
        self._max_json_depth = max_json_depth
        self._body_read_timeout_seconds = body_read_timeout_seconds
        self._metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        try:
            content_length = self._content_length(scope)
        except ValueError:
            self._record_rejection("invalid_content_length")
            await self._reject(send, 400, "invalid_content_length")
            return
        if content_length is not None and content_length > self._max_body_bytes:
            self._record_rejection("request_too_large")
            await self._reject(send, 413, "request_too_large")
            return

        body = bytearray()
        try:
            async with asyncio.timeout(self._body_read_timeout_seconds):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    if message["type"] != "http.request":
                        continue
                    body.extend(message.get("body", b""))
                    if len(body) > self._max_body_bytes:
                        self._record_rejection("request_too_large")
                        await self._reject(send, 413, "request_too_large")
                        return
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            self._record_rejection("request_body_timeout")
            await self._reject(send, 408, "request_body_timeout")
            return

        if self._is_json(scope) and body and _json_nesting_depth(body) > self._max_json_depth:
            self._record_rejection("request_complexity_exceeded")
            await self._reject(send, 422, "request_complexity_exceeded")
            return

        delivered = False

        async def replay_receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)

    def _record_rejection(self, reason: str) -> None:
        if self._metrics is not None:
            self._metrics.api_rejection(reason)

    @staticmethod
    def _content_length(scope: Scope) -> int | None:
        for raw_name, raw_value in scope.get("headers", []):
            if raw_name.lower() != b"content-length":
                continue
            try:
                value = int(raw_value.decode("ascii"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError("invalid Content-Length header") from exc
            if value < 0:
                raise ValueError("invalid Content-Length header")
            return value
        return None

    @staticmethod
    def _is_json(scope: Scope) -> bool:
        for raw_name, raw_value in scope.get("headers", []):
            if raw_name.lower() == b"content-type":
                media_type = bytes(raw_value).split(b";", 1)[0].strip().lower()
                return media_type == b"application/json" or media_type.endswith(b"+json")
        return False

    @staticmethod
    async def _reject(send: Send, status_code: int, detail: str) -> None:
        payload = ('{"detail":"' + detail + '"}').encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})


def _json_nesting_depth(body: bytes | bytearray) -> int:
    depth = 0
    maximum = 0
    in_string = False
    escaped = False
    for byte in body:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in (0x7B, 0x5B):
            depth += 1
            maximum = max(maximum, depth)
        elif byte in (0x7D, 0x5D):
            depth = max(0, depth - 1)
    return maximum


class CorrelationIdMiddleware:
    """Bind one validated correlation ID to each HTTP request and response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        correlation_id = self._extract_or_create(scope)
        token = bind_correlation_id(correlation_id)

        async def send_with_correlation_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((_HEADER_NAME, correlation_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_correlation_id)
        finally:
            reset_correlation_id(token)

    @staticmethod
    def _extract_or_create(scope: Scope) -> str:
        for raw_name, raw_value in scope.get("headers", []):
            if raw_name.lower() != _HEADER_NAME:
                continue
            try:
                candidate = raw_value.decode("ascii")
                return str(UUID(candidate))
            except (UnicodeDecodeError, ValueError):
                break
        return str(uuid4())

class OperationalMetricsMiddleware:
    """Measure bounded HTTP request outcomes without high-cardinality path labels."""

    def __init__(self, app: ASGIApp, *, metrics: OperationalMetrics) -> None:
        self.app = app
        self._metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        status_code = 500
        self._metrics.api_request_started()

        async def send_with_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        finally:
            route = scope.get("route")
            path_template = getattr(route, "path", None)
            bounded_route = path_template if isinstance(path_template, str) else "unmatched"
            method = str(scope.get("method", "UNKNOWN"))
            self._metrics.api_request_finished(
                method=method,
                route=bounded_route,
                status_code=status_code,
                duration_seconds=perf_counter() - started,
            )
