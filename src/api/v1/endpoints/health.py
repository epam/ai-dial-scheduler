import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.status import HTTP_200_OK, HTTP_503_SERVICE_UNAVAILABLE

from src.lib.db.engine import Database
from src.lib.schemas.health import ReadinessResponse
from src.lib.settings import get_settings
from src.lib.utils.logging import logger


router = APIRouter(tags=["health"])

_READY = ReadinessResponse(status="ready", database="ok")
_NOT_READY = ReadinessResponse(status="not_ready", database="unavailable")


async def _check_db(timeout: float) -> bool:
    """Return True if the database responds within *timeout* seconds."""
    try:

        async def _query() -> None:
            async with Database().sessionmaker() as session:
                await session.execute(text("SELECT 1"))

        await asyncio.wait_for(_query(), timeout=timeout)
        return True
    except Exception as exc:
        logger.warning("readiness_check_failed", error=str(exc))
        return False


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
    if await _check_db(settings.readiness_db_timeout_seconds):
        return JSONResponse(status_code=HTTP_200_OK, content=_READY.model_dump())
    return JSONResponse(status_code=HTTP_503_SERVICE_UNAVAILABLE, content=_NOT_READY.model_dump())
