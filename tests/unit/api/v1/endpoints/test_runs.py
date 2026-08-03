"""Unit tests for the /v1/schedules/{schedule_id}/runs endpoints (mocked service)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.lib.db.enums import ScheduleRunStatus
from src.lib.exceptions.errors import ScheduleNotFoundError, ScheduleRunNotFoundError
from src.lib.schemas.pagination import Page
from src.lib.schemas.run import RunListItem, RunResponse, RunScheduleSummary


TEST_USER_ID = "alice@example.com"

SCHEDULE_ID = str(uuid.uuid4())
RUN_ID = uuid.uuid4()


def make_run_list_item(run_id: uuid.UUID = RUN_ID) -> RunListItem:
    now = datetime.now(UTC)
    return RunListItem(id=run_id, status=ScheduleRunStatus.SUCCESS, start_time=now, end_time=now)


def make_run_response(run_id: uuid.UUID = RUN_ID) -> RunResponse:
    now = datetime.now(UTC)
    return RunResponse(
        id=run_id,
        status=ScheduleRunStatus.SUCCESS,
        start_time=now,
        end_time=now,
        result={"status_code": 200},
        schedule=RunScheduleSummary(
            display_name="Test Schedule",
            trigger={"cron": {"fields": {"hour": 9}}},
            properties={"target_type": "chat_completion", "url": "https://example.com"},
        ),
    )


def empty_page() -> Page[RunListItem]:
    return Page(count=0, limit=20, offset=0, results=[], next=None, previous=None)


@pytest.fixture
def mock_service():
    return AsyncMock()


@pytest.fixture
def client(mock_service):
    from src.api.dependencies.auth import get_user_id
    from src.api.dependencies.runs import get_schedule_run_service
    from src.api.v1.endpoints.runs import router as runs_router

    app = FastAPI(redirect_slashes=False)
    app.include_router(runs_router, prefix="/v1")

    async def override_service():
        yield mock_service

    async def override_user_id():
        return TEST_USER_ID

    app.dependency_overrides[get_schedule_run_service] = override_service
    app.dependency_overrides[get_user_id] = override_user_id
    return TestClient(app)


# ---------------------------------------------------------------------------
# US1: List runs
# ---------------------------------------------------------------------------


class TestListRunsEndpoint:
    def test_list_returns_200_with_page(self, client, mock_service):
        page = Page(
            count=1,
            limit=20,
            offset=0,
            results=[make_run_list_item()],
            next=None,
            previous=None,
        )
        mock_service.list_runs.return_value = page

        response = client.get(f"/v1/schedules/{SCHEDULE_ID}/runs")

        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1
        assert len(body["results"]) == 1

    def test_list_passes_resolved_user_id_to_service(self, client, mock_service):
        mock_service.list_runs.return_value = empty_page()

        client.get(f"/v1/schedules/{SCHEDULE_ID}/runs")

        call_args = mock_service.list_runs.call_args.args
        assert call_args[1] == TEST_USER_ID

    def test_list_returns_200_with_empty_results(self, client, mock_service):
        mock_service.list_runs.return_value = empty_page()

        response = client.get(f"/v1/schedules/{SCHEDULE_ID}/runs")

        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 0
        assert body["results"] == []

    def test_list_accepts_created_at_ordering(self, client, mock_service):
        mock_service.list_runs.return_value = empty_page()

        response = client.get(f"/v1/schedules/{SCHEDULE_ID}/runs?order_by=created_at&order_dir=asc")

        assert response.status_code == 200

    def test_list_rejects_next_run_time_ordering(self, client, mock_service):
        mock_service.list_runs.return_value = empty_page()

        response = client.get(f"/v1/schedules/{SCHEDULE_ID}/runs?order_by=next_run_time")

        assert response.status_code == 422

    def test_list_rejects_name_ordering(self, client, mock_service):
        mock_service.list_runs.return_value = empty_page()

        response = client.get(f"/v1/schedules/{SCHEDULE_ID}/runs?order_by=name")

        assert response.status_code == 422

    def test_list_includes_pagination_links_when_more_pages(self, client, mock_service):
        page = Page(
            count=10,
            limit=2,
            offset=0,
            results=[make_run_list_item(), make_run_list_item(uuid.uuid4())],
            next=f"/schedules/{SCHEDULE_ID}/runs?limit=2&offset=2",
            previous=None,
        )
        mock_service.list_runs.return_value = page

        response = client.get(f"/v1/schedules/{SCHEDULE_ID}/runs?limit=2")

        assert response.status_code == 200
        body = response.json()
        assert body["next"] is not None

    def test_list_pagination_links_absent_when_single_page(self, client, mock_service):
        mock_service.list_runs.return_value = empty_page()

        response = client.get(f"/v1/schedules/{SCHEDULE_ID}/runs")

        body = response.json()
        assert body["next"] is None
        assert body["previous"] is None


# ---------------------------------------------------------------------------
# US2: Get run detail
# ---------------------------------------------------------------------------


class TestGetRunEndpoint:
    def test_get_returns_200_with_full_response(self, client, mock_service):
        mock_service.get_run.return_value = make_run_response()

        response = client.get(f"/v1/schedules/{SCHEDULE_ID}/runs/{RUN_ID}")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(RUN_ID)
        assert body["status"] == ScheduleRunStatus.SUCCESS.value

    def test_get_passes_resolved_user_id_to_service(self, client, mock_service):
        mock_service.get_run.return_value = make_run_response()

        client.get(f"/v1/schedules/{SCHEDULE_ID}/runs/{RUN_ID}")

        call_args = mock_service.get_run.call_args.args
        assert call_args[1] == TEST_USER_ID

    def test_get_returns_404_when_run_not_found(self, client, mock_service):
        mock_service.get_run.side_effect = ScheduleRunNotFoundError(str(RUN_ID))

        response = client.get(f"/v1/schedules/{SCHEDULE_ID}/runs/{RUN_ID}")

        assert response.status_code == 404

    def test_get_includes_embedded_schedule_fields(self, client, mock_service):
        mock_service.get_run.return_value = make_run_response()

        response = client.get(f"/v1/schedules/{SCHEDULE_ID}/runs/{RUN_ID}")

        body = response.json()
        assert "schedule" in body
        assert body["schedule"]["display_name"] == "Test Schedule"
        assert "trigger" in body["schedule"]
        assert "properties" in body["schedule"]

    def test_get_malformed_uuid_returns_422(self, client, mock_service):
        response = client.get(f"/v1/schedules/{SCHEDULE_ID}/runs/not-a-uuid")

        assert response.status_code == 422
        mock_service.get_run.assert_not_awaited()


# ---------------------------------------------------------------------------
# US3: Ownership enforcement
# ---------------------------------------------------------------------------


class TestOwnershipEnforcement:
    def test_list_returns_404_when_schedule_not_found(self, client, mock_service):
        mock_service.list_runs.side_effect = ScheduleNotFoundError(SCHEDULE_ID)

        response = client.get(f"/v1/schedules/{SCHEDULE_ID}/runs")

        assert response.status_code == 404

    def test_detail_returns_404_when_schedule_not_found(self, client, mock_service):
        mock_service.get_run.side_effect = ScheduleNotFoundError(SCHEDULE_ID)

        response = client.get(f"/v1/schedules/{SCHEDULE_ID}/runs/{RUN_ID}")

        assert response.status_code == 404
