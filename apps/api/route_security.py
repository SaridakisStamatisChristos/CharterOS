from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from charteros.security.auth import Permission, PrincipalType

RouteKey = tuple[str, str]


class SelectorRequirement(StrEnum):
    NONE = "none"
    BUYER = "buyer"
    OPERATOR = "operator"
    PARTY_OR_ADMIN = "party-or-admin"


class ResourceRequirement(StrEnum):
    NONE = "none"
    MISSION_BUYER = "mission-buyer"
    TENDER_BUYER = "tender-buyer"
    INVITATION_OPERATOR = "invitation-operator"
    TENDER_INVITATION_OPERATOR = "tender-invitation-operator"


@dataclass(frozen=True, slots=True)
class RoutePolicy:
    permission: Permission
    allowed_principal_types: frozenset[PrincipalType]
    selector: SelectorRequirement = SelectorRequirement.NONE
    resource: ResourceRequirement = ResourceRequirement.NONE


ADMIN = frozenset({PrincipalType.ADMINISTRATOR})
BUYER = frozenset({PrincipalType.BUYER, PrincipalType.ADMINISTRATOR})
OPERATOR = frozenset({PrincipalType.OPERATOR, PrincipalType.ADMINISTRATOR})
PARTIES = frozenset({PrincipalType.BUYER, PrincipalType.OPERATOR, PrincipalType.ADMINISTRATOR})


def _policy(
    permission: Permission,
    principal_types: frozenset[PrincipalType],
    selector: SelectorRequirement = SelectorRequirement.NONE,
    resource: ResourceRequirement = ResourceRequirement.NONE,
) -> RoutePolicy:
    return RoutePolicy(
        permission=permission,
        allowed_principal_types=principal_types,
        selector=selector,
        resource=resource,
    )


