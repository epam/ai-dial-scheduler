import httpx
import openai
from openai.types.responses import Response
from pydantic import SecretStr
import pytest
from structlog.contextvars import bind_contextvars, clear_contextvars
import structlog.testing

from src.lib.db.enums import TargetType
from src.lib.exceptions.errors import ScheduleRunError
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.worker.executors.responses import ResponsesExecutor


class _FakeStream:
    """Async context manager mimicking client.responses.stream(...)."""

    def __init__(self, events, final):
        self._events = events
        self._final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def _gen():
            for event in self._events:
                yield event

        return _gen()

    async def get_final_response(self):
        return self._final


def _mock_client(mocker, final=None, events=None, create_side_effect=None, stream_side_effect=None):
    """Patch AsyncAzureOpenAI wiring both .responses.create (non-stream) and .responses.stream."""
    mock_client_cls = mocker.patch("src.worker.executors.responses.AsyncAzureOpenAI")
    mock_instance = mocker.MagicMock()
    mock_client_cls.return_value = mock_instance
    if final is None:
        final = mocker.MagicMock(spec=Response)
        final.model_dump.return_value = {"id": "resp-1", "output": []}
    if create_side_effect is not None:
        mock_instance.responses.create = mocker.AsyncMock(side_effect=create_side_effect)
    else:
        mock_instance.responses.create = mocker.AsyncMock(return_value=final)
    if stream_side_effect is not None:
        mock_instance.responses.stream = mocker.MagicMock(side_effect=stream_side_effect)
    else:
        mock_instance.responses.stream = mocker.MagicMock(return_value=_FakeStream(events or [], final))
    return mock_client_cls, mock_instance, final


RUN_ID = "run-resp-001"
SCHEDULE_ID = "sched-resp-001"
CTX = RunContext(run_id=RUN_ID, schedule_id=SCHEDULE_ID)
CREDS = OboCredentials(header_name="Api-Key", header_value=SecretStr("obo-token"))


def make_ctx(**kwargs) -> RunContext:
    return RunContext(run_id=RUN_ID, schedule_id=SCHEDULE_ID, **kwargs)


def make_creds(header_value: str, header_name: str = "Api-Key") -> OboCredentials:
    return OboCredentials(header_name=header_name, header_value=SecretStr(header_value))


RESPONSES_URL = "https://dial.example.com/openai/v1/responses"


def make_properties(**kwargs) -> ScheduleProperties:
    defaults = dict(
        target_type=TargetType.RESPONSES,
        url=RESPONSES_URL,
        payload={"input": "hello", "model": "gpt-5.6"},
        extra_headers={},
        api_version="2025-01-01-preview",
    )
    defaults.update(kwargs)
    return ScheduleProperties(**defaults)


def make_stream_properties(**payload_extra) -> ScheduleProperties:
    """Properties with streaming enabled (payload.stream=True)."""
    return make_properties(payload={"input": "hi", "model": "gpt-5.6", "stream": True, **payload_extra})


@pytest.fixture(autouse=True)
def setup_context():
    clear_contextvars()
    bind_contextvars(run_id=RUN_ID, schedule_id=SCHEDULE_ID)
    yield
    clear_contextvars()


@pytest.fixture(autouse=True)
def mock_http_client(mocker):
    provider = mocker.MagicMock()
    provider.client = mocker.AsyncMock(spec=httpx.AsyncClient)
    mocker.patch("src.worker.executors.responses.HttpClientProvider", return_value=provider)
    return provider.client


