from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from src.api.app import create_api
from src.lib.schemas.sign_in import SignInStatus


TEST_USER_ID = "alice@example.com"


@pytest.fixture
def mock_sign_in_service():
    return AsyncMock()


@pytest.fixture
def client(mock_sign_in_service):
    app = create_api()

    from src.api.dependencies.auth import get_user_id
    from src.api.dependencies.sign_in import get_sign_in_service

    async def override_user_id():
        return TEST_USER_ID

    app.dependency_overrides[get_user_id] = override_user_id
    app.dependency_overrides[get_sign_in_service] = lambda: mock_sign_in_service
    return TestClient(app, headers={"Api-Key": "test-key"})


class TestSignInStatusEndpoint:
    def test_signed_in_returns_200(self, client, mock_sign_in_service):
        mock_sign_in_service.check.return_value = SignInStatus(
            service_id="dial-native", state="signed_in", detail="Signed in."
        )

        response = client.get("/v1/sign-in/status", params={"service_id": "dial-native"})

        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "signed_in"
        assert body["signed_in"] is True
        assert body["service_id"] == "dial-native"

    def test_not_signed_in_returns_200_with_state(self, client, mock_sign_in_service):
        mock_sign_in_service.check.return_value = SignInStatus(
            service_id="dial-native", state="not_signed_in", detail="Please sign in."
        )

        response = client.get("/v1/sign-in/status", params={"service_id": "dial-native"})

        assert response.status_code == 200
        assert response.json()["state"] == "not_signed_in"
        assert response.json()["signed_in"] is False

    def test_unknown_returns_200_with_state(self, client, mock_sign_in_service):
        mock_sign_in_service.check.return_value = SignInStatus(
            service_id="dial-native", state="unknown", detail="DIAL is unreachable."
        )

        response = client.get("/v1/sign-in/status", params={"service_id": "dial-native"})

        assert response.status_code == 200
        assert response.json()["state"] == "unknown"

    def test_missing_service_id_returns_422(self, client, mock_sign_in_service):
        response = client.get("/v1/sign-in/status")

        assert response.status_code == 422
        mock_sign_in_service.check.assert_not_awaited()

    def test_whitespace_only_service_id_returns_422(self, client, mock_sign_in_service):
        response = client.get("/v1/sign-in/status", params={"service_id": "   "})

        assert response.status_code == 422
        mock_sign_in_service.check.assert_not_awaited()

    def test_calls_service_with_user_and_service_id(self, client, mock_sign_in_service):
        mock_sign_in_service.check.return_value = SignInStatus(service_id="dial-native", state="signed_in", detail="ok")

        client.get("/v1/sign-in/status", params={"service_id": "dial-native"})

        mock_sign_in_service.check.assert_awaited_once_with(TEST_USER_ID, "dial-native", "test-key", None)
