import httpx

from src.lib.constants import USER_AGENT_HEADER
from src.lib.settings import ApiHttpSettings, WorkerHttpSettings
from src.lib.utils.user_agent import get_user_agent


def create_http_client(config: ApiHttpSettings | WorkerHttpSettings) -> httpx.AsyncClient:
    """Create the shared outbound HTTP client used for DIAL Core and schedule targets.

    Every request carries the scheduler ``User-Agent`` so its traffic can be filtered downstream.

    Args:
        config: Timeout and connection-pool settings of the owning process.

    Returns:
        A configured ``httpx.AsyncClient``; the caller owns its lifecycle.
    """
    return httpx.AsyncClient(
        headers={USER_AGENT_HEADER: get_user_agent()},
        timeout=httpx.Timeout(
            connect=config.connect_timeout_seconds,
            read=config.read_timeout_seconds,
            write=config.write_timeout_seconds,
            pool=config.pool_timeout_seconds,
        ),
        limits=httpx.Limits(
            max_connections=config.max_connections,
            max_keepalive_connections=config.max_keepalive_connections,
        ),
    )
