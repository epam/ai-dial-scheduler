import asyncio
import contextlib
import signal

from apscheduler.events import EVENT_JOB_ERROR, JobExecutionEvent
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.lib.constants import HEARTBEAT_JOB_ID, HEARTBEAT_JOB_NAME
from src.lib.db.enums import DbDriver
from src.lib.db.url import build_database_url
from src.lib.exceptions.errors import ScheduleRunError
from src.lib.settings import get_settings
from src.lib.utils.logging import configure_logging, logger
from src.worker.jobs import heartbeat


def create_worker() -> AsyncIOScheduler:
    settings = get_settings()
    sync_url = build_database_url(settings.db, driver=DbDriver.SYNC)
    return AsyncIOScheduler(
        jobstores={
            "default": SQLAlchemyJobStore(url=sync_url),
        },
        job_defaults={"coalesce": False, "max_instances": 1, "misfire_grace_time": 60},
        timezone="UTC",
    )


def on_job_error(event: JobExecutionEvent) -> None:
    exc = event.exception
    if isinstance(exc, ScheduleRunError):
        logger.error("run_failed", schedule_id=exc.schedule_id, run_id=exc.run_id, error=str(exc))
    else:
        logger.error("job_error", job_id=event.job_id, error=str(exc))


async def _jobstore_poller(scheduler: AsyncIOScheduler, stop_event: asyncio.Event, interval_seconds: int = 5) -> None:
    """Periodically wake the scheduler so it re-queries the job store.

    APScheduler 3.x sleeps indefinitely when the job store is empty.  Jobs
    added by a separate API process will never be seen unless the scheduler is
    woken up explicitly.
    """
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass
        if not stop_event.is_set():
            scheduler.wakeup()


async def _run() -> None:
    settings = get_settings()
    scheduler = create_worker()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        loop.call_soon_threadsafe(stop_event.set)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:  # Windows
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop_event.set))

    scheduler.add_listener(on_job_error, EVENT_JOB_ERROR)

    scheduler.add_job(
        heartbeat,
        trigger=IntervalTrigger(seconds=settings.noop_interval_seconds),
        id=HEARTBEAT_JOB_ID,
        name=HEARTBEAT_JOB_NAME,
        replace_existing=True,
    )

    try:
        from aidial_sdk.telemetry.init import init_telemetry
        from aidial_sdk.telemetry.types import TelemetryConfig

        init_telemetry(app=None, config=TelemetryConfig())
    except Exception as exc:
        logger.warning("telemetry_init_failed", error=str(exc))

    logger.info("Starting worker.")
    scheduler.start()
    poller = asyncio.create_task(_jobstore_poller(scheduler, stop_event))
    try:
        await stop_event.wait()
    finally:
        poller.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poller
        scheduler.shutdown(wait=True)
        logger.info("Worker stopped.")


def run_worker() -> None:
    configure_logging()
    asyncio.run(_run())
