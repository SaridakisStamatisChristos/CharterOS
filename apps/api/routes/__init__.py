from apps.api.routes.catalog import router as catalog_router
from apps.api.routes.fleet import router as fleet_router
from apps.api.routes.missions import router as mission_router

__all__ = ["catalog_router", "fleet_router", "mission_router"]
