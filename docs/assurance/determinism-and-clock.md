# Determinism and clock assurance

CharterOS treats time and distance as explicit deterministic inputs rather than ambient process behavior.

## Clock boundary

Domain and application logic receive time through the shared `Clock` protocol. `SystemClock` supplies production UTC time; `FrozenClock` supplies deterministic test time. FastAPI composition provides the clock to application services, and route modules do not define independent wall-clock authority.

This boundary supports reproducible decisions, no-hindsight evaluation and tests that can prove exact temporal behavior.

## Distance determinism

Canonical geographic distance uses the documented pipeline:

1. decimal latitude/longitude inputs are normalized to six decimal degrees;
2. Haversine computation uses the runtime's floating-point/libm behavior;
3. nautical-mile output is stabilized to six decimal places;
4. policy-facing distance is converted to integer tenths of a nautical mile using deterministic half-up rounding.

Matching and repositioning reuse the same centralized distance implementation rather than maintaining independent formulas.

## Verification

The regression suite checks UTC-aware system time, frozen-clock stability, absence of direct wall-clock reads in protected domain/application paths, canonical distance quanta, symmetry, zero distance, representative route distance, input-noise stability and half-up rounding behavior.

The assurance claim is repository determinism under the documented runtime and policy contracts. It is not a live-deployment certification.
