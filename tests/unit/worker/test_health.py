"""Unit tests for the worker's filesystem-based health markers (src.worker.health)."""

import asyncio
import time

import pytest

from src.worker.health import (
    clear_heartbeat,
    is_heartbeat_fresh,
    is_started,
    mark_not_ready,
    mark_ready,
    mark_started,
    touch_heartbeat,
)


@pytest.fixture(autouse=True)
def health_dir(tmp_path, mocker):
    settings = mocker.MagicMock()
    settings.worker_health_dir = str(tmp_path)
    mocker.patch("src.worker.health.get_settings", return_value=settings)
    return tmp_path


class TestTouchHeartbeat:
    async def test_creates_file_if_absent(self, health_dir):
        heartbeat_path = health_dir / "heartbeat"
        assert not heartbeat_path.exists()

        await touch_heartbeat()

        assert heartbeat_path.exists()

    async def test_updates_mtime_if_present(self, health_dir):
        heartbeat_path = health_dir / "heartbeat"
        await touch_heartbeat()
        old_mtime = heartbeat_path.stat().st_mtime

        time.sleep(0.01)
        await touch_heartbeat()

        assert heartbeat_path.stat().st_mtime >= old_mtime


class TestIsHeartbeatFresh:
    async def test_true_when_within_staleness_window(self, health_dir):
        await touch_heartbeat()

        assert await is_heartbeat_fresh(staleness_seconds=180.0) is True

    async def test_false_when_stale(self, health_dir):
        heartbeat_path = health_dir / "heartbeat"
        heartbeat_path.touch()
        old_time = time.time() - 200
        import os

        os.utime(heartbeat_path, (old_time, old_time))

        assert await is_heartbeat_fresh(staleness_seconds=180.0) is False

    async def test_false_when_file_missing(self, health_dir):
        assert await is_heartbeat_fresh(staleness_seconds=180.0) is False


class TestClearHeartbeat:
    async def test_removes_existing_file(self, health_dir):
        await touch_heartbeat()
        heartbeat_path = health_dir / "heartbeat"
        assert heartbeat_path.exists()

        await clear_heartbeat()

        assert not heartbeat_path.exists()

    async def test_noop_when_file_missing(self, health_dir):
        await clear_heartbeat()  # should not raise


class TestStartedMarker:
    async def test_is_started_false_before_mark(self, health_dir):
        assert await is_started() is False

    async def test_is_started_true_after_mark(self, health_dir):
        await mark_started()

        assert await is_started() is True

    async def test_is_started_independent_of_heartbeat_freshness(self, health_dir):
        """Startup status must not depend on liveness/readiness state (User Story 3)."""
        assert await is_started() is False

        await touch_heartbeat()
        assert await is_started() is False  # a fresh heartbeat alone does not imply "started"

        await mark_started()
        assert await is_started() is True
        assert await is_heartbeat_fresh(staleness_seconds=180.0) is True  # both can be true independently

    async def test_clear_started_removes_existing_file(self, health_dir):
        from src.worker.health import clear_started

        await mark_started()
        assert await is_started() is True

        await clear_started()

        assert await is_started() is False

    async def test_clear_started_noop_when_absent(self, health_dir):
        from src.worker.health import clear_started

        await clear_started()  # should not raise


class TestReadyMarker:
    async def test_mark_ready_creates_file(self, health_dir):
        ready_path = health_dir / "ready"
        assert not ready_path.exists()

        await mark_ready()

        assert ready_path.exists()

    async def test_mark_not_ready_removes_file(self, health_dir):
        await mark_ready()
        ready_path = health_dir / "ready"
        assert ready_path.exists()

        await mark_not_ready()

        assert not ready_path.exists()

    async def test_mark_not_ready_noop_when_absent(self, health_dir):
        await mark_not_ready()  # should not raise

    async def test_clear_ready_removes_existing_file(self, health_dir):
        from src.worker.health import clear_ready

        await mark_ready()
        ready_path = health_dir / "ready"
        assert ready_path.exists()

        await clear_ready()

        assert not ready_path.exists()

    async def test_clear_ready_noop_when_absent(self, health_dir):
        from src.worker.health import clear_ready

        await clear_ready()  # should not raise


class TestReadinessPoller:
    async def test_touches_ready_when_db_check_succeeds(self, health_dir, mocker):
        from src.worker.health import readiness_poller

        mocker.patch("src.worker.health.check_database", mocker.AsyncMock(return_value=True))
        await mark_started()
        stop_event = asyncio.Event()

        async def _stop_soon():
            await asyncio.sleep(0.02)
            stop_event.set()

        asyncio.get_event_loop().create_task(_stop_soon())
        await readiness_poller(stop_event, interval_seconds=0.01, db_timeout_seconds=1.0)

        assert (health_dir / "ready").exists()

    async def test_removes_ready_when_db_check_fails(self, health_dir, mocker):
        from src.worker.health import readiness_poller

        mocker.patch("src.worker.health.check_database", mocker.AsyncMock(return_value=False))
        await mark_started()
        await mark_ready()
        stop_event = asyncio.Event()

        async def _stop_soon():
            await asyncio.sleep(0.02)
            stop_event.set()

        asyncio.get_event_loop().create_task(_stop_soon())
        await readiness_poller(stop_event, interval_seconds=0.01, db_timeout_seconds=1.0)

        assert not (health_dir / "ready").exists()

    async def test_stops_looping_once_stop_event_set(self, health_dir, mocker):
        from src.worker.health import readiness_poller

        mock_check = mocker.patch("src.worker.health.check_database", mocker.AsyncMock(return_value=True))
        await mark_started()
        stop_event = asyncio.Event()
        stop_event.set()

        await readiness_poller(stop_event, interval_seconds=0.01, db_timeout_seconds=1.0)

        mock_check.assert_not_called()

    async def test_does_not_check_db_before_started(self, health_dir, mocker):
        from src.worker.health import readiness_poller

        mock_check = mocker.patch("src.worker.health.check_database", mocker.AsyncMock(return_value=True))
        stop_event = asyncio.Event()

        async def _stop_soon():
            await asyncio.sleep(0.02)
            stop_event.set()

        asyncio.get_event_loop().create_task(_stop_soon())
        await readiness_poller(stop_event, interval_seconds=0.01, db_timeout_seconds=1.0)

        mock_check.assert_not_called()
        assert not (health_dir / "ready").exists()
