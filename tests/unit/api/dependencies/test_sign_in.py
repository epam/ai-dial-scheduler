"""Unit tests for the get_sign_in_service FastAPI dependency provider."""

from unittest.mock import MagicMock

import httpx

from src.lib.clients.dial_client import DialClient
from src.lib.services.sign_in_service import SignInService


class TestGetSignInService:
    def test_returns_sign_in_service_wired_to_dial_client(self):
        from src.api.dependencies.sign_in import get_sign_in_service

        dial_client = DialClient(httpx.AsyncClient(), base_url="https://dial.example.com")

        mock_settings = MagicMock()
        mock_settings.scheduler_app_id = "public/dial_scheduler__1.0.0"

        service = get_sign_in_service(dial_client, mock_settings)

        assert isinstance(service, SignInService)
        assert service._dial_client is dial_client
        assert service._scheduler_app_id == "public/dial_scheduler__1.0.0"
