from typing import Any

from sqlalchemy import String
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
        trigger: Structured trigger definition stored as JSONB.
        kwargs: Structured call arguments stored as JSONB.
    """

    id: str = Field(
        sa_type=String(191),  # type: ignore[call-overload]
        primary_key=True,
    )
    display_name: str
    created_by: str
    trigger: dict[str, Any] = Field(
        sa_type=JSONB(),  # type: ignore[call-overload]
        sa_column_kwargs={"nullable": False},
    )
    kwargs: dict[str, Any] = Field(
        sa_type=JSONB(),  # type: ignore[call-overload]
        sa_column_kwargs={"nullable": False},
    )
