from datetime import datetime
from typing import Any
import uuid

import httpx
from pydantic import BaseModel, ConfigDict

from src.lib.db.enums import ScheduleRunStatus


class RunContext(BaseModel):
    """Per-run state threaded through credential resolution, the retry orchestrator and executors.

    ``http_client`` and ``default_timeout`` let a run be executed by whichever process owns it.
    The worker leaves them unset and falls back to its process-wide shared client and
    ``WORKER_HTTP_*`` timeouts; the API passes its own app-state client and ``API_HTTP_*``
    timeouts, since FastAPI owns reusable clients in app state rather than in a global.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    schedule_id: str
    http_client: httpx.AsyncClient | None = None
    default_timeout: float | None = None
    effective_timeout: float | None = None


class RunListItem(BaseModel):
    id: uuid.UUID
    status: ScheduleRunStatus
    start_time: datetime
    end_time: datetime | None
    conversation_id: str | None = None


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
    conversation_id: str | None = None
