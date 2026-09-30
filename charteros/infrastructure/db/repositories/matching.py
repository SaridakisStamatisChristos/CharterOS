from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from charteros.application.exceptions import EntityConflictError
from charteros.domain.aircraft import (
    AircraftId,
    AircraftStatus,
    AircraftTypeId,
    AvailabilityRecordId,
    AvailabilityStatus,
    PositionObservationId,
)
from charteros.domain.airports import AirportId
from charteros.domain.operators import (
    CommercialStatus,
    InsuranceStatus,
    OperatorId,
    VerificationStatus,
)
from charteros.domain.shared.currency import Currency
from charteros.domain.shared.money import Money
from charteros.domain.shared.time_range import TimeRange
from charteros.matching import (
    AvailabilitySnapshot,
    MatchingCandidateSnapshot,
    MatchingProfileId,
    MatchingReferenceProfile,
    PositionSnapshot,
)

_QUERY = text(
    """
    SELECT
        a.id AS aircraft_id,
        a.version AS aircraft_version,
        a.operator_id,
        o.version AS operator_version,
        a.aircraft_type_id,
        a.seat_capacity,
        a.range_nm,
        a.status AS aircraft_status,
        o.verification_status,
        o.insurance_status,
        o.commercial_status,
        p.id AS position_id,
        p.airport_id AS position_airport_id,
        COALESCE(p.latitude, pa.latitude) AS position_latitude,
        COALESCE(p.longitude, pa.longitude) AS position_longitude,
        p.event_time AS position_event_time,
        p.recorded_at AS position_recorded_at,
        p.source AS position_source,
        p.provenance AS position_provenance,
        av.id AS availability_id,
        av.valid_from AS availability_valid_from,
        av.valid_to AS availability_valid_to,
        av.status AS availability_status,
        av.recorded_at AS availability_recorded_at,
        av.source AS availability_source,
        av.provenance AS availability_provenance,
        mp.id AS profile_id,
        mp.cruise_speed_kts,
        mp.operating_cost_per_hour_minor,
        mp.operating_cost_currency,
        mp.max_reposition_nm,
        mp.turnaround_buffer_minutes,
        mp.source AS profile_source,
        mp.provenance AS profile_provenance,
        mp.recorded_at AS profile_recorded_at
    FROM aircraft AS a
    JOIN operators AS o ON o.id = a.operator_id
    LEFT JOIN LATERAL (
        SELECT pos.*
        FROM aircraft_position_observations AS pos
        WHERE pos.aircraft_id = a.id
          AND pos.event_time <= :position_event_cutoff
          AND pos.recorded_at <= :known_as_of
        ORDER BY pos.event_time DESC, pos.recorded_at DESC, pos.id DESC
        LIMIT 1
    ) AS p ON TRUE
    LEFT JOIN airports AS pa ON pa.id = p.airport_id
    LEFT JOIN LATERAL (
        SELECT availability.*
        FROM aircraft_availability_records AS availability
        WHERE availability.aircraft_id = a.id
          AND availability.valid_from <= :availability_from
          AND availability.valid_to >= :availability_to
          AND availability.recorded_at <= :known_as_of
          AND (
              availability.superseded_at IS NULL
              OR availability.superseded_at > :known_as_of
          )
        ORDER BY availability.recorded_at DESC, availability.id DESC
        LIMIT 1
    ) AS av ON TRUE
    LEFT JOIN LATERAL (
        SELECT profile.*
        FROM matching_reference_profiles AS profile
        WHERE profile.aircraft_type_id = a.aircraft_type_id
          AND profile.recorded_at <= :known_as_of
          AND (profile.superseded_at IS NULL OR profile.superseded_at > :known_as_of)
        ORDER BY profile.recorded_at DESC, profile.id DESC
        LIMIT 1
    ) AS mp ON TRUE
    ORDER BY a.id
    LIMIT :limit_plus_one
    """
)
_QUERY_BY_AIRCRAFT = text(
    str(_QUERY).replace(
        "ORDER BY a.id\n    LIMIT :limit_plus_one",
        "WHERE a.id IN :aircraft_ids\n    ORDER BY a.id",
    )
).bindparams(bindparam("aircraft_ids", expanding=True))


def _uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise EntityConflictError("matching snapshot contains an invalid timestamp")
    return value


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EntityConflictError("matching snapshot contains an invalid integer")
    return value


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise EntityConflictError("matching snapshot contains an invalid coordinate") from exc


def _json_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise EntityConflictError("matching snapshot provenance must be a JSON object")
    return {str(key): item for key, item in value.items()}


