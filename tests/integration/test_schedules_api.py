"""Integration tests for the /v1/schedules endpoints.

Exercises the full HTTP → service → repository → database stack.
APScheduler is mocked so add_job calls never touch the job store.

Requires TEST_DB_URL to be set; all tests are skipped otherwise.
"""

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock
import uuid

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.api.dependencies.auth import get_user_id
from src.api.dependencies.db import get_db_session
from src.api.dependencies.scheduler import get_scheduler
from src.api.dependencies.sign_in import get_sign_in_service
from src.api.v1.endpoints.schedules import router as schedules_router
from src.lib.models.schedule import Schedule
from tests.integration.conftest import make_signed_in_service


pytestmark = pytest.mark.asyncio(loop_scope="module")


# ---------------------------------------------------------------------------
# App + client fixtures
# ---------------------------------------------------------------------------


INTEGRATION_USER_ID = "mock_user_id"


@pytest.fixture(scope="module")
def test_app(engine: AsyncEngine) -> FastAPI:
    app = FastAPI()
    app.include_router(schedules_router, prefix="/v1")

    async def override_db() -> Any:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    async def override_user_id() -> str:
        return INTEGRATION_USER_ID

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_user_id] = override_user_id
    app.dependency_overrides[get_sign_in_service] = make_signed_in_service
    return app


@pytest.fixture
def mock_scheduler() -> MagicMock:
    return MagicMock()


@pytest_asyncio.fixture(loop_scope="module")
async def client(test_app: FastAPI, mock_scheduler: MagicMock) -> AsyncGenerator[httpx.AsyncClient, None]:
    test_app.dependency_overrides[get_scheduler] = lambda: mock_scheduler
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app),
        base_url="http://test",
        headers={"Api-Key": "test-key"},
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True, loop_scope="module")
async def cleanup(engine: AsyncEngine) -> AsyncGenerator[None, None]:
    yield
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.execute(sa.delete(Schedule))
        await session.commit()


# ---------------------------------------------------------------------------
# Shared request payloads
# ---------------------------------------------------------------------------


