from decimal import Decimal

from charteros.matching.distance import (
    COORDINATE_QUANTUM_DEGREES,
    DISTANCE_STABILITY_QUANTUM_NM,
    _stable_distance_nm,
    haversine_distance_tenths_nm,
)


def test_distance_policy_constants_are_explicit() -> None:
    assert COORDINATE_QUANTUM_DEGREES == Decimal("0.000001")
    assert DISTANCE_STABILITY_QUANTUM_NM == Decimal("0.000001")


def test_haversine_distance_is_symmetric_and_zero_for_same_point() -> None:
    athens = (Decimal("37.9364"), Decimal("23.9445"))
    new_york = (Decimal("40.6413"), Decimal("-73.7781"))

    forward = haversine_distance_tenths_nm(*athens, *new_york)
    reverse = haversine_distance_tenths_nm(*new_york, *athens)

    assert forward == reverse == 42_831
    assert haversine_distance_tenths_nm(*athens, *athens) == 0


def test_sub_quantum_coordinate_noise_cannot_change_business_distance() -> None:
    canonical = haversine_distance_tenths_nm(
        Decimal("37.936400"),
        Decimal("23.944500"),
        Decimal("40.641300"),
        Decimal("-73.778100"),
    )
    noisy = haversine_distance_tenths_nm(
        Decimal("37.9364001"),
        Decimal("23.9445002"),
        Decimal("40.6413001"),
        Decimal("-73.7781002"),
    )

    assert noisy == canonical


def test_libm_distance_is_stabilized_before_business_rounding() -> None:
    assert _stable_distance_nm(1.23456749) == Decimal("1.234567")
    assert _stable_distance_nm(1.23456750) == Decimal("1.234568")
