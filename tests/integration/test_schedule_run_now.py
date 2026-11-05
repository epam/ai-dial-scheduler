"""Integration tests for POST /v1/schedules/{schedule_id}/run against a real PostgreSQL database.

Exercises the full HTTP → service → repository → database stack for on-demand runs: the run row
is written before the response is sent, the stored schedule definition is the only input, and the
scheduler is never touched so the schedule's next fire time cannot move.

The DIAL-facing part of execution (on-behalf-of credentials, the target request) is out of scope
here and is replaced by a stub; what is proven is the database round trip, the ordering, and the
isolation from the schedule itself.

Requires TEST_DB_URL to be set; all tests are skipped otherwise.
"""

import asyncio
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
from src.api.dependencies.runs import get_run_launcher
from src.api.dependencies.scheduler import get_scheduler
from src.api.dependencies.sign_in import get_sign_in_service
from src.api.v1.endpoints.runs import router as runs_router
from src.api.v1.endpoints.schedules import router as schedules_router
from src.lib.db.enums import ScheduleRunStatus
from src.lib.models.apscheduler_job import apscheduler_jobs
from src.lib.models.schedule import Schedule
from src.lib.models.schedule_run import ScheduleRun
from src.lib.repositories.schedule_run_repository import ScheduleRunRepository
from src.lib.schemas.run import RunTimeouts
from src.lib.services.run_launcher import BackgroundRunLauncher
from tests.integration.conftest import make_signed_in_service


pytestmark = pytest.mark.asyncio(loop_scope="module")


OWNER = "mock_user_id"
OTHER_OWNER = "someone-else@example.com"
NEXT_RUN_EPOCH = 1800000000.0
SAVED_KWARGS: dict[str, Any] = {
    "target_type": "rest",
    "method": "POST",
    "url": "https://example.com/target",
    "payload": {"saved": True},
    "extra_headers": {"X-Saved": "yes"},
}


@pytest.fixture
def launcher() -> MagicMock:
    """Stands in for the background launcher so no DIAL call is attempted."""
    return MagicMock()


@pytest.fixture
def scheduler() -> MagicMock:
    return MagicMock()


@pytest.fixture
def test_app(engine: AsyncEngine, launcher: MagicMock, scheduler: MagicMock) -> FastAPI:
    app = FastAPI()
    app.include_router(schedules_router, prefix="/v1")
    app.include_router(runs_router, prefix="/v1")

    async def override_db() -> Any:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    async def override_user_id() -> str:
        return OWNER

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_user_id] = override_user_id
    app.dependency_overrides[get_sign_in_service] = make_signed_in_service
    app.dependency_overrides[get_scheduler] = lambda: scheduler
    app.dependency_overrides[get_run_launcher] = lambda: launcher
    return app


@pytest_asyncio.fixture(loop_scope="module")
async def client(test_app: FastAPI) -> AsyncGenerator[httpx.AsyncClient, None]:
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
        await session.execute(sa.delete(apscheduler_jobs))
        await session.commit()


async def _insert_schedule(
    engine: AsyncEngine,
    *,
    created_by: str = OWNER,
    is_deleted: bool = False,
    with_job: bool = True,
    kwargs: dict[str, Any] | None = None,
) -> str:
    sched_id = f"run-now-{uuid.uuid4()}"
    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add(
            Schedule(
                id=sched_id,
                display_name="On-demand target",
                created_by=created_by,
                service_id="dial-native",
                trigger={"cron": {"fields": {"hour": 20, "minute": 0}}},
                kwargs=dict(kwargs if kwargs is not None else SAVED_KWARGS),
                is_deleted=is_deleted,
            )
        )
        if with_job:
            await session.execute(apscheduler_jobs.insert().values(id=sched_id, next_run_time=NEXT_RUN_EPOCH))
        await session.commit()
    return sched_id


async def _fetch_runs(engine: AsyncEngine, sched_id: str) -> list[ScheduleRun]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            sa.select(ScheduleRun).where(ScheduleRun.schedule_id == sched_id).order_by(ScheduleRun.created_at)
        )
        return list(result.scalars().all())


