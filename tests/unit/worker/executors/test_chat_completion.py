import asyncio
import time
from urllib.parse import unquote

import httpx
import openai
from openai.types.chat import ChatCompletion
from pydantic import SecretStr
import pytest
from structlog.contextvars import bind_contextvars, clear_contextvars
import structlog.testing

from src.lib.constants import CONVERSATION_ID_HEADER
from src.lib.db.enums import TargetType
from src.lib.exceptions.errors import ScheduleRunError, StreamIdleTimeoutError
from src.lib.schemas.conversation import ConversationLocation
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.lib.utils.conversation_header import encode_header_value
from src.worker.conversation import conversation_id_for
from src.worker.executors.chat_completion import ChatCompletionExecutor


class _FakeStream:
    """Async context manager mimicking client.chat.completions.stream(...)."""

    def __init__(self, events, final, gaps=None):
        self._events = events
        self._final = final
        # Seconds to wait before each event, so tests can simulate thinking time or a stall.
        self._gaps = gaps or [0.0] * len(events)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def _gen():
            for gap, event in zip(self._gaps, self._events, strict=True):
                await asyncio.sleep(gap)
                yield event

        return _gen()

    async def get_final_completion(self):
        return self._final


def _mock_stream(mocker, events=None, final=None, gaps=None):
    """Patch AsyncAzureOpenAI wiring both .chat.completions.create (non-stream) and .stream."""
    mock_client_cls = mocker.patch("src.worker.executors.chat_completion.AsyncAzureOpenAI")
    mock_instance = mocker.MagicMock()
    mock_client_cls.return_value = mock_instance
    if final is None:
        final = mocker.MagicMock(spec=ChatCompletion)
        final.model_dump.return_value = {"id": "cmpl-1", "choices": []}
    stream = _FakeStream(events or [], final, gaps)
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
    provider.client.timeout = httpx.Timeout(60.0, connect=5.0)
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

    async def test_sdk_and_transport_errors_become_run_errors(self, mocker):
        for exc in (openai.APIConnectionError(request=mocker.MagicMock()), httpx.ConnectError("dns failure")):
            mock_client_cls = mocker.patch("src.worker.executors.chat_completion.AsyncAzureOpenAI")
            mock_instance = mocker.MagicMock()
            mock_client_cls.return_value = mock_instance
            mock_instance.chat.completions.create = mocker.AsyncMock(side_effect=exc)

            with pytest.raises(ScheduleRunError) as exc_info:
                await ChatCompletionExecutor().execute(CTX, CREDS, make_properties())

            assert exc_info.value.status_code is None

    async def test_unexpected_error_is_not_wrapped(self, mocker):
        """A bug is not a run error: it must escape so the job boundary logs it at error level."""
        mock_client_cls = mocker.patch("src.worker.executors.chat_completion.AsyncAzureOpenAI")
        mock_instance = mocker.MagicMock()
        mock_client_cls.return_value = mock_instance
        mock_instance.chat.completions.create = mocker.AsyncMock(side_effect=AttributeError("bug"))

        with pytest.raises(AttributeError):
            await ChatCompletionExecutor().execute(CTX, CREDS, make_properties())


class TestChatCompletionExecutorTimeout:
    async def test_effective_timeout_passed_through_when_set(self, mocker):
        _, mock_instance = _mock_azure(mocker)

        ctx = make_ctx(effective_timeout=25.0)
        props = make_properties()  # non-stream default → .create()
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(ctx, CREDS, props)

        sent = mock_instance.chat.completions.create.call_args.kwargs["timeout"]
        assert sent.read == 25.0
        assert sent.connect == 5.0

    async def test_no_timeout_kwarg_when_absent(self, mocker):
        _, mock_instance = _mock_azure(mocker)

        props = make_properties()  # non-stream default → .create()
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, CREDS, props)

        assert "timeout" not in mock_instance.chat.completions.create.call_args.kwargs


