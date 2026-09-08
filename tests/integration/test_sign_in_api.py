"""Integration test for GET /v1/sign-in/status.

No database is involved — sign-in status is computed live against DIAL and nothing is
persisted — so, unlike the DB-backed integration suites in this package, this exercises only
the HTTP -> dependency -> endpoint stack with `SignInService` itself replaced by a stub; the
service's own DIAL-response-to-state mapping is covered separately by
tests/unit/lib/services/test_sign_in_service.py.
"""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

from fastapi import FastAPI
import httpx
import pytest

from src.api.dependencies.auth import get_user_id
from src.api.dependencies.sign_in import get_sign_in_service
from src.api.v1.endpoints.sign_in import router as sign_in_router
from src.lib.schemas.sign_in import SignInStatus


INTEGRATION_USER_ID = "mock_user_id"


@pytest.fixture
def mock_sign_in_service() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def test_app(mock_sign_in_service: AsyncMock) -> FastAPI:
    # A plain FastAPI() app wraps HTTPException.detail as {"detail": ...}; DIALApp (the real
    # app factory, create_api()) does not — see src/api/v1/endpoints/schedules.py's
    # _require_sign_in. This suite only asserts 200 bodies, so that divergence doesn't matter
    # here, but don't add a 4xx body-shape assertion against this fixture without accounting
    # for it.
    app = FastAPI()
    app.include_router(sign_in_router, prefix="/v1")

    async def override_user_id() -> str:
        return INTEGRATION_USER_ID

    app.dependency_overrides[get_user_id] = override_user_id
    app.dependency_overrides[get_sign_in_service] = lambda: mock_sign_in_service
    return app


@pytest.fixture
async def client(test_app: FastAPI) -> AsyncGenerator[httpx.AsyncClient, None]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test", headers={"Api-Key": "test-key"}
    ) as c:
        yield c


class TestSignInStatusIntegration:
    async def test_signed_in(self, client: httpx.AsyncClient, mock_sign_in_service: AsyncMock) -> None:
        mock_sign_in_service.check.return_value = SignInStatus(
            service_id="dial-native", state="signed_in", detail="Signed in."
        )

        response = await client.get("/v1/sign-in/status", params={"service_id": "dial-native"})

        assert response.status_code == 200
        body = response.json()
        assert body == {
            "service_id": "dial-native",
            "state": "signed_in",
            "detail": "Signed in.",
            "signed_in": True,
        }

    async def test_not_signed_in(self, client: httpx.AsyncClient, mock_sign_in_service: AsyncMock) -> None:
        mock_sign_in_service.check.return_value = SignInStatus(
            service_id="dial-native", state="not_signed_in", detail="Please sign in to dial-native."
        )

        response = await client.get("/v1/sign-in/status", params={"service_id": "dial-native"})

        assert response.status_code == 200
        assert response.json()["state"] == "not_signed_in"
        assert response.json()["signed_in"] is False

    async def test_unknown_on_dial_outage(self, client: httpx.AsyncClient, mock_sign_in_service: AsyncMock) -> None:
        mock_sign_in_service.check.return_value = SignInStatus(
            service_id="dial-native", state="unknown", detail="DIAL is unreachable."
        )

        response = await client.get("/v1/sign-in/status", params={"service_id": "dial-native"})

        assert response.status_code == 200
        assert response.json()["state"] == "unknown"
        assert response.json()["signed_in"] is False

    async def test_missing_service_id_returns_422(
        self, client: httpx.AsyncClient, mock_sign_in_service: AsyncMock
    ) -> None:
        response = await client.get("/v1/sign-in/status")

        assert response.status_code == 422
        mock_sign_in_service.check.assert_not_awaited()
