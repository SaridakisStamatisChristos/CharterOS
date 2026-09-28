from decimal import Decimal

import pytest

from charteros.domain.aircraft import Aircraft, AircraftStatus, AircraftType
from charteros.domain.airports import Airport
from charteros.domain.operators import Operator
from charteros.domain.organizations import (
    Organization,
    OrganizationStatus,
    OrganizationType,
)
from charteros.domain.shared.exceptions import DomainValidationError


def test_organization_is_canonical_and_emits_creation_event() -> None:
    organization = Organization.create(
        organization_type=OrganizationType.OPERATOR,
        legal_name="  Aegean   Charter  ",
        trading_name="Aegean",
        country="gr",
        status=OrganizationStatus.ACTIVE,
    )

    assert organization.legal_name == "Aegean Charter"
    assert organization.country == "GR"
    assert organization.version == 1
    assert organization.pending_events[0].event_type == "ORGANIZATION_CREATED"


def test_operator_normalizes_aoc_and_regions() -> None:
    organization = Organization.create(
        organization_type=OrganizationType.OPERATOR,
        legal_name="Aegean Charter",
        trading_name=None,
        country="GR",
    )
    operator = Operator.create(
        organization_id=organization.id,
        aoc_reference=" gr-123 ",
        operating_regions=("eu", "EU", "med"),
    )

    assert operator.aoc_reference == "GR-123"
    assert operator.operating_regions == ("EU", "MED")


def test_airport_validates_coordinates_and_timezone() -> None:
    airport = Airport.create(
        icao="lgav",
        iata="ath",
        latitude=Decimal("37.9364"),
        longitude=Decimal("23.9445"),
        timezone="Europe/Athens",
    )
    assert airport.icao == "LGAV"
    assert airport.iata == "ATH"

    with pytest.raises(DomainValidationError):
        Airport.create(
            icao="LGAV",
            iata="ATH",
            latitude=Decimal("91"),
            longitude=Decimal("23"),
            timezone="Europe/Athens",
        )


def test_aircraft_capacity_must_fit_reference_type() -> None:
    organization = Organization.create(
        organization_type=OrganizationType.OPERATOR,
        legal_name="Operator",
        trading_name=None,
        country="GR",
    )
    operator = Operator.create(
        organization_id=organization.id,
        aoc_reference="GR-456",
        operating_regions=("EU",),
    )
    airport = Airport.create(
        icao="LGAV",
        iata="ATH",
        latitude=Decimal("37.9364"),
        longitude=Decimal("23.9445"),
        timezone="Europe/Athens",
    )
    aircraft_type = AircraftType.create(
        manufacturer="Airbus",
        model="A320-200",
        category="airliner",
        seats_min=150,
        seats_max=186,
        range_nm=3300,
    )

    aircraft = Aircraft.create(
        operator_id=operator.id,
        registration="SX-ABC",
        aircraft_type=aircraft_type,
        seat_capacity=180,
        cargo_capacity=Decimal("1500"),
        range_nm=3200,
        home_base_id=airport.id,
        status=AircraftStatus.ACTIVE,
    )
    assert aircraft.registration == "SX-ABC"
    assert aircraft.pending_events[0].event_type == "AIRCRAFT_REGISTERED"

    with pytest.raises(DomainValidationError):
        Aircraft.create(
            operator_id=operator.id,
            registration="SX-DEF",
            aircraft_type=aircraft_type,
            seat_capacity=200,
            cargo_capacity=Decimal("1500"),
            range_nm=3200,
            home_base_id=airport.id,
        )
