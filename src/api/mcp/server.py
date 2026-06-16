from copy import deepcopy
from typing import Any

from fastapi import FastAPI
from fastmcp import FastMCP
import httpx

from src.api.mcp.backing_client import create_backing_client
from src.api.mcp.headers import forward_api_key
from src.api.mcp.route_maps import ROUTE_MAPS
from src.lib.constants import API_KEY_HEADER, MCP_SERVER_NAME
from src.lib.settings import Settings
from src.lib.utils.logging import logger


def _strip_auto_forwarded_headers(openapi_spec: dict[str, Any]) -> dict[str, Any]:
    """Remove auto-forwarded header parameters from every operation in the spec.

    Api-Key is injected automatically by forward_api_key; exposing it as a tool
    input would mislead agents into thinking they must supply it manually.
    """
    spec = deepcopy(openapi_spec)
    for path_item in spec.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            params = operation.get("parameters")
            if not params:
                continue
            operation["parameters"] = [
                p
                for p in params
                if not (p.get("in") == "header" and p.get("name", "").lower() == API_KEY_HEADER.lower())
            ]
    return spec


def build_mcp_server(app: FastAPI, settings: Settings) -> tuple[FastMCP, httpx.AsyncClient]:
    """Build a FastMCP server generated from the app's OpenAPI contract.

    All operations tagged 'schedules' or 'runs' become MCP Tools; everything
    else (health check, echo endpoints) is excluded. The Api-Key header is
    forwarded from each incoming MCP request to the backing endpoint call via
    an httpx request event hook. Api-Key is stripped from tool input schemas
    since it is injected automatically.

    Returns the FastMCP server and the backing httpx client. The caller is
    responsible for closing the client on shutdown.
    """
    backing_client = create_backing_client(
        app,
        settings,
        event_hooks={"request": [forward_api_key]},
    )
    openapi_spec = _strip_auto_forwarded_headers(app.openapi())
    mcp_server = FastMCP.from_openapi(
        openapi_spec=openapi_spec,
        client=backing_client,
        name=MCP_SERVER_NAME,
        route_maps=ROUTE_MAPS,
    )
    logger.info("mcp_server_built", server_name=MCP_SERVER_NAME)
    return mcp_server, backing_client
