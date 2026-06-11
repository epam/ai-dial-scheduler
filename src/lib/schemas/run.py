from datetime import datetime
from typing import Any
import uuid

from pydantic import BaseModel

from src.lib.db.enums import ScheduleRunStatus


class RunContext(BaseModel):
    run_id: str
    schedule_id: str


class RunListItem(BaseModel):
    id: uuid.UUID
    status: ScheduleRunStatus
    start_time: datetime
    end_time: datetime | None


class RunScheduleSummary(BaseModel):
    display_name: str
    trigger: dict[str, Any]
    properties: dict[str, Any]


class RunResponse(BaseModel):
    id: uuid.UUID
    status: ScheduleRunStatus
    start_time: datetime
    end_time: datetime | None
    result: dict[str, Any] | None
    schedule: RunScheduleSummary
