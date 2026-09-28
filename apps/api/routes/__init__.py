from apps.api.routes.bookings import router as booking_router
from apps.api.routes.catalog import router as catalog_router
from apps.api.routes.contracts import router as contract_router
from apps.api.routes.fleet import router as fleet_router
from apps.api.routes.matching import router as matching_router
from apps.api.routes.missions import router as mission_router
from apps.api.routes.quotes import router as quote_router
from apps.api.routes.rfqs import router as rfq_router

__all__ = [
    "booking_router",
    "catalog_router",
    "contract_router",
    "fleet_router",
    "matching_router",
    "mission_router",
    "quote_router",
    "rfq_router",
]