def _build_route_policies() -> dict[RouteKey, RoutePolicy]:
    policies: dict[RouteKey, RoutePolicy] = {}

    def add(policy: RoutePolicy, *routes: RouteKey) -> None:
        for route in routes:
            if route in policies:
                raise RuntimeError(f"duplicate route security policy: {route}")
            policies[route] = policy

    add(
        _policy(Permission.CATALOG_WRITE, ADMIN),
        ("POST", "/v1/organizations"),
        ("POST", "/v1/operators"),
        ("POST", "/v1/airports"),
        ("POST", "/v1/aircraft"),
    )
    add(
        _policy(Permission.FLEET_WRITE, ADMIN),
        ("POST", "/v1/aircraft/{aircraft_id}/positions"),
        ("POST", "/v1/aircraft/{aircraft_id}/availability"),
    )
    add(
        _policy(Permission.FLEET_READ, ADMIN),
        ("GET", "/v1/aircraft/{aircraft_id}/timeline"),
    )
    add(
        _policy(Permission.FX_RATE_WRITE, ADMIN),
        ("POST", "/v1/fx/rates"),
        ("POST", "/v1/fx/rates/{rate_id}/corrections"),
    )
    add(
        _policy(Permission.FX_RATE_READ, ADMIN),
        ("GET", "/v1/fx/rates/{rate_id}"),
    )
    add(
        _policy(Permission.GRAPH_READ, ADMIN),
        ("GET", "/v1/graph/aircraft/near-airport"),
        ("GET", "/v1/graph/aircraft/{aircraft_id}/historical-position"),
        ("GET", "/v1/graph/missions/{mission_id}/feasible-aircraft"),
        ("GET", "/v1/graph/operators/{operator_id}/route-history"),
        ("GET", "/v1/graph/quotes/{quote_id}/history"),
        ("GET", "/v1/graph/empty-leg-candidates"),
        ("GET", "/v1/graph/bookings/{booking_id}/flight-lineage"),
    )
    add(
        _policy(Permission.OPTIMIZATION_READ, ADMIN),
        ("GET", "/v1/optimization/repositioning"),
    )

    add(
        _policy(Permission.BUYER_MISSION_WRITE, BUYER, SelectorRequirement.BUYER),
        ("POST", "/v1/buyer-portal/missions"),
        ("POST", "/v1/buyer-portal/missions/{mission_id}/open"),
        ("POST", "/v1/buyer-portal/missions/{mission_id}/rfqs"),
    )
    add(
        _policy(Permission.BUYER_MISSION_READ, BUYER, SelectorRequirement.BUYER),
        ("GET", "/v1/buyer-portal/missions/{mission_id}"),
        ("GET", "/v1/buyer-portal/missions/{mission_id}/suppliers"),
        ("GET", "/v1/buyer-portal/missions/{mission_id}/rfqs"),
        ("GET", "/v1/buyer-portal/missions/{mission_id}/quotes/compare"),
        ("GET", "/v1/buyer-portal/missions/{mission_id}/booking"),
    )
    add(
        _policy(Permission.BUYER_FX_LOCK_CREATE, BUYER, SelectorRequirement.BUYER),
        ("POST", "/v1/buyer-portal/missions/{mission_id}/quotes/compare/fx-locks"),
    )
    add(
        _policy(Permission.BUYER_PROCUREMENT_APPROVE, BUYER, SelectorRequirement.BUYER),
        ("POST", "/v1/buyer-portal/missions/{mission_id}/quotes/{quote_id}/approve"),
        ("POST", "/v1/buyer-portal/approvals/{approval_id}/award"),
    )
    add(
        _policy(Permission.AUDIT_EVIDENCE_READ, BUYER, SelectorRequirement.BUYER),
        ("GET", "/v1/buyer-portal/missions/{mission_id}/audit"),
    )

    add(
        _policy(Permission.OPERATOR_FLEET_READ, OPERATOR, SelectorRequirement.OPERATOR),
        ("GET", "/v1/operator-portal/fleet"),
        ("GET", "/v1/operator-portal/fleet/{aircraft_id}"),
        ("GET", "/v1/operator-portal/fleet/{aircraft_id}/availability"),
    )
    add(
        _policy(Permission.OPERATOR_FLEET_WRITE, OPERATOR, SelectorRequirement.OPERATOR),
        ("POST", "/v1/operator-portal/fleet"),
        ("POST", "/v1/operator-portal/fleet/{aircraft_id}/availability"),
    )
    add(
        _policy(Permission.OPERATOR_RFQ_READ, OPERATOR, SelectorRequirement.OPERATOR),
        ("GET", "/v1/operator-portal/rfqs"),
        ("GET", "/v1/operator-portal/rfqs/{rfq_id}"),
    )
    add(
        _policy(Permission.OPERATOR_RFQ_WRITE, OPERATOR, SelectorRequirement.OPERATOR),
        ("POST", "/v1/operator-portal/rfqs/{rfq_id}/acknowledge"),
        ("POST", "/v1/operator-portal/rfqs/{rfq_id}/decline"),
    )
    add(
        _policy(Permission.OPERATOR_QUOTE_WRITE, OPERATOR, SelectorRequirement.OPERATOR),
        ("POST", "/v1/operator-portal/rfqs/{rfq_id}/quotes"),
        ("POST", "/v1/operator-portal/quotes/{quote_id}/revise"),
        ("POST", "/v1/operator-portal/quotes/{quote_id}/withdraw"),
    )
    add(
        _policy(Permission.OPERATOR_QUOTE_READ, OPERATOR, SelectorRequirement.OPERATOR),
        ("GET", "/v1/operator-portal/quotes/{quote_id}"),
    )
    add(
        _policy(Permission.OPERATOR_BOOKING_READ, OPERATOR, SelectorRequirement.OPERATOR),
        ("GET", "/v1/operator-portal/calendar"),
        ("GET", "/v1/operator-portal/empty-legs"),
        ("GET", "/v1/operator-portal/bookings"),
        ("GET", "/v1/operator-portal/bookings/{booking_id}"),
    )

    add(
        _policy(Permission.DISRUPTION_WRITE, PARTIES, SelectorRequirement.PARTY_OR_ADMIN),
        ("POST", "/v1/bookings/{booking_id}/disruptions"),
    )
    add(
        _policy(Permission.DISRUPTION_READ, PARTIES, SelectorRequirement.PARTY_OR_ADMIN),
        ("GET", "/v1/bookings/{booking_id}/disruptions"),
        ("GET", "/v1/disruptions/{disruption_id}"),
        ("GET", "/v1/disruptions/{disruption_id}/replacement-options"),
    )
    add(
        _policy(Permission.DISRUPTION_WRITE, OPERATOR, SelectorRequirement.OPERATOR),
        ("POST", "/v1/disruptions/{disruption_id}/replacement-options"),
        ("POST", "/v1/disruptions/{disruption_id}/requotes"),
        ("POST", "/v1/disruptions/{disruption_id}/resolve"),
    )
    add(
        _policy(Permission.DISRUPTION_WRITE, BUYER, SelectorRequirement.BUYER),
        ("POST", "/v1/disruptions/{disruption_id}/buyer-decisions"),
    )

    add(
        _policy(Permission.RECONCILIATION_WRITE, OPERATOR, SelectorRequirement.OPERATOR),
        ("POST", "/v1/bookings/{booking_id}/reconciliation"),
        ("POST", "/v1/reconciliations/{reconciliation_id}/invoices"),
        ("POST", "/v1/reconciliations/{reconciliation_id}/complete"),
    )
    add(
        _policy(Permission.RECONCILIATION_WRITE, BUYER, SelectorRequirement.BUYER),
        ("POST", "/v1/reconciliations/{reconciliation_id}/disputes"),
        ("POST", "/v1/reconciliations/{reconciliation_id}/variance-approvals"),
    )
    add(
        _policy(Permission.RECONCILIATION_READ, PARTIES, SelectorRequirement.PARTY_OR_ADMIN),
        ("GET", "/v1/bookings/{booking_id}/reconciliation"),
        ("GET", "/v1/reconciliations/{reconciliation_id}"),
        ("GET", "/v1/reconciliations/{reconciliation_id}/invoices"),
    )
    add(
        _policy(Permission.AUDIT_EVIDENCE_READ, PARTIES, SelectorRequirement.PARTY_OR_ADMIN),
        ("GET", "/v1/evidence/missions/{mission_id}"),
        ("GET", "/v1/evidence/bookings/{booking_id}"),
        ("GET", "/v1/evidence/disruptions/{disruption_id}"),
        ("GET", "/v1/evidence/reconciliations/{reconciliation_id}"),
    )
    add(
        _policy(
            Permission.TENDER_BUYER_WRITE,
            BUYER,
            SelectorRequirement.BUYER,
            ResourceRequirement.MISSION_BUYER,
        ),
        ("POST", "/v1/missions/{mission_id}/tenders"),
    )
    add(
        _policy(
            Permission.TENDER_BUYER_WRITE,
            BUYER,
            SelectorRequirement.BUYER,
            ResourceRequirement.TENDER_BUYER,
        ),
        ("POST", "/v1/tenders/{tender_id}/open"),
        ("POST", "/v1/tenders/{tender_id}/invitations"),
        ("POST", "/v1/tenders/{tender_id}/best-and-final"),
        ("POST", "/v1/tenders/{tender_id}/close"),
        ("POST", "/v1/tenders/{tender_id}/award"),
    )
    add(
        _policy(
            Permission.TENDER_BUYER_READ,
            BUYER,
            SelectorRequirement.BUYER,
            ResourceRequirement.TENDER_BUYER,
        ),
        ("GET", "/v1/tenders/{tender_id}"),
        ("GET", "/v1/tenders/{tender_id}/audit"),
    )
    add(
        _policy(
            Permission.TENDER_OPERATOR_WRITE,
            OPERATOR,
            SelectorRequirement.OPERATOR,
            ResourceRequirement.INVITATION_OPERATOR,
        ),
        ("POST", "/v1/tender-invitations/{invitation_id}/accept"),
        ("POST", "/v1/tender-invitations/{invitation_id}/decline"),
        ("POST", "/v1/tender-invitations/{invitation_id}/bids"),
        ("POST", "/v1/tender-invitations/{invitation_id}/bids/{quote_id}/revise"),
        ("POST", "/v1/tender-invitations/{invitation_id}/best-and-final/{quote_id}"),
        ("POST", "/v1/tender-invitations/{invitation_id}/bids/{quote_id}/withdraw"),
    )
    add(
        _policy(
            Permission.TENDER_SUPPLIER_READ,
            OPERATOR,
            SelectorRequirement.OPERATOR,
            ResourceRequirement.TENDER_INVITATION_OPERATOR,
        ),
        ("GET", "/v1/tenders/{tender_id}/supplier-view"),
    )
    add(
        _policy(Permission.TENDER_ADMIN_CORRECT, ADMIN),
        ("POST", "/v1/tenders/{tender_id}/admin-corrections"),
    )

    # Legacy domain surfaces without an established authenticated tenant selector are deliberately
    # privileged. Buyer/operator traffic should use the scoped portal routes above. This prevents
    # caller-controlled path/body IDs from becoming an implicit authorization mechanism.
    add(
        _policy(Permission.DOMAIN_WRITE, ADMIN),
        ("POST", "/v1/missions"),
        ("POST", "/v1/missions/{mission_id}/open"),
        ("POST", "/v1/missions/{mission_id}/rfqs"),
        ("POST", "/v1/rfqs/{rfq_id}/acknowledge"),
        ("POST", "/v1/rfqs/{rfq_id}/decline"),
        ("POST", "/v1/rfqs/{rfq_id}/expire"),
        ("POST", "/v1/rfqs/{rfq_id}/quotes"),
        ("POST", "/v1/quotes/{quote_id}/revise"),
        ("POST", "/v1/quotes/{quote_id}/withdraw"),
        ("POST", "/v1/quotes/{quote_id}/expire"),
        ("POST", "/v1/quotes/{quote_id}/accept"),
        ("POST", "/v1/bookings/{booking_id}/mark-contracted"),
        ("POST", "/v1/bookings/{booking_id}/mark-payment-pending"),
        ("POST", "/v1/bookings/{booking_id}/confirm"),
        ("POST", "/v1/bookings/{booking_id}/enter-pre-operation"),
        ("POST", "/v1/bookings/{booking_id}/start-operation"),
        ("POST", "/v1/bookings/{booking_id}/complete"),
        ("POST", "/v1/bookings/{booking_id}/reconcile"),
        ("POST", "/v1/bookings/{booking_id}/contract"),
        ("POST", "/v1/contracts/{contract_id}/accept/buyer"),
        ("POST", "/v1/contracts/{contract_id}/accept/operator"),
    )
    add(
        _policy(Permission.DOMAIN_READ, ADMIN),
        ("GET", "/v1/missions/{mission_id}"),
        ("GET", "/v1/missions/{mission_id}/matches"),
        ("GET", "/v1/missions/{mission_id}/rfqs"),
        ("GET", "/v1/rfqs/{rfq_id}/quotes"),
        ("GET", "/v1/missions/{mission_id}/quotes/compare"),
        ("GET", "/v1/quotes/{quote_id}"),
        ("GET", "/v1/quotes/{quote_id}/normalization"),
        ("GET", "/v1/bookings/{booking_id}"),
        ("GET", "/v1/contracts/{contract_id}"),
        ("GET", "/v1/bookings/{booking_id}/contract"),
    )
    return policies


ROUTE_POLICIES = _build_route_policies()


def route_policy(method: str, path_template: str) -> RoutePolicy | None:
    return ROUTE_POLICIES.get((method.upper(), path_template))

