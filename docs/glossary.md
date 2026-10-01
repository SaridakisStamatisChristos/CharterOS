# CharterOS glossary

A compact vocabulary for reviewers, operators, and contributors.

| Term | Meaning in CharterOS |
| --- | --- |
| **Mission** | Buyer intent and sourcing lifecycle for a requested charter movement. |
| **RFQ** | Supplier-directed request-for-quote evidence tied to a Mission. |
| **Quote** | Immutable commercial offer revision with explicit lineage/supersession. |
| **Tender** | Sealed multi-supplier reverse-auction workflow that reuses canonical Mission/RFQ/Quote/Booking authorities. |
| **ProcurementApproval** | Buyer approval bound to exact evidence before award; it is not itself an award or capacity reservation. |
| **Booking** | Canonical award/execution record created through the booking authority boundary. |
| **Capacity reservation** | Database-enforced aircraft time interval coupled to a Booking to prevent overlapping commitments. |
| **Contract** | Bilateral acceptance evidence that does not rewrite the accepted Quote. |
| **Disruption** | Post-booking operational exception/recovery workflow preserving original award history. |
| **Financial reconciliation** | Invoice, dispute, variance, and payable evidence; it does not claim external payment settlement. |
| **FX evidence** | Immutable bitemporal exchange-rate evidence used for auditable cross-currency decisions. |
| **FX lock** | Short-lived executable cross-currency commitment derived from explicit FX evidence. |
| **Charter Graph** | Versioned, event-derived, rebuildable read model. It is not canonical command authority. |
| **Projection** | Derived state built from canonical event history, with versioning, verification, and activation lifecycle. |
| **Outbox** | Transactional event table committed atomically with business state and delivered at least once. |
| **Consumer receipt** | Durable record used to make database-backed event consumption replay-safe and idempotent. |
| **Bitemporal** | Modeling both business/event validity time and system knowledge/recording time. |
| **known_as_of** | Knowledge cutoff used to prevent historical queries from seeing facts learned later. |
| **No hindsight** | Rule that historical decisions may use only evidence that was known at the decision cutoff. |
| **Canonical authority** | Component allowed to decide or mutate business truth under explicit invariants. |
| **Derived authority** | Read/analysis output with deliberately narrower authority; it cannot silently mutate canonical truth. |
| **Evidence package** | Deterministic reconstruction of audit-relevant facts and provenance from canonical records. |
| **Evidence-integrity stream** | Database-enforced append-only/hash-linked integrity evidence. |
| **Ambiguous commit** | Failure where the client cannot know from the transport outcome alone whether the database committed. |
| **Reposition optimization** | Exact deterministic decision-support calculation for structural empty-leg/deadhead economics. |
| **Completeness guard** | Rule that optimization fails closed when its bounded candidate universe is known to be incomplete. |
| **Recovery verification** | Post-restore consistency checks; verification does not rewrite canonical history. |
| **Release provenance** | Evidence binding source, dependencies, build artifact, SBOM, attestations, and release manifest. |
| **Candidate SLO** | Proposed service objective not yet established as a production fact by live-environment evidence. |
| **Legal hold** | Governance state that prevents otherwise eligible erasure/retention actions where policy requires preservation. |

## Naming principle

When a term can imply stronger guarantees than CharterOS actually provides, documentation uses the
narrower term. Examples: “at-least-once delivery” rather than generic “exactly once,” “evidence
integrity” rather than legal notarization, and “candidate SLO” rather than measured production SLO.
