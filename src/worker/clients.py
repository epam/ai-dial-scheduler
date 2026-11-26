import httpx

from src.lib.clients.http_client import create_http_client
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
            self._client = create_http_client(get_settings().worker_http)
            logger.info("Created shared worker HTTP client.")
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.info("Closed shared worker HTTP client.")