class TestResponsesExecutorHappyPath:
    async def test_base_url_passed_verbatim(self, mocker):
        mock_client_cls, _, _ = _mock_client(mocker)

        props = make_properties()
        executor = ResponsesExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, CREDS, props)

        assert mock_client_cls.call_args.kwargs["base_url"] == RESPONSES_URL

    async def test_model_passed_in_payload_not_as_deployment(self, mocker):
        mock_client_cls, mock_instance, _ = _mock_client(mocker)

        props = make_properties()  # non-streaming default → .create()
        executor = ResponsesExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, CREDS, props)

        # The Responses client is built with base_url only — no azure_deployment.
        assert "azure_deployment" not in mock_client_cls.call_args.kwargs
        # The model rides the request payload.
        assert mock_instance.responses.create.call_args.kwargs["model"] == "gpt-5.6"

    async def test_api_key_type_service_passes_value_verbatim(self, mocker):
        mock_client_cls, _, _ = _mock_client(mocker)

        props = make_properties()
        executor = ResponsesExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, make_creds("raw-api-key", header_name="Api-Key"), props)

        assert mock_client_cls.call_args.kwargs["api_key"] == "raw-api-key"

    async def test_oauth_type_service_strips_bearer_prefix(self, mocker):
        mock_client_cls, _, _ = _mock_client(mocker)

        props = make_properties()
        executor = ResponsesExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, make_creds("Bearer abc.def.ghi", header_name="Authorization"), props)

        assert mock_client_cls.call_args.kwargs["api_key"] == "abc.def.ghi"

    async def test_non_stream_uses_create_not_stream(self, mocker):
        _, mock_instance, _ = _mock_client(mocker)

        props = make_properties()  # stream defaults to False
        executor = ResponsesExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, CREDS, props)

        mock_instance.responses.create.assert_called_once()
        mock_instance.responses.stream.assert_not_called()

    async def test_stream_uses_stream_not_create(self, mocker):
        _, mock_instance, _ = _mock_client(mocker)

        props = make_stream_properties()
        executor = ResponsesExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, CREDS, props)

        mock_instance.responses.stream.assert_called_once()
        mock_instance.responses.create.assert_not_called()

    async def test_stream_events_log_event_type_only(self, mocker):
        events = [mocker.MagicMock(type="response.created"), mocker.MagicMock(type="response.output_text.delta")]
        _mock_client(mocker, events=events)

        props = make_stream_properties()
        executor = ResponsesExecutor()

        with structlog.testing.capture_logs() as logs:
            await executor.execute(CTX, CREDS, props)

        event_logs = [entry for entry in logs if entry.get("event") == "stream_event"]
        assert len(event_logs) == 2
        assert {e["event_type"] for e in event_logs} == {"response.created", "response.output_text.delta"}

    async def test_returns_assembled_result_for_non_stream(self, mocker):
        final = mocker.MagicMock(spec=Response)
        final.model_dump.return_value = {"id": "resp-1", "output": []}
        _mock_client(mocker, final=final)

        props = make_properties()  # stream defaults to False
        executor = ResponsesExecutor()

        with structlog.testing.capture_logs():
            result = await executor.execute(CTX, CREDS, props)

        assert result == {"result": {"id": "resp-1", "output": []}}

    async def test_returns_assembled_result_for_stream(self, mocker):
        final = mocker.MagicMock(spec=Response)
        final.model_dump.return_value = {"id": "resp-2", "output": []}
        _mock_client(mocker, events=[mocker.MagicMock(type="response.created")], final=final)

        props = make_stream_properties()
        executor = ResponsesExecutor()

        with structlog.testing.capture_logs():
            result = await executor.execute(CTX, CREDS, props)

        assert result == {"result": {"id": "resp-2", "output": []}}

    async def test_stream_flag_not_passed_to_client(self, mocker):
        _, mock_instance, _ = _mock_client(mocker)

        props = make_stream_properties()
        executor = ResponsesExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, CREDS, props)

        assert "stream" not in mock_instance.responses.stream.call_args.kwargs

    async def test_stream_flag_not_passed_to_create(self, mocker):
        _, mock_instance, _ = _mock_client(mocker)

        props = make_properties(payload={"input": "hi", "model": "gpt-5.6", "stream": False})
        executor = ResponsesExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, CREDS, props)

        assert "stream" not in mock_instance.responses.create.call_args.kwargs

    async def test_extra_headers_forwarded(self, mocker):
        _, mock_instance, _ = _mock_client(mocker)

        props = make_properties(extra_headers={"tenant_id": "hcscd"})  # non-stream → .create()
        executor = ResponsesExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, CREDS, props)

        assert mock_instance.responses.create.call_args.kwargs["extra_headers"] == {"tenant_id": "hcscd"}

    async def test_obo_secret_not_in_logs(self, mocker):
        _mock_client(mocker)

        props = make_properties()
        executor = ResponsesExecutor()

        with structlog.testing.capture_logs() as logs:
            await executor.execute(CTX, make_creds("super-secret-resp"), props)

        for log in logs:
            assert "super-secret-resp" not in str(log)


