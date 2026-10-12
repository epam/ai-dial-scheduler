from datetime import UTC, datetime, timezone

from pydantic import ValidationError
import pytest

from src.lib.db.enums import HttpMethod, TargetType, TriggerType
from src.lib.schemas.schedule import (
    CronFields,
    CronTriggerSpec,
    RetryPolicy,
    ScheduleListItem,
    ScheduleProperties,
    ScheduleRequest,
    ScheduleResponse,
    ScheduleTrigger,
)


def _valid_request_kwargs() -> dict:
    return {
        "display_name": "Test Schedule",
        "service_id": "dial-native",
        "trigger": ScheduleTrigger(cron=CronTriggerSpec(fields=CronFields(hour=9))),
        "properties": ScheduleProperties(
            target_type=TargetType.CHAT_COMPLETION,
            url="https://example.com/api",
            payload={"model": "gpt-4o"},
            api_version="2025-01-01-preview",
        ),
    }


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
            url="https://core.dial/openai/",
            payload={"model": "gpt-4o"},
            api_version="2025-01-01-preview",
        )
        assert props.method is None
        assert props.payload["stream"] is False

    def test_chat_completion_stream_default(self):
        props = ScheduleProperties(
            target_type=TargetType.CHAT_COMPLETION,
            url="https://example.com/api",
            payload={"model": "gpt-4o", "messages": []},
            api_version="2025-01-01-preview",
        )
        assert props.payload["stream"] is False

    def test_chat_completion_stream_preserved(self):
        props = ScheduleProperties(
            target_type=TargetType.CHAT_COMPLETION,
            url="https://example.com/api",
            payload={"model": "gpt-4o", "stream": True},
            api_version="2025-01-01-preview",
        )
        assert props.payload["stream"] is True

    def test_chat_completion_api_version_allowed(self):
        props = ScheduleProperties(
            target_type=TargetType.CHAT_COMPLETION,
            url="https://example.com/api",
            payload={"model": "gpt-4o"},
            api_version="2024-02-01",
        )
        assert props.api_version == "2024-02-01"

    def test_chat_completion_requires_api_version(self):
        with pytest.raises(ValidationError, match="api_version is required"):
            ScheduleProperties(
                target_type=TargetType.CHAT_COMPLETION,
                url="https://example.com/api",
                payload={"model": "gpt-4o"},
            )

    def test_chat_completion_empty_api_version_raises(self):
        with pytest.raises(ValidationError, match="api_version is required"):
            ScheduleProperties(
                target_type=TargetType.CHAT_COMPLETION,
                url="https://example.com/api",
                payload={"model": "gpt-4o"},
                api_version="",
            )

    def test_chat_completion_requires_model_in_payload(self):
        with pytest.raises(ValidationError, match="payload.model is required"):
            ScheduleProperties(
                target_type=TargetType.CHAT_COMPLETION,
                url="https://example.com/api",
                payload={"messages": [{"role": "user", "content": "hi"}]},
                api_version="2025-01-01-preview",
            )

    def test_chat_completion_empty_model_raises(self):
        with pytest.raises(ValidationError, match="payload.model is required"):
            ScheduleProperties(
                target_type=TargetType.CHAT_COMPLETION,
                url="https://example.com/api",
                payload={"model": ""},
                api_version="2025-01-01-preview",
            )

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
                payload={"model": "gpt-4o"},
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


