from datetime import datetime, timezone

from pydantic import ValidationError
import pytest

from src.lib.db.enums import HttpMethod, TargetType, TriggerType
from src.lib.schemas.schedule import (
    CronFields,
    CronTriggerSpec,
    ScheduleProperties,
    ScheduleRequest,
    ScheduleTrigger,
)


class TestScheduleTrigger:
    def test_cron_only_valid(self):
        t = ScheduleTrigger(cron=CronTriggerSpec(fields=CronFields(hour=9)))
        assert t.trigger_type == TriggerType.CRON

    def test_date_only_valid(self):
        dt = datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc)
        t = ScheduleTrigger(date=dt)
        assert t.trigger_type == TriggerType.DATE

    def test_both_triggers_raises(self):
        with pytest.raises(ValidationError, match="only one trigger allowed"):
            ScheduleTrigger(
                cron=CronTriggerSpec(fields=CronFields(hour=9)),
                date=datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc),
            )

    def test_zero_triggers_raises(self):
        with pytest.raises(ValidationError, match="exactly one trigger required"):
            ScheduleTrigger()

    def test_cron_fields_all_optional(self):
        fields = CronFields()
        assert fields.year is None
        assert fields.minute is None


class TestScheduleProperties:
    def test_chat_completion_valid(self):
        props = ScheduleProperties(
            target_type=TargetType.CHAT_COMPLETION,
            url="https://core.dial/openai/deployments/gpt-4o/chat/completions",
        )
        assert props.method is None
        assert props.payload["stream"] is False

    def test_chat_completion_stream_default(self):
        props = ScheduleProperties(
            target_type=TargetType.CHAT_COMPLETION,
            url="https://example.com/api",
            payload={"messages": []},
        )
        assert props.payload["stream"] is False

    def test_chat_completion_stream_preserved(self):
        props = ScheduleProperties(
            target_type=TargetType.CHAT_COMPLETION,
            url="https://example.com/api",
            payload={"stream": True},
        )
        assert props.payload["stream"] is True

    def test_chat_completion_api_version_allowed(self):
        props = ScheduleProperties(
            target_type=TargetType.CHAT_COMPLETION,
            url="https://example.com/api",
            api_version="2024-02-01",
        )
        assert props.api_version == "2024-02-01"

    def test_rest_requires_method(self):
        with pytest.raises(ValidationError, match="method is required"):
            ScheduleProperties(
                target_type=TargetType.REST,
                url="https://example.com/api",
            )

    def test_rest_valid_with_method(self):
        props = ScheduleProperties(
            target_type=TargetType.REST,
            url="https://example.com/api",
            method=HttpMethod.POST,
        )
        assert props.method == HttpMethod.POST

    def test_sse_requires_method(self):
        with pytest.raises(ValidationError, match="method is required"):
            ScheduleProperties(
                target_type=TargetType.SSE,
                url="https://example.com/sse",
            )

    def test_invalid_url_raises(self):
        with pytest.raises(ValidationError):
            ScheduleProperties(
                target_type=TargetType.REST,
                url="not-a-url",
                method=HttpMethod.GET,
            )

    def test_non_http_url_raises(self):
        with pytest.raises(ValidationError):
            ScheduleProperties(
                target_type=TargetType.REST,
                url="ftp://example.com/data",
                method=HttpMethod.GET,
            )

    def test_chat_completion_rejects_method(self):
        with pytest.raises(ValidationError, match="method must not be set"):
            ScheduleProperties(
                target_type=TargetType.CHAT_COMPLETION,
                url="https://example.com/api",
                method=HttpMethod.POST,
            )

    def test_rest_rejects_api_version(self):
        with pytest.raises(ValidationError, match="api_version must not be set"):
            ScheduleProperties(
                target_type=TargetType.REST,
                url="https://example.com/api",
                method=HttpMethod.POST,
                api_version="2024-02-01",
            )

    def test_sse_rejects_api_version(self):
        with pytest.raises(ValidationError, match="api_version must not be set"):
            ScheduleProperties(
                target_type=TargetType.SSE,
                url="https://example.com/sse",
                method=HttpMethod.POST,
                api_version="2024-02-01",
            )


class TestScheduleRequest:
    def test_empty_display_name_raises(self):
        with pytest.raises(ValidationError, match="display_name must not be empty"):
            ScheduleRequest(
                display_name="   ",
                trigger=ScheduleTrigger(cron=CronTriggerSpec(fields=CronFields(hour=9))),
                properties=ScheduleProperties(
                    target_type=TargetType.CHAT_COMPLETION,
                    url="https://example.com/api",
                ),
            )
