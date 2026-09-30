from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from math import asin, cos, radians, sin, sqrt

from charteros.domain.shared.exceptions import DomainValidationError

EARTH_RADIUS_NM = 3440.065
COORDINATE_QUANTUM_DEGREES = Decimal("0.000001")
DISTANCE_STABILITY_QUANTUM_NM = Decimal("0.000001")
_DISTANCE_TENTH_NM = Decimal("0.1")


def _canonical_coordinate(
    value: Decimal,
    *,
    minimum: Decimal,
    maximum: Decimal,
    field_name: str,
) -> Decimal:
    if value < minimum or value > maximum:
        raise DomainValidationError(f"{field_name} is outside the valid geographic range")
    return value.quantize(COORDINATE_QUANTUM_DEGREES, rounding=ROUND_HALF_UP)


def _stable_distance_nm(value: float) -> Decimal:
    if value < 0:
        raise DomainValidationError("distance cannot be negative")
    return Decimal(str(value)).quantize(
        DISTANCE_STABILITY_QUANTUM_NM,
        rounding=ROUND_HALF_UP,
    )


def haversine_distance_tenths_nm(
    latitude_a: Decimal,
    longitude_a: Decimal,
    latitude_b: Decimal,
    longitude_b: Decimal,
) -> int:
    """Return deterministic great-circle distance in tenths of a nautical mile.

    Coordinates are first canonicalized to 1e-6 degree. The libm result is then stabilized to
    1e-6 nautical mile before the business-visible 0.1-NM ROUND_HALF_UP boundary is applied.
    This preserves Haversine semantics while preventing insignificant platform-level floating
    differences from changing eligibility at the discrete business boundary.
    """

    lat_a = _canonical_coordinate(
        latitude_a,
        minimum=Decimal("-90"),
        maximum=Decimal("90"),
        field_name="latitude_a",
    )
    lon_a = _canonical_coordinate(
        longitude_a,
        minimum=Decimal("-180"),
        maximum=Decimal("180"),
        field_name="longitude_a",
    )
    lat_b = _canonical_coordinate(
        latitude_b,
        minimum=Decimal("-90"),
        maximum=Decimal("90"),
        field_name="latitude_b",
    )
    lon_b = _canonical_coordinate(
        longitude_b,
        minimum=Decimal("-180"),
        maximum=Decimal("180"),
        field_name="longitude_b",
    )

    lat1 = radians(float(lat_a))
    lon1 = radians(float(lon_a))
    lat2 = radians(float(lat_b))
    lon2 = radians(float(lon_b))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    haversine = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    raw_distance_nm = 2 * EARTH_RADIUS_NM * asin(min(1.0, sqrt(haversine)))
    stable_nm = _stable_distance_nm(raw_distance_nm)
    rounded_tenths = (stable_nm / _DISTANCE_TENTH_NM).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    )
    return int(rounded_tenths)