class TestCreateConversation:
    def test_defaults_to_false(self):
        props = ScheduleProperties(
            target_type=TargetType.CHAT_COMPLETION,
            url="https://example.com/api",
            payload={"model": "gpt-4o"},
            api_version="2024-02-01",
        )
        assert props.create_conversation is False

    def test_accepted_for_chat_completion(self):
        props = ScheduleProperties(
            target_type=TargetType.CHAT_COMPLETION,
            url="https://example.com/api",
            payload={"model": "gpt-4o"},
            api_version="2024-02-01",
            create_conversation=True,
        )
        assert props.create_conversation is True

    def test_rejected_for_rest(self):
        with pytest.raises(ValidationError, match="create_conversation is only allowed"):
            ScheduleProperties(
                target_type=TargetType.REST,
                url="https://example.com/api",
                method=HttpMethod.POST,
                create_conversation=True,
            )

    def test_rejected_for_sse(self):
        with pytest.raises(ValidationError, match="create_conversation is only allowed"):
            ScheduleProperties(
                target_type=TargetType.SSE,
                url="https://example.com/sse",
                method=HttpMethod.POST,
                create_conversation=True,
            )

    def test_false_allowed_for_rest(self):
        props = ScheduleProperties(
            target_type=TargetType.REST,
            url="https://example.com/api",
            method=HttpMethod.POST,
            create_conversation=False,
        )
        assert props.create_conversation is False

    def test_survives_kwargs_roundtrip(self):
        props = ScheduleProperties(
            target_type=TargetType.CHAT_COMPLETION,
            url="https://example.com/api",
            payload={"model": "gpt-4o"},
            api_version="2024-02-01",
            create_conversation=True,
        )
        rebuilt = ScheduleProperties(**props.model_dump(mode="json"))
        assert rebuilt.create_conversation is True


class TestResponsesProperties:
    def test_responses_valid(self):
        props = ScheduleProperties(
            target_type=TargetType.RESPONSES,
            url="https://core.dial/openai/v1/responses",
            payload={"input": "hi", "model": "gpt-5.6"},
            api_version="2025-01-01-preview",
        )
        assert props.method is None
        assert props.payload["stream"] is False

    def test_responses_stream_default(self):
        props = ScheduleProperties(
            target_type=TargetType.RESPONSES,
            url="https://example.com/openai/v1/responses",
            payload={"input": "hi", "model": "gpt-5.6"},
            api_version="2025-01-01-preview",
        )
        assert props.payload["stream"] is False

    def test_responses_stream_preserved(self):
        props = ScheduleProperties(
            target_type=TargetType.RESPONSES,
            url="https://example.com/openai/v1/responses",
            payload={"input": "hi", "model": "gpt-5.6", "stream": True},
            api_version="2025-01-01-preview",
        )
        assert props.payload["stream"] is True

    def test_responses_requires_model_in_payload(self):
        with pytest.raises(ValidationError, match="payload.model is required"):
            ScheduleProperties(
                target_type=TargetType.RESPONSES,
                url="https://example.com/openai/v1/responses",
                payload={"input": "hi"},
                api_version="2025-01-01-preview",
            )

    def test_responses_requires_api_version(self):
        with pytest.raises(ValidationError, match="api_version is required"):
            ScheduleProperties(
                target_type=TargetType.RESPONSES,
                url="https://example.com/openai/v1/responses",
                payload={"input": "hi", "model": "gpt-5.6"},
            )

    def test_responses_empty_api_version_raises(self):
        with pytest.raises(ValidationError, match="api_version is required"):
            ScheduleProperties(
                target_type=TargetType.RESPONSES,
                url="https://example.com/openai/v1/responses",
                payload={"input": "hi", "model": "gpt-5.6"},
                api_version="",
            )

    def test_responses_rejects_method(self):
        with pytest.raises(ValidationError, match="method must not be set"):
            ScheduleProperties(
                target_type=TargetType.RESPONSES,
                url="https://example.com/openai/v1/responses",
                payload={"input": "hi", "model": "gpt-5.6"},
                method=HttpMethod.POST,
                api_version="2025-01-01-preview",
            )

    def test_responses_rejects_create_conversation(self):
        with pytest.raises(ValidationError, match="create_conversation is only allowed"):
            ScheduleProperties(
                target_type=TargetType.RESPONSES,
                url="https://example.com/openai/v1/responses",
                payload={"input": "hi", "model": "gpt-5.6"},
                api_version="2025-01-01-preview",
                create_conversation=True,
            )


class TestScheduleTimeout:
    def test_timeout_defaults_to_none(self):
        props = ScheduleProperties(target_type=TargetType.REST, url="https://example.com/api", method=HttpMethod.POST)
        assert props.timeout is None

    def test_positive_timeout_accepted(self):
        props = ScheduleProperties(
            target_type=TargetType.REST, url="https://example.com/api", method=HttpMethod.POST, timeout=30
        )
        assert props.timeout == 30

    def test_zero_timeout_raises(self):
        with pytest.raises(ValidationError, match="timeout must be a positive number"):
            ScheduleProperties(
                target_type=TargetType.REST, url="https://example.com/api", method=HttpMethod.POST, timeout=0
            )

    def test_negative_timeout_raises(self):
        with pytest.raises(ValidationError, match="timeout must be a positive number"):
            ScheduleProperties(
                target_type=TargetType.REST, url="https://example.com/api", method=HttpMethod.POST, timeout=-5
            )


