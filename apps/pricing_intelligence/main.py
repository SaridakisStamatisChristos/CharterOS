from __future__ import annotations

import argparse

from sqlalchemy import text

from charteros.application.pricing_intelligence import PricingIntelligenceService
from charteros.domain.shared.time_range import TimeRange
from charteros.infrastructure.db.engine import build_engine, build_session_factory
from charteros.infrastructure.db.repositories import SqlAlchemyPricingIntelligenceRepository
from charteros.pricing_intelligence import canonical_json
from charteros.shared.config import get_settings
from charteros.shared.logging import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a deterministic read-only CharterOS historical pricing dataset"
    )
    parser.add_argument("--window-start", required=True)
    parser.add_argument("--window-end", required=True)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    from datetime import datetime

    source_window = TimeRange(
        datetime.fromisoformat(args.window_start),
        datetime.fromisoformat(args.window_end),
    )

    settings = get_settings()
    configure_logging(settings)
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        with factory() as session, session.begin():
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            dataset = PricingIntelligenceService(
                SqlAlchemyPricingIntelligenceRepository(session)
            ).build_dataset(
                source_window=source_window,
                limit=args.limit,
            )
        print(canonical_json(dataset))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