class TestChatCompletionExecutorIdleTimeout:
    """US1/US2 on the incident path: a DIAL quickapp reached through a streaming chat completion."""

    @staticmethod
    def _stream_properties() -> ScheduleProperties:
        return make_properties(
            payload={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        )

    async def test_healthy_long_stream_is_not_cut_off(self, mocker):
        """Ten 20ms gaps against a 100ms bound: every gap clears it, the total runs to twice it."""
        events = [mocker.MagicMock(type="chunk") for _ in range(10)]
        _mock_stream(mocker, events=events, gaps=[0.02] * len(events))
        ctx = make_ctx(effective_timeout=30.0, effective_idle_timeout=0.1)
        started = time.monotonic()

        with structlog.testing.capture_logs():
            result = await ChatCompletionExecutor().execute(ctx, CREDS, self._stream_properties())

        assert result == {"result": {"id": "cmpl-1", "choices": []}}
        assert time.monotonic() - started > 0.1, "premise broken: the stream did not outlive the idle bound"

    async def test_stalled_stream_raises_its_own_error(self, mocker):
        """The idle error must escape the executor's narrow ``except`` unchanged: rewrapping it as a
        plain ScheduleRunError would make the orchestrator log it as a generic failure."""
        events = [mocker.MagicMock(type="chunk"), mocker.MagicMock(type="chunk")]
        _mock_stream(mocker, events=events, gaps=[0.0, 0.5])
        ctx = make_ctx(effective_timeout=3600.0, effective_idle_timeout=0.05)

        with structlog.testing.capture_logs():
            with pytest.raises(StreamIdleTimeoutError, match="stream produced no event for 0.05s") as exc_info:
                await ChatCompletionExecutor().execute(ctx, CREDS, self._stream_properties())

        assert exc_info.value.status_code is None


def make_location(**kwargs) -> ConversationLocation:
    defaults = dict(
        deployment_id="gpt-4", bucket="bkt", schedule_id=SCHEDULE_ID, run_id=RUN_ID, display_name="Daily report"
    )
    defaults.update(kwargs)
    return ConversationLocation(**defaults)


def _conversation_header_keys(headers: dict[str, str]) -> list[str]:
    return [name for name in headers if name.lower() == CONVERSATION_ID_HEADER.lower()]


def _sent_headers(mock_instance, streaming: bool) -> dict[str, str]:
    call = mock_instance.chat.completions.stream if streaming else mock_instance.chat.completions.create
    return call.call_args.kwargs["extra_headers"]


_STREAMING_PAYLOAD = {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}], "stream": True}


