"""Integration tests for /v1/schedules/{id}/runs endpoints.

Exercises the full HTTP → service → repository → database stack.
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

from src.api.dependencies.db import get_db_session
from src.api.dependencies.scheduler import get_scheduler
from src.api.v1.endpoints.runs import router as runs_router
from src.api.v1.endpoints.schedules import router as schedules_router
from src.lib.db.enums import ScheduleRunStatus
from src.lib.models.schedule import Schedule
from src.lib.models.schedule_run import ScheduleRun


pytestmark = pytest.mark.asyncio(loop_scope="module")


# ---------------------------------------------------------------------------
# App + client fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def test_app(engine: AsyncEngine) -> FastAPI:
    app = FastAPI(redirect_slashes=False)
    app.include_router(schedules_router, prefix="/v1")
    app.include_router(runs_router, prefix="/v1")

    async def override_db() -> Any:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db
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
        await session.execute(sa.delete(ScheduleRun))
        await session.execute(sa.delete(Schedule))
        await session.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


CRON_REQUEST: dict[str, Any] = {
    "display_name": "Run List Test Schedule",
    "trigger": {"cron": {"fields": {"hour": 9, "minute": 0}}},
    "properties": {
        "target_type": "chat_completion",
        "url": "https://core.dial/openai/deployments/gpt-4o/chat/completions",
    },
}


async def create_schedule(client: httpx.AsyncClient, display_name: str = "Run List Test Schedule") -> str:
    resp = await client.post("/v1/schedules/", json={**CRON_REQUEST, "display_name": display_name})
    assert resp.status_code == 201
    return resp.json()["id"]


async def seed_run(
    engine: AsyncEngine,
    schedule_id: str,
    status: ScheduleRunStatus = ScheduleRunStatus.SUCCESS,
    result: dict[str, Any] | None = None,
) -> ScheduleRun:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        run = ScheduleRun(
            status=status.value,
            end_time=None,
            result=result,
            schedule_id=schedule_id,
            schedule_snapshot={"target_type": "chat_completion"},
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run


# ---------------------------------------------------------------------------
# GET /v1/schedules/{id}/runs  (US1: Browse Run History)
# ---------------------------------------------------------------------------


class TestListRunsApi:
    async def test_list_returns_200_with_paginated_body(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        schedule_id = await create_schedule(client)

        response = await client.get(f"/v1/schedules/{schedule_id}/runs")

        assert response.status_code == 200
        body = response.json()
        assert "count" in body
        assert "limit" in body
        assert "offset" in body
        assert "results" in body

    async def test_list_empty_schedule_returns_200_with_zero_count(
        self, client: httpx.AsyncClient, engine: AsyncEngine
    ) -> None:
        schedule_id = await create_schedule(client)

        response = await client.get(f"/v1/schedules/{schedule_id}/runs")

        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 0
        assert body["results"] == []

    async def test_list_returns_runs_in_created_at_desc_order(
        self, client: httpx.AsyncClient, engine: AsyncEngine
    ) -> None:
        schedule_id = await create_schedule(client)
        run1 = await seed_run(engine, schedule_id)
        await seed_run(engine, schedule_id)
        run3 = await seed_run(engine, schedule_id)

        response = await client.get(f"/v1/schedules/{schedule_id}/runs")

        body = response.json()
        assert body["count"] == 3
        ids = [r["id"] for r in body["results"]]
        # newest run first (run3 inserted last)
        assert ids[0] == str(run3.id)
        assert ids[-1] == str(run1.id)

    async def test_list_result_items_have_correct_shape(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        schedule_id = await create_schedule(client)
        await seed_run(engine, schedule_id)

        response = await client.get(f"/v1/schedules/{schedule_id}/runs")

        item = response.json()["results"][0]
        assert "id" in item
        assert "status" in item
        assert "start_time" in item
        assert "end_time" in item

    async def test_list_pagination_second_page_navigates_correctly(
        self, client: httpx.AsyncClient, engine: AsyncEngine
    ) -> None:
        schedule_id = await create_schedule(client)
        for _ in range(4):
            await seed_run(engine, schedule_id)

        response = await client.get(f"/v1/schedules/{schedule_id}/runs?limit=2&offset=0")
        body = response.json()
        assert len(body["results"]) == 2
        assert body["next"] is not None

        response2 = await client.get(f"/v1/schedules/{schedule_id}/runs?limit=2&offset=2")
        body2 = response2.json()
        assert len(body2["results"]) == 2

        ids_page1 = {r["id"] for r in body["results"]}
        ids_page2 = {r["id"] for r in body2["results"]}
        assert ids_page1.isdisjoint(ids_page2)

    async def test_list_nonexistent_schedule_returns_404(self, client: httpx.AsyncClient) -> None:
        fake_id = str(uuid.uuid4())
        response = await client.get(f"/v1/schedules/{fake_id}/runs")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /v1/schedules/{id}/runs/{run_id}  (US2: Inspect a Single Run)
# ---------------------------------------------------------------------------


class TestGetRunApi:
    async def test_get_success_run_returns_result(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        schedule_id = await create_schedule(client)
        run = await seed_run(engine, schedule_id, result={"status_code": 200})

        response = await client.get(f"/v1/schedules/{schedule_id}/runs/{run.id}")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(run.id)
        assert body["result"] == {"status_code": 200}

    async def test_get_failed_run_returns_error_text(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        schedule_id = await create_schedule(client)
        run = await seed_run(engine, schedule_id, status=ScheduleRunStatus.ERROR, result={"error_text": "timeout"})

        response = await client.get(f"/v1/schedules/{schedule_id}/runs/{run.id}")

        assert response.status_code == 200
        body = response.json()
        assert body["result"]["error_text"] == "timeout"

    async def test_get_in_progress_run_has_null_end_time(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        schedule_id = await create_schedule(client)
        run = await seed_run(engine, schedule_id, status=ScheduleRunStatus.IN_PROGRESS)

        response = await client.get(f"/v1/schedules/{schedule_id}/runs/{run.id}")

        assert response.status_code == 200
        assert response.json()["end_time"] is None

    async def test_get_includes_embedded_schedule_fields(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        schedule_id = await create_schedule(client, display_name="Embed Test Schedule")
        run = await seed_run(engine, schedule_id)

        response = await client.get(f"/v1/schedules/{schedule_id}/runs/{run.id}")

        body = response.json()
        assert body["schedule"]["display_name"] == "Embed Test Schedule"
        assert "trigger" in body["schedule"]
        assert "properties" in body["schedule"]

    async def test_get_wrong_run_id_returns_404(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        schedule_id = await create_schedule(client)
        fake_run_id = uuid.uuid4()

        response = await client.get(f"/v1/schedules/{schedule_id}/runs/{fake_run_id}")

        assert response.status_code == 404

    async def test_get_malformed_uuid_returns_422(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        schedule_id = await create_schedule(client)

        response = await client.get(f"/v1/schedules/{schedule_id}/runs/not-a-uuid")

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# US3: Ownership enforcement
# ---------------------------------------------------------------------------


class TestOwnershipEnforcementApi:
    async def test_list_cross_owner_schedule_returns_404(self, client: httpx.AsyncClient) -> None:
        fake_id = str(uuid.uuid4())
        response = await client.get(f"/v1/schedules/{fake_id}/runs")
        assert response.status_code == 404

    async def test_detail_cross_owner_schedule_returns_404(self, client: httpx.AsyncClient) -> None:
        fake_schedule_id = str(uuid.uuid4())
        fake_run_id = uuid.uuid4()
        response = await client.get(f"/v1/schedules/{fake_schedule_id}/runs/{fake_run_id}")
        assert response.status_code == 404

    async def test_run_from_different_schedule_returns_404(
        self, client: httpx.AsyncClient, engine: AsyncEngine
    ) -> None:
        schedule_a_id = await create_schedule(client, display_name="Schedule A")
        schedule_b_id = await create_schedule(client, display_name="Schedule B")
        run_b = await seed_run(engine, schedule_b_id)

        response = await client.get(f"/v1/schedules/{schedule_a_id}/runs/{run_b.id}")

        assert response.status_code == 404

    async def test_nonexistent_schedule_and_missing_schedule_return_identical_404(
        self, client: httpx.AsyncClient
    ) -> None:
        fake_id_1 = str(uuid.uuid4())
        fake_id_2 = str(uuid.uuid4())

        resp1 = await client.get(f"/v1/schedules/{fake_id_1}/runs")
        resp2 = await client.get(f"/v1/schedules/{fake_id_2}/runs")

        assert resp1.status_code == 404
        assert resp2.status_code == 404
