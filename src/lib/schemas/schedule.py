from datetime import datetime
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, Field, TypeAdapter, field_validator, model_validator

from src.lib.db.enums import HttpMethod, TargetType, TriggerType


_HTTP_URL: TypeAdapter[AnyHttpUrl] = TypeAdapter(AnyHttpUrl)


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


class ScheduleTrigger(BaseModel):
    """Exactly one of cron/date must be provided."""

    cron: CronTriggerSpec | None = None
    date: datetime | None = None

    @model_validator(mode="after")
    def validate_exactly_one(self) -> "ScheduleTrigger":
        has_cron = self.cron is not None
        has_date = self.date is not None
        if has_cron and has_date:
            raise ValueError("only one trigger allowed")
        if not has_cron and not has_date:
            raise ValueError("exactly one trigger required")
        return self

    @property
    def trigger_type(self) -> TriggerType:
        return TriggerType.CRON if self.cron is not None else TriggerType.DATE


class ScheduleProperties(BaseModel):
    """Target call properties; validation rules depend on target_type."""

    target_type: TargetType
    method: HttpMethod | None = None
    url: str
    payload: dict[str, Any] = Field(default_factory=dict)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    api_version: str | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        _HTTP_URL.validate_python(v)
        return v

    @model_validator(mode="after")
    def validate_target_rules(self) -> "ScheduleProperties":
        if self.target_type == TargetType.CHAT_COMPLETION:
            if self.method is not None:
                raise ValueError("method must not be set for target_type 'chat_completion'")
            if "stream" not in self.payload:
                self.payload = {**self.payload, "stream": False}
        else:
            if self.method is None:
                raise ValueError(f"method is required for target_type '{self.target_type}'")
            if self.api_version is not None:
                raise ValueError(f"api_version must not be set for target_type '{self.target_type}'")
        return self


class ScheduleRequest(BaseModel):
    """Request body for Create and Update schedule endpoints."""

    display_name: str
    trigger: ScheduleTrigger
    properties: ScheduleProperties

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("display_name must not be empty")
        return v


class ScheduleResponse(BaseModel):
    """Full schedule detail returned by Create, Update, and Details endpoints."""

    id: str
    display_name: str
    next_run_time: datetime | None
    trigger: dict[str, Any]
    properties: dict[str, Any]
    created_by: str
    created_at: datetime
    updated_at: datetime


class ScheduleListItem(BaseModel):
    """Summary row returned in the paginated list response."""

    id: str
    display_name: str
    next_run_time: datetime | None
    trigger_type: TriggerType
    created_by: str
    created_at: datetime
    updated_at: datetime
