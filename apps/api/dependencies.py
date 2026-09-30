from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from charteros.domain.shared.ids import CorrelationId
from charteros.shared.clock import Clock
from charteros.shared.context import correlation_id_context


def get_clock(request: Request) -> Clock:
    return request.app.state.clock


def get_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session


def get_correlation_id() -> CorrelationId:
    value = correlation_id_context.get()
    if value is None:
        return CorrelationId.new()
    return CorrelationId.parse(value)
