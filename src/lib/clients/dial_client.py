from typing import Any
from urllib.parse import quote

import httpx

from src.lib.constants import (
    API_KEY_HEADER,
    APPLICATION_DETAILS_PATH_TEMPLATE,
    BUCKET_PATH,
    CONVERSATION_PATH_TEMPLATE,
    OBO_CREDENTIALS_PATH,
)
from src.lib.exceptions.errors import ConversationCreationError, DialServiceError, OboCredentialsError
from src.lib.schemas.conversation import ApplicationDetails, DialConversation
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

    async def get_bucket(self, credentials: OboCredentials) -> str:
        """Resolve the caller's DIAL storage bucket id via GET {base_url}/v1/bucket.

        Args:
            credentials: The run owner's on-behalf-of credentials.

        Returns:
            The bucket id used to build conversation storage paths.

        Raises:
            ConversationCreationError: On any non-2xx, transport error, or a body missing
                ``bucket``. Callers treat this as a non-fatal, post-run failure.
        """
        url = f"{self._base_url}{BUCKET_PATH}"
        headers = {credentials.header_name: credentials.header_value.get_secret_value()}
        try:
            response = await self._http_client.get(url, headers=headers, timeout=self._timeout)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ConversationCreationError(f"bucket request failed with status {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ConversationCreationError(f"bucket request failed: {exc}") from exc

        body = _safe_body(response)
        if not isinstance(body, dict) or not body.get("bucket"):
            raise ConversationCreationError("bucket response missing 'bucket'")
        return str(body["bucket"])

    async def get_application(self, credentials: OboCredentials, application_id: str) -> ApplicationDetails:
        """Fetch application details to resolve the underlying model ``reference``.

        Calls GET {base_url}/v1/applications/{application_id}. Only used for
        application deployments (ids beginning with ``applications/``).

        Args:
            credentials: The run owner's on-behalf-of credentials.
            application_id: The application deployment id (e.g. ``applications/{bucket}/{name}``)
                with raw (unescaped) segments — this method percent-encodes them for the URL, so
                passing DIAL's already-encoded URL form would escape it a second time and 404.

        Returns:
            Parsed application details carrying the model ``reference``.

        Raises:
            ConversationCreationError: On any non-2xx, transport error, or a body missing
                ``reference``.
        """
        path = APPLICATION_DETAILS_PATH_TEMPLATE.format(application_id=_encode_resource_path(application_id))
        url = f"{self._base_url}{path}"
        headers = {credentials.header_name: credentials.header_value.get_secret_value()}
        try:
            response = await self._http_client.get(url, headers=headers, timeout=self._timeout)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ConversationCreationError(
                f"application details request failed with status {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ConversationCreationError(f"application details request failed: {exc}") from exc

        body = _safe_body(response)
        try:
            return ApplicationDetails.model_validate(body)
        except ValueError as exc:
            raise ConversationCreationError("application details response missing 'reference'") from exc

    async def put_conversation(self, credentials: OboCredentials, path: str, conversation: DialConversation) -> None:
        """Write a conversation document via PUT {base_url}/v1/conversations/{path}.

        Args:
            credentials: The run owner's on-behalf-of credentials.
            path: The conversation resource path (``{bucket}/{folder...}/{name}``) without the
                ``/v1/conversations/`` prefix, with raw (unescaped) segments — this method
                percent-encodes them for the URL.
            conversation: The conversation document to store.

        Raises:
            ConversationCreationError: On any non-2xx response or transport error.
        """
        encoded_path = _encode_resource_path(path)
        url = f"{self._base_url}{CONVERSATION_PATH_TEMPLATE.format(conversation_path=encoded_path)}"
        headers = {credentials.header_name: credentials.header_value.get_secret_value()}
        try:
            response = await self._http_client.put(
                url,
                headers=headers,
                json=conversation.model_dump(exclude_none=True),
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ConversationCreationError(
                f"conversation write failed with status {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ConversationCreationError(f"conversation write failed: {exc}") from exc


def _encode_resource_path(path: str) -> str:
    """Percent-encode a DIAL resource path for use in a request URL, one segment at a time.

    Segment separators are preserved; everything else — spaces, ``?``, ``#``, ``%``, non-ASCII — is
    escaped, so a bucket, folder or conversation name carrying such characters cannot leak into the
    URL's structure as an extra segment or a query string. This is the only place percent-encoding
    is applied: DIAL stores resource names raw and decodes the path back to them, so escaping here
    keeps the stored ``id`` and the request target identical.

    Args:
        path: The raw resource path, with ``/`` between segments.

    Returns:
        The path with every segment percent-encoded.
    """
    return "/".join(quote(segment, safe="") for segment in path.split("/"))


def _safe_body(response: httpx.Response) -> Any:
    """Return the parsed JSON body, falling back to raw text when not JSON."""
    try:
        return response.json()
    except ValueError:
        return response.text
