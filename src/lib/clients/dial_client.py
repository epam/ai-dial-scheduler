from typing import Any

import httpx

from src.lib.constants import API_KEY_HEADER, OBO_CREDENTIALS_PATH
from src.lib.exceptions.errors import DialServiceError, OboCredentialsError
from src.lib.schemas.credentials import OboCredentials, OboCredentialsRequest


class DialClient:
    """Thin wrapper around httpx for calling DIAL operational endpoints."""

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

    async def get_obo_credentials(self, api_key: str, request: OboCredentialsRequest) -> OboCredentials:
        """Fetch on-behalf-of credentials for a schedule's owner and external service.

        Calls POST {base_url}/v1/ops/external-service/obo-credentials authenticated with
        the scheduler service ``api_key``. On success the response ``header_name`` and
        ``header_value`` are returned for the caller to apply to the outgoing target request.

        Raises:
            OboCredentialsError: On any non-2xx response, a body missing
                ``header_name``/``header_value``, or a transport error. The error carries
                the DIAL response status and body for diagnosis (never an issued secret).
        """
        url = f"{self._base_url}{OBO_CREDENTIALS_PATH}"
        try:
            response = await self._http_client.post(
                url,
                headers={API_KEY_HEADER: api_key},
                json=request.model_dump(),
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise OboCredentialsError(
                f"OBO credentials request failed with status {exc.response.status_code}",
                status_code=exc.response.status_code,
                response_body=_safe_body(exc.response),
            ) from exc
        except httpx.HTTPError as exc:
            # Catches httpx.TransportError plus any other request-side failure (e.g.
            # TooManyRedirects, InvalidURL) so every OBO failure is uniformly a credentials-stage
            # error, never silently recorded as a target-stage failure by the caller.
            raise OboCredentialsError(str(exc)) from exc

        body = _safe_body(response)
        if not isinstance(body, dict) or not body.get("header_name") or not body.get("header_value"):
            raise OboCredentialsError(
                "OBO credentials response missing header_name/header_value",
                status_code=response.status_code,
                response_body=body,
            )
        return OboCredentials(header_name=body["header_name"], header_value=body["header_value"])


def _safe_body(response: httpx.Response) -> Any:
    """Return the parsed JSON body, falling back to raw text when not JSON."""
    try:
        return response.json()
    except ValueError:
        return response.text
