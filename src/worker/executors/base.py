import asyncio
from collections.abc import AsyncIterator
from typing import Any, Protocol, TypeVar

import httpx

from src.lib.constants import SDK_AUTH_HEADERS, USER_AGENT_HEADER
from src.lib.exceptions.errors import StreamIdleTimeoutError
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.lib.utils.headers import without_headers


T = TypeVar("T")


class Executor(Protocol):
    async def execute(
        self, ctx: RunContext, credentials: OboCredentials, properties: ScheduleProperties
    ) -> dict[str, Any]: ...


def http_request_headers(credentials: OboCredentials, properties: ScheduleProperties) -> dict[str, str]:
    """Build the headers for a direct HTTP target (REST, SSE): caller headers plus the OBO header.

    Caller-supplied ``User-Agent`` and any entry matching the OBO header name are dropped regardless
    of casing. A plain dict merge is case-sensitive, so ``authorization`` in ``extra_headers`` would
    otherwise be sent next to the injected ``Authorization`` and a target reading the first value
    would authenticate as the caller's identity instead of the schedule owner.

    Args:
        credentials: The run owner's on-behalf-of credentials.
        properties: The run's schedule properties (source of ``extra_headers``).

    Returns:
        The request headers; the OBO header is always present exactly once.
    """
    caller = without_headers(properties.extra_headers, (USER_AGENT_HEADER, credentials.header_name))
    return {**caller, credentials.header_name: credentials.header_value.get_secret_value()}


def sdk_extra_headers(credentials: OboCredentials, properties: ScheduleProperties) -> dict[str, str]:
    """Return the caller headers that may be passed to the OpenAI SDK as ``extra_headers``.

    The SDK emits the OBO value itself (``api-key``) and merges ``extra_headers`` case-sensitively, so
    a caller copy of an auth header in any casing would be sent alongside it. Those, the OBO header
    name and ``User-Agent`` are dropped.

    Args:
        credentials: The run owner's on-behalf-of credentials.
        properties: The run's schedule properties (source of ``extra_headers``).

    Returns:
        The sanitized caller headers.
    """
    return without_headers(properties.extra_headers, (USER_AGENT_HEADER, credentials.header_name, *SDK_AUTH_HEADERS))


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
