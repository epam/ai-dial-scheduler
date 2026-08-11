from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from src.api.app import create_api
from src.lib.exceptions.errors import (
    JobRegistrationError,
    JobRemovalError,
    ScheduleAlreadyDeletedError,
    ScheduleNotFoundError,
)
from src.lib.schemas.ordering import ScheduleOrdering
from src.lib.schemas.pagination import Page, Paginator
from src.lib.schemas.schedule import ScheduleListItem, ScheduleResponse


TEST_USER_ID = "alice@example.com"

VALID_REQUEST = {
    "display_name": "Test Schedule",
    "service_id": "dial-oauth",
    "trigger": {
        "cron": {
            "fields": {"hour": 9, "minute": 0},
        }
    },
    "properties": {
        "target_type": "chat_completion",
        "url": "https://core.dial/openai/",
        "payload": {"model": "gpt-4o"},
        "api_version": "2025-01-01-preview",
    },
}


def make_response(
    schedule_id: str = "test-uuid",
    metadata: dict | None = None,
    service_id: str = "dial-oauth",
    is_deleted: bool = False,
) -> ScheduleResponse:
    now = datetime.now(UTC)
    return ScheduleResponse(
        id=schedule_id,
        display_name="Test Schedule",
        service_id=service_id,
        next_run_time=None if is_deleted else now,
        trigger={"cron": {"fields": {"hour": 9}}},
        properties={"target_type": "chat_completion", "url": "https://example.com"},
        metadata=metadata if metadata is not None else {},
        is_deleted=is_deleted,
        created_by=TEST_USER_ID,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def mock_service():
    return AsyncMock()


@pytest.fixture
def client(mock_service):
    app = create_api()

    from src.api.dependencies.auth import get_user_id
    from src.api.dependencies.scheduler import get_schedule_service

    async def override_service():
        yield mock_service

    async def override_user_id():
        return TEST_USER_ID

    app.dependency_overrides[get_schedule_service] = override_service
    app.dependency_overrides[get_user_id] = override_user_id
    return TestClient(app)


class TestPostSchedule:
    def test_create_returns_201(self, client, mock_service):
        mock_service.create.return_value = make_response()

        response = client.post("/v1/schedules/", json=VALID_REQUEST)

        assert response.status_code == 201
        body = response.json()
        assert "id" in body
        assert body["display_name"] == "Test Schedule"
        assert body["service_id"] == "dial-oauth"
        assert body["created_by"] == TEST_USER_ID

    def test_create_missing_service_id_returns_422(self, client, mock_service):
        bad_request = {k: v for k, v in VALID_REQUEST.items() if k != "service_id"}
        response = client.post("/v1/schedules/", json=bad_request)
        assert response.status_code == 422
        mock_service.create.assert_not_awaited()

    def test_create_passes_resolved_user_id_to_service(self, client, mock_service):
        mock_service.create.return_value = make_response()

        client.post("/v1/schedules/", json=VALID_REQUEST)

        mock_service.create.assert_awaited_once()
        _, user_id_arg = mock_service.create.call_args.args
        assert user_id_arg == TEST_USER_ID

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

    def test_update_missing_service_id_returns_422(self, client, mock_service):
        bad_request = {k: v for k, v in VALID_REQUEST.items() if k != "service_id"}
        response = client.put("/v1/schedules/test-uuid", json=bad_request)
        assert response.status_code == 422
        mock_service.update.assert_not_awaited()

    def test_update_passes_resolved_user_id_to_service(self, client, mock_service):
        mock_service.update.return_value = make_response()

        client.put("/v1/schedules/test-uuid", json=VALID_REQUEST)

        mock_service.update.assert_awaited_once()
        _, _, user_id_arg = mock_service.update.call_args.args
        assert user_id_arg == TEST_USER_ID

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

    def test_list_passes_resolved_user_id_to_service(self, client, mock_service):
        mock_service.list.return_value = empty_page()

        client.get("/v1/schedules/")

        mock_service.list.assert_awaited_once_with(
            TEST_USER_ID,
            Paginator(limit=20, offset=0, base_path="/schedules"),
            {},
            name_filter=None,
            ordering=ScheduleOrdering(),
            is_deleted=False,
        )

    def test_list_passes_metadata_filter_to_service(self, client, mock_service):
        mock_service.list.return_value = empty_page()

        client.get("/v1/schedules/?metadata__tenant=hcscd&metadata__env=prod")

        mock_service.list.assert_awaited_once()
        _, _, metadata_filter = mock_service.list.call_args.args
        assert metadata_filter == {"tenant": "hcscd", "env": "prod"}


class TestGetScheduleDetail:
    def test_get_returns_200(self, client, mock_service):
        mock_service.get.return_value = make_response("test-uuid", service_id="dial-api-key")
        response = client.get("/v1/schedules/test-uuid")
        assert response.status_code == 200
        assert response.json()["id"] == "test-uuid"
        assert response.json()["service_id"] == "dial-api-key"

    def test_get_passes_resolved_user_id_to_service(self, client, mock_service):
        mock_service.get.return_value = make_response("test-uuid")

        client.get("/v1/schedules/test-uuid")

        mock_service.get.assert_awaited_once_with("test-uuid", TEST_USER_ID)

    def test_get_returns_metadata_in_body(self, client, mock_service):
        mock_service.get.return_value = make_response("test-uuid", metadata={"tenant": "hcscd"})
        response = client.get("/v1/schedules/test-uuid")
        assert response.status_code == 200
        assert response.json()["metadata"] == {"tenant": "hcscd"}

    def test_get_unknown_id_returns_404(self, client, mock_service):
        mock_service.get.side_effect = ScheduleNotFoundError("nonexistent")
        response = client.get("/v1/schedules/nonexistent")
        assert response.status_code == 404

    def test_get_soft_deleted_returns_200_with_flag_and_no_next_run(self, client, mock_service):
        mock_service.get.return_value = make_response("test-uuid", is_deleted=True)

        response = client.get("/v1/schedules/test-uuid")

        assert response.status_code == 200
        assert response.json()["is_deleted"] is True
        assert response.json()["next_run_time"] is None

    def test_get_live_schedule_reports_not_deleted(self, client, mock_service):
        mock_service.get.return_value = make_response("test-uuid")

        response = client.get("/v1/schedules/test-uuid")

        assert response.json()["is_deleted"] is False


class TestDeleteSchedule:
    def test_delete_returns_204_with_empty_body(self, client, mock_service):
        mock_service.delete.return_value = None

        response = client.delete("/v1/schedules/test-uuid")

        assert response.status_code == 204
        assert response.content == b""

    def test_delete_passes_resolved_user_id_to_service(self, client, mock_service):
        mock_service.delete.return_value = None

        client.delete("/v1/schedules/test-uuid")

        mock_service.delete.assert_awaited_once_with("test-uuid", TEST_USER_ID)

    def test_delete_unknown_id_returns_404(self, client, mock_service):
        mock_service.delete.side_effect = ScheduleNotFoundError("nonexistent")

        response = client.delete("/v1/schedules/nonexistent")

        assert response.status_code == 404
        assert "not found" in response.json().lower()

    def test_delete_already_soft_deleted_returns_409(self, client, mock_service):
        mock_service.delete.side_effect = ScheduleAlreadyDeletedError("test-uuid")

        response = client.delete("/v1/schedules/test-uuid")

        assert response.status_code == 409
        assert "already deleted" in response.json().lower()

    def test_delete_job_removal_failure_returns_502(self, client, mock_service):
        mock_service.delete.side_effect = JobRemovalError("job store unreachable")

        response = client.delete("/v1/schedules/test-uuid")

        assert response.status_code == 502

    def test_delete_502_body_does_not_leak_scheduler_internals(self, client, mock_service):
        mock_service.delete.side_effect = JobRemovalError(
            "(psycopg2.OperationalError) could not connect to server at 10.0.0.5:5432"
        )

        response = client.delete("/v1/schedules/test-uuid")

        body = response.json()
        assert "psycopg2" not in body
        assert "10.0.0.5" not in body
        assert "retry" in body.lower()


class TestSoftDeletedScheduleIsFrozen:
    def test_put_returns_409(self, client, mock_service):
        mock_service.update.side_effect = ScheduleAlreadyDeletedError("test-uuid")

        response = client.put("/v1/schedules/test-uuid", json=VALID_REQUEST)

        assert response.status_code == 409

    def test_pause_returns_409(self, client, mock_service):
        mock_service.pause.side_effect = ScheduleAlreadyDeletedError("test-uuid")

        response = client.post("/v1/schedules/test-uuid/pause")

        assert response.status_code == 409

    def test_resume_returns_409(self, client, mock_service):
        mock_service.resume.side_effect = ScheduleAlreadyDeletedError("test-uuid")

        response = client.post("/v1/schedules/test-uuid/resume")

        assert response.status_code == 409


class TestIsDeletedIsNotWritable:
    def test_post_ignores_is_deleted_in_body(self, client, mock_service):
        """FR-009: the field is ignored, not rejected — no 422, and it never reaches the service."""
        mock_service.create.return_value = make_response("test-uuid")

        response = client.post("/v1/schedules/", json={**VALID_REQUEST, "is_deleted": True})

        assert response.status_code == 201
        assert response.json()["is_deleted"] is False
        submitted = mock_service.create.await_args.args[0]
        assert not hasattr(submitted, "is_deleted")

    def test_put_ignores_is_deleted_in_body(self, client, mock_service):
        mock_service.update.return_value = make_response("test-uuid")

        response = client.put("/v1/schedules/test-uuid", json={**VALID_REQUEST, "is_deleted": True})

        assert response.status_code == 200
        assert response.json()["is_deleted"] is False
        submitted = mock_service.update.await_args.args[1]
        assert not hasattr(submitted, "is_deleted")


class TestListDeletedFilter:
    def test_list_defaults_to_live_schedules(self, client, mock_service):
        mock_service.list.return_value = empty_page()

        client.get("/v1/schedules/")

        assert mock_service.list.await_args.kwargs["is_deleted"] is False

    def test_list_accepts_is_deleted_true(self, client, mock_service):
        mock_service.list.return_value = empty_page()

        response = client.get("/v1/schedules/?is_deleted=true")

        assert response.status_code == 200
        assert mock_service.list.await_args.kwargs["is_deleted"] is True

    def test_list_accepts_is_deleted_false(self, client, mock_service):
        mock_service.list.return_value = empty_page()

        client.get("/v1/schedules/?is_deleted=false")

        assert mock_service.list.await_args.kwargs["is_deleted"] is False

    def test_list_rejects_non_boolean_is_deleted_with_422(self, client, mock_service):
        mock_service.list.return_value = empty_page()

        response = client.get("/v1/schedules/?is_deleted=maybe")

        assert response.status_code == 422

    def test_list_items_include_is_deleted(self, client, mock_service):
        now = datetime.now(UTC)
        item = ScheduleListItem(
            id="test-uuid",
            display_name="Test Schedule",
            service_id="dial-oauth",
            next_run_time=None,
            trigger_type="cron",
            trigger={"cron": {"fields": {"hour": 9}}},
            is_deleted=True,
            created_by=TEST_USER_ID,
            created_at=now,
            updated_at=now,
        )
        mock_service.list.return_value = Page(count=1, limit=20, offset=0, results=[item], next=None, previous=None)

        response = client.get("/v1/schedules/?is_deleted=true")

        assert response.json()["results"][0]["is_deleted"] is True
