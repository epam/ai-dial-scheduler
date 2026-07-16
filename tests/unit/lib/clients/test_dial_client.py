"""Unit tests for DialClient — success path (US1) and error mapping (US2)."""

from unittest.mock import AsyncMock

import httpx
import pytest

from src.lib.clients.dial_client import DialClient
from src.lib.exceptions.errors import DialServiceError, OboCredentialsError
from src.lib.schemas.credentials import OboCredentialsRequest


_DUMMY_REQUEST = httpx.Request("GET", "https://dial.example.com/v1/user/info")


def make_response(status_code: int, json_data=None, text: str = "") -> httpx.Response:
    if json_data is not None:
        import json

        content = json.dumps(json_data).encode()
        headers = {"content-type": "application/json"}
    else:
        content = text.encode()
        headers = {"content-type": "text/plain"}
    return httpx.Response(status_code=status_code, content=content, headers=headers, request=_DUMMY_REQUEST)


class TestDialClientSuccess:
    async def test_get_user_info_returns_dict_on_2xx_json(self):
        mock_http_client = AsyncMock()
        response = make_response(200, {"userClaims": {"mail": ["alice@example.com"]}})
        mock_http_client.get.return_value = response

        client = DialClient(http_client=mock_http_client, base_url="https://dial.example.com", timeout=30.0)
        result = await client.get_user_info("alice-key")

        assert result == {"userClaims": {"mail": ["alice@example.com"]}}

    async def test_get_user_info_sends_api_key_header(self):
        mock_http_client = AsyncMock()
        response = make_response(200, {"project": "alpha"})
        mock_http_client.get.return_value = response

        client = DialClient(http_client=mock_http_client, base_url="https://dial.example.com", timeout=30.0)
        await client.get_user_info("my-api-key")

        call_kwargs = mock_http_client.get.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers.get("Api-Key") == "my-api-key"

    async def test_get_user_info_calls_correct_url(self):
        mock_http_client = AsyncMock()
        response = make_response(200, {})
        mock_http_client.get.return_value = response

        client = DialClient(http_client=mock_http_client, base_url="https://dial.example.com", timeout=30.0)
        await client.get_user_info("key")

        url_called = mock_http_client.get.call_args.args[0]
        assert url_called == "https://dial.example.com/v1/user/info"

    async def test_get_user_info_passes_timeout_to_http_client(self):
        mock_http_client = AsyncMock()
        response = make_response(200, {})
        mock_http_client.get.return_value = response

        client = DialClient(http_client=mock_http_client, base_url="https://dial.example.com", timeout=15.0)
        await client.get_user_info("key")

        timeout_kwarg = mock_http_client.get.call_args.kwargs.get("timeout")
        assert timeout_kwarg == 15.0

    async def test_get_user_info_returns_empty_dict_on_non_json_2xx(self):
        mock_http_client = AsyncMock()
        response = make_response(200, text="not json at all")
        mock_http_client.get.return_value = response

        client = DialClient(http_client=mock_http_client, base_url="https://dial.example.com", timeout=30.0)
        result = await client.get_user_info("key")

        assert result == {}


class TestDialClientErrorMapping:
    async def test_401_raises_dial_service_error(self):
        mock_http_client = AsyncMock()
        request = httpx.Request("GET", "https://dial.example.com/v1/user/info")
        response = httpx.Response(401, request=request)
        mock_http_client.get.return_value = response

        client = DialClient(http_client=mock_http_client, base_url="https://dial.example.com", timeout=30.0)
        with pytest.raises(DialServiceError):
            await client.get_user_info("bad-key")

    async def test_403_raises_dial_service_error(self):
        mock_http_client = AsyncMock()
        request = httpx.Request("GET", "https://dial.example.com/v1/user/info")
        response = httpx.Response(403, request=request)
        mock_http_client.get.return_value = response

        client = DialClient(http_client=mock_http_client, base_url="https://dial.example.com", timeout=30.0)
        with pytest.raises(DialServiceError):
            await client.get_user_info("bad-key")

    async def test_5xx_raises_dial_service_error(self):
        mock_http_client = AsyncMock()
        request = httpx.Request("GET", "https://dial.example.com/v1/user/info")
        response = httpx.Response(500, request=request)
        mock_http_client.get.return_value = response

        client = DialClient(http_client=mock_http_client, base_url="https://dial.example.com", timeout=30.0)
        with pytest.raises(DialServiceError):
            await client.get_user_info("key")

    async def test_404_raises_dial_service_error(self):
        mock_http_client = AsyncMock()
        request = httpx.Request("GET", "https://dial.example.com/v1/user/info")
        response = httpx.Response(404, request=request)
        mock_http_client.get.return_value = response

        client = DialClient(http_client=mock_http_client, base_url="https://dial.example.com", timeout=30.0)
        with pytest.raises(DialServiceError):
            await client.get_user_info("key")

    async def test_transport_error_raises_dial_service_error(self):
        mock_http_client = AsyncMock()
        mock_http_client.get.side_effect = httpx.TransportError("Connection refused")

        client = DialClient(http_client=mock_http_client, base_url="https://dial.example.com", timeout=30.0)
        with pytest.raises(DialServiceError):
            await client.get_user_info("key")

    async def test_connect_timeout_raises_dial_service_error(self):
        mock_http_client = AsyncMock()
        mock_http_client.get.side_effect = httpx.ConnectTimeout("Timeout")

        client = DialClient(http_client=mock_http_client, base_url="https://dial.example.com", timeout=30.0)
        with pytest.raises(DialServiceError):
            await client.get_user_info("key")


