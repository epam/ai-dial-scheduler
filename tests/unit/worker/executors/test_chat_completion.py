import httpx
import openai
from openai.types.chat import ChatCompletion
from pydantic import SecretStr
import pytest
from structlog.contextvars import bind_contextvars, clear_contextvars
import structlog.testing

from src.lib.db.enums import TargetType
from src.lib.exceptions.errors import ScheduleRunError
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.worker.executors.chat_completion import ChatCompletionExecutor


RUN_ID = "run-cc-001"
SCHEDULE_ID = "sched-cc-001"
CTX = RunContext(
    run_id=RUN_ID,
    schedule_id=SCHEDULE_ID,
    auth_header_name="Api-Key",
    auth_header_value=SecretStr("obo-token"),
)


def make_ctx(header_value: str, header_name: str = "Api-Key", **kwargs) -> RunContext:
    return RunContext(
        run_id=RUN_ID,
        schedule_id=SCHEDULE_ID,
        auth_header_name=header_name,
        auth_header_value=SecretStr(header_value),
        **kwargs,
    )


def make_properties(**kwargs) -> ScheduleProperties:
    defaults = dict(
        target_type=TargetType.CHAT_COMPLETION,
        url="https://dial.example.com/openai/deployments/gpt-4/chat/completions",
        payload={"messages": [{"role": "user", "content": "hello"}]},
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
    mock_client_cls = mocker.patch("src.worker.executors.chat_completion.AsyncAzureOpenAI")
    mock_instance = mocker.AsyncMock()
    mock_client_cls.return_value = mock_instance
    mock_instance.chat.completions.create = mocker.AsyncMock(return_value=mocker.MagicMock(spec=ChatCompletion))
    return mock_client_cls, mock_instance


class TestChatCompletionExecutorHappyPath:
    async def test_azure_endpoint_uses_scheme_and_netloc(self, mocker):
        mock_client_cls, _ = _mock_azure(mocker)

        props = make_properties()
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, props)

        assert mock_client_cls.call_args.kwargs["azure_endpoint"] == "https://dial.example.com"

    async def test_deployment_extracted_from_url_path(self, mocker):
        mock_client_cls, _ = _mock_azure(mocker)

        props = make_properties()
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, props)

        assert mock_client_cls.call_args.kwargs["azure_deployment"] == "gpt-4"

    async def test_api_key_type_service_passes_value_verbatim(self, mocker):
        mock_client_cls, _ = _mock_azure(mocker)

        props = make_properties()
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(make_ctx("raw-api-key", header_name="Api-Key"), props)

        assert mock_client_cls.call_args.kwargs["api_key"] == "raw-api-key"

    async def test_oauth_type_service_strips_bearer_prefix(self, mocker):
        mock_client_cls, _ = _mock_azure(mocker)

        props = make_properties()
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(make_ctx("Bearer abc.def.ghi", header_name="Authorization"), props)

        assert mock_client_cls.call_args.kwargs["api_key"] == "abc.def.ghi"

    async def test_method_is_ignored(self, mocker):
        _mock_azure(mocker)

        props = make_properties()  # method is None for chat_completion
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, props)  # should not raise

    async def test_single_response_logs_one_record(self, mocker):
        _mock_azure(mocker)

        props = make_properties()
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs() as logs:
            await executor.execute(CTX, props)

        result_logs = [entry for entry in logs if entry.get("event") in ("run_result", "stream_chunk")]
        assert len(result_logs) == 1
        assert result_logs[0]["run_id"] == RUN_ID

    async def test_stream_logs_one_record_per_chunk(self, mocker):
        mock_client_cls = mocker.patch("src.worker.executors.chat_completion.AsyncAzureOpenAI")
        mock_instance = mocker.AsyncMock()
        mock_client_cls.return_value = mock_instance

        chunks = [mocker.MagicMock(), mocker.MagicMock(), mocker.MagicMock()]

        async def async_stream():
            for c in chunks:
                yield c

        mock_instance.chat.completions.create = mocker.AsyncMock(return_value=async_stream())

        props = make_properties(payload={"messages": [{"role": "user", "content": "hi"}], "stream": True})
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs() as logs:
            await executor.execute(CTX, props)

        chunk_logs = [entry for entry in logs if entry.get("event") == "stream_chunk"]
        assert len(chunk_logs) == 3

    async def test_returns_result_dict_for_non_stream(self, mocker):
        mock_client_cls = mocker.patch("src.worker.executors.chat_completion.AsyncAzureOpenAI")
        mock_instance = mocker.AsyncMock()
        mock_client_cls.return_value = mock_instance
        completion = mocker.MagicMock(spec=ChatCompletion)
        completion.model_dump.return_value = {"id": "cmpl-1", "choices": []}
        mock_instance.chat.completions.create = mocker.AsyncMock(return_value=completion)

        props = make_properties()
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            result = await executor.execute(CTX, props)

        assert "result" in result

    async def test_returns_chunks_list_for_stream(self, mocker):
        mock_client_cls = mocker.patch("src.worker.executors.chat_completion.AsyncAzureOpenAI")
        mock_instance = mocker.AsyncMock()
        mock_client_cls.return_value = mock_instance

        chunk1 = mocker.MagicMock()
        chunk1.model_dump.return_value = {"id": "c1"}
        chunk2 = mocker.MagicMock()
        chunk2.model_dump.return_value = {"id": "c2"}

        async def async_stream():
            yield chunk1
            yield chunk2

        mock_instance.chat.completions.create = mocker.AsyncMock(return_value=async_stream())

        props = make_properties(payload={"messages": [{"role": "user", "content": "hi"}], "stream": True})
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            result = await executor.execute(CTX, props)

        assert "chunks" in result
        assert len(result["chunks"]) == 2

    async def test_obo_secret_not_in_logs(self, mocker):
        _mock_azure(mocker)

        props = make_properties()
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs() as logs:
            await executor.execute(make_ctx("super-secret-cc"), props)

        for log in logs:
            assert "super-secret-cc" not in str(log)


