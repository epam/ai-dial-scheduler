from fastmcp.server.dependencies import get_http_headers
import httpx

from src.lib.constants import API_KEY_HEADER, CLIENT_CHANNEL_HEADER
from src.lib.utils.logging import logger


async def forward_api_key(request: httpx.Request) -> None:
    """Inject the caller's Api-Key from the incoming MCP HTTP request onto the backing call.

    Reads the current MCP HTTP request headers (via FastMCP context) and copies
    the Api-Key header to the outgoing backing request. No-ops when the header is absent
    or no HTTP request is active. The value is never logged.
    """
    incoming = get_http_headers()
    api_key = incoming.get(API_KEY_HEADER.lower())
    if api_key:
        request.headers[API_KEY_HEADER] = api_key
    logger.debug("mcp_api_key_forward", forwarded=bool(api_key), path=str(request.url.path))


async def forward_client_channel_id(request: httpx.Request) -> None:
    """Forward the caller's client-channel id from the incoming MCP request to the backing call."""
    incoming = get_http_headers()
    channel_id = incoming.get(CLIENT_CHANNEL_HEADER.lower())
    if channel_id:
        request.headers[CLIENT_CHANNEL_HEADER] = channel_id
    logger.debug("mcp_client_channel_forward", forwarded=bool(channel_id), path=str(request.url.path))