def _position(mapping: RowMapping) -> PositionSnapshot | None:
    if mapping["position_id"] is None:
        return None
    latitude = mapping["position_latitude"]
    longitude = mapping["position_longitude"]
    if latitude is None or longitude is None:
        return None
    airport_id = mapping["position_airport_id"]
    return PositionSnapshot(
        id=PositionObservationId(_uuid(mapping["position_id"])),
        aircraft_id=AircraftId(_uuid(mapping["aircraft_id"])),
        airport_id=AirportId(_uuid(airport_id)) if airport_id is not None else None,
        latitude=_decimal(latitude),
        longitude=_decimal(longitude),
        event_time=_datetime(mapping["position_event_time"]),
        recorded_at=_datetime(mapping["position_recorded_at"]),
        source=str(mapping["position_source"]),
        provenance=_json_mapping(mapping["position_provenance"]),
    )


def _availability(mapping: RowMapping) -> AvailabilitySnapshot | None:
    if mapping["availability_id"] is None:
        return None
    return AvailabilitySnapshot(
        id=AvailabilityRecordId(_uuid(mapping["availability_id"])),
        aircraft_id=AircraftId(_uuid(mapping["aircraft_id"])),
        interval=TimeRange(
            _datetime(mapping["availability_valid_from"]),
            _datetime(mapping["availability_valid_to"]),
        ),
        status=AvailabilityStatus(str(mapping["availability_status"])),
        recorded_at=_datetime(mapping["availability_recorded_at"]),
        source=str(mapping["availability_source"]),
        provenance=_json_mapping(mapping["availability_provenance"]),
    )


def _profile(mapping: RowMapping) -> MatchingReferenceProfile | None:
    if mapping["profile_id"] is None:
        return None
    return MatchingReferenceProfile(
        id=MatchingProfileId(_uuid(mapping["profile_id"])),
        aircraft_type_id=AircraftTypeId(_uuid(mapping["aircraft_type_id"])),
        cruise_speed_kts=_integer(mapping["cruise_speed_kts"]),
        operating_cost_per_hour=Money(
            _integer(mapping["operating_cost_per_hour_minor"]),
            Currency(str(mapping["operating_cost_currency"])),
        ),
        max_reposition_nm=_integer(mapping["max_reposition_nm"]),
        turnaround_buffer_minutes=_integer(mapping["turnaround_buffer_minutes"]),
        source=str(mapping["profile_source"]),
        provenance=_json_mapping(mapping["profile_provenance"]),
        recorded_at=_datetime(mapping["profile_recorded_at"]),
    )


def _candidate(mapping: RowMapping) -> MatchingCandidateSnapshot:
    return MatchingCandidateSnapshot(
        aircraft_id=AircraftId(_uuid(mapping["aircraft_id"])),
        operator_id=OperatorId(_uuid(mapping["operator_id"])),
        aircraft_type_id=AircraftTypeId(_uuid(mapping["aircraft_type_id"])),
        seat_capacity=int(mapping["seat_capacity"]),
        range_nm=int(mapping["range_nm"]),
        aircraft_status=AircraftStatus(str(mapping["aircraft_status"])),
        verification_status=VerificationStatus(str(mapping["verification_status"])),
        insurance_status=InsuranceStatus(str(mapping["insurance_status"])),
        commercial_status=CommercialStatus(str(mapping["commercial_status"])),
        position=_position(mapping),
        availability=_availability(mapping),
        reference_profile=_profile(mapping),
        aircraft_version=_integer(mapping["aircraft_version"]),
        operator_version=_integer(mapping["operator_version"]),
    )


class SqlAlchemyMatchingSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def load_candidates(
        self,
        *,
        known_as_of: datetime,
        position_event_cutoff: datetime,
        availability_from: datetime,
        availability_to: datetime,
        limit: int,
    ) -> tuple[MatchingCandidateSnapshot, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        rows = (
            self._session.execute(
                _QUERY,
                {
                    "known_as_of": known_as_of,
                    "position_event_cutoff": position_event_cutoff,
                    "availability_from": availability_from,
                    "availability_to": availability_to,
                    "limit_plus_one": limit + 1,
                },
            )
            .mappings()
            .all()
        )
        if len(rows) > limit:
            raise EntityConflictError(
                f"matching candidate set exceeds bounded v1 limit of {limit} aircraft"
            )
        return tuple(_candidate(mapping) for mapping in rows)

    def load_candidates_by_aircraft_ids(
        self,
        *,
        aircraft_ids: tuple[AircraftId, ...],
        known_as_of: datetime,
        position_event_cutoff: datetime,
        availability_from: datetime,
        availability_to: datetime,
    ) -> tuple[MatchingCandidateSnapshot, ...]:
        if not aircraft_ids:
            return ()
        rows = (
            self._session.execute(
                _QUERY_BY_AIRCRAFT,
                {
                    "aircraft_ids": [aircraft_id.value for aircraft_id in aircraft_ids],
                    "known_as_of": known_as_of,
                    "position_event_cutoff": position_event_cutoff,
                    "availability_from": availability_from,
                    "availability_to": availability_to,
                },
            )
            .mappings()
            .all()
        )
        return tuple(_candidate(mapping) for mapping in rows)
