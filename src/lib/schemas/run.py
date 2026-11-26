from datetime import datetime
from typing import Any
import uuid

import httpx
from pydantic import BaseModel, ConfigDict

from src.lib.db.enums import ScheduleRunStatus
from src.lib.schemas.conversation import ConversationLocation


class RunTimeouts(BaseModel):
    """Per-process bounds applied to an attempt when the schedule sets none of its own.

    Grouped rather than passed as separate floats: there are three of them, they are always
    supplied together, and three adjacent positional floats are easy to transpose.

    ``streaming_max_duration`` is deliberately larger than ``max_duration``. A streaming target
    has ``idle_timeout`` watching it, so a stall is caught long before the ceiling; a
    non-streaming target offers no progress signal at all, leaving total duration as the only
    instrument -- and a blunt instrument should not be set to an hour.
    """

    max_duration: float
    streaming_max_duration: float
    idle_timeout: float


class RunContext(BaseModel):
    """Per-run state threaded through credential resolution, the retry orchestrator and executors.

    ``http_client`` and ``timeouts`` let a run be executed by whichever process owns it. The
    worker leaves them unset and falls back to its process-wide shared client and
    ``WORKER_HTTP_*`` settings; the API passes its own app-state client and ``API_HTTP_*``
    values, since FastAPI owns reusable clients in app state rather than in a global.

    The ``effective_*`` fields are resolved once per attempt by the orchestrator and are the
    only source executors read -- never ``properties`` directly.

    ``schedule_display_name`` is recorded by credential resolution from the schedule row it already
    loads, so later steps of the run never read the schedule again.

    ``conversation_location`` is set once per run by ``execute_run`` for chat completion schedules
    with ``create_conversation``, before any attempt; conversation creation saves at that location.
    ``conversation_id`` is the id the chat completion executor sends as ``X-CONVERSATION-ID``. It is
    left unset when the location is unknown or the encoded id is too long for a header, in which
    case the conversation is still saved but the request carries no header.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    schedule_id: str
    http_client: httpx.AsyncClient | None = None
    timeouts: RunTimeouts | None = None
    effective_timeout: float | None = None
    effective_idle_timeout: float | None = None
    schedule_display_name: str | None = None
    conversation_id: str | None = None
    conversation_location: ConversationLocation | None = None


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
