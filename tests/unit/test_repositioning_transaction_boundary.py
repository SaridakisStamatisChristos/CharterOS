from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import cast

import pytest
from sqlalchemy.orm import Session

from apps.api.routes import repositioning as route
from charteros.application.repositioning import (
    RepositionOptimizationSnapshot,
    optimize_reposition_snapshot,
)
from charteros.repositioning import RepositionOptimization

BASE = datetime(2026, 9, 30, tzinfo=UTC)


class _FakeTransaction(AbstractContextManager[None]):
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def __enter__(self) -> None:
        assert not self._session.active
        self._session.active = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._session.active = False


class _FakeSession:
    def __init__(self) -> None:
        self.active = False
        self.executed = 0

    def begin(self) -> AbstractContextManager[None]:
        return _FakeTransaction(self)

    def execute(self, _statement: object) -> None:
        assert self.active
        self.executed += 1

    def in_transaction(self) -> bool:
        return self.active


class _FakeService:
    def __init__(
        self,
        session: _FakeSession,
        snapshot: RepositionOptimizationSnapshot,
    ) -> None:
        self._session = session
        self._snapshot = snapshot

    def materialize_snapshot(self, **_kwargs: object) -> RepositionOptimizationSnapshot:
        assert self._session.active
        return self._snapshot


def test_route_closes_repeatable_read_transaction_before_cpu_solver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = _FakeSession()
    snapshot = RepositionOptimizationSnapshot(
        projection_version=32,
        graph_knowledge_cutoff=BASE,
        evaluated_at=BASE,
        window_start=BASE,
        window_end=BASE + timedelta(hours=1),
        structural=(),
        candidates=(),
        opportunities=(),
        airports=(),
    )
    service = _FakeService(fake_session, snapshot)
    solver_observed_transaction: list[bool] = []

    monkeypatch.setattr(route, "_service", lambda _session: service)

    def guarded_solver(
        value: RepositionOptimizationSnapshot,
    ) -> RepositionOptimization:
        solver_observed_transaction.append(fake_session.in_transaction())
        return optimize_reposition_snapshot(value)

    monkeypatch.setattr(route, "optimize_reposition_snapshot", guarded_solver)

    response = route.optimize_repositioning(
        cast(Session, fake_session),
        window_start=BASE,
        window_end=BASE + timedelta(hours=1),
        evaluated_at=BASE,
        empty_leg_limit=1,
        opportunity_limit=1,
    )

    assert fake_session.executed == 1
    assert solver_observed_transaction == [False]
    assert response.projection_version == 32
    assert response.structural_empty_leg_count == 0
