from __future__ import annotations

from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from charteros.domain.shared.aggregate import AggregateRoot
from charteros.domain.shared.exceptions import DomainValidationError
from charteros.domain.shared.ids import CorrelationId, TypedId


class AirportId(TypedId):
    __slots__ = ()


def _airport_code(value: str, *, field_name: str, length: int) -> str:
    normalized = value.strip().upper()
    if (
        len(normalized) != length
        or not normalized.isascii()
        or not normalized.isalpha()
    ):
        raise DomainValidationError(
            f"{field_name} must contain exactly {length} uppercase ASCII letters"
        )
    return normalized


def _validate_coordinates(latitude: Decimal, longitude: Decimal) -> None:
    if latitude < Decimal("-90") or latitude > Decimal("90"):
        raise DomainValidationError("latitude must be between -90 and 90")
    if longitude < Decimal("-180") or longitude > Decimal("180"):
        raise DomainValidationError("longitude must be between -180 and 180")


def _validate_timezone(value: str) -> str:
    timezone_name = value.strip()
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise DomainValidationError("timezone must be a valid IANA timezone") from exc
    return timezone_name


class Airport(AggregateRoot[AirportId]):
    aggregate_type = "airport"

    def __init__(
        self,
        airport_id: AirportId,
        *,
        icao: str,
        iata: str | None,
        latitude: Decimal,
        longitude: Decimal,
        timezone: str,
        runway_metadata: dict[str, object],
        curfew_metadata: dict[str, object],
        operational_flags: tuple[str, ...],
        version: int = 0,
    ) -> None:
        super().__init__(airport_id, version=version)
        self.icao = _airport_code(icao, field_name="icao", length=4)
        self.iata = _airport_code(iata, field_name="iata", length=3) if iata else None
        _validate_coordinates(latitude, longitude)
        self.latitude = latitude
        self.longitude = longitude
        self.timezone = _validate_timezone(timezone)
        self.runway_metadata = dict(runway_metadata)
        self.curfew_metadata = dict(curfew_metadata)
        self.operational_flags = tuple(
            dict.fromkeys(flag.strip().upper() for flag in operational_flags if flag.strip())
        )

    @classmethod
    def create(
        cls,
        *,
        icao: str,
        iata: str | None,
        latitude: Decimal,
        longitude: Decimal,
        timezone: str,
        runway_metadata: dict[str, object] | None = None,
        curfew_metadata: dict[str, object] | None = None,
        operational_flags: tuple[str, ...] = (),
        correlation_id: CorrelationId | None = None,
    ) -> Airport:
        airport = cls(
            AirportId.new(),
            icao=icao,
            iata=iata,
            latitude=latitude,
            longitude=longitude,
            timezone=timezone,
            runway_metadata=runway_metadata or {},
            curfew_metadata=curfew_metadata or {},
            operational_flags=operational_flags,
        )
        airport._record_event(
            "AIRPORT_REGISTERED",
            {
                "icao": airport.icao,
                "iata": airport.iata,
                "timezone": airport.timezone,
            },
            correlation_id=correlation_id,
        )
        return airport