class TestScheduleIdleTimeout:
    def test_idle_timeout_defaults_to_none(self):
        props = ScheduleProperties(target_type=TargetType.REST, url="https://example.com/api", method=HttpMethod.POST)
        assert props.idle_timeout is None

    def test_positive_idle_timeout_accepted(self):
        props = ScheduleProperties(
            target_type=TargetType.REST, url="https://example.com/api", method=HttpMethod.POST, idle_timeout=30
        )
        assert props.idle_timeout == 30

    @pytest.mark.parametrize("value", [0, -5])
    def test_non_positive_idle_timeout_raises(self, value):
        with pytest.raises(ValidationError, match="idle_timeout must be a positive number"):
            ScheduleProperties(
                target_type=TargetType.REST,
                url="https://example.com/api",
                method=HttpMethod.POST,
                idle_timeout=value,
            )

    def test_idle_timeout_above_timeout_raises(self):
        with pytest.raises(ValidationError, match="idle_timeout must not exceed timeout"):
            ScheduleProperties(
                target_type=TargetType.REST,
                url="https://example.com/api",
                method=HttpMethod.POST,
                timeout=60,
                idle_timeout=120,
            )

    def test_idle_timeout_equal_to_timeout_accepted(self):
        props = ScheduleProperties(
            target_type=TargetType.REST,
            url="https://example.com/api",
            method=HttpMethod.POST,
            timeout=60,
            idle_timeout=60,
        )
        assert props.idle_timeout == 60

    def test_idle_timeout_without_timeout_accepted(self):
        """The cross-field rule applies only when both are set: comparing an explicit field
        against a process default would make validity depend on deployment configuration."""
        props = ScheduleProperties(
            target_type=TargetType.REST,
            url="https://example.com/api",
            method=HttpMethod.POST,
            idle_timeout=9000,
        )
        assert props.idle_timeout == 9000
        assert props.timeout is None

    def test_round_trips_through_json(self):
        props = ScheduleProperties(
            target_type=TargetType.REST,
            url="https://example.com/api",
            method=HttpMethod.POST,
            timeout=1800,
            idle_timeout=90,
        )

        restored = ScheduleProperties(**props.model_dump(mode="json"))

        assert restored.timeout == 1800
        assert restored.idle_timeout == 90


class TestIsStreaming:
    """``is_streaming`` picks both the ceiling default and the SDK call shape, so every target
    type and ``stream`` value must resolve deterministically."""

    @pytest.mark.parametrize(
        ("target_type", "stream", "expected"),
        [
            (TargetType.CHAT_COMPLETION, True, True),
            (TargetType.CHAT_COMPLETION, False, False),
            (TargetType.CHAT_COMPLETION, None, False),
            (TargetType.RESPONSES, True, True),
            (TargetType.RESPONSES, False, False),
            (TargetType.RESPONSES, None, False),
        ],
    )
    def test_sdk_targets_follow_payload_stream(self, target_type, stream, expected):
        payload: dict = {"model": "gpt-4o"}
        if stream is not None:
            payload["stream"] = stream

        props = ScheduleProperties(
            target_type=target_type,
            url="https://example.com/api",
            payload=payload,
            api_version="2025-01-01-preview",
        )

        assert props.is_streaming is expected

    def test_sse_always_streams(self):
        props = ScheduleProperties(target_type=TargetType.SSE, url="https://example.com/api", method=HttpMethod.POST)
        assert props.is_streaming is True

    def test_rest_never_streams_even_with_a_stream_flag(self):
        props = ScheduleProperties(
            target_type=TargetType.REST, url="https://example.com/api", method=HttpMethod.POST, payload={"stream": True}
        )
        assert props.is_streaming is False


