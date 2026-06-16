from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import FastAPI
import httpx

from src.lib.constants import MCP_BACKING_BASE_URL
from src.lib.settings import Settings


type AsyncRequestHook = Callable[[httpx.Request], Coroutine[Any, Any, None]]


def create_backing_client(
    app: FastAPI,
    settings: Settings,
    event_hooks: dict[str, list[AsyncRequestHook]] | None = None,
) -> httpx.AsyncClient:
    """Create an in-process httpx client backed by ASGITransport for the given app.

    Uses the same timeout and connection-limit configuration as the public API HTTP client.
    The caller is responsible for closing the returned client.
    """
    api_http = settings.api_http
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=MCP_BACKING_BASE_URL,
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
        event_hooks=event_hooks or {},
    )
