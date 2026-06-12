"""Unit tests for get_user_id FastAPI dependency — success (US1) and rejection branches (US2)."""

from typing import Annotated
from unittest.mock import AsyncMock, MagicMock

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.lib.exceptions.errors import DialServiceError


def _build_test_app(mock_dial_client, path_list=None):
    from src.api.dependencies.auth import get_dial_client, get_settings_dep, get_user_id

    app = FastAPI()
    app.dependency_overrides[get_dial_client] = lambda: mock_dial_client

    if path_list is not None:
        mock_settings = MagicMock()
        mock_settings.user_id_path_list = path_list
        app.dependency_overrides[get_settings_dep] = lambda: mock_settings

    @app.get("/test")
    async def test_route(user_id: Annotated[str, Depends(get_user_id)]) -> dict:
        return {"user_id": user_id}

    return app


class TestGetUserIdSuccess:
    def test_returns_resolved_user_id(self):
        mock_client = AsyncMock()
        mock_client.get_user_info.return_value = {"userClaims": {"mail": ["alice@example.com"]}}

        app = _build_test_app(mock_client, path_list=["userClaims.mail.0", "project"])
        client = TestClient(app, raise_server_exceptions=True)

        response = client.get("/test", headers={"Api-Key": "alice-key"})

        assert response.status_code == 200
        assert response.json()["user_id"] == "alice@example.com"
        mock_client.get_user_info.assert_awaited_once_with("alice-key")


class TestGetUserIdRejections:
    def test_missing_api_key_returns_422(self):
        mock_client = AsyncMock()
        app = _build_test_app(mock_client, path_list=["userClaims.mail.0"])

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/test")

        assert response.status_code == 422
        mock_client.get_user_info.assert_not_awaited()

    def test_dial_error_returns_502(self):
        mock_client = AsyncMock()
        mock_client.get_user_info.side_effect = DialServiceError("rejected")
        app = _build_test_app(mock_client, path_list=["userClaims.mail.0"])

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/test", headers={"Api-Key": "bad-key"})

        assert response.status_code == 502

    def test_no_identity_in_response_returns_401(self):
        mock_client = AsyncMock()
        mock_client.get_user_info.return_value = {"other": "data"}
        app = _build_test_app(mock_client, path_list=["userClaims.mail.0", "project"])

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/test", headers={"Api-Key": "some-key"})

        assert response.status_code == 401

    def test_dial_unavailable_returns_502(self):
        mock_client = AsyncMock()
        mock_client.get_user_info.side_effect = DialServiceError("timeout")
        app = _build_test_app(mock_client, path_list=["userClaims.mail.0"])

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/test", headers={"Api-Key": "key"})

        assert response.status_code == 502
