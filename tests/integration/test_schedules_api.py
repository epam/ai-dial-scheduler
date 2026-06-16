"""Integration tests for the /v1/schedules endpoints.

Exercises the full HTTP → service → repository → database stack.
APScheduler is mocked so add_job calls never touch the job store.

Requires TEST_DB_URL to be set; all tests are skipped otherwise.
"""

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import MagicMock
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
from src.api.v1.endpoints.schedules import router as schedules_router
from src.lib.models.schedule import Schedule


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
    "trigger": {
        "cron": {
            "fields": {"hour": 9, "minute": 0},
        }
    },
    "properties": {
        "target_type": "chat_completion",
        "url": "https://core.dial/openai/deployments/gpt-4o/chat/completions",
        "api_version": "2025-01-01-preview",
    },
}

DATE_REQUEST: dict[str, Any] = {
    "display_name": "Date Schedule",
    "trigger": {"date": "2030-01-01T09:00:00"},
    "properties": {
        "target_type": "rest",
        "url": "https://api.example.com/hook",
        "method": "POST",
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
        assert body["created_by"] == "mock_user_id"
        assert body["next_run_time"] is None  # no real APScheduler row in test DB
        assert "trigger" in body
        assert "properties" in body
        assert "created_at" in body
        assert "updated_at" in body

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
