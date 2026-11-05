from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.status import HTTP_200_OK, HTTP_503_SERVICE_UNAVAILABLE

from src.lib.schemas.health import ReadinessResponse
from src.lib.services.health_check import check_database
from src.lib.settings import get_settings


router = APIRouter(tags=["health"])

_READY = ReadinessResponse(status="ready", database="ok")
_NOT_READY = ReadinessResponse(status="not_ready", database="unavailable")


@router.get(
    "/ready",
    responses={
        HTTP_200_OK: {"model": ReadinessResponse},
        HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse},
    },
)
async def readiness() -> JSONResponse:
    """Return 200 when the database is reachable, 503 otherwise."""
    settings = get_settings()
    if await check_database(settings.readiness_db_timeout_seconds):
        return JSONResponse(status_code=HTTP_200_OK, content=_READY.model_dump())
    return JSONResponse(status_code=HTTP_503_SERVICE_UNAVAILABLE, content=_NOT_READY.model_dump())
