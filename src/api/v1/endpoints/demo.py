from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Request
from pydantic import BaseModel

from src.worker.jobs import dummy_job


router = APIRouter(prefix="/demo", tags=["demo"])


class ScheduleRequest(BaseModel):
    message: str
    cron: str = "* * * * *"


class ScheduleResponse(BaseModel):
    job_id: str
    next_run_at: datetime


@router.post("/schedule", response_model=ScheduleResponse)
async def schedule_demo_job(body: ScheduleRequest, request: Request) -> ScheduleResponse:
    scheduler: AsyncIOScheduler = request.app.state.scheduler
    job = scheduler.add_job(
        dummy_job,
        trigger=CronTrigger.from_crontab(body.cron, timezone="UTC"),
        kwargs={"message": body.message},
    )
    return ScheduleResponse(job_id=job.id, next_run_at=job.next_run_time)
