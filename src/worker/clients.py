import httpx

from src.lib.settings import get_settings
from src.lib.utils.logging import logger


class HttpClientProvider:
    _instance: "HttpClientProvider | None" = None

    def __new__(cls) -> "HttpClientProvider":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialized"):
            return
        self._client: httpx.AsyncClient | None = None
        self._initialized = True

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            worker_http = get_settings().worker_http
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    worker_http.timeout_seconds,
                    connect=worker_http.connect_timeout_seconds,
                    read=worker_http.read_timeout_seconds,
                    write=worker_http.write_timeout_seconds,
                    pool=worker_http.pool_timeout_seconds,
                ),
                limits=httpx.Limits(
                    max_connections=worker_http.max_connections,
                    max_keepalive_connections=worker_http.max_keepalive_connections,
                ),
            )
            logger.info("Created shared worker HTTP client.")
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.info("Closed shared worker HTTP client.")