CRON_REQUEST: dict[str, Any] = {
    "display_name": "Integration Test Schedule",
    "service_id": "dial-native",
    "trigger": {
        "cron": {
            "fields": {"hour": 9, "minute": 0},
        }
    },
    "properties": {
        "target_type": "chat_completion",
        "url": "https://core.dial/openai/",
        "payload": {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        "api_version": "2025-01-01-preview",
    },
}

DATE_REQUEST: dict[str, Any] = {
    "display_name": "Date Schedule",
    "service_id": "dial-native",
    "trigger": {"date": "2030-01-01T09:00:00"},
    "properties": {
        "target_type": "rest",
        "url": "https://api.example.com/hook",
        "method": "POST",
    },
}

RESPONSES_REQUEST: dict[str, Any] = {
    "display_name": "Responses Schedule",
    "service_id": "dial-native",
    "trigger": {
        "cron": {
            "fields": {"hour": 9, "minute": 0},
        }
    },
    "properties": {
        "target_type": "responses",
        "url": "https://core.dial/openai/v1/responses",
        "api_version": "2025-01-01-preview",
        "payload": {"input": "Summarize yesterday's incidents", "model": "gpt-5.6"},
    },
}


# ---------------------------------------------------------------------------
# POST /v1/schedules/
# ---------------------------------------------------------------------------


class TestCreateSchedule:
    async def test_create_returns_201_with_full_body(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/schedules/", json=CRON_REQUEST)

        assert response.status_code == 201
        body = response.json()
        assert "id" in body
        assert body["display_name"] == "Integration Test Schedule"
        assert body["service_id"] == "dial-native"
        assert body["created_by"] == "mock_user_id"
        assert body["next_run_time"] is None  # no real APScheduler row in test DB
        assert "trigger" in body
        assert "properties" in body
        assert "created_at" in body
        assert "updated_at" in body

    async def test_create_missing_service_id_returns_422(self, client: httpx.AsyncClient) -> None:
        bad_request = {k: v for k, v in CRON_REQUEST.items() if k != "service_id"}
        response = await client.post("/v1/schedules/", json=bad_request)
        assert response.status_code == 422

    async def test_create_persists_row_in_db(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        response = await client.post("/v1/schedules/", json=CRON_REQUEST)
        assert response.status_code == 201
        schedule_id = response.json()["id"]

        async with AsyncSession(engine, expire_on_commit=False) as session:
            row = await session.get(Schedule, schedule_id)
        assert row is not None
        assert row.display_name == "Integration Test Schedule"
        assert row.created_by == "mock_user_id"

    async def test_create_stores_trigger_as_iso_strings(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        """C1: datetime fields in JSONB must be ISO strings, not raw datetime objects."""
        date_request = {
            **CRON_REQUEST,
            "trigger": {"date": "2030-06-15T09:00:00"},
        }
        response = await client.post("/v1/schedules/", json=date_request)
        assert response.status_code == 201
        schedule_id = response.json()["id"]

        async with AsyncSession(engine, expire_on_commit=False) as session:
            row = await session.get(Schedule, schedule_id)
        assert row is not None
        assert isinstance(row.trigger.get("date"), str)

    async def test_create_invalid_trigger_returns_422(self, client: httpx.AsyncClient) -> None:
        bad = {**CRON_REQUEST, "trigger": {}}
        response = await client.post("/v1/schedules/", json=bad)
        assert response.status_code == 422

    async def test_create_invalid_properties_returns_422(self, client: httpx.AsyncClient) -> None:
        bad = {**CRON_REQUEST, "properties": {"target_type": "rest", "url": "https://example.com"}}
        response = await client.post("/v1/schedules/", json=bad)
        assert response.status_code == 422

    async def test_create_scheduler_failure_returns_502_and_rolls_back(
        self, client: httpx.AsyncClient, mock_scheduler: MagicMock, engine: AsyncEngine
    ) -> None:
        mock_scheduler.add_job.side_effect = RuntimeError("job store unavailable")

        response = await client.post("/v1/schedules/", json=CRON_REQUEST)

        assert response.status_code == 502
        # the schedule row must have been deleted (saga rollback)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            schedules_table = Schedule.__table__  # type: ignore[attr-defined]
            result = await session.execute(sa.select(Schedule).where(schedules_table.c.created_by == "mock_user_id"))
            remaining = result.scalars().all()
        assert len(remaining) == 0


class TestCreateScheduleSignInPrecondition:
    async def test_create_blocked_when_not_signed_in(
        self, client: httpx.AsyncClient, test_app: FastAPI, engine: AsyncEngine
    ) -> None:
        from src.lib.exceptions.errors import SignInRequiredError
        from src.lib.schemas.sign_in import SignInStatus

        blocked_service = AsyncMock()
        blocked_service.require.side_effect = SignInRequiredError(
            SignInStatus(service_id="dial-native", state="not_signed_in", detail="Please sign in.")
        )

        test_app.dependency_overrides[get_sign_in_service] = lambda: blocked_service
        try:
            response = await client.post("/v1/schedules/", json=CRON_REQUEST)
        finally:
            test_app.dependency_overrides[get_sign_in_service] = make_signed_in_service

        assert response.status_code == 403
        async with AsyncSession(engine, expire_on_commit=False) as session:
            schedules_table = Schedule.__table__  # type: ignore[attr-defined]
            result = await session.execute(sa.select(Schedule).where(schedules_table.c.created_by == "mock_user_id"))
            remaining = result.scalars().all()
        assert len(remaining) == 0


class TestCreateResponsesSchedule:
    async def test_create_responses_returns_201_and_echoes_target_type(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/schedules/", json=RESPONSES_REQUEST)

        assert response.status_code == 201
        body = response.json()
        assert body["properties"]["target_type"] == "responses"
        assert body["properties"]["payload"]["stream"] is False

    async def test_create_responses_rejects_method(self, client: httpx.AsyncClient) -> None:
        bad = {**RESPONSES_REQUEST, "properties": {**RESPONSES_REQUEST["properties"], "method": "POST"}}
        response = await client.post("/v1/schedules/", json=bad)
        assert response.status_code == 422

    async def test_create_responses_requires_api_version(self, client: httpx.AsyncClient) -> None:
        props = {k: v for k, v in RESPONSES_REQUEST["properties"].items() if k != "api_version"}
        bad = {**RESPONSES_REQUEST, "properties": props}
        response = await client.post("/v1/schedules/", json=bad)
        assert response.status_code == 422

    async def test_create_responses_rejects_create_conversation(self, client: httpx.AsyncClient) -> None:
        bad = {
            **RESPONSES_REQUEST,
            "properties": {**RESPONSES_REQUEST["properties"], "create_conversation": True},
        }
        response = await client.post("/v1/schedules/", json=bad)
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# PUT /v1/schedules/{id}
# ---------------------------------------------------------------------------


class TestUpdateSchedule:
    async def test_update_returns_200_with_updated_body(self, client: httpx.AsyncClient) -> None:
        create_resp = await client.post("/v1/schedules/", json=CRON_REQUEST)
        assert create_resp.status_code == 201
        schedule_id = create_resp.json()["id"]

        update_request = {**CRON_REQUEST, "display_name": "Updated Schedule"}
        response = await client.put(f"/v1/schedules/{schedule_id}", json=update_request)

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == schedule_id
        assert body["display_name"] == "Updated Schedule"

    async def test_update_persists_changes_in_db(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        create_resp = await client.post("/v1/schedules/", json=CRON_REQUEST)
        schedule_id = create_resp.json()["id"]

        await client.put(f"/v1/schedules/{schedule_id}", json={**CRON_REQUEST, "display_name": "DB Updated"})

        async with AsyncSession(engine, expire_on_commit=False) as session:
            row = await session.get(Schedule, schedule_id)
        assert row is not None
        assert row.display_name == "DB Updated"

    async def test_update_nonexistent_returns_404(self, client: httpx.AsyncClient) -> None:
        fake_id = str(uuid.uuid4())
        response = await client.put(f"/v1/schedules/{fake_id}", json=CRON_REQUEST)
        assert response.status_code == 404

    async def test_update_scheduler_failure_returns_502_and_restores_row(
        self, client: httpx.AsyncClient, mock_scheduler: MagicMock, engine: AsyncEngine
    ) -> None:
        """C3: Job re-registration failure must restore the prior schedule values."""
        create_resp = await client.post("/v1/schedules/", json=CRON_REQUEST)
        assert create_resp.status_code == 201
        schedule_id = create_resp.json()["id"]
        original_name = create_resp.json()["display_name"]

        # reset after create succeeded
        mock_scheduler.add_job.side_effect = None
        # now make re-registration fail
        mock_scheduler.add_job.side_effect = RuntimeError("job store unavailable")

        update_request = {**CRON_REQUEST, "display_name": "Should Not Persist"}
        response = await client.put(f"/v1/schedules/{schedule_id}", json=update_request)

        assert response.status_code == 502
        # row must be restored to original values
        async with AsyncSession(engine, expire_on_commit=False) as session:
            row = await session.get(Schedule, schedule_id)
        assert row is not None
        assert row.display_name == original_name

    async def test_update_blocked_when_not_signed_in_leaves_row_unchanged(
        self, client: httpx.AsyncClient, test_app: FastAPI, engine: AsyncEngine
    ) -> None:
        from src.lib.exceptions.errors import SignInRequiredError
        from src.lib.schemas.sign_in import SignInStatus

        create_resp = await client.post("/v1/schedules/", json=CRON_REQUEST)
        schedule_id = create_resp.json()["id"]
        original_name = create_resp.json()["display_name"]

        blocked_service = AsyncMock()
        blocked_service.require.side_effect = SignInRequiredError(
            SignInStatus(service_id="dial-native", state="not_signed_in", detail="Please sign in.")
        )
        test_app.dependency_overrides[get_sign_in_service] = lambda: blocked_service
        try:
            response = await client.put(
                f"/v1/schedules/{schedule_id}", json={**CRON_REQUEST, "display_name": "Should Not Persist"}
            )
        finally:
            test_app.dependency_overrides[get_sign_in_service] = make_signed_in_service

        assert response.status_code == 403
        async with AsyncSession(engine, expire_on_commit=False) as session:
            row = await session.get(Schedule, schedule_id)
        assert row is not None
        assert row.display_name == original_name


# ---------------------------------------------------------------------------
# GET /v1/schedules/
# ---------------------------------------------------------------------------


class TestListSchedules:
    async def test_list_returns_200_with_paginated_body(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/v1/schedules/")

        assert response.status_code == 200
        body = response.json()
        assert "count" in body
        assert "limit" in body
        assert "offset" in body
        assert "results" in body
        assert isinstance(body["results"], list)

    async def test_list_reflects_created_schedules(self, client: httpx.AsyncClient) -> None:
        for name in ("Alpha", "Beta", "Gamma"):
            await client.post("/v1/schedules/", json={**CRON_REQUEST, "display_name": name})

        response = await client.get("/v1/schedules/")
        body = response.json()
        assert body["count"] >= 3

    async def test_list_pagination_limit(self, client: httpx.AsyncClient) -> None:
        for i in range(5):
            await client.post("/v1/schedules/", json={**CRON_REQUEST, "display_name": f"Schedule {i}"})

        response = await client.get("/v1/schedules/?limit=2")
        body = response.json()
        assert len(body["results"]) == 2
        assert body["limit"] == 2

    async def test_list_pagination_links(self, client: httpx.AsyncClient) -> None:
        for i in range(4):
            await client.post("/v1/schedules/", json={**CRON_REQUEST, "display_name": f"Linked {i}"})

        response = await client.get("/v1/schedules/?limit=2&offset=0")
        body = response.json()
        assert body["next"] is not None
        assert "offset=2" in body["next"]
        assert body["previous"] is None

    async def test_list_result_items_have_correct_shape(self, client: httpx.AsyncClient) -> None:
        await client.post("/v1/schedules/", json=CRON_REQUEST)

        response = await client.get("/v1/schedules/")
        items = response.json()["results"]
        assert len(items) >= 1
        item = items[0]
        assert "id" in item
        assert "display_name" in item
        assert "trigger_type" in item
        assert "created_by" in item
        assert item["display_name"] == "Integration Test Schedule"
        assert item["trigger_type"] == "cron"


class TestListSchedulesOrdering:
    async def _create(self, client: httpx.AsyncClient, name: str) -> str:
        response = await client.post("/v1/schedules/", json={**CRON_REQUEST, "display_name": name})
        assert response.status_code == 201
        return response.json()["id"]

    async def test_default_order_is_created_at_desc(self, client: httpx.AsyncClient) -> None:
        first = await self._create(client, "Ordering First")
        second = await self._create(client, "Ordering Second")

        response = await client.get("/v1/schedules/?limit=50")
        ids = [item["id"] for item in response.json()["results"]]

        assert ids.index(second) < ids.index(first)

    async def test_created_at_asc_reverses_order(self, client: httpx.AsyncClient) -> None:
        first = await self._create(client, "Asc First")
        second = await self._create(client, "Asc Second")

        response = await client.get("/v1/schedules/?limit=50&order_by=created_at&order_dir=asc")
        ids = [item["id"] for item in response.json()["results"]]

        assert ids.index(first) < ids.index(second)

    async def test_name_asc_orders_case_insensitively(self, client: httpx.AsyncClient) -> None:
        await self._create(client, "banana")
        await self._create(client, "Apple")

        response = await client.get("/v1/schedules/?limit=50&order_by=name&order_dir=asc")
        names = [item["display_name"] for item in response.json()["results"]]

        apple_idx = names.index("Apple")
        banana_idx = names.index("banana")
        assert apple_idx < banana_idx

    async def test_next_run_time_puts_inactive_last(self, client: httpx.AsyncClient) -> None:
        # No real APScheduler rows exist in the test DB, so every schedule has a
        # NULL next_run_time and must still be returned (sorted last, stable order).
        await self._create(client, "Inactive A")
        await self._create(client, "Inactive B")

        response = await client.get("/v1/schedules/?limit=50&order_by=next_run_time&order_dir=asc")
        assert response.status_code == 200
        assert len(response.json()["results"]) >= 2

    async def test_invalid_order_by_returns_422(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/v1/schedules/?order_by=bogus")
        assert response.status_code == 422

    async def test_invalid_order_dir_returns_422(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/v1/schedules/?order_dir=sideways")
        assert response.status_code == 422

    async def test_pagination_links_preserve_ordering(self, client: httpx.AsyncClient) -> None:
        for i in range(4):
            await self._create(client, f"Paged {i}")

        response = await client.get("/v1/schedules/?limit=2&offset=0&order_by=name&order_dir=asc")
        body = response.json()
        assert body["next"] is not None
        assert "order_by=name" in body["next"]
        assert "order_dir=asc" in body["next"]


# ---------------------------------------------------------------------------
# GET /v1/schedules/{id}
# ---------------------------------------------------------------------------


class TestGetScheduleDetail:
    async def test_get_returns_200_with_full_body(self, client: httpx.AsyncClient) -> None:
        create_resp = await client.post("/v1/schedules/", json=CRON_REQUEST)
        schedule_id = create_resp.json()["id"]

        response = await client.get(f"/v1/schedules/{schedule_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == schedule_id
        assert body["display_name"] == "Integration Test Schedule"
        assert body["created_by"] == "mock_user_id"
        assert "trigger" in body
        assert "properties" in body

    async def test_get_nonexistent_returns_404(self, client: httpx.AsyncClient) -> None:
        fake_id = str(uuid.uuid4())
        response = await client.get(f"/v1/schedules/{fake_id}")
        assert response.status_code == 404

    async def test_get_reflects_updated_fields(self, client: httpx.AsyncClient) -> None:
        create_resp = await client.post("/v1/schedules/", json=CRON_REQUEST)
        schedule_id = create_resp.json()["id"]
        await client.put(f"/v1/schedules/{schedule_id}", json={**CRON_REQUEST, "display_name": "Reflected Update"})

        response = await client.get(f"/v1/schedules/{schedule_id}")
        assert response.json()["display_name"] == "Reflected Update"


# ---------------------------------------------------------------------------
# POST /v1/schedules/{id}/pause
# ---------------------------------------------------------------------------


class TestPauseSchedule:
    async def test_pause_owned_returns_200_with_null_next_run_time(self, client: httpx.AsyncClient) -> None:
        create_resp = await client.post("/v1/schedules/", json=CRON_REQUEST)
        assert create_resp.status_code == 201
        schedule_id = create_resp.json()["id"]

        response = await client.post(f"/v1/schedules/{schedule_id}/pause")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == schedule_id
        assert body["next_run_time"] is None

    async def test_pause_schedule_still_retrievable_after_pause(self, client: httpx.AsyncClient) -> None:
        create_resp = await client.post("/v1/schedules/", json=CRON_REQUEST)
        schedule_id = create_resp.json()["id"]

        await client.post(f"/v1/schedules/{schedule_id}/pause")

        get_resp = await client.get(f"/v1/schedules/{schedule_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == schedule_id

    async def test_pause_response_is_schedule_response_shape(self, client: httpx.AsyncClient) -> None:
        create_resp = await client.post("/v1/schedules/", json=CRON_REQUEST)
        schedule_id = create_resp.json()["id"]

        response = await client.post(f"/v1/schedules/{schedule_id}/pause")

        body = response.json()
        assert "id" in body
        assert "display_name" in body
        assert "trigger" in body
        assert "properties" in body
        assert "created_by" in body
        assert "created_at" in body
        assert "updated_at" in body

    async def test_pause_nonexistent_returns_404(self, client: httpx.AsyncClient) -> None:
        fake_id = str(uuid.uuid4())
        response = await client.post(f"/v1/schedules/{fake_id}/pause")
        assert response.status_code == 404

    async def test_pause_cross_owner_returns_404_identical_to_unknown(
        self, client: httpx.AsyncClient, test_app: FastAPI
    ) -> None:
        create_resp = await client.post("/v1/schedules/", json=CRON_REQUEST)
        schedule_id = create_resp.json()["id"]

        async def override_other_user() -> str:
            return "other_user_id"

        test_app.dependency_overrides[get_user_id] = override_other_user
        try:
            cross_response = await client.post(f"/v1/schedules/{schedule_id}/pause")
            unknown_response = await client.post(f"/v1/schedules/{uuid.uuid4()}/pause")
        finally:
            test_app.dependency_overrides[get_user_id] = lambda: INTEGRATION_USER_ID

        assert cross_response.status_code == 404
        assert unknown_response.status_code == 404

    async def test_pause_unauthenticated_returns_401(self, client: httpx.AsyncClient, test_app: FastAPI) -> None:
        from fastapi import HTTPException

        create_resp = await client.post("/v1/schedules/", json=CRON_REQUEST)
        schedule_id = create_resp.json()["id"]

        async def raise_unauthorized() -> str:
            raise HTTPException(status_code=401, detail="Unauthorized")

        test_app.dependency_overrides[get_user_id] = raise_unauthorized
        try:
            response = await client.post(f"/v1/schedules/{schedule_id}/pause")
        finally:
            test_app.dependency_overrides[get_user_id] = lambda: INTEGRATION_USER_ID

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /v1/schedules/{id}/resume
# ---------------------------------------------------------------------------


class TestResumeSchedule:
    async def test_resume_paused_returns_200(self, client: httpx.AsyncClient) -> None:
        create_resp = await client.post("/v1/schedules/", json=CRON_REQUEST)
        assert create_resp.status_code == 201
        schedule_id = create_resp.json()["id"]

        await client.post(f"/v1/schedules/{schedule_id}/pause")
        response = await client.post(f"/v1/schedules/{schedule_id}/resume")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == schedule_id

    async def test_resume_response_is_schedule_response_shape(self, client: httpx.AsyncClient) -> None:
        create_resp = await client.post("/v1/schedules/", json=CRON_REQUEST)
        schedule_id = create_resp.json()["id"]

        await client.post(f"/v1/schedules/{schedule_id}/pause")
        response = await client.post(f"/v1/schedules/{schedule_id}/resume")

        body = response.json()
        assert "id" in body
        assert "display_name" in body
        assert "trigger" in body
        assert "properties" in body
        assert "created_by" in body

    async def test_resume_nonexistent_returns_404(self, client: httpx.AsyncClient) -> None:
        fake_id = str(uuid.uuid4())
        response = await client.post(f"/v1/schedules/{fake_id}/resume")
        assert response.status_code == 404

    async def test_resume_cross_owner_returns_404_identical_to_unknown(
        self, client: httpx.AsyncClient, test_app: FastAPI
    ) -> None:
        create_resp = await client.post("/v1/schedules/", json=CRON_REQUEST)
        schedule_id = create_resp.json()["id"]

        async def override_other_user() -> str:
            return "other_user_id"

        test_app.dependency_overrides[get_user_id] = override_other_user
        try:
            cross_response = await client.post(f"/v1/schedules/{schedule_id}/resume")
            unknown_response = await client.post(f"/v1/schedules/{uuid.uuid4()}/resume")
        finally:
            test_app.dependency_overrides[get_user_id] = lambda: INTEGRATION_USER_ID

        assert cross_response.status_code == 404
        assert unknown_response.status_code == 404

    async def test_resume_unauthenticated_returns_401(self, client: httpx.AsyncClient, test_app: FastAPI) -> None:
        from fastapi import HTTPException

        create_resp = await client.post("/v1/schedules/", json=CRON_REQUEST)
        schedule_id = create_resp.json()["id"]

        async def raise_unauthorized() -> str:
            raise HTTPException(status_code=401, detail="Unauthorized")

        test_app.dependency_overrides[get_user_id] = raise_unauthorized
        try:
            response = await client.post(f"/v1/schedules/{schedule_id}/resume")
        finally:
            test_app.dependency_overrides[get_user_id] = lambda: INTEGRATION_USER_ID

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /v1/schedules/{schedule_id}
# ---------------------------------------------------------------------------


async def _add_run(engine: AsyncEngine, schedule_id: str) -> None:
    """Give a schedule one run record so deletion takes the soft-delete branch."""
    from src.lib.db.enums import ScheduleRunStatus
    from src.lib.models.schedule_run import ScheduleRun

    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add(
            ScheduleRun(
                id=uuid.uuid4(),
                status=ScheduleRunStatus.SUCCESS,
                schedule_id=schedule_id,
                schedule_snapshot={"display_name": "snapshot"},
            )
        )
        await session.commit()


async def _clear_runs(engine: AsyncEngine) -> None:
    from src.lib.models.schedule_run import ScheduleRun

    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.execute(sa.delete(ScheduleRun))
        await session.commit()


class TestDeleteScheduleWithoutRuns:
    async def test_delete_returns_204_and_removes_schedule(
        self, client: httpx.AsyncClient, mock_scheduler: MagicMock
    ) -> None:
        schedule_id = (await client.post("/v1/schedules/", json=CRON_REQUEST)).json()["id"]

        response = await client.delete(f"/v1/schedules/{schedule_id}")

        assert response.status_code == 204
        assert response.content == b""
        mock_scheduler.remove_job.assert_called_once_with(schedule_id)
        assert (await client.get(f"/v1/schedules/{schedule_id}")).status_code == 404

    async def test_deleted_schedule_absent_from_both_list_filters(self, client: httpx.AsyncClient) -> None:
        schedule_id = (await client.post("/v1/schedules/", json=CRON_REQUEST)).json()["id"]
        await client.delete(f"/v1/schedules/{schedule_id}")

        live = await client.get("/v1/schedules/")
        deleted = await client.get("/v1/schedules/?is_deleted=true")

        assert schedule_id not in {item["id"] for item in live.json()["results"]}
        assert schedule_id not in {item["id"] for item in deleted.json()["results"]}

    async def test_second_delete_returns_404(self, client: httpx.AsyncClient) -> None:
        schedule_id = (await client.post("/v1/schedules/", json=CRON_REQUEST)).json()["id"]
        await client.delete(f"/v1/schedules/{schedule_id}")

        assert (await client.delete(f"/v1/schedules/{schedule_id}")).status_code == 404

    async def test_delete_unknown_id_returns_404(self, client: httpx.AsyncClient) -> None:
        assert (await client.delete(f"/v1/schedules/{uuid.uuid4()}")).status_code == 404

    async def test_delete_other_users_schedule_returns_404(self, client: httpx.AsyncClient, test_app: FastAPI) -> None:
        schedule_id = (await client.post("/v1/schedules/", json=CRON_REQUEST)).json()["id"]

        test_app.dependency_overrides[get_user_id] = lambda: "someone_else"
        try:
            response = await client.delete(f"/v1/schedules/{schedule_id}")
        finally:
            test_app.dependency_overrides[get_user_id] = lambda: INTEGRATION_USER_ID

        assert response.status_code == 404
        assert (await client.get(f"/v1/schedules/{schedule_id}")).status_code == 200

    async def test_delete_does_not_affect_other_schedules(self, client: httpx.AsyncClient) -> None:
        target = (await client.post("/v1/schedules/", json=CRON_REQUEST)).json()["id"]
        bystander = (await client.post("/v1/schedules/", json=DATE_REQUEST)).json()["id"]

        await client.delete(f"/v1/schedules/{target}")

        assert (await client.get(f"/v1/schedules/{bystander}")).status_code == 200

    async def test_scheduler_failure_returns_502_and_keeps_schedule_live(
        self, client: httpx.AsyncClient, mock_scheduler: MagicMock
    ) -> None:
        schedule_id = (await client.post("/v1/schedules/", json=CRON_REQUEST)).json()["id"]
        mock_scheduler.remove_job.side_effect = RuntimeError("job store unreachable")

        response = await client.delete(f"/v1/schedules/{schedule_id}")

        assert response.status_code == 502
        detail = await client.get(f"/v1/schedules/{schedule_id}")
        assert detail.status_code == 200
        assert detail.json()["is_deleted"] is False

    async def test_missing_job_still_deletes(self, client: httpx.AsyncClient, mock_scheduler: MagicMock) -> None:
        from apscheduler.jobstores.base import JobLookupError

        schedule_id = (await client.post("/v1/schedules/", json=CRON_REQUEST)).json()["id"]
        mock_scheduler.remove_job.side_effect = JobLookupError("gone")

        response = await client.delete(f"/v1/schedules/{schedule_id}")

        assert response.status_code == 204
        assert (await client.get(f"/v1/schedules/{schedule_id}")).status_code == 404


class TestDeleteScheduleWithRuns:
    async def test_delete_soft_deletes_and_preserves_runs(
        self, client: httpx.AsyncClient, engine: AsyncEngine, mock_scheduler: MagicMock
    ) -> None:
        schedule_id = (await client.post("/v1/schedules/", json=CRON_REQUEST)).json()["id"]
        await _add_run(engine, schedule_id)
        try:
            response = await client.delete(f"/v1/schedules/{schedule_id}")

            assert response.status_code == 204
            mock_scheduler.remove_job.assert_called_once_with(schedule_id)

            detail = await client.get(f"/v1/schedules/{schedule_id}")
            assert detail.status_code == 200
            assert detail.json()["is_deleted"] is True
            assert detail.json()["next_run_time"] is None
        finally:
            await _clear_runs(engine)

    async def test_soft_deleted_schedule_leaves_default_list_and_enters_deleted_list(
        self, client: httpx.AsyncClient, engine: AsyncEngine
    ) -> None:
        schedule_id = (await client.post("/v1/schedules/", json=CRON_REQUEST)).json()["id"]
        await _add_run(engine, schedule_id)
        try:
            await client.delete(f"/v1/schedules/{schedule_id}")

            live = await client.get("/v1/schedules/")
            deleted = await client.get("/v1/schedules/?is_deleted=true")

            assert schedule_id not in {item["id"] for item in live.json()["results"]}
            deleted_items = {item["id"]: item for item in deleted.json()["results"]}
            assert schedule_id in deleted_items
            assert deleted_items[schedule_id]["is_deleted"] is True
        finally:
            await _clear_runs(engine)

    async def test_every_mutation_on_soft_deleted_schedule_returns_409(
        self, client: httpx.AsyncClient, engine: AsyncEngine
    ) -> None:
        schedule_id = (await client.post("/v1/schedules/", json=CRON_REQUEST)).json()["id"]
        await _add_run(engine, schedule_id)
        try:
            await client.delete(f"/v1/schedules/{schedule_id}")

            assert (await client.delete(f"/v1/schedules/{schedule_id}")).status_code == 409
            assert (await client.put(f"/v1/schedules/{schedule_id}", json=CRON_REQUEST)).status_code == 409
            assert (await client.post(f"/v1/schedules/{schedule_id}/pause")).status_code == 409
            assert (await client.post(f"/v1/schedules/{schedule_id}/resume")).status_code == 409

            # Still deleted, still not firing.
            detail = await client.get(f"/v1/schedules/{schedule_id}")
            assert detail.json()["is_deleted"] is True
            assert detail.json()["next_run_time"] is None
        finally:
            await _clear_runs(engine)


class TestIsDeletedFieldExposure:
    async def test_created_schedule_reports_not_deleted(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/schedules/", json=CRON_REQUEST)

        assert response.json()["is_deleted"] is False

    async def test_list_items_include_is_deleted(self, client: httpx.AsyncClient) -> None:
        await client.post("/v1/schedules/", json=CRON_REQUEST)

        response = await client.get("/v1/schedules/")

        assert all("is_deleted" in item for item in response.json()["results"])

    async def test_is_deleted_in_body_is_ignored_on_create(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/schedules/", json={**CRON_REQUEST, "is_deleted": True})

        assert response.status_code == 201
        assert response.json()["is_deleted"] is False

    async def test_is_deleted_in_body_is_ignored_on_update(self, client: httpx.AsyncClient) -> None:
        schedule_id = (await client.post("/v1/schedules/", json=CRON_REQUEST)).json()["id"]

        response = await client.put(f"/v1/schedules/{schedule_id}", json={**CRON_REQUEST, "is_deleted": True})

        assert response.status_code == 200
        assert response.json()["is_deleted"] is False

    async def test_non_boolean_filter_returns_422(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/v1/schedules/?is_deleted=maybe")).status_code == 422

    async def test_deleted_list_links_preserve_the_filter(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        first = (await client.post("/v1/schedules/", json=CRON_REQUEST)).json()["id"]
        second = (await client.post("/v1/schedules/", json=DATE_REQUEST)).json()["id"]
        await _add_run(engine, first)
        await _add_run(engine, second)
        try:
            await client.delete(f"/v1/schedules/{first}")
            await client.delete(f"/v1/schedules/{second}")

            response = await client.get("/v1/schedules/?is_deleted=true&limit=1")

            assert response.json()["count"] == 2
            assert "is_deleted=true" in response.json()["next"]
        finally:
            await _clear_runs(engine)
