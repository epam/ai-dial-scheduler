"""Integration test: fire a job end-to-end through a real AsyncIOScheduler."""

import asyncio

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
import httpx
import pytest
from structlog.contextvars import clear_contextvars
import structlog.testing

from src.lib.constants import EXECUTE_SCHEDULED_JOB_REF
from src.lib.db.enums import HttpMethod, TargetType


@pytest.fixture(autouse=True)
def reset_context():
    clear_contextvars()
    yield
    clear_contextvars()


@pytest.fixture
def scheduler():
    sched = AsyncIOScheduler(
        jobstores={"default": MemoryJobStore()},
        job_defaults={"coalesce": False, "max_instances": 1, "misfire_grace_time": 60},
        timezone="UTC",
    )
    return sched


async def test_rest_job_fires_end_to_end(scheduler, mocker):
    """A rest job fired through a real scheduler calls the executor and logs a result."""
    import uuid

    created_row = mocker.MagicMock()
    created_row.id = uuid.uuid4()
    mock_repo = mocker.AsyncMock()
    mock_repo.create_in_progress = mocker.AsyncMock(return_value=created_row)
    mock_repo.mark_success = mocker.AsyncMock(return_value=mocker.MagicMock())
    mocker.patch("src.worker.jobs.ScheduleRunRepository", return_value=mock_repo)
    mocker.patch("src.worker.jobs.Database")

    mock_response = mocker.MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.text = "ok"
    mock_response.raise_for_status = mocker.MagicMock()

    mock_http_client = mocker.AsyncMock(spec=httpx.AsyncClient)
    mock_http_client.request = mocker.AsyncMock(return_value=mock_response)
    mock_http_client.__aenter__ = mocker.AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = mocker.AsyncMock(return_value=False)
    mocker.patch("src.worker.executors.rest.httpx.AsyncClient", return_value=mock_http_client)

    fired = asyncio.Event()
    original_execute = None

    async def capture_execute(*args, **kwargs):
        try:
            await original_execute(*args, **kwargs)
        finally:
            fired.set()

    from src.worker import jobs as jobs_mod

    original_execute = jobs_mod.execute_scheduled_job
    mocker.patch.object(jobs_mod, "execute_scheduled_job", side_effect=capture_execute)

    scheduler.start()
    try:
        scheduler.add_job(
            EXECUTE_SCHEDULED_JOB_REF,
            trigger=DateTrigger(run_date="2000-01-01 00:00:00"),
            kwargs={
                "schedule_id": "integration-test-sched",
                "target_type": TargetType.REST,
                "method": HttpMethod.POST,
                "url": "https://example.com/test",
                "payload": {},
                "extra_headers": {},
            },
            misfire_grace_time=None,
        )

        with structlog.testing.capture_logs():
            await asyncio.wait_for(fired.wait(), timeout=10.0)

    finally:
        scheduler.shutdown(wait=False)

    mock_http_client.request.assert_called_once()
