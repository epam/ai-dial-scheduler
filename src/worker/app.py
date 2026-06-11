import asyncio
import signal

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.lib.db.enums import DbDriver
from src.lib.db.url import build_database_url
from src.lib.settings import get_settings
from src.lib.utils.logging import configure_logging, logger


def create_worker() -> AsyncIOScheduler:
    settings = get_settings()
    sync_url = build_database_url(settings.db, driver=DbDriver.SYNC)
    return AsyncIOScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=sync_url)},
        job_defaults={"coalesce": False, "max_instances": 1, "misfire_grace_time": 60},
        timezone="UTC",
    )


async def _jobstore_poller(scheduler, stop_event: asyncio.Event, interval_seconds: int = 5) -> None:
    """Periodically wake the scheduler so it re-queries the job store.

    APScheduler 3.x sleeps indefinitely when the job store is empty.  Jobs
    added by a separate API process will never be seen unless the scheduler is
    woken up explicitly.
    """
    while not stop_event.is_set():
        await asyncio.sleep(interval_seconds)
        scheduler.wakeup()


async def _run() -> None:
    scheduler = create_worker()
    stop_event = asyncio.Event()

    # add_signal_handler is Unix-only; signal.signal works on both platforms.
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop_event.set())

    logger.info("Starting worker.")
    scheduler.start()
    asyncio.create_task(_jobstore_poller(scheduler, stop_event))
    try:
        await stop_event.wait()
    finally:
        scheduler.shutdown(wait=True)
        logger.info("Worker stopped.")


def run_worker() -> None:
    configure_logging()
    asyncio.run(_run())
