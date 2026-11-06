from datetime import UTC, datetime, tzinfo
from functools import cache
from typing import Any
import zoneinfo

from apscheduler.triggers.cron import CronTrigger
from pydantic import AnyHttpUrl, BaseModel, Field, TypeAdapter, ValidationInfo, field_validator, model_validator

from src.lib.constants import DEFAULT_SCHEDULE_TIMEZONE, MAX_CRON_ENTRIES
from src.lib.db.enums import HttpMethod, TargetType, TriggerType


_HTTP_URL: TypeAdapter[AnyHttpUrl] = TypeAdapter(AnyHttpUrl)

_TIMEZONE_DESCRIPTION = (
    "IANA timezone identifier, for example 'Europe/Berlin'. Cron fields and any start_date, end_date "
    "or date given without an offset are local times in this zone, so runs keep their wall-clock time "
    "across daylight-saving changes. Defaults to 'UTC'; null also means 'UTC'. Case-sensitive. Every "
    "value of the browser's Intl.supportedValuesOf('timeZone') is accepted, including legacy names such "
    "as 'Europe/Kiev', and it is returned exactly as sent. UTC offsets such as '+02:00' are rejected. "
    "Update replaces the whole schedule, so omitting timezone on update resets it to 'UTC'."
)


@cache
def _known_timezones() -> frozenset[str]:
    # Set membership is case-sensitive on every platform, unlike ZoneInfo(), which resolves names
    # through the file system and accepts 'europe/berlin' on case-insensitive macOS volumes.
    return frozenset(zoneinfo.available_timezones())


class CronFields(BaseModel):
    """APScheduler CronTrigger field names, all optional."""

    year: str | int | None = None
    month: str | int | None = None
    day: str | int | None = None
    week: str | int | None = None
    day_of_week: str | int | None = None
    hour: str | int | None = None
    minute: str | int | None = None
    second: str | int | None = None


class CronTriggerSpec(BaseModel):
    """Cron trigger specification with optional date bounds."""

    fields: CronFields
    start_date: datetime | None = None
    end_date: datetime | None = None

    def trigger_kwargs(self, timezone: tzinfo = UTC) -> dict[str, Any]:
        """Keyword arguments for an APScheduler ``CronTrigger`` of this spec evaluated in ``timezone``."""
        fields = {k: v for k, v in self.fields.model_dump().items() if v is not None}
        return {"timezone": timezone, "start_date": self.start_date, "end_date": self.end_date, **fields}

    @model_validator(mode="after")
    def validate_fields_are_schedulable(self) -> "CronTriggerSpec":
        # Rejects out-of-range values (e.g. hour 25) at request time, so the client gets a 422
        # that locates the entry instead of a registration failure after the row is written.
        CronTrigger(**self.trigger_kwargs())
        return self


class ScheduleTrigger(BaseModel):
    """Exactly one of cron/crons/date must be provided."""

    cron: CronTriggerSpec | None = None
    crons: list[CronTriggerSpec] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_CRON_ENTRIES,
        description=(
            "List of cron specifications. The schedule fires whenever any entry fires, and runs "
            "once when entries coincide. Use it for different times on different days, such as "
            "Monday 08:00 and Friday 10:00, instead of creating several schedules. Times are local to the "
            "schedule's timezone."
        ),
    )
    date: datetime | None = None

    @model_validator(mode="after")
    def validate_exactly_one(self) -> "ScheduleTrigger":
        provided = sum(trigger is not None for trigger in (self.cron, self.crons, self.date))
        if provided > 1:
            raise ValueError("only one trigger allowed")
        if provided == 0:
            raise ValueError("exactly one trigger required")
        return self

    @property
    def trigger_type(self) -> TriggerType:
        return TriggerType.DATE if self.date is not None else TriggerType.CRON


