from typing import Any

import httpx

from src.lib.constants import API_KEY_HEADER
from src.lib.exceptions.errors import DialServiceError


class DialClient:
    """Thin wrapper around httpx for calling the DIAL /v1/user/info endpoint."""

    def __init__(self, http_client: httpx.AsyncClient, base_url: str, timeout: float) -> None:
        self._http_client = http_client
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def get_user_info(self, api_key: str) -> dict[str, Any]:
        """Call GET {base_url}/v1/user/info with the caller's Api-Key and return the JSON body.

        Returns an empty dict when the response body is not valid JSON.
        Raises DialServiceError on any non-2xx or transport error.
        """
        url = f"{self._base_url}/v1/user/info"
        try:
            response = await self._http_client.get(url, headers={API_KEY_HEADER: api_key}, timeout=self._timeout)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DialServiceError(f"DIAL returned unexpected status: {exc.response.status_code}") from exc
        except httpx.TransportError as exc:
            raise DialServiceError(str(exc)) from exc

        try:
            return response.json()
        except ValueError:
            return {}
