"""Integration tests for conversation_id in GET /v1/schedules/{id}/runs endpoints.

Covers data-model §6 cases 18–21 and FR-007, SC-001, SC-002, SC-003, SC-005.
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
from src.api.dependencies.sign_in import get_sign_in_service
from src.api.v1.endpoints.runs import router as runs_router
from src.api.v1.endpoints.schedules import router as schedules_router
from src.lib.db.enums import ScheduleRunStatus
from src.lib.models.schedule import Schedule
from src.lib.models.schedule_run import ScheduleRun
from tests.integration.conftest import make_signed_in_service


pytestmark = pytest.mark.asyncio(loop_scope="module")


INTEGRATION_USER_ID = "mock_user_id"
CONV_ID = "conversations/bkt/.scheduler/{schedule_id}/gpt-4o__QS conversation run__{run_id}"


@pytest.fixture(scope="module")
def test_app(engine: AsyncEngine) -> FastAPI:
    app = FastAPI(redirect_slashes=False)
    app.include_router(schedules_router, prefix="/v1")
    app.include_router(runs_router, prefix="/v1")

    async def override_db() -> Any:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    async def override_user_id() -> str:
        return INTEGRATION_USER_ID

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_user_id] = override_user_id
    # Schedule creation runs the sign-in precondition, which would otherwise reach for the
    # DIAL client in app.state — absent here, since this app is built without the lifespan.
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


@pytest_asyncio.fixture(autouse=True, loop_scope="module")
async def cleanup(engine: AsyncEngine) -> AsyncGenerator[None, None]:
    yield
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.execute(sa.delete(ScheduleRun))
        await session.execute(sa.delete(Schedule))
        await session.commit()


CRON_REQUEST: dict[str, Any] = {
    "display_name": "QS conversation run",
    "service_id": "dial-oauth",
    "trigger": {"cron": {"fields": {"hour": 9, "minute": 0}}},
    "properties": {
        "target_type": "chat_completion",
        "url": "https://core.dial/openai/",
        "payload": {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        "api_version": "2025-01-01-preview",
    },
}


async def create_schedule(client: httpx.AsyncClient, display_name: str = "QS conversation run") -> str:
    resp = await client.post("/v1/schedules/", json={**CRON_REQUEST, "display_name": display_name})
    assert resp.status_code == 201
    return resp.json()["id"]


async def seed_run(
    engine: AsyncEngine,
    schedule_id: str,
    status: ScheduleRunStatus = ScheduleRunStatus.SUCCESS,
    result: dict[str, Any] | None = None,
    conversation_id: str | None = None,
) -> ScheduleRun:
    """Seed a run record with an optional pre-persisted conversation_id."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        run = ScheduleRun(
            status=status.value,
            end_time=None,
            result=result,
            schedule_id=schedule_id,
            schedule_snapshot={"target_type": "chat_completion"},
            conversation_id=conversation_id,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run


