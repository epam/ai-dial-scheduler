from typing import Any

from sqlalchemy import Boolean, Column, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from src.lib.models.base import BaseSQLModel


class Schedule(BaseSQLModel, table=True):  # type: ignore[call-arg, no-redef]
    # mypy reports "kwargs already defined" here because SQLModel's __init_subclass__
    # uses **kwargs, and our field named 'kwargs' conflicts in mypy's name resolution.
    """Queryable business metadata for one APScheduler job.

    Attributes:
        id: Application-supplied PK equal to the APScheduler job id (VARCHAR 191).
            No FK to apscheduler_jobs — that table is engine-owned.
        display_name: Human-readable schedule name.
        created_by: Identity of the schedule creator/owner.
        service_id: External service whose on-behalf-of credentials the run uses.
        description: Optional free-text notes about the schedule (max 1000 chars,
            enforced at the API schema boundary). NULL when absent.
        trigger: Structured trigger definition stored as JSONB.
        kwargs: Structured call arguments stored as JSONB.
        schedule_metadata: Business tags stored in the JSONB ``metadata`` column.
            Mapped under ``schedule_metadata`` because ``metadata`` is reserved by
            SQLAlchemy's declarative base. Only string-valued entries are filterable.
        is_deleted: Soft-delete marker. Set to true by the delete operation only when the
            schedule has run history; a schedule with no runs is deleted outright instead.
            Never cleared — deletion is irreversible. Not writable through the API.
    """

    id: str = Field(
        sa_type=String(191),  # type: ignore[call-overload]
        primary_key=True,
    )
    display_name: str
    created_by: str
    service_id: str
    description: str | None = Field(
        default=None,
        sa_type=String(1000),  # type: ignore[call-overload]
        nullable=True,
    )
    trigger: dict[str, Any] = Field(
        sa_type=JSONB(),  # type: ignore[call-overload]
        sa_column_kwargs={"nullable": False},
    )
    kwargs: dict[str, Any] = Field(
        sa_type=JSONB(),  # type: ignore[call-overload]
        sa_column_kwargs={"nullable": False},
    )
    schedule_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONB(), nullable=False, server_default=text("'{}'::jsonb")),
    )
    is_deleted: bool = Field(
        default=False,
        sa_column=Column("is_deleted", Boolean(), nullable=False, server_default=text("false")),
    )

    __table_args__ = (Index("IX_schedules_metadata", "metadata", postgresql_using="gin"),)
