import asyncio
from pathlib import Path
import time

from src.lib.services.health_check import check_database
from src.lib.settings import get_settings
from src.lib.utils.logging import logger


def _health_dir() -> Path:
    return Path(get_settings().worker_health_dir)


def _heartbeat_path() -> Path:
    return _health_dir() / "heartbeat"


def _started_path() -> Path:
    return _health_dir() / "started"


def _ready_path() -> Path:
    return _health_dir() / "ready"


def _touch(path: Path) -> None:
    path.touch(exist_ok=True)


def _unlink(path: Path) -> None:
    path.unlink(missing_ok=True)


def _mtime_age_seconds(path: Path) -> float | None:
    if not path.exists():
        return None
    return time.time() - path.stat().st_mtime


async def touch_heartbeat() -> None:
    """Mark the worker's scheduling loop as having made forward progress."""
    await asyncio.to_thread(_touch, _heartbeat_path())


async def is_heartbeat_fresh(staleness_seconds: float) -> bool:
    """Return True if the heartbeat marker exists and was updated within *staleness_seconds*."""
    age = await asyncio.to_thread(_mtime_age_seconds, _heartbeat_path())
    if age is None:
        return False
    return age <= staleness_seconds


async def clear_heartbeat() -> None:
    """Remove any stale heartbeat marker left over from a previous crash in this container."""
    await asyncio.to_thread(_unlink, _heartbeat_path())


async def mark_started() -> None:
    """Mark that the worker has completed startup (scheduler running, heartbeat job registered)."""
    await asyncio.to_thread(_touch, _started_path())


async def is_started() -> bool:
    """Return True once the worker has completed startup."""
    return await asyncio.to_thread(_started_path().exists)


async def clear_started() -> None:
    """Remove any stale started marker left over from a previous crash in this container."""
    await asyncio.to_thread(_unlink, _started_path())


async def mark_ready() -> None:
    """Mark the worker as ready to do useful work (started and database reachable)."""
    await asyncio.to_thread(_touch, _ready_path())


async def mark_not_ready() -> None:
    """Mark the worker as not ready (starting up, database unreachable, or shutting down)."""
    await asyncio.to_thread(_unlink, _ready_path())


async def clear_ready() -> None:
    """Remove any stale ready marker left over from a previous crash in this container."""
    await asyncio.to_thread(_unlink, _ready_path())


async def readiness_poller(stop_event: asyncio.Event, interval_seconds: float, db_timeout_seconds: float) -> None:
    """Periodically check database connectivity and maintain the `ready` marker accordingly.

    Only checks once the worker has completed startup (`is_started()`), since readiness is
    not-ready by definition until then.
    """
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass
        if stop_event.is_set():
            break
        if not await is_started():
            continue
        if await check_database(db_timeout_seconds):
            await mark_ready()
        else:
            logger.warning("worker_readiness_check_failed")
            await mark_not_ready()
