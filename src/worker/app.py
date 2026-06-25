import asyncio
import contextlib
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.lib.constants import HEARTBEAT_JOB_ID, HEARTBEAT_JOB_NAME
from src.lib.db.jobstore import build_jobstore
from src.lib.settings import get_settings
from src.lib.utils.logging import configure_logging, logger
from src.worker.clients import HttpClientProvider
from src.worker.jobs import heartbeat


def create_worker() -> AsyncIOScheduler:
    settings = get_settings()
    return AsyncIOScheduler(
        jobstores={
            "default": build_jobstore(settings.db),
        },
        job_defaults={
            "coalesce": settings.job_coalesce,
            "max_instances": settings.job_max_instances,
            "misfire_grace_time": settings.job_misfire_grace_time,
        },
        timezone="UTC",
    )


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
    # Build the shared HTTP client up front so its TLS context is created once,
    # before jobs fire, rather than blocking the event loop on the first run.
    _ = HttpClientProvider().client
    poller = asyncio.create_task(_jobstore_poller(scheduler, stop_event))
    try:
        await stop_event.wait()
    finally:
        poller.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poller
        scheduler.shutdown(wait=False)
        await HttpClientProvider().aclose()
        logger.info("Worker stopped.")


def run_worker() -> None:
    configure_logging()
    asyncio.run(_run())
