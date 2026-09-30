from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from charteros.application.resource_limits import AbuseBudget, RateBudgetDecision


class SqlAlchemyRateBudgetRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def consume(
        self,
        *,
        budget: AbuseBudget,
        identity_digest: str,
        limit: int,
        window_seconds: int,
    ) -> RateBudgetDecision:
        if limit < 1:
            raise ValueError("rate budget limit must be positive")
        if window_seconds < 1:
            raise ValueError("rate budget window must be positive")
        if len(identity_digest) != 64:
            raise ValueError("identity digest must be a SHA-256 hex digest")

        row = (
            self._session.execute(
                text(
                    """
                WITH params AS (
                    SELECT to_timestamp(
                        floor(extract(epoch FROM clock_timestamp()) / :window_seconds)
                        * :window_seconds
                    ) AS window_started_at
                ),
                attempted AS (
                    INSERT INTO api_rate_limit_windows (
                        budget,
                        identity_digest,
                        window_started_at,
                        request_count
                    )
                    SELECT
                        :budget,
                        :identity_digest,
                        params.window_started_at,
                        1
                    FROM params
                    ON CONFLICT (budget, identity_digest, window_started_at)
                    DO UPDATE
                    SET request_count = api_rate_limit_windows.request_count + 1
                    WHERE api_rate_limit_windows.request_count < :request_limit
                    RETURNING request_count, window_started_at
                )
                SELECT
                    TRUE AS allowed,
                    attempted.request_count,
                    attempted.window_started_at,
                    GREATEST(
                        1,
                        CEIL(
                            extract(
                                epoch FROM (
                                    attempted.window_started_at
                                    + make_interval(secs => :window_seconds)
                                    - clock_timestamp()
                                )
                            )
                        )::integer
                    ) AS retry_after_seconds
                FROM attempted
                UNION ALL
                SELECT
                    FALSE AS allowed,
                    existing.request_count,
                    existing.window_started_at,
                    GREATEST(
                        1,
                        CEIL(
                            extract(
                                epoch FROM (
                                    existing.window_started_at
                                    + make_interval(secs => :window_seconds)
                                    - clock_timestamp()
                                )
                            )
                        )::integer
                    ) AS retry_after_seconds
                FROM api_rate_limit_windows AS existing
                CROSS JOIN params
                WHERE existing.budget = :budget
                  AND existing.identity_digest = :identity_digest
                  AND existing.window_started_at = params.window_started_at
                  AND NOT EXISTS (SELECT 1 FROM attempted)
                LIMIT 1
                """
                ),
                {
                    "budget": budget.value,
                    "identity_digest": identity_digest,
                    "request_limit": limit,
                    "window_seconds": window_seconds,
                },
            )
            .mappings()
            .one()
        )

        return RateBudgetDecision(
            allowed=bool(row["allowed"]),
            request_count=int(row["request_count"]),
            limit=limit,
            retry_after_seconds=int(row["retry_after_seconds"]),
        )

    def delete_windows_older_than(self, *, retention_seconds: int, limit: int) -> int:
        if retention_seconds < 1:
            raise ValueError("retention_seconds must be positive")
        if limit < 1:
            raise ValueError("cleanup limit must be positive")
        rows = self._session.execute(
            text(
                """
                WITH victims AS (
                    SELECT budget, identity_digest, window_started_at
                    FROM api_rate_limit_windows
                    WHERE window_started_at
                        < clock_timestamp() - make_interval(secs => :retention)
                    ORDER BY window_started_at, budget, identity_digest
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED
                )
                DELETE FROM api_rate_limit_windows AS target
                USING victims
                WHERE target.budget = victims.budget
                  AND target.identity_digest = victims.identity_digest
                  AND target.window_started_at = victims.window_started_at
                RETURNING target.budget
                """
            ),
            {"retention": retention_seconds, "limit": limit},
        ).all()
        return len(rows)
