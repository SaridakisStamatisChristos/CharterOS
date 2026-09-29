from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise
from typing import cast
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session, aliased

from charteros.application.exceptions import EntityConflictError, EntityNotFoundError
from charteros.application.graph_projection import PROJECTION_NAME
from charteros.application.graph_queries import (
    BookingFlightLineage,
    EmptyLegCandidate,
    HistoricalPosition,
    NearbyAircraft,
    OperatorRoute,
    QuoteEventHistoryItem,
    QuoteHistory,
    QuoteRevision,
)
from charteros.infrastructure.db.models.catalog import AirportRow, OutboxEventRow
from charteros.infrastructure.db.models.graph import (
    GraphEdgeRow,
    GraphNodeRow,
    GraphProjectionVersionRow,
)
from charteros.matching import haversine_distance_tenths_nm

MAX_POSITION_SCAN = 50_000
MAX_BOOKING_SCAN = 10_000


class SqlAlchemyGraphQueryRepository:
    """Read-only bounded queries over the active PR15 graph projection."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def active_version(self) -> int:
        versions = tuple(
            self._session.scalars(
                select(GraphProjectionVersionRow.projection_version).where(
                    GraphProjectionVersionRow.projection_name == PROJECTION_NAME,
                    GraphProjectionVersionRow.status == "active",
                )
            )
        )
        if not versions:
            raise EntityConflictError(
                "no active Charter Graph projection; rebuild, verify, and activate PR15 first"
            )
        if len(versions) != 1:
            raise EntityConflictError("multiple active Charter Graph projections detected")
        return versions[0]

    def has_node(self, *, node_type: str, node_id: UUID) -> bool:
        return self._node_exists(self.active_version(), node_type, node_id)

    def historical_position(
        self,
        *,
        aircraft_id: UUID,
        event_time: datetime,
        known_as_of: datetime,
    ) -> HistoricalPosition | None:
        version = self.active_version()
        position_node = aliased(GraphNodeRow)
        rows = self._session.execute(
            select(
                GraphEdgeRow.source_id,
                position_node.node_id,
                position_node.attributes,
            )
            .join(
                position_node,
                and_(
                    position_node.projection_name == GraphEdgeRow.projection_name,
                    position_node.projection_version == GraphEdgeRow.projection_version,
                    position_node.node_type == GraphEdgeRow.target_type,
                    position_node.node_id == GraphEdgeRow.target_id,
                ),
            )
            .where(
                GraphEdgeRow.projection_name == PROJECTION_NAME,
                GraphEdgeRow.projection_version == version,
                GraphEdgeRow.edge_type == "HAS_POSITION",
                GraphEdgeRow.source_type == "aircraft",
                GraphEdgeRow.source_id == aircraft_id,
                GraphEdgeRow.target_type == "aircraft_position",
            )
            .limit(MAX_POSITION_SCAN + 1)
        ).all()
        if len(rows) > MAX_POSITION_SCAN:
            raise EntityConflictError(
                f"aircraft position history exceeds bounded PR16 scan of {MAX_POSITION_SCAN}"
            )
        if not rows:
            if not self._node_exists(version, "aircraft", aircraft_id):
                raise EntityNotFoundError("aircraft does not exist in the active Charter Graph")
            return None
        positions, airport_ids = _visible_positions(
            rows,
            event_time=event_time,
            known_as_of=known_as_of,
        )
        airports = self._airport_coordinates(airport_ids)
        resolved = [
            _resolved_position(item, airports)
            for item in positions
        ]
        visible = [item for item in resolved if item is not None]
        return max(
            visible,
            key=lambda item: (item.event_time, item.knowledge_time, item.position_id.hex),
            default=None,
        )

    def nearby_aircraft(
        self,
        *,
        airport_id: UUID,
        event_time: datetime,
        known_as_of: datetime,
        radius_nm: Decimal,
        limit: int,
    ) -> tuple[NearbyAircraft, ...]:
        version = self.active_version()
        target = self._session.get(AirportRow, airport_id)
        if target is None:
            raise EntityNotFoundError("airport does not exist")

        position_node = aliased(GraphNodeRow)
        rows = self._session.execute(
            select(
                GraphEdgeRow.source_id,
                position_node.node_id,
                position_node.attributes,
            )
            .join(
                position_node,
                and_(
                    position_node.projection_name == GraphEdgeRow.projection_name,
                    position_node.projection_version == GraphEdgeRow.projection_version,
                    position_node.node_type == GraphEdgeRow.target_type,
                    position_node.node_id == GraphEdgeRow.target_id,
                ),
            )
            .where(
                GraphEdgeRow.projection_name == PROJECTION_NAME,
                GraphEdgeRow.projection_version == version,
                GraphEdgeRow.edge_type == "HAS_POSITION",
                GraphEdgeRow.source_type == "aircraft",
                GraphEdgeRow.target_type == "aircraft_position",
            )
            .limit(MAX_POSITION_SCAN + 1)
        ).all()
        if len(rows) > MAX_POSITION_SCAN:
            raise EntityConflictError(
                f"position projection exceeds bounded PR16 scan of {MAX_POSITION_SCAN}; "
                "use a narrower indexed projection version before serving this query"
            )

        positions, airport_ids = _visible_positions(
            rows,
            event_time=event_time,
            known_as_of=known_as_of,
        )
        airports = self._airport_coordinates(airport_ids | {airport_id})
        latest: dict[UUID, HistoricalPosition] = {}
        for raw in positions:
            item = _resolved_position(raw, airports)
            if item is None:
                continue
            previous = latest.get(item.aircraft_id)
            if previous is None or (
                item.event_time,
                item.knowledge_time,
                item.position_id.hex,
            ) > (
                previous.event_time,
                previous.knowledge_time,
                previous.position_id.hex,
            ):
                latest[item.aircraft_id] = item

        target_coordinates = airports[airport_id]
        found: list[NearbyAircraft] = []
        for item in latest.values():
            distance_tenths = haversine_distance_tenths_nm(
                target_coordinates[0],
                target_coordinates[1],
                item.latitude,
                item.longitude,
            )
            distance = (Decimal(distance_tenths) / Decimal(10)).quantize(Decimal("0.1"))
            if distance <= radius_nm:
                found.append(NearbyAircraft(position=item, distance_nm=distance))
        found.sort(
            key=lambda item: (
                item.distance_nm,
                item.position.aircraft_id.hex,
                item.position.position_id.hex,
            )
        )
        return tuple(found[:limit])

    def operator_route_history(
        self,
        *,
        operator_id: UUID,
        limit: int,
    ) -> tuple[OperatorRoute, ...]:
        version = self.active_version()
        operator_edges = tuple(
            self._session.scalars(
                select(GraphEdgeRow)
                .where(
                    GraphEdgeRow.projection_name == PROJECTION_NAME,
                    GraphEdgeRow.projection_version == version,
                    GraphEdgeRow.edge_type == "WITH_OPERATOR",
                    GraphEdgeRow.source_type == "booking",
                    GraphEdgeRow.target_type == "operator",
                    GraphEdgeRow.target_id == operator_id,
                )
                .limit(MAX_BOOKING_SCAN + 1)
            )
        )
        if not operator_edges:
            if not self._node_exists(version, "operator", operator_id):
                raise EntityNotFoundError("operator does not exist in the active Charter Graph")
            return ()
        if len(operator_edges) > MAX_BOOKING_SCAN:
            raise EntityConflictError(
                f"operator route history exceeds bounded PR16 scan of {MAX_BOOKING_SCAN}"
            )
        booking_ids = tuple(edge.source_id for edge in operator_edges)
        return self._routes_for_bookings(version, booking_ids)[:limit]

    def quote_history(self, *, quote_id: UUID) -> QuoteHistory | None:
        version = self.active_version()
        quote_node = self._session.get(
            GraphNodeRow,
            (PROJECTION_NAME, version, "quote", quote_id),
        )
        if quote_node is None:
            return None
        rfq_edge = self._session.scalar(
            select(GraphEdgeRow).where(
                GraphEdgeRow.projection_name == PROJECTION_NAME,
                GraphEdgeRow.projection_version == version,
                GraphEdgeRow.edge_type == "HAS_QUOTE",
                GraphEdgeRow.source_type == "rfq",
                GraphEdgeRow.target_type == "quote",
                GraphEdgeRow.target_id == quote_id,
            )
        )
        if rfq_edge is None:
            raise EntityConflictError("quote graph node has no RFQ lineage edge")
        rfq_id = rfq_edge.source_id

        lineage_edges = tuple(
            self._session.scalars(
                select(GraphEdgeRow).where(
                    GraphEdgeRow.projection_name == PROJECTION_NAME,
                    GraphEdgeRow.projection_version == version,
                    GraphEdgeRow.edge_type == "HAS_QUOTE",
                    GraphEdgeRow.source_type == "rfq",
                    GraphEdgeRow.source_id == rfq_id,
                    GraphEdgeRow.target_type == "quote",
                )
            )
        )
        quote_ids = tuple(edge.target_id for edge in lineage_edges)
        nodes = tuple(
            self._session.scalars(
                select(GraphNodeRow).where(
                    GraphNodeRow.projection_name == PROJECTION_NAME,
                    GraphNodeRow.projection_version == version,
                    GraphNodeRow.node_type == "quote",
                    GraphNodeRow.node_id.in_(quote_ids),
                )
            )
        )
        if len(nodes) != len(set(quote_ids)):
            raise EntityConflictError("quote revision lineage references a missing quote node")
        revisions = tuple(
            sorted(
                (_quote_revision(node) for node in nodes),
                key=lambda item: (item.revision_number, item.quote_id.hex),
            )
        )

        rows = tuple(
            self._session.scalars(
                select(OutboxEventRow)
                .where(
                    OutboxEventRow.aggregate_type == "quote",
                    OutboxEventRow.aggregate_id == quote_id,
                )
                .order_by(OutboxEventRow.aggregate_version, OutboxEventRow.event_id)
            )
        )
        if not rows:
            raise EntityConflictError("quote graph node has no authoritative event history")
        events = tuple(_quote_event(row) for row in rows)
        return QuoteHistory(
            quote_id=quote_id,
            rfq_id=rfq_id,
            revisions=revisions,
            events=events,
        )

    def empty_leg_candidates(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        limit: int,
    ) -> tuple[EmptyLegCandidate, ...]:
        version = self.active_version()
        aircraft_edges = tuple(
            self._session.scalars(
                select(GraphEdgeRow)
                .where(
                    GraphEdgeRow.projection_name == PROJECTION_NAME,
                    GraphEdgeRow.projection_version == version,
                    GraphEdgeRow.edge_type == "USES_AIRCRAFT",
                    GraphEdgeRow.source_type == "booking",
                    GraphEdgeRow.target_type == "aircraft",
                )
                .limit(MAX_BOOKING_SCAN + 1)
            )
        )
        if len(aircraft_edges) > MAX_BOOKING_SCAN:
            raise EntityConflictError(
                f"booking graph exceeds bounded PR16 scan of {MAX_BOOKING_SCAN}"
            )
        if not aircraft_edges:
            return ()

        booking_ids = tuple(edge.source_id for edge in aircraft_edges)
        routes = self._routes_for_bookings(version, booking_ids)
        operator_by_booking = self._edge_target_map(
            version,
            edge_type="WITH_OPERATOR",
            source_type="booking",
            target_type="operator",
            source_ids=booking_ids,
        )
        aircraft_by_booking = {
            edge.source_id: edge.target_id for edge in aircraft_edges
        }

        by_aircraft: dict[UUID, list[OperatorRoute]] = defaultdict(list)
        for route in routes:
            aircraft_id = aircraft_by_booking.get(route.booking_id)
            if aircraft_id is not None:
                by_aircraft[aircraft_id].append(route)

        airport_ids = {
            route.origin_airport_id for route in routes
        } | {
            route.destination_airport_id for route in routes
        }
        icao_by_airport = self._airport_icaos(airport_ids)

        candidates: list[EmptyLegCandidate] = []
        for aircraft_id, items in by_aircraft.items():
            ordered = sorted(
                items,
                key=lambda item: (
                    item.departure_from,
                    item.departure_to,
                    item.booking_id.hex,
                ),
            )
            for previous, following in pairwise(ordered):
                if previous.destination_airport_id == following.origin_airport_id:
                    continue
                if previous.departure_to > following.departure_from:
                    continue
                candidate_start = previous.departure_to
                candidate_end = following.departure_from
                if candidate_end < window_start or candidate_start > window_end:
                    continue
                operator_id = operator_by_booking.get(previous.booking_id)
                next_operator_id = operator_by_booking.get(following.booking_id)
                if operator_id is None or next_operator_id is None:
                    raise EntityConflictError("booking graph is missing operator lineage")
                if operator_id != next_operator_id:
                    raise EntityConflictError(
                        "one aircraft is linked to sequential bookings for different operators"
                    )
                gap_minutes = int((candidate_end - candidate_start).total_seconds() // 60)
                candidates.append(
                    EmptyLegCandidate(
                        aircraft_id=aircraft_id,
                        operator_id=operator_id,
                        previous_booking_id=previous.booking_id,
                        previous_mission_id=previous.mission_id,
                        next_booking_id=following.booking_id,
                        next_mission_id=following.mission_id,
                        from_airport_id=previous.destination_airport_id,
                        from_icao=icao_by_airport[previous.destination_airport_id],
                        to_airport_id=following.origin_airport_id,
                        to_icao=icao_by_airport[following.origin_airport_id],
                        window_start=candidate_start,
                        window_end=candidate_end,
                        gap_minutes=gap_minutes,
                    )
                )
        candidates.sort(
            key=lambda item: (
                item.window_start,
                item.aircraft_id.hex,
                item.previous_booking_id.hex,
                item.next_booking_id.hex,
            )
        )
        return tuple(candidates[:limit])

    def booking_flight_lineage(
        self,
        *,
        booking_id: UUID,
    ) -> BookingFlightLineage | None:
        version = self.active_version()
        booking = self._session.get(
            GraphNodeRow,
            (PROJECTION_NAME, version, "booking", booking_id),
        )
        if booking is None:
            return None
        routes = self._routes_for_bookings(version, (booking_id,))
        if len(routes) != 1:
            raise EntityConflictError("booking graph does not resolve to exactly one mission route")
        route = routes[0]
        operator = self._single_edge_target(
            version,
            edge_type="WITH_OPERATOR",
            source_type="booking",
            source_id=booking_id,
            target_type="operator",
        )
        aircraft = self._single_edge_target(
            version,
            edge_type="USES_AIRCRAFT",
            source_type="booking",
            source_id=booking_id,
            target_type="aircraft",
        )
        quote = self._single_edge_target(
            version,
            edge_type="ACCEPTED_QUOTE",
            source_type="booking",
            source_id=booking_id,
            target_type="quote",
        )
        return BookingFlightLineage(
            booking_id=booking_id,
            mission_id=route.mission_id,
            accepted_quote_id=quote,
            operator_id=operator,
            aircraft_id=aircraft,
            origin_airport_id=route.origin_airport_id,
            origin_icao=route.origin_icao,
            destination_airport_id=route.destination_airport_id,
            destination_icao=route.destination_icao,
            departure_from=route.departure_from,
            departure_to=route.departure_to,
            booking_state=route.booking_state,
        )

    def _routes_for_bookings(
        self,
        version: int,
        booking_ids: tuple[UUID, ...],
    ) -> tuple[OperatorRoute, ...]:
        if not booking_ids:
            return ()
        mission_to_booking = tuple(
            self._session.scalars(
                select(GraphEdgeRow).where(
                    GraphEdgeRow.projection_name == PROJECTION_NAME,
                    GraphEdgeRow.projection_version == version,
                    GraphEdgeRow.edge_type == "HAS_BOOKING",
                    GraphEdgeRow.source_type == "mission",
                    GraphEdgeRow.target_type == "booking",
                    GraphEdgeRow.target_id.in_(booking_ids),
                )
            )
        )
        if len(mission_to_booking) != len(set(booking_ids)):
            raise EntityConflictError("booking graph has incomplete or ambiguous mission lineage")
        mission_by_booking = {
            edge.target_id: edge.source_id for edge in mission_to_booking
        }
        mission_ids = tuple(mission_by_booking.values())
        mission_nodes = {
            node.node_id: node
            for node in self._session.scalars(
                select(GraphNodeRow).where(
                    GraphNodeRow.projection_name == PROJECTION_NAME,
                    GraphNodeRow.projection_version == version,
                    GraphNodeRow.node_type == "mission",
                    GraphNodeRow.node_id.in_(mission_ids),
                )
            )
        }
        booking_nodes = {
            node.node_id: node
            for node in self._session.scalars(
                select(GraphNodeRow).where(
                    GraphNodeRow.projection_name == PROJECTION_NAME,
                    GraphNodeRow.projection_version == version,
                    GraphNodeRow.node_type == "booking",
                    GraphNodeRow.node_id.in_(booking_ids),
                )
            )
        }
        origin = self._edge_target_map(
            version,
            edge_type="ORIGIN",
            source_type="mission",
            target_type="airport",
            source_ids=mission_ids,
        )
        destination = self._edge_target_map(
            version,
            edge_type="DESTINATION",
            source_type="mission",
            target_type="airport",
            source_ids=mission_ids,
        )
        aircraft = self._edge_target_map(
            version,
            edge_type="USES_AIRCRAFT",
            source_type="booking",
            target_type="aircraft",
            source_ids=booking_ids,
        )
        quote = self._edge_target_map(
            version,
            edge_type="ACCEPTED_QUOTE",
            source_type="booking",
            target_type="quote",
            source_ids=booking_ids,
        )
        airport_ids = set(origin.values()) | set(destination.values())
        icao = self._airport_icaos(airport_ids)

        routes: list[OperatorRoute] = []
        for booking_id in booking_ids:
            mission_id = mission_by_booking.get(booking_id)
            if mission_id is None:
                continue
            mission = mission_nodes.get(mission_id)
            booking = booking_nodes.get(booking_id)
            aircraft_id = aircraft.get(booking_id)
            origin_id = origin.get(mission_id)
            destination_id = destination.get(mission_id)
            if (
                mission is None
                or booking is None
                or aircraft_id is None
                or origin_id is None
                or destination_id is None
            ):
                raise EntityConflictError("graph route lineage is incomplete")
            routes.append(
                OperatorRoute(
                    booking_id=booking_id,
                    mission_id=mission_id,
                    aircraft_id=aircraft_id,
                    accepted_quote_id=quote.get(booking_id),
                    origin_airport_id=origin_id,
                    origin_icao=icao[origin_id],
                    destination_airport_id=destination_id,
                    destination_icao=icao[destination_id],
                    departure_from=_datetime_attr(mission.attributes, "departure_from"),
                    departure_to=_datetime_attr(mission.attributes, "departure_to"),
                    booking_state=_string_attr(
                        booking.attributes,
                        "state",
                        fallback_key="to_state",
                    ),
                )
            )
        routes.sort(
            key=lambda item: (
                item.departure_from,
                item.booking_id.hex,
            ),
            reverse=True,
        )
        return tuple(routes)

    def _edge_target_map(
        self,
        version: int,
        *,
        edge_type: str,
        source_type: str,
        target_type: str,
        source_ids: tuple[UUID, ...],
    ) -> dict[UUID, UUID]:
        if not source_ids:
            return {}
        edges = tuple(
            self._session.scalars(
                select(GraphEdgeRow).where(
                    GraphEdgeRow.projection_name == PROJECTION_NAME,
                    GraphEdgeRow.projection_version == version,
                    GraphEdgeRow.edge_type == edge_type,
                    GraphEdgeRow.source_type == source_type,
                    GraphEdgeRow.source_id.in_(source_ids),
                    GraphEdgeRow.target_type == target_type,
                )
            )
        )
        result: dict[UUID, UUID] = {}
        for edge in edges:
            previous = result.setdefault(edge.source_id, edge.target_id)
            if previous != edge.target_id:
                raise EntityConflictError(
                    f"graph contains multiple {edge_type} targets for {edge.source_id}"
                )
        return result

    def _single_edge_target(
        self,
        version: int,
        *,
        edge_type: str,
        source_type: str,
        source_id: UUID,
        target_type: str,
    ) -> UUID:
        values = self._edge_target_map(
            version,
            edge_type=edge_type,
            source_type=source_type,
            target_type=target_type,
            source_ids=(source_id,),
        )
        value = values.get(source_id)
        if value is None:
            raise EntityConflictError(f"graph is missing {edge_type} lineage for {source_id}")
        return value

    def _airport_coordinates(
        self,
        airport_ids: set[UUID],
    ) -> dict[UUID, tuple[Decimal, Decimal]]:
        if not airport_ids:
            return {}
        rows = tuple(
            self._session.scalars(select(AirportRow).where(AirportRow.id.in_(airport_ids)))
        )
        values = {
            row.id: (Decimal(row.latitude), Decimal(row.longitude)) for row in rows
        }
        missing = airport_ids - set(values)
        if missing:
            raise EntityConflictError("graph position references an unknown canonical airport")
        return values

    def _airport_icaos(self, airport_ids: set[UUID]) -> dict[UUID, str]:
        if not airport_ids:
            return {}
        rows = self._session.execute(
            select(AirportRow.id, AirportRow.icao).where(AirportRow.id.in_(airport_ids))
        ).all()
        values = {row[0]: str(row[1]) for row in rows}
        if airport_ids - set(values):
            raise EntityConflictError("graph route references an unknown canonical airport")
        return values

    def _node_exists(self, version: int, node_type: str, node_id: UUID) -> bool:
        return (
            self._session.get(
                GraphNodeRow,
                (PROJECTION_NAME, version, node_type, node_id),
            )
            is not None
        )


def _visible_positions(
    rows: list[object] | tuple[object, ...],
    *,
    event_time: datetime,
    known_as_of: datetime,
) -> tuple[list[tuple[UUID, UUID, dict[str, object]]], set[UUID]]:
    values: list[tuple[UUID, UUID, dict[str, object]]] = []
    airport_ids: set[UUID] = set()
    for row in rows:
        source_id = cast(UUID, row[0])
        position_id = cast(UUID, row[1])
        raw = row[2]
        if not isinstance(raw, dict):
            raise EntityConflictError("position projection attributes must be an object")
        attributes = {str(key): value for key, value in raw.items()}
        item_event_time = _datetime_attr(attributes, "event_time")
        item_knowledge_time = _datetime_attr(attributes, "knowledge_time")
        if item_event_time > event_time or item_knowledge_time > known_as_of:
            continue
        airport_id = _optional_uuid_attr(attributes, "airport_id")
        if airport_id is not None:
            airport_ids.add(airport_id)
        values.append((source_id, position_id, attributes))
    return values, airport_ids


def _resolved_position(
    raw: tuple[UUID, UUID, dict[str, object]],
    airports: dict[UUID, tuple[Decimal, Decimal]],
) -> HistoricalPosition | None:
    aircraft_id, position_id, attributes = raw
    airport_id = _optional_uuid_attr(attributes, "airport_id")
    if airport_id is not None:
        coordinates = airports.get(airport_id)
        if coordinates is None:
            return None
        latitude, longitude = coordinates
    else:
        latitude = _decimal_attr(attributes, "latitude")
        longitude = _decimal_attr(attributes, "longitude")
    provenance = attributes.get("provenance", {})
    if not isinstance(provenance, dict):
        raise EntityConflictError("position provenance must be an object")
    source = _string_attr(attributes, "source")
    return HistoricalPosition(
        aircraft_id=aircraft_id,
        position_id=position_id,
        airport_id=airport_id,
        latitude=latitude,
        longitude=longitude,
        event_time=_datetime_attr(attributes, "event_time"),
        knowledge_time=_datetime_attr(attributes, "knowledge_time"),
        source=source,
        provenance={str(key): value for key, value in provenance.items()},
    )


def _quote_revision(node: GraphNodeRow) -> QuoteRevision:
    attributes = node.attributes
    revision = attributes.get("revision_number")
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise EntityConflictError("quote projection is missing revision_number")
    return QuoteRevision(
        quote_id=node.node_id,
        revision_number=revision,
        status=_string_attr(attributes, "status"),
        supersedes_quote_id=_optional_uuid_attr(attributes, "supersedes_quote_id"),
    )


def _quote_event(row: OutboxEventRow) -> QuoteEventHistoryItem:
    try:
        document = json.loads(row.canonical_json)
    except json.JSONDecodeError as exc:
        raise EntityConflictError("quote history contains invalid canonical event JSON") from exc
    if not isinstance(document, dict):
        raise EntityConflictError("quote history canonical event must be an object")
    payload = document.get("payload")
    if not isinstance(payload, dict):
        raise EntityConflictError("quote history event payload must be an object")
    return QuoteEventHistoryItem(
        event_id=row.event_id,
        aggregate_version=row.aggregate_version,
        event_type=row.event_type,
        event_version=row.event_version,
        occurred_at=row.occurred_at,
        recorded_at=row.recorded_at,
        payload={str(key): value for key, value in payload.items()},
    )


def _datetime_attr(attributes: dict[str, object], key: str) -> datetime:
    value = attributes.get(key)
    if not isinstance(value, str):
        raise EntityConflictError(f"graph attribute {key} must be an ISO timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EntityConflictError(f"graph attribute {key} is not a valid timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise EntityConflictError(f"graph attribute {key} must be timezone-aware")
    return result.astimezone(UTC)


def _decimal_attr(attributes: dict[str, object], key: str) -> Decimal:
    value = attributes.get(key)
    if not isinstance(value, str):
        raise EntityConflictError(f"graph attribute {key} must be a decimal string")
    try:
        return Decimal(value)
    except Exception as exc:
        raise EntityConflictError(f"graph attribute {key} is not a valid decimal") from exc


def _string_attr(
    attributes: dict[str, object],
    key: str,
    *,
    fallback_key: str | None = None,
) -> str:
    value = attributes.get(key)
    if not isinstance(value, str) and fallback_key is not None:
        value = attributes.get(fallback_key)
    if not isinstance(value, str) or not value:
        raise EntityConflictError(f"graph attribute {key} must be a non-empty string")
    return value


def _optional_uuid_attr(attributes: dict[str, object], key: str) -> UUID | None:
    value = attributes.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise EntityConflictError(f"graph attribute {key} must be a UUID string")
    try:
        return UUID(value)
    except ValueError as exc:
        raise EntityConflictError(f"graph attribute {key} is not a valid UUID") from exc
