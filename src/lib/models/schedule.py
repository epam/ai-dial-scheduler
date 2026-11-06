from typing import Any

from sqlalchemy import Boolean, Column, Computed, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from src.lib.constants import DEFAULT_SCHEDULE_TIMEZONE
from src.lib.models.base import BaseSQLModel


# Single source of truth for the generated ``model`` column, shared with the migration that
# creates it so the two can never state different expressions.
MODEL_GENERATION_EXPRESSION = "kwargs -> 'payload' ->> 'model'"


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
        model: Read-only projection of ``kwargs -> payload -> model``, maintained by the
            database. NULL whenever the payload carries no string ``model`` — REST and SSE
            targets, most obviously. It exists so listing and filtering never have to
            detoast the whole ``kwargs`` document, which dominates the cost once payloads
            grow (long chat histories); being generated, it cannot drift from ``kwargs``.
        timezone: IANA timezone identifier in which the trigger's cron fields and naive
            datetimes are evaluated, stored exactly as submitted (legacy aliases such as
            ``Europe/Kiev`` are not renamed). Rows created before the column existed are
            backfilled to ``UTC``, which is how they were always evaluated.
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
    model: str | None = Field(
        default=None,
        sa_column=Column("model", String(), Computed(MODEL_GENERATION_EXPRESSION, persisted=True), nullable=True),
    )
    timezone: str = Field(
        default=DEFAULT_SCHEDULE_TIMEZONE,
        sa_column=Column("timezone", String(64), nullable=False, server_default=text(f"'{DEFAULT_SCHEDULE_TIMEZONE}'")),
    )

    __table_args__ = (Index("IX_schedules_metadata", "metadata", postgresql_using="gin"),)
