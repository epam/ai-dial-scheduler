"""Unit tests for the GET /ready readiness endpoint."""

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import OperationalError


@pytest.fixture()
def client():
    from src.api.v1.endpoints.health import router as health_router

    app = FastAPI()
    app.include_router(health_router)
    return TestClient(app, raise_server_exceptions=False)


class TestReadinessEndpointReady:
    def test_returns_200_when_db_reachable(self, client, mocker):
        mock_session = mocker.AsyncMock()
        mock_session.execute = mocker.AsyncMock(return_value=mocker.MagicMock())

        mock_ctx = mocker.AsyncMock()
        mock_ctx.__aenter__ = mocker.AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = mocker.AsyncMock(return_value=False)

        mock_sessionmaker = mocker.MagicMock(return_value=mock_ctx)
        mocker.patch("src.api.v1.endpoints.health.Database").return_value.sessionmaker = mock_sessionmaker

        response = client.get("/ready")

        assert response.status_code == 200

    def test_response_body_ready_when_db_reachable(self, client, mocker):
        mock_session = mocker.AsyncMock()
        mock_session.execute = mocker.AsyncMock(return_value=mocker.MagicMock())

        mock_ctx = mocker.AsyncMock()
        mock_ctx.__aenter__ = mocker.AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = mocker.AsyncMock(return_value=False)

        mock_sessionmaker = mocker.MagicMock(return_value=mock_ctx)
        mocker.patch("src.api.v1.endpoints.health.Database").return_value.sessionmaker = mock_sessionmaker

        response = client.get("/ready")

        body = response.json()
        assert body["status"] == "ready"
        assert body["database"] == "ok"


class TestReadinessEndpointNotReady:
    def test_returns_503_when_db_raises_operational_error(self, client, mocker):
        mock_ctx = mocker.AsyncMock()
        mock_ctx.__aenter__ = mocker.AsyncMock(side_effect=OperationalError("connection refused", None, None))
        mock_ctx.__aexit__ = mocker.AsyncMock(return_value=False)

        mock_sessionmaker = mocker.MagicMock(return_value=mock_ctx)
        mocker.patch("src.api.v1.endpoints.health.Database").return_value.sessionmaker = mock_sessionmaker

        response = client.get("/ready")

        assert response.status_code == 503

    def test_response_body_not_ready_when_db_fails(self, client, mocker):
        mock_ctx = mocker.AsyncMock()
        mock_ctx.__aenter__ = mocker.AsyncMock(side_effect=Exception("connection error"))
        mock_ctx.__aexit__ = mocker.AsyncMock(return_value=False)

        mock_sessionmaker = mocker.MagicMock(return_value=mock_ctx)
        mocker.patch("src.api.v1.endpoints.health.Database").return_value.sessionmaker = mock_sessionmaker

        response = client.get("/ready")

        body = response.json()
        assert body["status"] == "not_ready"
        assert body["database"] == "unavailable"

    def test_returns_503_on_timeout(self, client, mocker):
        async def slow_execute(*_args, **_kwargs):
            await asyncio.sleep(10)

        mock_session = mocker.AsyncMock()
        mock_session.execute.side_effect = slow_execute

        mock_ctx = mocker.AsyncMock()
        mock_ctx.__aenter__ = mocker.AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = mocker.AsyncMock(return_value=False)

        mock_sessionmaker = mocker.MagicMock(return_value=mock_ctx)
        mocker.patch("src.api.v1.endpoints.health.Database").return_value.sessionmaker = mock_sessionmaker
        mocker.patch("src.api.v1.endpoints.health.get_settings").return_value.readiness_db_timeout_seconds = 0.01

        response = client.get("/ready")

        assert response.status_code == 503

    def test_service_does_not_crash_when_db_unavailable(self, client, mocker):
        mock_ctx = mocker.AsyncMock()
        mock_ctx.__aenter__ = mocker.AsyncMock(side_effect=Exception("DB gone"))
        mock_ctx.__aexit__ = mocker.AsyncMock(return_value=False)

        mock_sessionmaker = mocker.MagicMock(return_value=mock_ctx)
        mocker.patch("src.api.v1.endpoints.health.Database").return_value.sessionmaker = mock_sessionmaker

        response = client.get("/ready")

        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"
