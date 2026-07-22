import httpx
import openai
from openai.types.chat import ChatCompletion
from pydantic import SecretStr
import pytest
from structlog.contextvars import bind_contextvars, clear_contextvars
import structlog.testing

from src.lib.db.enums import TargetType
from src.lib.exceptions.errors import ScheduleRunError
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.worker.executors.chat_completion import ChatCompletionExecutor


class _FakeStream:
    """Async context manager mimicking client.chat.completions.stream(...)."""

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

    async def get_final_completion(self):
        return self._final


def _mock_stream(mocker, events=None, final=None):
    """Patch AsyncAzureOpenAI wiring both .chat.completions.create (non-stream) and .stream."""
    mock_client_cls = mocker.patch("src.worker.executors.chat_completion.AsyncAzureOpenAI")
    mock_instance = mocker.MagicMock()
    mock_client_cls.return_value = mock_instance
    if final is None:
        final = mocker.MagicMock(spec=ChatCompletion)
        final.model_dump.return_value = {"id": "cmpl-1", "choices": []}
    stream = _FakeStream(events or [], final)
    mock_instance.chat.completions.stream = mocker.MagicMock(return_value=stream)
    mock_instance.chat.completions.create = mocker.AsyncMock(return_value=final)
    return mock_client_cls, mock_instance, stream


RUN_ID = "run-cc-001"
SCHEDULE_ID = "sched-cc-001"
CTX = RunContext(run_id=RUN_ID, schedule_id=SCHEDULE_ID)
CREDS = OboCredentials(header_name="Api-Key", header_value=SecretStr("obo-token"))


def make_ctx(**kwargs) -> RunContext:
    return RunContext(run_id=RUN_ID, schedule_id=SCHEDULE_ID, **kwargs)


def make_creds(header_value: str, header_name: str = "Api-Key") -> OboCredentials:
    return OboCredentials(header_name=header_name, header_value=SecretStr(header_value))


CHAT_COMPLETION_URL = "https://dial.example.com/openai/"


def make_properties(**kwargs) -> ScheduleProperties:
    defaults = dict(
        target_type=TargetType.CHAT_COMPLETION,
        url=CHAT_COMPLETION_URL,
        payload={"model": "gpt-4", "messages": [{"role": "user", "content": "hello"}]},
        extra_headers={},
        api_version="2024-02-01",
    )
    defaults.update(kwargs)
    return ScheduleProperties(**defaults)


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
    mocker.patch("src.worker.executors.chat_completion.HttpClientProvider", return_value=provider)
    return provider.client


def _mock_azure(mocker):
    mock_client_cls, mock_instance, _ = _mock_stream(mocker)
    return mock_client_cls, mock_instance


