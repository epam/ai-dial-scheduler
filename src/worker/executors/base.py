import asyncio
from collections.abc import AsyncIterator
from typing import Any, Protocol, TypeVar

import httpx

from src.lib.exceptions.errors import StreamIdleTimeoutError
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties


T = TypeVar("T")


class Executor(Protocol):
    async def execute(
        self, ctx: RunContext, credentials: OboCredentials, properties: ScheduleProperties
    ) -> dict[str, Any]: ...


def build_timeout(client: httpx.AsyncClient, read_seconds: float | None) -> httpx.Timeout | None:
    """Build a per-request timeout that overrides only the read phase.

    ``httpx.Timeout(x)`` with a single positional argument sets all four phases, which silently
    discards the connect timeout configured on the client. With the attempt ceiling sized as an
    hour-long backstop, that would leave an unreachable host hanging for the whole hour instead
    of failing within the configured connect timeout.

    Returns ``None`` when no read bound is set, so callers can omit the argument entirely and
    leave the client's own defaults in place.
    """
    if read_seconds is None:
        return None
    configured = client.timeout
    return httpx.Timeout(
        read=read_seconds,
        connect=configured.connect,
        write=configured.write,
        pool=configured.pool,
    )


async def iterate_with_idle_timeout(
    iterator: AsyncIterator[T], idle_timeout: float | None, ctx: RunContext
) -> AsyncIterator[T]:
    """Yield from ``iterator``, failing if any single step exceeds ``idle_timeout``.

    The bound is applied per item rather than to the stream as a whole, so it measures
    inactivity rather than total duration and resets on every item.

    It deliberately counts *parsed events*, not socket reads: the DIAL SDK emits SSE heartbeat
    pings precisely when a response is idle, and a transport-level read timeout would be reset
    by the very silence it is meant to detect. Heartbeat comments are discarded by the parsers
    above this function and never arrive here, so a target that pings while making no progress
    is still caught.
    """
    if idle_timeout is None:
        async for item in iterator:
            yield item
        return

    while True:
        try:
            async with asyncio.timeout(idle_timeout) as scope:
                item = await anext(iterator)
        except StopAsyncIteration:
            return
        except TimeoutError as exc:
            # Since 3.10 TimeoutError is also socket.timeout, so a transport-level timeout
            # raised by the target could land here and be mislabelled as inactivity. Only
            # claim an idle expiry when this scope is the one that fired.
            if not scope.expired():
                raise
            raise StreamIdleTimeoutError(
                f"stream produced no event for {idle_timeout}s",
                schedule_id=ctx.schedule_id,
                run_id=ctx.run_id,
            ) from exc
        yield item