class TestRunRecordCreation:
    async def test_returns_202_with_an_in_progress_run(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        sched_id = await _insert_schedule(engine)

        response = await client.post(f"/v1/schedules/{sched_id}/run")

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == ScheduleRunStatus.IN_PROGRESS
        assert body["end_time"] is None
        assert body["conversation_id"] is None
        uuid.UUID(body["id"])

    async def test_row_is_persisted_before_the_response(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        """The returned id must already be resolvable, so the caller can poll it immediately."""
        sched_id = await _insert_schedule(engine)

        run_id = (await client.post(f"/v1/schedules/{sched_id}/run")).json()["id"]

        rows = await _fetch_runs(engine, sched_id)
        assert [str(r.id) for r in rows] == [run_id]
        assert rows[0].status == ScheduleRunStatus.IN_PROGRESS
        assert rows[0].end_time is None
        assert rows[0].result is None

    async def test_snapshot_is_the_stored_definition(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        sched_id = await _insert_schedule(engine)

        await client.post(f"/v1/schedules/{sched_id}/run")

        rows = await _fetch_runs(engine, sched_id)
        assert rows[0].schedule_snapshot == SAVED_KWARGS

    async def test_snapshot_carries_both_timeout_bounds(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        """FR-012: both bounds ride kwargs into the snapshot unchanged, with no migration."""
        stored = {**SAVED_KWARGS, "timeout": 1800, "idle_timeout": 90}
        sched_id = await _insert_schedule(engine, kwargs=stored)

        await client.post(f"/v1/schedules/{sched_id}/run")

        rows = await _fetch_runs(engine, sched_id)
        assert rows[0].schedule_snapshot["timeout"] == 1800
        assert rows[0].schedule_snapshot["idle_timeout"] == 90

    async def test_request_body_cannot_override_the_stored_definition(
        self, client: httpx.AsyncClient, engine: AsyncEngine
    ) -> None:
        sched_id = await _insert_schedule(engine)

        response = await client.post(
            f"/v1/schedules/{sched_id}/run",
            json={"properties": {"url": "https://attacker.example", "target_type": "rest"}},
        )

        assert response.status_code == 202
        rows = await _fetch_runs(engine, sched_id)
        assert rows[0].schedule_snapshot == SAVED_KWARGS

    async def test_run_is_visible_through_the_runs_api(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        sched_id = await _insert_schedule(engine)
        run_id = (await client.post(f"/v1/schedules/{sched_id}/run")).json()["id"]

        listed = (await client.get(f"/v1/schedules/{sched_id}/runs")).json()
        detail = (await client.get(f"/v1/schedules/{sched_id}/runs/{run_id}")).json()

        assert listed["count"] == 1
        assert listed["results"][0]["id"] == run_id
        assert detail["id"] == run_id
        assert detail["schedule"]["properties"] == SAVED_KWARGS


class TestScheduleIsUntouched:
    async def test_next_run_time_does_not_move(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        sched_id = await _insert_schedule(engine)
        before = (await client.get(f"/v1/schedules/{sched_id}")).json()["next_run_time"]

        await client.post(f"/v1/schedules/{sched_id}/run")

        after = (await client.get(f"/v1/schedules/{sched_id}")).json()["next_run_time"]
        assert after == before
        async with AsyncSession(engine, expire_on_commit=False) as session:
            stored = await session.scalar(
                sa.select(apscheduler_jobs.c.next_run_time).where(apscheduler_jobs.c.id == sched_id)
            )
        assert stored == NEXT_RUN_EPOCH

    async def test_schedule_row_is_not_modified(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        sched_id = await _insert_schedule(engine)
        before = (await client.get(f"/v1/schedules/{sched_id}")).json()

        await client.post(f"/v1/schedules/{sched_id}/run")

        after = (await client.get(f"/v1/schedules/{sched_id}")).json()
        assert after == before

    async def test_scheduler_is_never_called(
        self, client: httpx.AsyncClient, engine: AsyncEngine, scheduler: MagicMock
    ) -> None:
        sched_id = await _insert_schedule(engine)

        await client.post(f"/v1/schedules/{sched_id}/run")

        assert scheduler.mock_calls == []


class TestConcurrentRuns:
    async def test_an_in_progress_run_does_not_block_another(
        self, client: httpx.AsyncClient, engine: AsyncEngine
    ) -> None:
        sched_id = await _insert_schedule(engine)

        first = await client.post(f"/v1/schedules/{sched_id}/run")
        second = await client.post(f"/v1/schedules/{sched_id}/run")

        assert (first.status_code, second.status_code) == (202, 202)
        rows = await _fetch_runs(engine, sched_id)
        assert len(rows) == 2
        assert {r.status for r in rows} == {ScheduleRunStatus.IN_PROGRESS}
        assert first.json()["id"] != second.json()["id"]


class TestRejections:
    async def test_unknown_schedule_returns_404(self, client: httpx.AsyncClient, launcher: MagicMock) -> None:
        response = await client.post(f"/v1/schedules/{uuid.uuid4()}/run")

        assert response.status_code == 404
        launcher.launch.assert_not_called()

    async def test_other_owners_schedule_returns_404(
        self, client: httpx.AsyncClient, engine: AsyncEngine, launcher: MagicMock
    ) -> None:
        sched_id = await _insert_schedule(engine, created_by=OTHER_OWNER)

        response = await client.post(f"/v1/schedules/{sched_id}/run")

        assert response.status_code == 404
        assert await _fetch_runs(engine, sched_id) == []
        launcher.launch.assert_not_called()

    async def test_soft_deleted_schedule_returns_409(
        self, client: httpx.AsyncClient, engine: AsyncEngine, launcher: MagicMock
    ) -> None:
        sched_id = await _insert_schedule(engine, is_deleted=True)

        response = await client.post(f"/v1/schedules/{sched_id}/run")

        assert response.status_code == 409
        assert await _fetch_runs(engine, sched_id) == []
        launcher.launch.assert_not_called()


class TestLauncherHandoff:
    async def test_launched_with_the_persisted_run_and_snapshot(
        self, client: httpx.AsyncClient, engine: AsyncEngine, launcher: MagicMock
    ) -> None:
        sched_id = await _insert_schedule(engine)

        run_id = (await client.post(f"/v1/schedules/{sched_id}/run")).json()["id"]

        launcher.launch.assert_called_once_with(run_id, sched_id, SAVED_KWARGS)


class TestBackgroundCompletion:
    """The real launcher, with only the DIAL-facing execution replaced."""

    async def test_run_reaches_a_terminal_status_after_the_response(
        self, test_app: FastAPI, engine: AsyncEngine, mocker
    ) -> None:
        sched_id = await _insert_schedule(engine)
        released = asyncio.Event()

        async def fake_execute_run(ctx, snapshot):
            await released.wait()
            async with AsyncSession(engine, expire_on_commit=False) as session:
                await ScheduleRunRepository(session).mark_success(ctx.run_id, {"snapshot_seen": snapshot})

        mocker.patch("src.lib.services.run_launcher.execute_run", side_effect=fake_execute_run)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=test_app), base_url="http://test"
        ) as http_client:
            real_launcher = BackgroundRunLauncher(
                http_client,
                timeouts=RunTimeouts(max_duration=300.0, streaming_max_duration=3600.0, idle_timeout=60.0),
            )
            test_app.dependency_overrides[get_run_launcher] = lambda: real_launcher

            run_id = (await http_client.post(f"/v1/schedules/{sched_id}/run")).json()["id"]

            # The response landed while execution is still blocked: it did not wait for the run.
            rows = await _fetch_runs(engine, sched_id)
            assert rows[0].status == ScheduleRunStatus.IN_PROGRESS
            assert real_launcher.in_flight == 1

            released.set()
            await real_launcher.drain()

        rows = await _fetch_runs(engine, sched_id)
        assert str(rows[0].id) == run_id
        assert rows[0].status == ScheduleRunStatus.SUCCESS
        assert rows[0].end_time is not None
        assert rows[0].result == {"snapshot_seen": SAVED_KWARGS}
