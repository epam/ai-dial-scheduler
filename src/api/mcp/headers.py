from fastmcp.server.dependencies import get_http_headers
import httpx

from src.lib.constants import API_KEY_HEADER
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