class TestRetryPolicy:
    def test_valid_retry_accepted(self):
        policy = RetryPolicy(interval_sequence=[2, 5, 8], retryable_errors=[500, 502])
        assert policy.interval_sequence == [2, 5, 8]
        assert policy.retryable_errors == [500, 502]

    def test_retry_defaults_to_none_on_properties(self):
        props = ScheduleProperties(target_type=TargetType.REST, url="https://example.com/api", method=HttpMethod.POST)
        assert props.retry is None

    def test_retry_accepted_on_properties(self):
        props = ScheduleProperties(
            target_type=TargetType.REST,
            url="https://example.com/api",
            method=HttpMethod.POST,
            retry=RetryPolicy(interval_sequence=[1], retryable_errors=[500]),
        )
        assert props.retry is not None
        assert props.retry.interval_sequence == [1]

    def test_retry_accepts_nested_dict(self):
        props = ScheduleProperties(
            target_type=TargetType.REST,
            url="https://example.com/api",
            method=HttpMethod.POST,
            retry={"interval_sequence": [2, 5], "retryable_errors": [500]},
        )
        assert props.retry is not None
        assert props.retry.retryable_errors == [500]

    def test_empty_interval_sequence_raises(self):
        with pytest.raises(ValidationError, match="interval_sequence must not be empty"):
            RetryPolicy(interval_sequence=[], retryable_errors=[500])

    def test_non_positive_interval_raises(self):
        with pytest.raises(ValidationError, match="interval_sequence entries must be positive"):
            RetryPolicy(interval_sequence=[2, 0], retryable_errors=[500])

    def test_empty_retryable_errors_raises(self):
        with pytest.raises(ValidationError, match="retryable_errors must not be empty"):
            RetryPolicy(interval_sequence=[2], retryable_errors=[])

    def test_out_of_range_retryable_error_raises(self):
        with pytest.raises(ValidationError, match="valid HTTP status codes"):
            RetryPolicy(interval_sequence=[2], retryable_errors=[999])

    def test_properties_roundtrip_through_kwargs_and_response(self):
        """timeout/retry survive the request -> kwargs -> worker -> response path (FR-014)."""
        props = ScheduleProperties(
            target_type=TargetType.REST,
            url="https://example.com/api",
            method=HttpMethod.POST,
            timeout=30,
            retry=RetryPolicy(interval_sequence=[2, 5], retryable_errors=[500, 502]),
        )

        # Service stores properties as JSON (Schedule.kwargs).
        dumped = props.model_dump(mode="json")
        assert dumped["timeout"] == 30
        assert dumped["retry"] == {"interval_sequence": [2, 5], "retryable_errors": [500, 502]}

        # Worker reconstructs ScheduleProperties(**kwargs).
        rebuilt = ScheduleProperties(**dumped)
        assert rebuilt.timeout == 30
        assert rebuilt.retry is not None
        assert rebuilt.retry.interval_sequence == [2, 5]

        # Response echoes stored properties verbatim.
        now = datetime.now(UTC)
        resp = ScheduleResponse(
            id="x",
            display_name="x",
            service_id="dial-native",
            next_run_time=None,
            trigger={},
            properties=dumped,
            is_deleted=False,
            created_by="u",
            created_at=now,
            updated_at=now,
        )
        assert resp.properties["timeout"] == 30
        assert resp.properties["retry"]["retryable_errors"] == [500, 502]


class TestScheduleMetadata:
    def test_request_metadata_defaults_to_empty_dict(self):
        req = ScheduleRequest(**_valid_request_kwargs())
        assert req.metadata == {}

    def test_request_accepts_metadata(self):
        req = ScheduleRequest(**_valid_request_kwargs(), metadata={"tenant": "hcscd", "env": "prod"})
        assert req.metadata == {"tenant": "hcscd", "env": "prod"}

    def test_request_metadata_allows_non_string_values(self):
        req = ScheduleRequest(**_valid_request_kwargs(), metadata={"count": 5, "active": True})
        assert req.metadata == {"count": 5, "active": True}

    def test_response_includes_metadata(self):
        now = datetime.now(UTC)
        resp = ScheduleResponse(
            id="x",
            display_name="x",
            service_id="dial-native",
            next_run_time=None,
            trigger={},
            properties={},
            metadata={"tenant": "hcscd"},
            is_deleted=False,
            created_by="u",
            created_at=now,
            updated_at=now,
        )
        assert resp.metadata == {"tenant": "hcscd"}

    def test_response_metadata_defaults_to_empty_dict(self):
        now = datetime.now(UTC)
        resp = ScheduleResponse(
            id="x",
            display_name="x",
            service_id="dial-native",
            next_run_time=None,
            trigger={},
            properties={},
            is_deleted=False,
            created_by="u",
            created_at=now,
            updated_at=now,
        )
        assert resp.metadata == {}


