from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.lib.db.enums import DbDriver
from src.lib.db.url import build_database_url
from src.lib.settings import get_settings


def create_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    sync_url = build_database_url(settings.db, driver=DbDriver.SYNC)
    return AsyncIOScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=sync_url)},
        job_defaults={"coalesce": False, "max_instances": 1, "misfire_grace_time": 60},
        timezone="UTC",
    )