class TestListRunsConversationId:
    """GET /v1/schedules/{id}/runs — conversation_id field (cases 18, 19, 20, 21)."""

    async def test_list_returns_conversation_id_for_run_that_has_one(
        self, client: httpx.AsyncClient, engine: AsyncEngine
    ) -> None:
        """Case 18 (set): successful run with persisted conversation_id is returned (SC-001)."""
        schedule_id = await create_schedule(client)
        conv_id = CONV_ID.format(schedule_id=schedule_id, run_id=str(uuid.uuid4()))
        run = await seed_run(engine, schedule_id, conversation_id=conv_id)

        response = await client.get(f"/v1/schedules/{schedule_id}/runs")

        assert response.status_code == 200
        results = response.json()["results"]
        assert len(results) == 1
        assert results[0]["id"] == str(run.id)
        assert results[0]["conversation_id"] == conv_id
        assert results[0]["conversation_id"].startswith("conversations/")

    async def test_list_returns_null_conversation_id_for_run_without_one(
        self, client: httpx.AsyncClient, engine: AsyncEngine
    ) -> None:
        """Case 18 (null): run without conversation_id reports null (SC-002)."""
        schedule_id = await create_schedule(client)
        run = await seed_run(engine, schedule_id, conversation_id=None)

        response = await client.get(f"/v1/schedules/{schedule_id}/runs")

        assert response.status_code == 200
        results = response.json()["results"]
        assert len(results) == 1
        assert results[0]["id"] == str(run.id)
        assert results[0]["conversation_id"] is None

    async def test_list_mixed_page_returns_both_set_and_null(
        self, client: httpx.AsyncClient, engine: AsyncEngine
    ) -> None:
        """Case 21: page with one run with conversation_id and one without."""
        schedule_id = await create_schedule(client)
        conv_id = CONV_ID.format(schedule_id=schedule_id, run_id=str(uuid.uuid4()))
        run_with = await seed_run(engine, schedule_id, conversation_id=conv_id)
        run_without = await seed_run(engine, schedule_id, conversation_id=None)

        response = await client.get(f"/v1/schedules/{schedule_id}/runs")

        assert response.status_code == 200
        results = response.json()["results"]
        assert len(results) == 2

        # Results are ordered by created_at desc, so run_without is first
        by_id = {r["id"]: r for r in results}
        assert by_id[str(run_with.id)]["conversation_id"] == conv_id
        assert by_id[str(run_without.id)]["conversation_id"] is None

    async def test_list_error_run_returns_null_conversation_id(
        self, client: httpx.AsyncClient, engine: AsyncEngine
    ) -> None:
        """Case 18 (null): failed run reports conversation_id as null (SC-002)."""
        schedule_id = await create_schedule(client)
        await seed_run(engine, schedule_id, status=ScheduleRunStatus.ERROR, conversation_id=None)

        response = await client.get(f"/v1/schedules/{schedule_id}/runs")

        assert response.status_code == 200
        results = response.json()["results"]
        assert results[0]["status"] == "error"
        assert results[0]["conversation_id"] is None

    async def test_list_in_progress_run_returns_null_conversation_id(
        self, client: httpx.AsyncClient, engine: AsyncEngine
    ) -> None:
        """In-progress run (no conversation yet) reports conversation_id as null."""
        schedule_id = await create_schedule(client)
        await seed_run(engine, schedule_id, status=ScheduleRunStatus.IN_PROGRESS, conversation_id=None)

        response = await client.get(f"/v1/schedules/{schedule_id}/runs")

        assert response.status_code == 200
        results = response.json()["results"]
        assert results[0]["status"] == "in_progress"
        assert results[0]["conversation_id"] is None


class TestGetRunConversationId:
    """GET /v1/schedules/{id}/runs/{run_id} — conversation_id field (cases 19, 20)."""

    async def test_get_run_returns_same_conversation_id_as_list(
        self, client: httpx.AsyncClient, engine: AsyncEngine
    ) -> None:
        """Case 19: GET run detail returns same conversation_id as the list entry (SC-005)."""
        schedule_id = await create_schedule(client)
        conv_id = CONV_ID.format(schedule_id=schedule_id, run_id=str(uuid.uuid4()))
        run = await seed_run(engine, schedule_id, conversation_id=conv_id)

        list_resp = await client.get(f"/v1/schedules/{schedule_id}/runs")
        detail_resp = await client.get(f"/v1/schedules/{schedule_id}/runs/{run.id}")

        assert list_resp.status_code == 200
        assert detail_resp.status_code == 200

        list_conv_id = list_resp.json()["results"][0]["conversation_id"]
        detail_conv_id = detail_resp.json()["conversation_id"]

        assert list_conv_id == detail_conv_id == conv_id

    async def test_listing_same_run_twice_returns_same_conversation_id(
        self, client: httpx.AsyncClient, engine: AsyncEngine
    ) -> None:
        """Case 20: repeated listing returns identical, unchanged conversation_id (FR-007, SC-005)."""
        schedule_id = await create_schedule(client)
        conv_id = CONV_ID.format(schedule_id=schedule_id, run_id=str(uuid.uuid4()))
        await seed_run(engine, schedule_id, conversation_id=conv_id)

        response1 = await client.get(f"/v1/schedules/{schedule_id}/runs")
        response2 = await client.get(f"/v1/schedules/{schedule_id}/runs")

        conv_id_1 = response1.json()["results"][0]["conversation_id"]
        conv_id_2 = response2.json()["results"][0]["conversation_id"]

        assert conv_id_1 == conv_id_2 == conv_id

    async def test_get_run_returns_null_conversation_id_when_not_set(
        self, client: httpx.AsyncClient, engine: AsyncEngine
    ) -> None:
        """Run detail returns null conversation_id when none was persisted (SC-002)."""
        schedule_id = await create_schedule(client)
        run = await seed_run(engine, schedule_id, conversation_id=None)

        response = await client.get(f"/v1/schedules/{schedule_id}/runs/{run.id}")

        assert response.status_code == 200
        assert response.json()["conversation_id"] is None