_OBO_REQUEST = OboCredentialsRequest(
    url="applications/dial_scheduler/external_services/dial-oauth",
    owner_sub="user-sub-123",
)


class TestGetOboCredentialsSuccess:
    async def test_posts_to_obo_endpoint_with_service_key_and_body(self):
        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = make_response(200, {"header_name": "Api-Key", "header_value": "tok"})

        client = DialClient(http_client=mock_http_client, base_url="https://dial.example.com", timeout=20.0)
        await client.get_obo_credentials("service-key", _OBO_REQUEST)

        call = mock_http_client.post.call_args
        assert call.args[0] == "https://dial.example.com/v1/ops/external-service/obo-credentials"
        assert call.kwargs["headers"]["Api-Key"] == "service-key"
        assert call.kwargs["json"] == {
            "url": "applications/dial_scheduler/external_services/dial-oauth",
            "owner_sub": "user-sub-123",
        }
        assert call.kwargs["timeout"] == 20.0

    async def test_api_key_type_service_returns_api_key_header(self):
        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = make_response(200, {"header_name": "Api-Key", "header_value": "raw-key"})

        client = DialClient(http_client=mock_http_client, base_url="https://dial.example.com", timeout=20.0)
        creds = await client.get_obo_credentials("service-key", _OBO_REQUEST)

        assert creds.header_name == "Api-Key"
        assert creds.header_value.get_secret_value() == "raw-key"

    async def test_oauth_type_service_returns_bearer_authorization_header(self):
        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = make_response(
            200, {"header_name": "Authorization", "header_value": "Bearer abc.def.ghi"}
        )

        client = DialClient(http_client=mock_http_client, base_url="https://dial.example.com", timeout=20.0)
        creds = await client.get_obo_credentials("service-key", _OBO_REQUEST)

        assert creds.header_name == "Authorization"
        assert creds.header_value.get_secret_value() == "Bearer abc.def.ghi"


class TestGetOboCredentialsFailure:
    async def test_404_raises_obo_error_with_response(self):
        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = make_response(404, {"error": "no credentials"})

        client = DialClient(http_client=mock_http_client, base_url="https://dial.example.com", timeout=20.0)
        with pytest.raises(OboCredentialsError) as exc_info:
            await client.get_obo_credentials("service-key", _OBO_REQUEST)

        assert exc_info.value.status_code == 404
        assert exc_info.value.response_body == {"error": "no credentials"}

    async def test_missing_fields_raises_obo_error(self):
        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = make_response(200, {"header_name": "Api-Key"})

        client = DialClient(http_client=mock_http_client, base_url="https://dial.example.com", timeout=20.0)
        with pytest.raises(OboCredentialsError):
            await client.get_obo_credentials("service-key", _OBO_REQUEST)

    async def test_transport_error_raises_obo_error(self):
        mock_http_client = AsyncMock()
        mock_http_client.post.side_effect = httpx.TransportError("connection refused")

        client = DialClient(http_client=mock_http_client, base_url="https://dial.example.com", timeout=20.0)
        with pytest.raises(OboCredentialsError) as exc_info:
            await client.get_obo_credentials("service-key", _OBO_REQUEST)

        assert exc_info.value.status_code is None

    async def test_non_transport_http_error_raises_obo_error(self):
        """Any httpx.HTTPError subclass (not just TransportError) is mapped to a credentials
        failure, so it is never mistakenly recorded as a target-stage error by the caller."""
        mock_http_client = AsyncMock()
        mock_http_client.post.side_effect = httpx.TooManyRedirects("too many redirects")

        client = DialClient(http_client=mock_http_client, base_url="https://dial.example.com", timeout=20.0)
        with pytest.raises(OboCredentialsError):
            await client.get_obo_credentials("service-key", _OBO_REQUEST)
