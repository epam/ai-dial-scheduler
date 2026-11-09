from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
import uuid

from fastapi.testclient import TestClient
import pytest

from src.api.app import create_api
from src.lib.db.enums import ScheduleRunStatus
from src.lib.exceptions.errors import (
    JobRegistrationError,
    JobRemovalError,
    ScheduleAlreadyDeletedError,
    ScheduleNotFoundError,
    SignInRequiredError,
)
from src.lib.schemas.ordering import ScheduleOrdering
from src.lib.schemas.pagination import Page, Paginator
from src.lib.schemas.schedule import ScheduleListItem, ScheduleResponse
from src.lib.schemas.sign_in import SignInStatus


TEST_USER_ID = "alice@example.com"

VALID_REQUEST = {
    "display_name": "Test Schedule",
    "service_id": "dial-native",
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
    service_id: str = "dial-native",
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
def mock_sign_in_service():
    """Defaults to reporting the caller as signed in, so existing tests are unaffected."""
    service = AsyncMock()
    service.require.return_value = None
    return service


@pytest.fixture
def mock_run_service():
    return AsyncMock()


@pytest.fixture
def mock_launcher():
    return MagicMock()


@pytest.fixture
def client(mock_service, mock_sign_in_service, mock_run_service, mock_launcher):
    app = create_api()

    from src.api.dependencies.auth import get_user_id
    from src.api.dependencies.runs import get_run_launcher, get_schedule_run_service
    from src.api.dependencies.scheduler import get_schedule_service
    from src.api.dependencies.sign_in import get_sign_in_service

    async def override_service():
        yield mock_service

    async def override_user_id():
        return TEST_USER_ID

    app.dependency_overrides[get_schedule_service] = override_service
    app.dependency_overrides[get_user_id] = override_user_id
    app.dependency_overrides[get_sign_in_service] = lambda: mock_sign_in_service
    app.dependency_overrides[get_schedule_run_service] = lambda: mock_run_service
    app.dependency_overrides[get_run_launcher] = lambda: mock_launcher
    return TestClient(app, headers={"Api-Key": "test-key"})


def make_sign_in_required(service_id: str = "dial-native", state: str = "not_signed_in") -> SignInRequiredError:
    return SignInRequiredError(
        SignInStatus(
            service_id=service_id,
            state=state,
            detail=(
                f"You are not signed in to {service_id}. Open the DIAL Scheduler application in "
                "DIAL Chat and sign in to it, then retry this request."
                if state == "not_signed_in"
                else f"Could not determine sign-in status for {service_id}: DIAL is unreachable."
            ),
        )
    )


class TestPostSchedule:
    def test_create_returns_201(self, client, mock_service):
        mock_service.create.return_value = make_response()

        response = client.post("/v1/schedules/", json=VALID_REQUEST)

        assert response.status_code == 201
        body = response.json()
        assert "id" in body
        assert body["display_name"] == "Test Schedule"
        assert body["service_id"] == "dial-native"
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


class TestPostScheduleSignIn:
    """FR-001/003/004/008 — sign-in is a precondition of creating a schedule."""

    def test_create_without_sign_in_returns_403(self, client, mock_service, mock_sign_in_service):
        mock_sign_in_service.require.side_effect = make_sign_in_required()

        response = client.post("/v1/schedules/", json=VALID_REQUEST)

        assert response.status_code == 403
        mock_service.create.assert_not_awaited()

    def test_403_detail_names_the_service_and_the_required_action(self, client, mock_sign_in_service):
        mock_sign_in_service.require.side_effect = make_sign_in_required()

        response = client.post("/v1/schedules/", json=VALID_REQUEST)

        detail = response.json()
        assert detail["service_id"] == "dial-native"
        assert detail["state"] == "not_signed_in"
        assert "sign in" in detail["detail"].lower()

    def test_create_checks_body_service_id(self, client, mock_sign_in_service, mock_service):
        mock_service.create.return_value = make_response(service_id="dial-api-key")

        client.post("/v1/schedules/", json={**VALID_REQUEST, "service_id": "dial-api-key"})

        mock_sign_in_service.require.assert_awaited_once_with(TEST_USER_ID, "dial-api-key", "test-key", None)

    def test_dial_unavailable_returns_502(self, client, mock_service, mock_sign_in_service):
        """An outage must not be reported as a missing sign-in."""
        mock_sign_in_service.require.side_effect = make_sign_in_required(state="unknown")

        response = client.post("/v1/schedules/", json=VALID_REQUEST)

        assert response.status_code == 502
        assert response.json()["state"] == "unknown"
        mock_service.create.assert_not_awaited()

    def test_invalid_body_is_rejected_before_the_sign_in_check(self, client, mock_sign_in_service):
        bad_request = {k: v for k, v in VALID_REQUEST.items() if k != "service_id"}

        response = client.post("/v1/schedules/", json=bad_request)

        assert response.status_code == 422
        mock_sign_in_service.require.assert_not_awaited()


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


class TestPutScheduleSignIn:
    """FR-001/003/004/008 — sign-in is a precondition of updating a schedule."""

    def test_update_without_sign_in_returns_403(self, client, mock_service, mock_sign_in_service):
        mock_sign_in_service.require.side_effect = make_sign_in_required()

        response = client.put("/v1/schedules/test-uuid", json=VALID_REQUEST)

        assert response.status_code == 403
        mock_service.update.assert_not_awaited()

    def test_update_checks_body_service_id(self, client, mock_service, mock_sign_in_service):
        mock_service.update.return_value = make_response(service_id="dial-api-key")

        client.put("/v1/schedules/test-uuid", json={**VALID_REQUEST, "service_id": "dial-api-key"})

        mock_sign_in_service.require.assert_awaited_once_with(TEST_USER_ID, "dial-api-key", "test-key", None)

    def test_dial_unavailable_returns_502(self, client, mock_service, mock_sign_in_service):
        mock_sign_in_service.require.side_effect = make_sign_in_required(state="unknown")

        response = client.put("/v1/schedules/test-uuid", json=VALID_REQUEST)

        assert response.status_code == 502
        mock_service.update.assert_not_awaited()


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
            model_filter=None,
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
            service_id="dial-native",
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


def make_run_row(run_id: uuid.UUID | None = None, snapshot: dict | None = None):
    """An ``in_progress`` run row as create_on_demand_run returns it."""
    run = MagicMock()
    run.id = run_id or uuid.uuid4()
    run.status = ScheduleRunStatus.IN_PROGRESS
    run.created_at = datetime.now(UTC)
    run.end_time = None
    run.conversation_id = None
    run.schedule_snapshot = snapshot if snapshot is not None else {"target_type": "rest", "url": "https://e.com"}
    return run


class TestRunScheduleNow:
    def test_returns_202_with_an_in_progress_run(self, client, mock_run_service):
        run = make_run_row()
        mock_run_service.create_on_demand_run.return_value = run

        response = client.post("/v1/schedules/sched-1/run")

        assert response.status_code == 202
        body = response.json()
        assert body["id"] == str(run.id)
        assert body["status"] == "in_progress"
        assert body["end_time"] is None

    def test_hands_the_saved_snapshot_to_the_launcher(self, client, mock_run_service, mock_launcher):
        snapshot = {"target_type": "chat_completion", "url": "https://core.dial/openai/"}
        run = make_run_row(snapshot=snapshot)
        mock_run_service.create_on_demand_run.return_value = run

        client.post("/v1/schedules/sched-1/run")

        mock_launcher.launch.assert_called_once_with(str(run.id), "sched-1", snapshot)

    def test_passes_the_resolved_user_id_to_the_service(self, client, mock_run_service):
        mock_run_service.create_on_demand_run.return_value = make_run_row()

        client.post("/v1/schedules/sched-1/run")

        mock_run_service.create_on_demand_run.assert_awaited_once_with("sched-1", TEST_USER_ID)

    def test_accepts_no_request_body(self, client, mock_run_service):
        """Only the saved payload runs, so a body must not be able to override anything."""
        mock_run_service.create_on_demand_run.return_value = make_run_row()

        response = client.post("/v1/schedules/sched-1/run", json={"properties": {"url": "https://evil.example"}})

        assert response.status_code == 202
        mock_run_service.create_on_demand_run.assert_awaited_once_with("sched-1", TEST_USER_ID)

    def test_does_not_touch_the_scheduler(self, client, mock_run_service, mock_service):
        """An on-demand run must not move the schedule's next_run_time."""
        mock_run_service.create_on_demand_run.return_value = make_run_row()

        client.post("/v1/schedules/sched-1/run")

        mock_service.update.assert_not_awaited()
        mock_service.pause.assert_not_awaited()
        mock_service.resume.assert_not_awaited()

    def test_unknown_schedule_returns_404(self, client, mock_run_service, mock_launcher):
        mock_run_service.create_on_demand_run.side_effect = ScheduleNotFoundError("sched-1")

        response = client.post("/v1/schedules/sched-1/run")

        assert response.status_code == 404
        mock_launcher.launch.assert_not_called()

    def test_soft_deleted_schedule_returns_409(self, client, mock_run_service, mock_launcher):
        mock_run_service.create_on_demand_run.side_effect = ScheduleAlreadyDeletedError("sched-1")

        response = client.post("/v1/schedules/sched-1/run")

        assert response.status_code == 409
        mock_launcher.launch.assert_not_called()

    def test_run_is_launched_after_the_record_exists(self, client, mock_run_service, mock_launcher):
        """The run row must exist before execution starts, so the id is always resolvable."""
        run = make_run_row()
        mock_run_service.create_on_demand_run.return_value = run
        order = []
        mock_run_service.create_on_demand_run.side_effect = lambda *_: order.append("create") or run
        mock_launcher.launch.side_effect = lambda *_: order.append("launch")

        client.post("/v1/schedules/sched-1/run")

        assert order == ["create", "launch"]


class TestListModelFilter:
    def test_model_query_param_is_forwarded(self, client, mock_service):
        mock_service.list.return_value = empty_page()

        client.get("/v1/schedules/?model=gpt-4")

        assert mock_service.list.await_args.kwargs["model_filter"] == "gpt-4"

    def test_absent_model_param_forwards_none(self, client, mock_service):
        mock_service.list.return_value = empty_page()

        client.get("/v1/schedules/")

        assert mock_service.list.await_args.kwargs["model_filter"] is None

    def test_surrounding_whitespace_is_trimmed(self, client, mock_service):
        mock_service.list.return_value = empty_page()

        client.get("/v1/schedules/?model=%20gpt-4%20")

        assert mock_service.list.await_args.kwargs["model_filter"] == "gpt-4"

    def test_whitespace_only_model_is_treated_as_absent(self, client, mock_service):
        mock_service.list.return_value = empty_page()

        client.get("/v1/schedules/?model=%20%20")

        assert mock_service.list.await_args.kwargs["model_filter"] is None

    def test_model_is_declared_in_the_openapi_schema(self, client):
        """Unlike `name`, this filter must be visible to MCP clients."""
        spec = client.get("/openapi.json").json()
        params = spec["paths"]["/v1/schedules/"]["get"]["parameters"]

        assert "model" in {p["name"] for p in params if p["in"] == "query"}


class TestScheduleExamples:
    def test_every_example_is_a_valid_request(self):
        from src.api.v1.endpoints.schedules import _SCHEDULE_EXAMPLES
        from src.lib.schemas.schedule import ScheduleRequest

        for example in _SCHEDULE_EXAMPLES.values():
            ScheduleRequest.model_validate(example["value"])

    def test_multiple_crons_example_lists_two_entries(self):
        from src.api.v1.endpoints.schedules import _SCHEDULE_EXAMPLES

        assert len(_SCHEDULE_EXAMPLES["multiple_crons"]["value"]["trigger"]["crons"]) == 2


class TestCreateScheduleWithCrons:
    def test_create_accepts_crons_list(self, client, mock_service):
        body = {
            **VALID_REQUEST,
            "trigger": {"crons": [{"fields": {"hour": 8}}, {"fields": {"hour": 10}}]},
        }
        mock_service.create.return_value = make_response()

        response = client.post("/v1/schedules/", json=body)

        assert response.status_code == 201
        sent = mock_service.create.await_args[0][0]
        assert sent.trigger.crons is not None
        assert len(sent.trigger.crons) == 2

    @pytest.mark.parametrize(
        "trigger",
        [
            {"crons": []},
            {"crons": [{"fields": {"hour": 9}}] * 21},
            {"cron": {"fields": {"hour": 9}}, "crons": [{"fields": {"hour": 9}}]},
        ],
    )
    def test_create_rejects_invalid_crons(self, client, mock_service, trigger):
        response = client.post("/v1/schedules/", json={**VALID_REQUEST, "trigger": trigger})

        assert response.status_code == 422
        mock_service.create.assert_not_awaited()

    def test_create_out_of_range_entry_returns_422_naming_the_entry(self, client, mock_service):
        trigger = {"crons": [{"fields": {"hour": 9}}, {"fields": {"hour": 25}}]}

        response = client.post("/v1/schedules/", json={**VALID_REQUEST, "trigger": trigger})

        assert response.status_code == 422
        locations = [tuple(error["loc"]) for error in response.json()["detail"]]
        assert all(loc[:4] == ("body", "trigger", "crons", 1) for loc in locations)
        mock_service.create.assert_not_awaited()

    def test_create_out_of_range_single_cron_returns_422(self, client, mock_service):
        trigger = {"cron": {"fields": {"hour": 25}}}

        response = client.post("/v1/schedules/", json={**VALID_REQUEST, "trigger": trigger})

        assert response.status_code == 422
        mock_service.create.assert_not_awaited()

    def test_create_error_locates_invalid_entry(self, client, mock_service):
        trigger = {"crons": [{"fields": {"hour": 9}}, {"fields": {"hour": {"bad": 1}}}]}

        response = client.post("/v1/schedules/", json={**VALID_REQUEST, "trigger": trigger})

        assert response.status_code == 422
        locations = [tuple(error["loc"]) for error in response.json()["detail"]]
        assert all(loc[:4] == ("body", "trigger", "crons", 1) for loc in locations)
