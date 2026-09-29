from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from charteros.application.exceptions import EntityNotFoundError
from charteros.application.ports.bookings import BookingRepository
from charteros.application.ports.catalog import OrganizationRepository
from charteros.application.ports.missions import MissionRepository
from charteros.domain.bookings import Booking
from charteros.domain.missions import Mission, MissionId
from charteros.domain.organizations import OrganizationId, OrganizationStatus


@dataclass(frozen=True, slots=True)
class BuyerProcurementAuditEvent:
    event_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    aggregate_version: int
    event_type: str
    event_version: int
    occurred_at: datetime
    recorded_at: datetime
    actor_id: UUID | None
    correlation_id: UUID | None
    causation_id: UUID | None


@dataclass(frozen=True, slots=True)
class BuyerProcurementAuditPage:
    items: tuple[BuyerProcurementAuditEvent, ...]
    truncated: bool


class BuyerProcurementAuditRepository(Protocol):
    def list_mission_audit(
        self,
        mission_id: MissionId,
        *,
        limit: int,
    ) -> BuyerProcurementAuditPage: ...


class BuyerPortalService:
    """Buyer ownership boundary for portal composition; headers are not authentication."""

    def __init__(
        self,
        *,
        organizations: OrganizationRepository,
        missions: MissionRepository,
        bookings: BookingRepository,
        audit: BuyerProcurementAuditRepository | None = None,
    ) -> None:
        self._organizations = organizations
        self._missions = missions
        self._bookings = bookings
        self._audit = audit

    def assert_buyer(self, buyer_id: OrganizationId) -> None:
        buyer = self._organizations.get(buyer_id)
        if buyer is None or buyer.status is not OrganizationStatus.ACTIVE:
            raise EntityNotFoundError("buyer context does not exist or is not active")

    def mission(self, *, buyer_id: OrganizationId, mission_id: MissionId) -> Mission:
        self.assert_buyer(buyer_id)
        mission = self._missions.get(mission_id)
        if mission is None or mission.buyer_id != buyer_id:
            raise EntityNotFoundError("mission is not available in the buyer context")
        return mission

    def booking_for_mission(
        self,
        *,
        buyer_id: OrganizationId,
        mission_id: MissionId,
    ) -> Booking:
        mission = self.mission(buyer_id=buyer_id, mission_id=mission_id)
        booking = self._bookings.get_for_mission(mission.id)
        if booking is None:
            raise EntityNotFoundError("mission does not have a booking")
        return booking

    def audit(
        self,
        *,
        buyer_id: OrganizationId,
        mission_id: MissionId,
        limit: int,
    ) -> BuyerProcurementAuditPage:
        self.mission(buyer_id=buyer_id, mission_id=mission_id)
        if self._audit is None:
            raise RuntimeError("buyer procurement audit repository is required")
        return self._audit.list_mission_audit(mission_id, limit=limit)
