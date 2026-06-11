from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from aidial_sdk import DIALApp
from aidial_sdk.telemetry.types import TelemetryConfig
import httpx
from starlette.middleware.cors import CORSMiddleware
import uvicorn

from src.api.v1.endpoints.schedules import router as schedules_router
from src.lib.db.engine import Database
from src.lib.scheduler import create_scheduler
from src.lib.settings import get_settings
from src.lib.utils.logging import LoggingConfig, configure_logging, logger


@asynccontextmanager
async def lifespan(app: DIALApp) -> AsyncGenerator[None, None]:
    database = Database()
    database.init_engine()
    scheduler = create_scheduler()
    # Start paused: persists jobs to the store but never dispatches them.
    # The worker process is the sole executor.
    scheduler.start(paused=True)
    app.state.scheduler = scheduler  # type: ignore[attr-defined]
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=5.0, read=60.0, write=60.0, pool=5.0),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=20),
        ) as app_client:
            logger.info("Created global HTTP Client for the app lifespan.")
            app.state.app_client = app_client  # type: ignore[attr-defined]
            yield
        logger.info("Destroyed global HTTP Client for the app lifespan.")
    finally:
        scheduler.shutdown(wait=False)
        await database.dispose()


def create_api() -> DIALApp:
    configure_logging()
    settings = get_settings()
    app = DIALApp(
        dial_url=settings.dial_url,
        title=settings.app_name,
        add_healthcheck=True,
        telemetry_config=TelemetryConfig(service_name=settings.app_name),
        lifespan=lifespan,
        redirect_slashes=False,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(schedules_router, prefix="/v1")

    return app


def run_api() -> None:
    settings = get_settings()
    options: dict = {
        "host": "0.0.0.0",
        "port": settings.port,
        "log_level": settings.log_level.lower(),
        "reload": settings.env == "dev",
        "log_config": LoggingConfig.get_config(),
        "factory": True,
    }
    uvicorn.run("src.api.app:create_api", **options)