class TestChatCompletionExecutorFailurePath:
    async def test_api_status_error_captures_status_code(self, mocker):
        mock_client_cls = mocker.patch("src.worker.executors.chat_completion.AsyncAzureOpenAI")
        mock_instance = mocker.AsyncMock()
        mock_client_cls.return_value = mock_instance
        response = mocker.MagicMock()
        response.status_code = 500
        api_error = openai.APIStatusError("server error", response=response, body=None)
        mock_instance.chat.completions.create = mocker.AsyncMock(side_effect=api_error)

        props = make_properties()
        executor = ChatCompletionExecutor()

        with pytest.raises(ScheduleRunError) as exc_info:
            await executor.execute(CTX, props)

        assert exc_info.value.status_code == 500

    async def test_generic_error_has_no_status_code(self, mocker):
        mock_client_cls = mocker.patch("src.worker.executors.chat_completion.AsyncAzureOpenAI")
        mock_instance = mocker.AsyncMock()
        mock_client_cls.return_value = mock_instance
        mock_instance.chat.completions.create = mocker.AsyncMock(side_effect=ValueError("bad payload"))

        props = make_properties()
        executor = ChatCompletionExecutor()

        with pytest.raises(ScheduleRunError) as exc_info:
            await executor.execute(CTX, props)

        assert exc_info.value.status_code is None


class TestChatCompletionExecutorTimeout:
    async def test_effective_timeout_passed_through_when_set(self, mocker):
        _, mock_instance = _mock_azure(mocker)

        ctx = make_ctx("obo-token", effective_timeout=25.0)
        props = make_properties()
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(ctx, props)

        assert mock_instance.chat.completions.create.call_args.kwargs["timeout"] == 25.0

    async def test_no_timeout_kwarg_when_absent(self, mocker):
        _, mock_instance = _mock_azure(mocker)

        props = make_properties()
        executor = ChatCompletionExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, props)

        assert "timeout" not in mock_instance.chat.completions.create.call_args.kwargs
