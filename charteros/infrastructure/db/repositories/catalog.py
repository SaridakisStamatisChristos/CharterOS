from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from charteros.application.exceptions import EntityConflictError
from charteros.application.idempotency import StoredResponse
from charteros.domain.aircraft import Aircraft, AircraftType, AircraftTypeId
from charteros.domain.airports import Airport, AirportId
from charteros.domain.missions import Mission
from charteros.domain.operators import (
    CommercialStatus,
    InsuranceStatus,
    Operator,
    OperatorId,
    VerificationStatus,
)
from charteros.domain.organizations import (
    Organization,
    OrganizationId,
    OrganizationStatus,
    OrganizationType,
)
from charteros.domain.rfqs import Rfq
from charteros.infrastructure.db.models.catalog import (
    AircraftRow,
    AircraftTypeRow,
    AirportRow,
    IdempotencyRecordRow,
    OperatorRow,
    OrganizationRow,
    OutboxEventRow,
)


def _flush(session: Session, *, conflict_message: str) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        raise EntityConflictError(conflict_message) from exc


class SqlAlchemyOrganizationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, organization: Organization) -> None:
        self._session.add(
            OrganizationRow(
                id=organization.id.value,
                version=organization.version,
                type=organization.organization_type.value,
                legal_name=organization.legal_name,
                legal_name_key=organization.legal_name.casefold(),
                trading_name=organization.trading_name,
                country=organization.country,
                status=organization.status.value,
            )
        )
        _flush(
            self._session,
            conflict_message="organization with the same legal name and country already exists",
        )

    def get(self, organization_id: OrganizationId) -> Organization | None:
        row = self._session.get(OrganizationRow, organization_id.value)
        if row is None:
            return None
        return Organization(
            OrganizationId(row.id),
            organization_type=OrganizationType(row.type),
            legal_name=row.legal_name,
            trading_name=row.trading_name,
            country=row.country,
            status=OrganizationStatus(row.status),
            version=row.version,
        )


class SqlAlchemyOperatorRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, operator: Operator) -> None:
        self._session.add(
            OperatorRow(
                id=operator.id.value,
                version=operator.version,
                organization_id=operator.organization_id.value,
                aoc_reference=operator.aoc_reference,
                operating_regions=list(operator.operating_regions),
                verification_status=operator.verification_status.value,
                insurance_status=operator.insurance_status.value,
                safety_documents=list(operator.safety_documents),
                commercial_status=operator.commercial_status.value,
            )
        )
        _flush(
            self._session,
            conflict_message="operator organization or AOC reference already exists",
        )

    def get(self, operator_id: OperatorId) -> Operator | None:
        row = self._session.get(OperatorRow, operator_id.value)
        if row is None:
            return None
        return Operator(
            OperatorId(row.id),
            organization_id=OrganizationId(row.organization_id),
            aoc_reference=row.aoc_reference,
            operating_regions=tuple(row.operating_regions),
            verification_status=VerificationStatus(row.verification_status),
            insurance_status=InsuranceStatus(row.insurance_status),
            safety_documents=tuple(row.safety_documents),
            commercial_status=CommercialStatus(row.commercial_status),
            version=row.version,
        )


class SqlAlchemyAirportRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, airport: Airport) -> None:
        self._session.add(
            AirportRow(
                id=airport.id.value,
                version=airport.version,
                icao=airport.icao,
                iata=airport.iata,
                latitude=airport.latitude,
                longitude=airport.longitude,
                timezone=airport.timezone,
                runway_metadata=airport.runway_metadata,
                curfew_metadata=airport.curfew_metadata,
                operational_flags=list(airport.operational_flags),
            )
        )
        _flush(self._session, conflict_message="airport ICAO or IATA code already exists")

    def get(self, airport_id: AirportId) -> Airport | None:
        row = self._session.get(AirportRow, airport_id.value)
        if row is None:
            return None
        return Airport(
            AirportId(row.id),
            icao=row.icao,
            iata=row.iata,
            latitude=row.latitude,
            longitude=row.longitude,
            timezone=row.timezone,
            runway_metadata=row.runway_metadata,
            curfew_metadata=row.curfew_metadata,
            operational_flags=tuple(row.operational_flags),
            version=row.version,
        )


class SqlAlchemyAircraftTypeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, aircraft_type: AircraftType) -> None:
        self._session.add(
            AircraftTypeRow(
                id=aircraft_type.id.value,
                manufacturer=aircraft_type.manufacturer,
                manufacturer_key=aircraft_type.manufacturer.casefold(),
                model=aircraft_type.model,
                model_key=aircraft_type.model.casefold(),
                category=aircraft_type.category,
                seats_min=aircraft_type.seats_min,
                seats_max=aircraft_type.seats_max,
                range_nm=aircraft_type.range_nm,
                runway_requirements=aircraft_type.runway_requirements,
                baggage_cargo_profile=aircraft_type.baggage_cargo_profile,
            )
        )
        _flush(self._session, conflict_message="aircraft type already exists")

    def find_by_make_model(self, manufacturer: str, model: str) -> AircraftType | None:
        statement = select(AircraftTypeRow).where(
            AircraftTypeRow.manufacturer_key == " ".join(manufacturer.split()).casefold(),
            AircraftTypeRow.model_key == " ".join(model.split()).casefold(),
        )
        row = self._session.scalar(statement)
        return self._to_domain(row) if row is not None else None

    def get(self, aircraft_type_id: AircraftTypeId) -> AircraftType | None:
        row = self._session.get(AircraftTypeRow, aircraft_type_id.value)
        return self._to_domain(row) if row is not None else None

    @staticmethod
    def _to_domain(row: AircraftTypeRow) -> AircraftType:
        return AircraftType(
            id=AircraftTypeId(row.id),
            manufacturer=row.manufacturer,
            model=row.model,
            category=row.category,
            seats_min=row.seats_min,
            seats_max=row.seats_max,
            range_nm=row.range_nm,
            runway_requirements=row.runway_requirements,
            baggage_cargo_profile=row.baggage_cargo_profile,
        )


class SqlAlchemyAircraftRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, aircraft: Aircraft) -> None:
        self._session.add(
            AircraftRow(
                id=aircraft.id.value,
                version=aircraft.version,
                operator_id=aircraft.operator_id.value,
                registration=aircraft.registration,
                aircraft_type_id=aircraft.aircraft_type_id.value,
                seat_capacity=aircraft.seat_capacity,
                cargo_capacity=aircraft.cargo_capacity,
                range_nm=aircraft.range_nm,
                home_base_id=aircraft.home_base_id.value,
                status=aircraft.status.value,
            )
        )
        _flush(self._session, conflict_message="aircraft registration already exists")


class SqlAlchemyIdempotencyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock(self, scope: str, key: str) -> None:
        bind = self._session.get_bind()
        if bind.dialect.name == "postgresql":
            self._session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:idempotency_lock_key, 0))"),
                {"idempotency_lock_key": f"{scope}:{key}"},
            )

    def get(self, scope: str, key: str) -> StoredResponse | None:
        row = self._session.get(IdempotencyRecordRow, (scope, key))
        if row is None:
            return None
        return StoredResponse(
            request_hash=row.request_hash,
            status_code=row.status_code,
            response_body=row.response_body,
        )

    def add(
        self,
        *,
        scope: str,
        key: str,
        request_hash: str,
        status_code: int,
        response_body: dict[str, object],
    ) -> None:
        self._session.add(
            IdempotencyRecordRow(
                scope=scope,
                key=key,
                request_hash=request_hash,
                status_code=status_code,
                response_body=response_body,
            )
        )
        _flush(self._session, conflict_message="idempotency key already exists")


class SqlAlchemyDomainEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_aggregate_events(
        self,
        aggregate: Organization | Operator | Airport | Aircraft | Mission | Rfq,
    ) -> None:
        for event in aggregate.collect_events():
            self._session.add(
                OutboxEventRow(
                    event_id=event.event_id.value,
                    aggregate_type=event.aggregate_type,
                    aggregate_id=event.aggregate_id.value,
                    aggregate_version=event.aggregate_version,
                    event_type=event.event_type,
                    event_version=event.event_version,
                    occurred_at=event.occurred_at,
                    recorded_at=event.recorded_at,
                    actor_id=event.actor_id.value if event.actor_id else None,
                    correlation_id=(event.correlation_id.value if event.correlation_id else None),
                    causation_id=event.causation_id.value if event.causation_id else None,
                    canonical_json=event.to_json(),
                    publish_attempts=0,
                )
            )
        _flush(self._session, conflict_message="domain event version already exists")
