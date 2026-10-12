import asyncio
from collections.abc import AsyncIterator
import time

import httpx
import pytest

from src.lib.exceptions.errors import ScheduleRunError, StreamIdleTimeoutError
from src.lib.schemas.run import RunContext
from src.worker.executors.base import build_timeout, iterate_with_idle_timeout


CTX = RunContext(run_id="run-1", schedule_id="sched-1")


def make_client(mocker, timeout: httpx.Timeout) -> httpx.AsyncClient:
    client = mocker.AsyncMock(spec=httpx.AsyncClient)
    client.timeout = timeout
    return client


async def spaced(gaps: list[float]) -> AsyncIterator[str]:
    """Emit one event per entry, waiting that many seconds before each."""
    for index, gap in enumerate(gaps):
        await asyncio.sleep(gap)
        yield f"event-{index}"


class TestBuildTimeout:
    def test_returns_none_without_a_read_bound(self, mocker):
        client = make_client(mocker, httpx.Timeout(60.0, connect=5.0))

        assert build_timeout(client, None) is None

    def test_overrides_only_the_read_phase(self, mocker):
        client = make_client(mocker, httpx.Timeout(60.0, connect=5.0, write=7.0, pool=8.0))

        timeout = build_timeout(client, 3600.0)

        assert timeout is not None
        assert timeout.read == 3600.0
        assert timeout.connect == 5.0
        assert timeout.write == 7.0
        assert timeout.pool == 8.0


class TestIterateWithIdleTimeout:
    async def test_yields_every_item_within_the_bound(self):
        items = [item async for item in iterate_with_idle_timeout(spaced([0, 0, 0]), 1.0, CTX)]

        assert items == ["event-0", "event-1", "event-2"]

    async def test_total_duration_may_exceed_the_bound(self):
        """The reported incident: a healthy stream that runs longer in total than the idle
        bound must not be cut off, as long as no single gap exceeds it.

        Ten 20ms gaps against a 100ms bound: every gap clears it five times over, while the
        total runs to twice the bound. The elapsed assertion keeps the test honest -- without
        it, widening the margins could quietly make the premise false.
        """
        started = time.monotonic()

        items = [item async for item in iterate_with_idle_timeout(spaced([0.02] * 10), 0.1, CTX)]

        elapsed = time.monotonic() - started
        assert len(items) == 10
        assert elapsed > 0.1, "premise broken: the stream did not outlive the idle bound"

    async def test_raises_when_a_single_gap_exceeds_the_bound(self):
        """A target that has stopped making progress. Heartbeat pings are discarded by the SSE
        parser above this function and never arrive as events, so the silence is visible here
        even while the socket stays busy."""
        with pytest.raises(ScheduleRunError, match="stream produced no event for 0.05s"):
            async for _ in iterate_with_idle_timeout(spaced([0, 0.5]), 0.05, CTX):
                pass

    async def test_error_carries_correlation_ids_and_no_status(self):
        with pytest.raises(StreamIdleTimeoutError) as excinfo:
            async for _ in iterate_with_idle_timeout(spaced([0.5]), 0.05, CTX):
                pass

        assert excinfo.value.schedule_id == "sched-1"
        assert excinfo.value.run_id == "run-1"
        # No status code: an idle expiry is terminal and must never be retried.
        assert excinfo.value.status_code is None
        # Still a ScheduleRunError, so existing handling keeps working.
        assert isinstance(excinfo.value, ScheduleRunError)

    async def test_foreign_timeout_error_is_not_relabelled(self):
        """TimeoutError is also socket.timeout since 3.10. A transport timeout raised by the
        target must surface as itself, not as a false claim about inactivity."""

        async def raises_foreign_timeout() -> AsyncIterator[str]:
            raise TimeoutError("connection timed out")
            yield  # pragma: no cover

        with pytest.raises(TimeoutError) as excinfo:
            async for _ in iterate_with_idle_timeout(raises_foreign_timeout(), 5.0, CTX):
                pass

        assert not isinstance(excinfo.value, StreamIdleTimeoutError)

    async def test_passthrough_when_no_bound_is_set(self):
        items = [item async for item in iterate_with_idle_timeout(spaced([0.02]), None, CTX)]

        assert items == ["event-0"]

    async def test_empty_stream_terminates(self):
        async def empty() -> AsyncIterator[str]:
            return
            yield

        assert [item async for item in iterate_with_idle_timeout(empty(), 1.0, CTX)] == []
