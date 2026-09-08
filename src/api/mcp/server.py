from copy import deepcopy
from typing import Any

from fastapi import FastAPI
from fastmcp import FastMCP
import httpx

from src.api.mcp.backing_client import create_backing_client
from src.api.mcp.headers import forward_api_key, forward_client_channel_id
from src.api.mcp.route_maps import ROUTE_MAPS
from src.lib.constants import API_KEY_HEADER, CLIENT_CHANNEL_HEADER, MCP_SERVER_NAME
from src.lib.settings import Settings
from src.lib.utils.logging import logger


_AUTO_FORWARDED_HEADERS = {API_KEY_HEADER.lower(), CLIENT_CHANNEL_HEADER.lower()}


def _strip_auto_forwarded_headers(openapi_spec: dict[str, Any]) -> dict[str, Any]:
    """Remove auto-forwarded header parameters from every operation in the spec.

    Api-Key and the client-channel id are injected automatically; exposing them as tool
    input would mislead agents into thinking they must supply them manually.
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
                if not (p.get("in") == "header" and p.get("name", "").lower() in _AUTO_FORWARDED_HEADERS)
            ]
    return spec


def build_mcp_server(app: FastAPI, settings: Settings) -> tuple[FastMCP, httpx.AsyncClient]:
    """Build a FastMCP server generated from the app's OpenAPI contract.

    All operations tagged 'schedules', 'runs' or 'sign-in' become MCP Tools; everything
    else (health check, echo endpoints) is excluded. Api-Key and X-DIAL-CLIENT-CHANNEL-ID
    are forwarded from each incoming MCP request to the backing endpoint call via httpx
    request event hooks, and stripped from tool input schemas since both are injected
    automatically.

    Returns the FastMCP server and the backing httpx client. The caller is
    responsible for closing the client on shutdown.
    """
    backing_client = create_backing_client(
        app,
        settings,
        event_hooks={"request": [forward_api_key, forward_client_channel_id]},
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
