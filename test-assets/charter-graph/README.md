# Charter Graph PR4 reference assets

This directory captures the PR4 bitemporal regression scenario reused from the standalone
`charter_graph_testbench_v0.1.0` development asset supplied with the PR4 handoff.

The standalone testbench remains a reference/simulation package; its NetworkX graph is **not**
production state. PR4 ports the no-hindsight invariant into the PostgreSQL-backed fleet timeline:
a position can affect a historical reconstruction only if its event time is applicable **and** its
recorded/knowledge time is at or before the requested knowledge cutoff.

The JSON fixture deliberately mirrors the testbench's late-position backtest shape so future graph
projection and matching PRs can cross-check the same scenario without making NetworkX a runtime
source of truth.