class TestScheduleDescription:
    def test_description_defaults_to_none(self):
        req = ScheduleRequest(**_valid_request_kwargs())
        assert req.description is None

    def test_description_accepted(self):
        req = ScheduleRequest(**_valid_request_kwargs(), description="Daily digest notes")
        assert req.description == "Daily digest notes"

    def test_description_max_length_accepted(self):
        req = ScheduleRequest(**_valid_request_kwargs(), description="x" * 1000)
        assert req.description is not None
        assert len(req.description) == 1000

    def test_description_over_max_length_raises(self):
        with pytest.raises(ValidationError, match="description"):
            ScheduleRequest(**_valid_request_kwargs(), description="x" * 1001)

    def test_whitespace_only_description_accepted(self):
        req = ScheduleRequest(**_valid_request_kwargs(), description="   ")
        assert req.description == "   "

    def test_response_includes_description(self):
        now = datetime.now(UTC)
        resp = ScheduleResponse(
            id="x",
            display_name="x",
            service_id="dial-native",
            next_run_time=None,
            trigger={},
            properties={},
            description="notes",
            is_deleted=False,
            created_by="u",
            created_at=now,
            updated_at=now,
        )
        assert resp.description == "notes"

    def test_response_description_defaults_to_none(self):
        now = datetime.now(UTC)
        resp = ScheduleResponse(
            id="x",
            display_name="x",
            service_id="dial-native",
            next_run_time=None,
            trigger={},
            properties={},
            is_deleted=False,
            created_by="u",
            created_at=now,
            updated_at=now,
        )
        assert resp.description is None

    def test_list_item_includes_description_and_trigger(self):
        now = datetime.now(UTC)
        item = ScheduleListItem(
            id="x",
            display_name="x",
            service_id="dial-native",
            next_run_time=None,
            trigger_type=TriggerType.CRON,
            trigger={"cron": {"fields": {"hour": 9}}},
            description="notes",
            is_deleted=False,
            created_by="u",
            created_at=now,
            updated_at=now,
        )
        assert item.description == "notes"
        assert item.trigger == {"cron": {"fields": {"hour": 9}}}

    def test_list_item_description_defaults_to_none(self):
        now = datetime.now(UTC)
        item = ScheduleListItem(
            id="x",
            display_name="x",
            service_id="dial-native",
            next_run_time=None,
            trigger_type=TriggerType.DATE,
            trigger={"date": "2026-06-12T09:00:00Z"},
            is_deleted=False,
            created_by="u",
            created_at=now,
            updated_at=now,
        )
        assert item.description is None


class TestScheduleRequest:
    def test_empty_display_name_raises(self):
        with pytest.raises(ValidationError, match="display_name must not be empty"):
            ScheduleRequest(
                display_name="   ",
                service_id="dial-native",
                trigger=ScheduleTrigger(cron=CronTriggerSpec(fields=CronFields(hour=9))),
                properties=ScheduleProperties(
                    target_type=TargetType.CHAT_COMPLETION,
                    url="https://example.com/api",
                    payload={"model": "gpt-4o"},
                    api_version="2025-01-01-preview",
                ),
            )

    def test_missing_service_id_raises(self):
        kwargs = _valid_request_kwargs()
        del kwargs["service_id"]
        with pytest.raises(ValidationError, match="service_id"):
            ScheduleRequest(**kwargs)

    def test_service_id_persisted(self):
        req = ScheduleRequest(**{**_valid_request_kwargs(), "service_id": "dial-api-key"})
        assert req.service_id == "dial-api-key"
