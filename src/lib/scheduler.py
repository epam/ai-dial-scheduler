from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.lib.db.jobstore import build_jobstore
from src.lib.settings import get_settings


def create_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    return AsyncIOScheduler(
        jobstores={"default": build_jobstore(settings.db)},
        job_defaults={"coalesce": False, "max_instances": 1, "misfire_grace_time": 60},
        timezone="UTC",
    )
