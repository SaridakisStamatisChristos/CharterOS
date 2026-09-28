# ADR 0001: Modular Monolith with PostgreSQL Foundation

- Status: Accepted
- Date: 2026-09-28

## Context

CharterOS is starting from an empty repository and will eventually coordinate procurement, transactional booking state, operational execution, projections, and analytical workloads. Splitting these concerns into independently deployed services before stable domain boundaries exist would add distributed failure modes without present value.

## Decision

Start as a typed Python 3.13 modular monolith using FastAPI at the HTTP boundary, SQLAlchemy/Alembic for persistence infrastructure, and PostgreSQL as the canonical transactional database direction.

Graph databases, event brokers, and other specialized stores may be introduced later as rebuildable projections or integration infrastructure. They are not canonical sources of truth.

## Consequences

- Cross-domain transactions can remain local while invariants mature.
- Module boundaries must be enforced in code review and tests rather than by network boundaries.
- Future extraction remains possible through application ports and infrastructure adapters.
- The system avoids premature Kubernetes and microservice operational burden.
