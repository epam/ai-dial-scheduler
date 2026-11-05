from datetime import datetime
from typing import Any, Optional
import uuid

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from src.lib.db.enums import ScheduleRunStatus
from src.lib.models.base import BaseSQLModel


_STATUS_CHECK = "status IN ({})".format(", ".join(f"'{s.value}'" for s in ScheduleRunStatus))


class ScheduleRun(BaseSQLModel, table=True):  # type: ignore[call-arg]
    """One recorded execution attempt of a Schedule.

    Attributes:
        id: Globally unique UUID, generated application-side.
        end_time: Timestamp when the run finished; NULL while in_progress.
        status: Execution outcome constrained to ScheduleRunStatus values.
        result: Result payload; NULL until the run finishes.
        conversation_id: Addressable DIAL conversation id (``conversations/...``) for the
            conversation created for this run; ``NULL`` when no conversation was created
            (creation disabled, run not successful, in-progress, creation or persistence
            failed, or the run predates this feature).
        schedule_id: FK to schedules.id; ON DELETE RESTRICT preserves run history.
        schedule_snapshot: Point-in-time copy of schedule definition at run time.
    """

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        sa_type=sa.UUID(),  # type: ignore[call-overload]
    )
    end_time: Optional[datetime] = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[call-overload]
        sa_column_kwargs={"nullable": True},
    )
    status: str
    result: Optional[dict[str, Any]] = Field(
        default=None,
        sa_type=JSONB(),  # type: ignore[call-overload]
        sa_column_kwargs={"nullable": True},
    )
    conversation_id: Optional[str] = Field(
        default=None,
        sa_type=String(),  # type: ignore[call-overload]
        sa_column_kwargs={"nullable": True},
    )
    schedule_id: str = Field(
        sa_type=String(191),  # type: ignore[call-overload]
        sa_column_kwargs={"nullable": False},
    )
    schedule_snapshot: dict[str, Any] = Field(
        sa_type=JSONB(),  # type: ignore[call-overload]
        sa_column_kwargs={"nullable": False},
    )

    __table_args__ = (
        CheckConstraint(_STATUS_CHECK, name="status"),
        ForeignKeyConstraint(
            ["schedule_id"],
            ["schedules.id"],
            ondelete="RESTRICT",
        ),
        Index("IX_schedule_runs_schedule_id", "schedule_id"),
    )