class TestConversationIdHeader:
    """US1: chat completion runs carry the X-CONVERSATION-ID set on the run context."""

    @pytest.mark.parametrize("streaming", [False, True])
    async def test_header_is_the_encoded_conversation_id(self, mocker, streaming):
        _, mock_instance, _ = _mock_stream(mocker)
        location = make_location(display_name="Щоденний звіт")
        props = make_properties(payload=_STREAMING_PAYLOAD) if streaming else make_properties()

        with structlog.testing.capture_logs():
            await ChatCompletionExecutor().execute(
                make_ctx(conversation_id=conversation_id_for(location)), CREDS, props
            )

        headers = _sent_headers(mock_instance, streaming)
        assert headers[CONVERSATION_ID_HEADER] == encode_header_value(conversation_id_for(location))

    async def test_ascii_header_equals_the_stored_conversation_id(self, mocker):
        _, mock_instance, _ = _mock_stream(mocker)
        location = make_location()

        with structlog.testing.capture_logs():
            await ChatCompletionExecutor().execute(
                make_ctx(conversation_id=conversation_id_for(location)), CREDS, make_properties()
            )

        assert _sent_headers(mock_instance, False)[CONVERSATION_ID_HEADER] == conversation_id_for(location)

    async def test_no_location_passes_caller_header_through(self, mocker):
        _, mock_instance, _ = _mock_stream(mocker)
        props = make_properties(extra_headers={"x-conversation-id": "manual", "X-Other": "1"})

        with structlog.testing.capture_logs():
            await ChatCompletionExecutor().execute(CTX, CREDS, props)

        assert _sent_headers(mock_instance, False) == {"x-conversation-id": "manual", "X-Other": "1"}

    async def test_scheduler_value_replaces_caller_header(self, mocker):
        _, mock_instance, _ = _mock_stream(mocker)
        location = make_location()
        props = make_properties(extra_headers={"x-Conversation-ID": "manual", "X-Other": "1"})

        with structlog.testing.capture_logs():
            await ChatCompletionExecutor().execute(
                make_ctx(conversation_id=conversation_id_for(location)), CREDS, props
            )

        headers = _sent_headers(mock_instance, False)
        assert _conversation_header_keys(headers) == [CONVERSATION_ID_HEADER]
        assert headers[CONVERSATION_ID_HEADER] == conversation_id_for(location)
        assert headers["X-Other"] == "1"

    async def test_caller_user_agent_is_still_stripped(self, mocker):
        _, mock_instance, _ = _mock_stream(mocker)
        props = make_properties(extra_headers={"User-Agent": "spoofed", "user-agent": "spoofed"})

        with structlog.testing.capture_logs():
            await ChatCompletionExecutor().execute(
                make_ctx(conversation_id=conversation_id_for(make_location())), CREDS, props
            )

        headers = _sent_headers(mock_instance, False)
        assert not [name for name in headers if name.lower() == "user-agent"]
        assert CONVERSATION_ID_HEADER in headers

    async def test_location_without_header_id_sends_no_header(self, mocker):
        # An id too long for a header keeps the location (the conversation is saved) but no header.
        _, mock_instance, _ = _mock_stream(mocker)

        with structlog.testing.capture_logs():
            await ChatCompletionExecutor().execute(
                make_ctx(conversation_location=make_location()), CREDS, make_properties()
            )

        assert _conversation_header_keys(_sent_headers(mock_instance, False)) == []

    @pytest.mark.parametrize("streaming", [False, True])
    async def test_header_omitted_without_location(self, mocker, streaming):
        # Omitted entirely -- never sent as an empty value.
        _, mock_instance, _ = _mock_stream(mocker)
        props = make_properties(payload=_STREAMING_PAYLOAD) if streaming else make_properties()

        with structlog.testing.capture_logs():
            await ChatCompletionExecutor().execute(CTX, CREDS, props)

        assert _conversation_header_keys(_sent_headers(mock_instance, streaming)) == []


_COMPLETION_JSON = {
    "id": "cmpl-wire",
    "object": "chat.completion",
    "created": 0,
    "model": "gpt-4",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
}


class TestConversationHeaderOnTheWire:
    """US3: through the real SDK and httpx, any display name yields a sendable ASCII header."""

    @pytest.mark.parametrize("display_name", ["Daily report", "Щоденний звіт", "📈 KPI", "50% done", "line1\nline2"])
    async def test_run_succeeds_and_header_is_ascii(self, display_name):
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json=_COMPLETION_JSON)

        location = make_location(display_name=display_name)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            ctx = make_ctx(http_client=http_client, conversation_id=conversation_id_for(location))
            with structlog.testing.capture_logs():
                result = await ChatCompletionExecutor().execute(ctx, CREDS, make_properties())

        assert result["result"]["id"] == "cmpl-wire"
        header = captured[0].headers["x-conversation-id"]
        assert header.isascii()
        assert "\n" not in header
        assert "\r" not in header
        assert unquote(header) == unquote(conversation_id_for(location))
        assert header == encode_header_value(conversation_id_for(location))

    async def test_ascii_name_is_sent_byte_for_byte_as_stored(self):
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json=_COMPLETION_JSON)

        location = make_location(display_name="Daily report")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            ctx = make_ctx(http_client=http_client, conversation_id=conversation_id_for(location))
            with structlog.testing.capture_logs():
                await ChatCompletionExecutor().execute(ctx, CREDS, make_properties())

        assert captured[0].headers["x-conversation-id"] == conversation_id_for(location)
        assert captured[0].headers.get_list("x-conversation-id") == [conversation_id_for(location)]
