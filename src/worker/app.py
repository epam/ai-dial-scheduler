import asyncio
import signal

from src.lib.scheduler import create_scheduler
from src.lib.utils.logging import configure_logging, logger


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
    scheduler = create_scheduler()
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

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
