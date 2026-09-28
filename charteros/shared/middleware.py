from __future__ import annotations

from uuid import UUID, uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from charteros.shared.context import bind_correlation_id, reset_correlation_id

_HEADER_NAME = b"x-correlation-id"


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