class TestResponsesExecutorFailurePath:
    async def test_api_status_error_captures_status_code(self, mocker):
        response = mocker.MagicMock()
        response.status_code = 500
        api_error = openai.APIStatusError("server error", response=response, body=None)
        _mock_client(mocker, create_side_effect=api_error)

        props = make_properties()
        executor = ResponsesExecutor()

        with pytest.raises(ScheduleRunError) as exc_info:
            await executor.execute(CTX, CREDS, props)

        assert exc_info.value.status_code == 500

    async def test_generic_error_has_no_status_code(self, mocker):
        _mock_client(mocker, create_side_effect=ValueError("bad payload"))

        props = make_properties()
        executor = ResponsesExecutor()

        with pytest.raises(ScheduleRunError) as exc_info:
            await executor.execute(CTX, CREDS, props)

        assert exc_info.value.status_code is None

    async def test_stream_api_status_error_captures_status_code(self, mocker):
        response = mocker.MagicMock()
        response.status_code = 500
        api_error = openai.APIStatusError("server error", response=response, body=None)
        _mock_client(mocker, stream_side_effect=api_error)

        props = make_stream_properties()
        executor = ResponsesExecutor()

        with pytest.raises(ScheduleRunError) as exc_info:
            await executor.execute(CTX, CREDS, props)

        assert exc_info.value.status_code == 500


class TestResponsesExecutorPolicyParity:
    """US2/US3: the executor behaves identically to chat completion under the shared
    timeout/retry orchestration and stores the same assembled result shape regardless of transport."""

    async def test_retryable_status_is_retried_then_succeeds(self, mocker):
        from src.lib.schemas.schedule import RetryPolicy
        from src.worker.retry import execute_with_policy

        mocker.patch("src.worker.retry.asyncio.sleep", new=mocker.AsyncMock())
        response = mocker.MagicMock()
        response.status_code = 503
        api_error = openai.APIStatusError("unavailable", response=response, body=None)

        final = mocker.MagicMock(spec=Response)
        final.model_dump.return_value = {"id": "resp-ok", "output": []}
        # Non-streaming default: first .create() raises a retryable error, second succeeds.
        _, mock_instance, _ = _mock_client(mocker, create_side_effect=[api_error, final])

        props = make_properties(retry=RetryPolicy(interval_sequence=[0.01], retryable_errors=[503]))
        executor = ResponsesExecutor()

        with structlog.testing.capture_logs():
            outcome = await execute_with_policy(make_ctx(), CREDS, props, executor)

        assert outcome.succeeded is True
        assert outcome.result == {"result": {"id": "resp-ok", "output": []}}
        assert outcome.attempt_count == 2

    async def test_non_retryable_status_is_terminal(self, mocker):
        from src.worker.retry import execute_with_policy

        response = mocker.MagicMock()
        response.status_code = 400
        api_error = openai.APIStatusError("bad request", response=response, body=None)
        _mock_client(mocker, create_side_effect=api_error)

        props = make_properties()  # no retry policy
        executor = ResponsesExecutor()

        with structlog.testing.capture_logs():
            outcome = await execute_with_policy(make_ctx(), CREDS, props, executor)

        assert outcome.succeeded is False
        assert outcome.error is not None
        assert outcome.error.status_code == 400

    async def test_stream_and_non_stream_produce_same_result_shape(self, mocker):
        # Non-streaming (.create)
        final_ns = mocker.MagicMock(spec=Response)
        final_ns.model_dump.return_value = {"id": "resp-x", "output": [{"a": 1}]}
        _mock_client(mocker, final=final_ns)
        with structlog.testing.capture_logs():
            result_ns = await ResponsesExecutor().execute(CTX, CREDS, make_properties())

        # Streaming (.stream)
        final_s = mocker.MagicMock(spec=Response)
        final_s.model_dump.return_value = {"id": "resp-x", "output": [{"a": 1}]}
        _mock_client(mocker, events=[mocker.MagicMock(type="response.created")], final=final_s)
        with structlog.testing.capture_logs():
            result_s = await ResponsesExecutor().execute(CTX, CREDS, make_stream_properties())

        assert result_ns == result_s
        assert set(result_ns.keys()) == {"result"}


class TestResponsesExecutorTimeout:
    async def test_effective_timeout_passed_through_when_set(self, mocker):
        _, mock_instance, _ = _mock_client(mocker)

        ctx = make_ctx(effective_timeout=25.0)
        props = make_properties()  # non-stream → .create()
        executor = ResponsesExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(ctx, CREDS, props)

        assert mock_instance.responses.create.call_args.kwargs["timeout"] == 25.0

    async def test_no_timeout_kwarg_when_absent(self, mocker):
        _, mock_instance, _ = _mock_client(mocker)

        props = make_properties()
        executor = ResponsesExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, CREDS, props)

        assert "timeout" not in mock_instance.responses.create.call_args.kwargs