class RetryPolicy(BaseModel):
    """Per-schedule retry policy.

    Attributes:
        interval_sequence: Ordered list of seconds to wait before each successive retry.
            Its length defines the number of retries (total attempts = 1 + len).
        retryable_errors: HTTP status codes that make a failed attempt eligible for retry.
    """

    interval_sequence: list[float]
    retryable_errors: list[int]

    @field_validator("interval_sequence")
    @classmethod
    def validate_interval_sequence(cls, v: list[float]) -> list[float]:
        if not v:
            raise ValueError("interval_sequence must not be empty")
        if any(interval <= 0 for interval in v):
            raise ValueError("interval_sequence entries must be positive numbers")
        return v

    @field_validator("retryable_errors")
    @classmethod
    def validate_retryable_errors(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("retryable_errors must not be empty")
        if any(not (100 <= code <= 599) for code in v):
            raise ValueError("retryable_errors entries must be valid HTTP status codes (100-599)")
        return v


class ScheduleProperties(BaseModel):
    """Target call properties; validation rules depend on target_type."""

    target_type: TargetType
    method: HttpMethod | None = None
    url: str
    payload: dict[str, Any] = Field(default_factory=dict)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    api_version: str | None = None
    # Descriptions are part of the OpenAPI contract, so they also reach MCP clients as the field's
    # tool-input schema description -- the only place an agent sees this guidance.
    timeout: float | None = Field(
        default=None,
        description=(
            "Absolute ceiling in seconds on a single attempt. Never resets while the attempt "
            "runs. Omit to use the process default, which is sized as a backstop against a run "
            "that never ends rather than as a routine bound."
        ),
    )
    idle_timeout: float | None = Field(
        default=None,
        description=(
            "Maximum gap in seconds between two consecutive stream events. Resets on every "
            "event, so it bounds inactivity rather than total duration. Applies only to "
            "streaming targets: `sse`, or `chat_completion`/`responses` with `payload.stream` "
            "set to `true`. Must not exceed `timeout` when both are set. Omit to use the "
            "process default."
        ),
    )
    retry: RetryPolicy | None = None
    create_conversation: bool = Field(
        default=False,
        description=(
            "Save each successful run's prompt and response as a DIAL conversation in the run "
            "owner's storage, so the result is readable in DIAL Chat. Set this to `true` when "
            "creating a `chat_completion` schedule unless the caller asked for the run not to be "
            "saved. Only allowed for `target_type: chat_completion`. Conversation creation is "
            "best-effort: a failure is logged and never fails the run."
        ),
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        _HTTP_URL.validate_python(v)
        return v

    @field_validator("timeout", "idle_timeout")
    @classmethod
    def validate_positive_seconds(cls, v: float | None, info: ValidationInfo) -> float | None:
        if v is not None and v <= 0:
            raise ValueError(f"{info.field_name} must be a positive number (seconds)")
        return v

    @property
    def is_streaming(self) -> bool:
        """Whether the target delivers its response as a stream of events.

        Both bounds key off this. Only a streaming target gives a progress signal, which is
        what earns it the longer ceiling; a non-streaming call is indistinguishable from a
        hung one at the transport layer, so total duration is the only instrument available.
        """
        if self.target_type is TargetType.SSE:
            return True
        if self.target_type in (TargetType.CHAT_COMPLETION, TargetType.RESPONSES):
            return bool(self.payload.get("stream"))
        return False

    @model_validator(mode="after")
    def validate_timeout_bounds(self) -> "ScheduleProperties":
        # Only when both are explicit: comparing against a process default would make a stored
        # schedule's validity depend on deployment configuration.
        if self.timeout is not None and self.idle_timeout is not None and self.idle_timeout > self.timeout:
            raise ValueError("idle_timeout must not exceed timeout")
        return self

    @model_validator(mode="after")
    def validate_target_rules(self) -> "ScheduleProperties":
        if self.target_type in (TargetType.CHAT_COMPLETION, TargetType.RESPONSES):
            if self.method is not None:
                raise ValueError(f"method must not be set for target_type '{self.target_type}'")
            if not self.api_version:
                raise ValueError(f"api_version is required for target_type '{self.target_type}'")
            if "stream" not in self.payload:
                self.payload = {**self.payload, "stream": False}
            if not self.payload.get("model"):
                raise ValueError(f"payload.model is required for target_type '{self.target_type}'")
            if self.create_conversation and self.target_type != TargetType.CHAT_COMPLETION:
                raise ValueError("create_conversation is only allowed for target_type 'chat_completion'")
        else:
            if self.method is None:
                raise ValueError(f"method is required for target_type '{self.target_type}'")
            if self.api_version is not None:
                raise ValueError(f"api_version must not be set for target_type '{self.target_type}'")
            if self.create_conversation:
                raise ValueError("create_conversation is only allowed for target_type 'chat_completion'")
        return self


class ScheduleRequest(BaseModel):
    """Request body for Create and Update schedule endpoints."""

    display_name: str
    service_id: str
    description: str | None = Field(default=None, max_length=1000)
    trigger: ScheduleTrigger
    timezone: str = Field(default=DEFAULT_SCHEDULE_TIMEZONE, description=_TIMEZONE_DESCRIPTION)
    properties: ScheduleProperties
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("display_name must not be empty")
        return v

    @field_validator("timezone", mode="before")
    @classmethod
    def default_null_timezone(cls, v: object) -> object:
        """Treat an explicit ``null`` timezone like an omitted one."""
        return DEFAULT_SCHEDULE_TIMEZONE if v is None else v

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        """Accept only identifiers in the IANA timezone database, matched case-sensitively."""
        if v not in _known_timezones():
            raise ValueError(f"unknown timezone {v!r}; expected an IANA identifier such as 'Europe/Berlin'")
        return v


class ScheduleResponse(BaseModel):
    """Full schedule detail returned by Create, Update, and Details endpoints.

    ``is_deleted`` is read-only: it is deliberately absent from :class:`ScheduleRequest`, so a
    value supplied in a create/update body is ignored rather than rejected. It is required here
    (no default) so that a missing mapping fails loudly instead of reporting a soft-deleted
    schedule as live.
    """

    id: str
    display_name: str
    service_id: str
    description: str | None = None
    next_run_time: datetime | None
    trigger: dict[str, Any]
    timezone: str
    properties: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_deleted: bool
    created_by: str
    created_at: datetime
    updated_at: datetime


class ScheduleListItem(BaseModel):
    """Summary row returned in the paginated list response.

    ``is_deleted`` is required for the same reason as on :class:`ScheduleResponse`.

    ``model`` is the deployment from ``properties.payload.model``, present only for targets
    that carry one — it is absent for REST and SSE schedules, and for any payload without a
    string ``model``. Callers must treat it as optional rather than as a property of every
    schedule.
    """

    id: str
    display_name: str
    service_id: str
    description: str | None = None
    model: str | None = None
    next_run_time: datetime | None
    trigger_type: TriggerType
    trigger: dict[str, Any]
    timezone: str
    is_deleted: bool
    created_by: str
    created_at: datetime
    updated_at: datetime
