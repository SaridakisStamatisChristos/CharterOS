# CharterOS Architecture

## Decision

CharterOS begins as a **modular monolith** with PostgreSQL as the future canonical transactional store. This keeps transaction boundaries explicit while avoiding premature service distribution.

The repository is divided into four conceptual layers:

1. `domain` — pure business rules and value objects; no ORM or FastAPI dependencies.
2. `application` — use-case orchestration and ports.
3. `infrastructure` — SQLAlchemy persistence, outbox, graph projections, and external adapters.
4. `apps` — delivery processes such as the FastAPI API and, later, background workers.

PR1 establishes only the runtime and architectural boundary. Domain behavior arrives sequentially in later roadmap PRs.

## Canonical data direction

PostgreSQL will become the system of record for transactional business state. Graph and analytical stores are projections and must be rebuildable from canonical domain events. PR1 does not introduce a graph database or event broker.

## Reliability foundation

- Configuration is validated on process startup and environment-driven.
- Logs are JSON and carry correlation IDs.
- Database mappings are infrastructure-only; ORM entities must not leak into the domain layer.
- Alembic owns schema migration history from the first implementation PR.
- CI runs formatting, linting, typing, migration, tests, API boot smoke, and Compose validation.

## Scaling posture

The architecture preserves future seams for transactional outbox workers, projections, optimization, and integrations without creating Kubernetes or microservices before load and organizational boundaries justify them.

## Non-goals in PR1

No mission, RFQ, quote, booking, matching, pricing, graph, tender, payment, auth, or UI functionality is implemented here.
