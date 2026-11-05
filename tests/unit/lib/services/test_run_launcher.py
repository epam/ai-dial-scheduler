import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from src.lib.exceptions.errors import ScheduleRunError
from src.lib.schemas.run import RunTimeouts
from src.lib.services.run_launcher import BackgroundRunLauncher


RUN_ID = "run-on-demand-001"
SCHEDULE_ID = "sched-on-demand-001"
SNAPSHOT = {"target_type": "rest", "method": "POST", "url": "https://example.com/api"}


async def _hang(ctx, snapshot):
    """Stand-in for a run that never finishes, so drain has to cancel it."""
    await asyncio.Event().wait()


@pytest.fixture
def http_client(mocker):
    return mocker.MagicMock(spec=httpx.AsyncClient)


@pytest.fixture
def launcher(http_client):
    return BackgroundRunLauncher(
        http_client,
        timeouts=RunTimeouts(max_duration=300.0, streaming_max_duration=900.0, idle_timeout=20.0),
    )


class TestLaunch:
    async def test_executes_run_with_saved_snapshot(self, mocker, launcher, http_client):
        execute = mocker.patch("src.lib.services.run_launcher.execute_run", new_callable=AsyncMock)

        launcher.launch(RUN_ID, SCHEDULE_ID, SNAPSHOT)
        await launcher.drain()

        execute.assert_awaited_once()
        ctx, snapshot = execute.await_args.args
        assert snapshot == SNAPSHOT
        assert ctx.run_id == RUN_ID
        assert ctx.schedule_id == SCHEDULE_ID

    async def test_run_uses_the_api_client_not_the_worker_singleton(self, mocker, launcher, http_client) -> None:
        """The run must execute with the API's own client, since the worker is not involved."""
        execute = mocker.patch("src.lib.services.run_launcher.execute_run", new_callable=AsyncMock)

        launcher.launch(RUN_ID, SCHEDULE_ID, SNAPSHOT)
        await launcher.drain()

        ctx, _ = execute.await_args.args
        assert ctx.http_client is http_client

    async def test_run_carries_the_api_process_ceiling(self, mocker, launcher) -> None:
        """The API's own bounds must reach the orchestrator; the worker's are irrelevant here.

        Covers the idle bound too: it is per-process configuration, so an API-side value must
        not be silently replaced by the worker's.
        """
        execute = mocker.patch("src.lib.services.run_launcher.execute_run", new_callable=AsyncMock)

        launcher.launch(RUN_ID, SCHEDULE_ID, SNAPSHOT)
        await launcher.drain()

        ctx, _ = execute.await_args.args
        assert ctx.timeouts is not None
        assert ctx.timeouts.max_duration == 300.0
        assert ctx.timeouts.streaming_max_duration == 900.0
        assert ctx.timeouts.idle_timeout == 20.0

    async def test_returns_before_the_run_finishes(self, mocker, launcher):
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_run(ctx, snapshot):
            started.set()
            await release.wait()

        mocker.patch("src.lib.services.run_launcher.execute_run", side_effect=slow_run)

        launcher.launch(RUN_ID, SCHEDULE_ID, SNAPSHOT)
        await started.wait()
        assert launcher.in_flight == 1

        release.set()
        await launcher.drain()
        assert launcher.in_flight == 0

    async def test_run_failure_is_swallowed(self, mocker, launcher):
        """execute_run already persisted and logged the failure; nothing awaits this task."""
        mocker.patch(
            "src.lib.services.run_launcher.execute_run",
            side_effect=ScheduleRunError("boom", schedule_id=SCHEDULE_ID, run_id=RUN_ID),
        )

        launcher.launch(RUN_ID, SCHEDULE_ID, SNAPSHOT)
        await launcher.drain()

        assert launcher.in_flight == 0

    async def test_concurrent_runs_are_allowed(self, mocker, launcher):
        execute = mocker.patch("src.lib.services.run_launcher.execute_run", new_callable=AsyncMock)

        launcher.launch("run-a", SCHEDULE_ID, SNAPSHOT)
        launcher.launch("run-b", SCHEDULE_ID, SNAPSHOT)
        await launcher.drain()

        assert execute.await_count == 2


class TestDrain:
    async def test_waits_for_an_in_flight_run_to_finish(self, mocker, launcher):
        finished = []

        async def slow_run(ctx, snapshot):
            await asyncio.sleep(0.01)
            finished.append(ctx.run_id)

        mocker.patch("src.lib.services.run_launcher.execute_run", side_effect=slow_run)

        launcher.launch(RUN_ID, SCHEDULE_ID, SNAPSHOT)
        await launcher.drain()

        assert finished == [RUN_ID]

    async def test_is_a_noop_without_runs(self, launcher):
        await launcher.drain()
        assert launcher.in_flight == 0

    async def test_hung_run_is_failed_then_cancelled(self, mocker, launcher):
        """CancelledError derives from BaseException, so execute_run cannot record it itself."""
        mocker.patch("src.lib.services.run_launcher.execute_run", side_effect=_hang)
        mocker.patch("src.lib.services.run_launcher.Database")
        repo = mocker.AsyncMock()
        mocker.patch("src.lib.services.run_launcher.ScheduleRunRepository", return_value=repo)

        launcher.launch(RUN_ID, SCHEDULE_ID, SNAPSHOT)
        await launcher.drain(timeout=0.01)

        repo.mark_error.assert_awaited_once()
        run_id, error_text = repo.mark_error.await_args.args
        assert run_id == RUN_ID
        assert "shut down" in error_text
        assert launcher.in_flight == 0

    async def test_persist_failure_while_cancelling_does_not_raise(self, mocker, launcher):
        mocker.patch("src.lib.services.run_launcher.execute_run", side_effect=_hang)
        mocker.patch("src.lib.services.run_launcher.Database", side_effect=RuntimeError("db gone"))

        launcher.launch(RUN_ID, SCHEDULE_ID, SNAPSHOT)
        await launcher.drain(timeout=0.01)

        assert launcher.in_flight == 0
