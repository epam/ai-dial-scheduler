from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from src.api.app import create_api
from src.lib.exceptions.errors import JobRegistrationError, ScheduleNotFoundError
from src.lib.schemas.pagination import Page, Paginator
from src.lib.schemas.schedule import ScheduleListItem, ScheduleResponse


VALID_REQUEST = {
    "display_name": "Test Schedule",
    "trigger": {
        "cron": {
            "fields": {"hour": 9, "minute": 0},
        }
    },
    "properties": {
        "target_type": "chat_completion",
        "url": "https://core.dial/openai/deployments/gpt-4o/chat/completions",
    },
}


def make_response(schedule_id: str = "test-uuid") -> ScheduleResponse:
    now = datetime.now(UTC)
    return ScheduleResponse(
        id=schedule_id,
        display_name="Test Schedule",
        next_run_time=now,
        trigger={"cron": {"fields": {"hour": 9}}},
        properties={"target_type": "chat_completion", "url": "https://example.com"},
        created_by="mock_user_id",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def mock_service():
    return AsyncMock()


@pytest.fixture
def client(mock_service):
    app = create_api()

    from src.api.dependencies.scheduler import get_schedule_service

    async def override_service():
        yield mock_service

    app.dependency_overrides[get_schedule_service] = override_service
    return TestClient(app)


class TestPostSchedule:
    def test_create_returns_201(self, client, mock_service):
        mock_service.create.return_value = make_response()

        response = client.post("/v1/schedules/", json=VALID_REQUEST)

        assert response.status_code == 201
        body = response.json()
        assert "id" in body
        assert body["display_name"] == "Test Schedule"
        assert body["created_by"] == "mock_user_id"

    def test_create_invalid_trigger_returns_422(self, client, mock_service):
        bad_request = {**VALID_REQUEST, "trigger": {}}  # no cron or date
        response = client.post("/v1/schedules/", json=bad_request)
        assert response.status_code == 422
        mock_service.create.assert_not_awaited()

    def test_create_invalid_properties_returns_422(self, client, mock_service):
        bad_request = {
            **VALID_REQUEST,
            "properties": {
                "target_type": "rest",
                "url": "https://example.com",
                # missing method for rest
            },
        }
        response = client.post("/v1/schedules/", json=bad_request)
        assert response.status_code == 422
        mock_service.create.assert_not_awaited()

    def test_create_registration_failure_returns_502(self, client, mock_service):
        mock_service.create.side_effect = JobRegistrationError("scheduler error")
        response = client.post("/v1/schedules/", json=VALID_REQUEST)
        assert response.status_code == 502


class TestPutSchedule:
    def test_update_returns_200(self, client, mock_service):
        mock_service.update.return_value = make_response()

        response = client.put("/v1/schedules/test-uuid", json=VALID_REQUEST)

        assert response.status_code == 200
        assert response.json()["id"] == "test-uuid"

    def test_update_unknown_id_returns_404(self, client, mock_service):
        mock_service.update.side_effect = ScheduleNotFoundError("nonexistent")
        response = client.put("/v1/schedules/nonexistent", json=VALID_REQUEST)
        assert response.status_code == 404

    def test_update_invalid_body_returns_422(self, client, mock_service):
        response = client.put("/v1/schedules/test-uuid", json={"display_name": "x"})
        assert response.status_code == 422
        mock_service.update.assert_not_awaited()

    def test_update_registration_failure_returns_502(self, client, mock_service):
        mock_service.update.side_effect = JobRegistrationError("scheduler error")
        response = client.put("/v1/schedules/test-uuid", json=VALID_REQUEST)
        assert response.status_code == 502


def empty_page() -> Page[ScheduleListItem]:
    return Page(count=0, limit=20, offset=0, results=[], next=None, previous=None)


class TestGetSchedules:
    def test_list_returns_200(self, client, mock_service):
        mock_service.list.return_value = empty_page()

        response = client.get("/v1/schedules/")

        assert response.status_code == 200
        body = response.json()
        assert "count" in body
        assert body["limit"] == 20
        assert body["offset"] == 0

    def test_list_passes_paginator_with_defaults(self, client, mock_service):
        mock_service.list.return_value = empty_page()

        client.get("/v1/schedules/")

        mock_service.list.assert_awaited_once_with(
            "mock_user_id", Paginator(limit=20, offset=0, base_path="/schedules")
        )


class TestGetScheduleDetail:
    def test_get_returns_200(self, client, mock_service):
        mock_service.get.return_value = make_response("test-uuid")
        response = client.get("/v1/schedules/test-uuid")
        assert response.status_code == 200
        assert response.json()["id"] == "test-uuid"

    def test_get_unknown_id_returns_404(self, client, mock_service):
        mock_service.get.side_effect = ScheduleNotFoundError("nonexistent")
        response = client.get("/v1/schedules/nonexistent")
        assert response.status_code == 404
