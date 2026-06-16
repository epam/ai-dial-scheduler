"""Unit tests for the Api-Key forwarding event hook (T012, T021)."""

from unittest.mock import patch

import httpx
import pytest

from src.lib.constants import API_KEY_HEADER


def _make_request(extra_headers: dict[str, str] | None = None) -> httpx.Request:
    return httpx.Request(
        "GET",
        "http://scheduler.internal/v1/schedules/",
        headers=extra_headers or {},
    )


class TestForwardApiKey:
    async def test_injects_api_key_when_present(self) -> None:
        from src.api.mcp.headers import forward_api_key

        request = _make_request()
        incoming = {API_KEY_HEADER.lower(): "test-key-abc"}

        with patch("src.api.mcp.headers.get_http_headers", return_value=incoming):
            await forward_api_key(request)

        assert request.headers[API_KEY_HEADER] == "test-key-abc"

    async def test_no_injection_when_key_absent(self) -> None:
        from src.api.mcp.headers import forward_api_key

        request = _make_request()

        with patch("src.api.mcp.headers.get_http_headers", return_value={}):
            await forward_api_key(request)

        assert API_KEY_HEADER not in request.headers

    async def test_incoming_headers_are_lowercase(self) -> None:
        """get_http_headers always returns lowercase names — verify lookup uses lowercase."""
        from src.api.mcp.headers import forward_api_key

        request = _make_request()
        incoming = {"api-key": "lowercase-key"}

        with patch("src.api.mcp.headers.get_http_headers", return_value=incoming):
            await forward_api_key(request)

        assert request.headers[API_KEY_HEADER] == "lowercase-key"

    async def test_does_not_mutate_unrelated_headers(self) -> None:
        from src.api.mcp.headers import forward_api_key

        request = _make_request(extra_headers={"X-Custom": "keep-me"})
        incoming = {"api-key": "my-key"}

        with patch("src.api.mcp.headers.get_http_headers", return_value=incoming):
            await forward_api_key(request)

        assert request.headers["X-Custom"] == "keep-me"
        assert request.headers[API_KEY_HEADER] == "my-key"

    async def test_api_key_value_not_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """The Api-Key value must never appear in log output (SC-006)."""
        from src.api.mcp.headers import forward_api_key

        secret_value = "super-secret-api-key-12345"
        request = _make_request()
        incoming = {"api-key": secret_value}

        with patch("src.api.mcp.headers.get_http_headers", return_value=incoming):
            with caplog.at_level("DEBUG"):
                await forward_api_key(request)

        for record in caplog.records:
            assert secret_value not in record.getMessage()
