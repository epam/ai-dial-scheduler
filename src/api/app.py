from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from aidial_sdk import DIALApp
from aidial_sdk.telemetry.types import TelemetryConfig
import httpx
from starlette.middleware.cors import CORSMiddleware
import uvicorn

from src.api.v1.endpoints.runs import router as runs_router
from src.api.v1.endpoints.schedules import router as schedules_router
from src.lib.db.engine import Database
from src.lib.scheduler import create_scheduler
from src.lib.settings import get_settings
from src.lib.utils.logging import LoggingConfig, configure_logging, logger


@asynccontextmanager
async def lifespan(app: DIALApp) -> AsyncGenerator[None, None]:
    settings = get_settings()
    database = Database()
    database.init_engine()
    scheduler = create_scheduler()
    # Start paused: persists jobs to the store but never dispatches them.
    # The worker process is the sole executor.
    scheduler.start(paused=True)
    app.state.scheduler = scheduler  # type: ignore[attr-defined]
    try:
        api_http = settings.api_http
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                api_http.timeout_seconds,
                connect=api_http.connect_timeout_seconds,
                read=api_http.read_timeout_seconds,
                write=api_http.write_timeout_seconds,
                pool=api_http.pool_timeout_seconds,
            ),
            limits=httpx.Limits(
                max_connections=api_http.max_connections,
                max_keepalive_connections=api_http.max_keepalive_connections,
            ),
        ) as app_client:
            logger.info("Created global HTTP Client for the app lifespan.")
            app.state.app_client = app_client  # type: ignore[attr-defined]
            yield
        logger.info("Destroyed global HTTP Client for the app lifespan.")
    finally:
        mcp_client = getattr(app.state, "mcp_backing_client", None)
        if mcp_client is not None:
            await mcp_client.aclose()
        scheduler.shutdown(wait=False)
        await database.dispose()


def _wrap_with_mcp_lifespan(original_lifespan: Any, mcp_asgi: Any) -> Any:
    """Return a combined lifespan that starts the original then the MCP sub-app."""

    @asynccontextmanager
    async def combined(app: DIALApp) -> AsyncGenerator[None, None]:
        async with original_lifespan(app):
            async with mcp_asgi.lifespan(mcp_asgi):
                yield

    return combined


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
    app.include_router(runs_router, prefix="/v1")

    if settings.echo_endpoints:
        from src.api.v1.echo_chat import EchoChatCompletion
        from src.api.v1.endpoints.echo import router as echo_router

        app.include_router(echo_router)
        app.add_chat_completion("echo", EchoChatCompletion())

    if settings.mcp_enabled:
        from src.api.mcp.server import build_mcp_server

        mcp_server, mcp_backing_client = build_mcp_server(app, settings)
        mcp_asgi = mcp_server.http_app(path="/")
        app.state.mcp_backing_client = mcp_backing_client  # type: ignore[attr-defined]
        original_lifespan = app.router.lifespan_context
        app.router.lifespan_context = _wrap_with_mcp_lifespan(original_lifespan, mcp_asgi)
        app.mount(settings.mcp_path, mcp_asgi)
        logger.info("mcp_mounted", path=settings.mcp_path)

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