class TestChatCompletionExecutorHappyPath:
    async def test_base_url_passed_verbatim(self, mocker):
        mock_client_cls, _ = _mock_azure(mocker)

        props = make_properties()
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, CREDS, props)

        assert mock_client_cls.call_args.kwargs["base_url"] == CHAT_COMPLETION_URL

    async def test_model_passed_in_payload_not_as_deployment(self, mocker):
        mock_client_cls, mock_instance = _mock_azure(mocker)

        props = make_properties()  # non-streaming default → .create()
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, CREDS, props)

        # The client is built with base_url only — no azure_endpoint/azure_deployment.
        assert "azure_deployment" not in mock_client_cls.call_args.kwargs
        assert "azure_endpoint" not in mock_client_cls.call_args.kwargs
        # The model rides the request payload.
        assert mock_instance.chat.completions.create.call_args.kwargs["model"] == "gpt-4"

    async def test_api_key_type_service_passes_value_verbatim(self, mocker):
        mock_client_cls, _ = _mock_azure(mocker)

        props = make_properties()
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, make_creds("raw-api-key", header_name="Api-Key"), props)

        assert mock_client_cls.call_args.kwargs["api_key"] == "raw-api-key"

    async def test_oauth_type_service_strips_bearer_prefix(self, mocker):
        mock_client_cls, _ = _mock_azure(mocker)

        props = make_properties()
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, make_creds("Bearer abc.def.ghi", header_name="Authorization"), props)

        assert mock_client_cls.call_args.kwargs["api_key"] == "abc.def.ghi"

    async def test_method_is_ignored(self, mocker):
        _mock_azure(mocker)

        props = make_properties()  # method is None for chat_completion
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, CREDS, props)  # should not raise

    async def test_stream_events_log_event_type_only(self, mocker):
        events = [mocker.MagicMock(type="chunk"), mocker.MagicMock(type="content.delta")]
        _mock_stream(mocker, events=events)

        props = make_properties(
            payload={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        )
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs() as logs:
            await executor.execute(CTX, CREDS, props)

        event_logs = [entry for entry in logs if entry.get("event") == "stream_event"]
        assert len(event_logs) == 2
        assert {e["event_type"] for e in event_logs} == {"chunk", "content.delta"}

    async def test_returns_assembled_result_for_non_stream(self, mocker):
        final = mocker.MagicMock(spec=ChatCompletion)
        final.model_dump.return_value = {"id": "cmpl-1", "choices": []}
        _mock_stream(mocker, final=final)

        props = make_properties()  # stream defaults to False
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            result = await executor.execute(CTX, CREDS, props)

        assert result == {"result": {"id": "cmpl-1", "choices": []}}

    async def test_returns_assembled_result_for_stream(self, mocker):
        final = mocker.MagicMock(spec=ChatCompletion)
        final.model_dump.return_value = {"id": "cmpl-2", "choices": []}
        _mock_stream(mocker, events=[mocker.MagicMock(type="chunk")], final=final)

        props = make_properties(
            payload={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        )
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            result = await executor.execute(CTX, CREDS, props)

        assert result == {"result": {"id": "cmpl-2", "choices": []}}

    async def test_stream_flag_not_passed_to_client(self, mocker):
        _, mock_instance, _ = _mock_stream(mocker)

        props = make_properties(
            payload={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        )
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, CREDS, props)

        assert "stream" not in mock_instance.chat.completions.stream.call_args.kwargs

    async def test_obo_secret_not_in_logs(self, mocker):
        _mock_azure(mocker)

        props = make_properties()
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs() as logs:
            await executor.execute(CTX, make_creds("super-secret-cc"), props)

        for log in logs:
            assert "super-secret-cc" not in str(log)


class TestChatCompletionExecutorFailurePath:
    async def test_api_status_error_captures_status_code(self, mocker):
        mock_client_cls = mocker.patch("src.worker.executors.chat_completion.AsyncAzureOpenAI")
        mock_instance = mocker.MagicMock()
        mock_client_cls.return_value = mock_instance
        response = mocker.MagicMock()
        response.status_code = 500
        api_error = openai.APIStatusError("server error", response=response, body=None)
        mock_instance.chat.completions.create = mocker.AsyncMock(side_effect=api_error)

        props = make_properties()  # non-stream default → .create()
        executor = ChatCompletionExecutor()

        with pytest.raises(ScheduleRunError) as exc_info:
            await executor.execute(CTX, CREDS, props)

        assert exc_info.value.status_code == 500

    async def test_generic_error_has_no_status_code(self, mocker):
        mock_client_cls = mocker.patch("src.worker.executors.chat_completion.AsyncAzureOpenAI")
        mock_instance = mocker.MagicMock()
        mock_client_cls.return_value = mock_instance
        mock_instance.chat.completions.create = mocker.AsyncMock(side_effect=ValueError("bad payload"))

        props = make_properties()  # non-stream default → .create()
        executor = ChatCompletionExecutor()

        with pytest.raises(ScheduleRunError) as exc_info:
            await executor.execute(CTX, CREDS, props)

        assert exc_info.value.status_code is None


class TestChatCompletionExecutorTimeout:
    async def test_effective_timeout_passed_through_when_set(self, mocker):
        _, mock_instance = _mock_azure(mocker)

        ctx = make_ctx(effective_timeout=25.0)
        props = make_properties()  # non-stream default → .create()
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(ctx, CREDS, props)

        assert mock_instance.chat.completions.create.call_args.kwargs["timeout"] == 25.0

    async def test_no_timeout_kwarg_when_absent(self, mocker):
        _, mock_instance = _mock_azure(mocker)

        props = make_properties()  # non-stream default → .create()
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, CREDS, props)

        assert "timeout" not in mock_instance.chat.completions.create.call_args.kwargs
