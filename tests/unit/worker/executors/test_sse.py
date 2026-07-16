import httpx
from pydantic import SecretStr
import pytest
from structlog.contextvars import bind_contextvars, clear_contextvars
import structlog.testing

from src.lib.db.enums import HttpMethod, TargetType
from src.lib.exceptions.errors import ScheduleRunError
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.worker.executors.sse import SseExecutor


RUN_ID = "run-sse-001"
SCHEDULE_ID = "sched-sse-001"
CTX = RunContext(
    run_id=RUN_ID,
    schedule_id=SCHEDULE_ID,
    auth_header_name="Api-Key",
    auth_header_value=SecretStr("obo-token"),
)

# (header_name, header_value) shapes for both external-service auth types.
AUTH_TYPE_CASES = [
    ("Api-Key", "user-api-key"),
    ("Authorization", "Bearer user.oauth.token"),
]


def make_properties(**kwargs) -> ScheduleProperties:
    defaults = dict(
        target_type=TargetType.SSE,
        method=HttpMethod.POST,
        url="https://example.com/stream",
        payload={"prompt": "hello"},
        extra_headers={},
    )
    defaults.update(kwargs)
    return ScheduleProperties(**defaults)


@pytest.fixture(autouse=True)
def setup_context():
    clear_contextvars()
    bind_contextvars(run_id=RUN_ID, schedule_id=SCHEDULE_ID)
    yield
    clear_contextvars()


@pytest.fixture
def mock_client(mocker):
    client = mocker.AsyncMock(spec=httpx.AsyncClient)
    provider = mocker.MagicMock()
    provider.client = client
    mocker.patch("src.worker.executors.sse.HttpClientProvider", return_value=provider)
    return client


class TestSseExecutorHappyPath:
    async def test_aconnect_sse_called_with_method_and_url(self, mock_client, mocker):
        async def async_iter():
            return
            yield

        event_source = mocker.MagicMock()
        event_source.response = mocker.MagicMock(status_code=200)
        event_source.aiter_sse = async_iter
        event_source.__aenter__ = mocker.AsyncMock(return_value=event_source)
        event_source.__aexit__ = mocker.AsyncMock(return_value=False)

        mock_connect = mocker.patch("src.worker.executors.sse.aconnect_sse", return_value=event_source)

        props = make_properties()
        executor = SseExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, props)

        call_kwargs = mock_connect.call_args
        assert call_kwargs.args[1] == "POST"
        assert call_kwargs.args[2] == "https://example.com/stream"

    @pytest.mark.parametrize("header_name, header_value", AUTH_TYPE_CASES)
    async def test_injects_obo_header_and_no_shared_key(self, mock_client, mocker, header_name, header_value):
        async def async_iter():
            return
            yield

        event_source = mocker.MagicMock()
        event_source.response = mocker.MagicMock(status_code=200)
        event_source.aiter_sse = async_iter
        event_source.__aenter__ = mocker.AsyncMock(return_value=event_source)
        event_source.__aexit__ = mocker.AsyncMock(return_value=False)

        mock_connect = mocker.patch("src.worker.executors.sse.aconnect_sse", return_value=event_source)

        ctx = RunContext(
            run_id=RUN_ID,
            schedule_id=SCHEDULE_ID,
            auth_header_name=header_name,
            auth_header_value=SecretStr(header_value),
        )
        props = make_properties()
        executor = SseExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(ctx, props)

        headers = mock_connect.call_args.kwargs["headers"]
        assert headers[header_name] == header_value
        assert set(headers) == {header_name}

    async def test_logs_per_event(self, mock_client, mocker):
        events = [
            mocker.MagicMock(event="data", data="chunk1"),
            mocker.MagicMock(event="data", data="chunk2"),
        ]

        async def async_iter():
            for e in events:
                yield e

        event_source = mocker.MagicMock()
        event_source.response = mocker.MagicMock(status_code=200)
        event_source.aiter_sse = async_iter
        event_source.__aenter__ = mocker.AsyncMock(return_value=event_source)
        event_source.__aexit__ = mocker.AsyncMock(return_value=False)

        mocker.patch("src.worker.executors.sse.aconnect_sse", return_value=event_source)

        props = make_properties()
        executor = SseExecutor()

        with structlog.testing.capture_logs() as logs:
            await executor.execute(CTX, props)

        event_logs = [entry for entry in logs if entry.get("event") == "sse_event"]
        assert len(event_logs) == 2

    async def test_zero_event_stream_succeeds(self, mock_client, mocker):
        async def async_iter():
            return
            yield

        event_source = mocker.MagicMock()
        event_source.response = mocker.MagicMock(status_code=200)
        event_source.aiter_sse = async_iter
        event_source.__aenter__ = mocker.AsyncMock(return_value=event_source)
        event_source.__aexit__ = mocker.AsyncMock(return_value=False)

        mocker.patch("src.worker.executors.sse.aconnect_sse", return_value=event_source)

        props = make_properties()
        executor = SseExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, props)

    async def test_obo_secret_not_in_logs(self, mock_client, mocker):
        async def async_iter():
            return
            yield

        event_source = mocker.MagicMock()
        event_source.response = mocker.MagicMock(status_code=200)
        event_source.aiter_sse = async_iter
        event_source.__aenter__ = mocker.AsyncMock(return_value=event_source)
        event_source.__aexit__ = mocker.AsyncMock(return_value=False)

        mocker.patch("src.worker.executors.sse.aconnect_sse", return_value=event_source)

        ctx = RunContext(
            run_id=RUN_ID,
            schedule_id=SCHEDULE_ID,
            auth_header_name="Api-Key",
            auth_header_value=SecretStr("my-sse-secret"),
        )
        props = make_properties()
        executor = SseExecutor()

        with structlog.testing.capture_logs() as logs:
            await executor.execute(ctx, props)

        for log in logs:
            assert "my-sse-secret" not in str(log)


class TestSseExecutorReturnsResult:
    async def test_returns_events_list(self, mock_client, mocker):
        events = [
            mocker.MagicMock(event="data", data="chunk1"),
            mocker.MagicMock(event="data", data="chunk2"),
        ]

        async def async_iter():
            for e in events:
                yield e

        event_source = mocker.MagicMock()
        event_source.response = mocker.MagicMock(status_code=200)
        event_source.aiter_sse = async_iter
        event_source.__aenter__ = mocker.AsyncMock(return_value=event_source)
        event_source.__aexit__ = mocker.AsyncMock(return_value=False)

        mocker.patch("src.worker.executors.sse.aconnect_sse", return_value=event_source)

        props = make_properties()
        executor = SseExecutor()

        with structlog.testing.capture_logs():
            result = await executor.execute(CTX, props)

        assert result == {"events": [{"event": "data", "data": "chunk1"}, {"event": "data", "data": "chunk2"}]}

    async def test_returns_empty_events_for_zero_event_stream(self, mock_client, mocker):
        async def async_iter():
            return
            yield

        event_source = mocker.MagicMock()
        event_source.response = mocker.MagicMock(status_code=200)
        event_source.aiter_sse = async_iter
        event_source.__aenter__ = mocker.AsyncMock(return_value=event_source)
        event_source.__aexit__ = mocker.AsyncMock(return_value=False)

        mocker.patch("src.worker.executors.sse.aconnect_sse", return_value=event_source)

        props = make_properties()
        executor = SseExecutor()

        with structlog.testing.capture_logs():
            result = await executor.execute(CTX, props)

        assert result == {"events": []}


class TestSseExecutorFailurePath:
    async def test_non_2xx_raises_schedule_run_error(self, mock_client, mocker):
        event_source = mocker.MagicMock()
        mock_response = mocker.MagicMock(status_code=500)
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=mocker.MagicMock(), response=mocker.MagicMock()
        )
        event_source.response = mock_response
        event_source.__aenter__ = mocker.AsyncMock(return_value=event_source)
        event_source.__aexit__ = mocker.AsyncMock(return_value=False)

        mocker.patch("src.worker.executors.sse.aconnect_sse", return_value=event_source)

        props = make_properties()
        executor = SseExecutor()

        with pytest.raises(ScheduleRunError) as exc_info:
            await executor.execute(CTX, props)

        assert exc_info.value.run_id == RUN_ID
        assert exc_info.value.schedule_id == SCHEDULE_ID

    async def test_http_status_error_captures_status_code(self, mock_client, mocker):
        response = mocker.MagicMock(status_code=503)
        event_source = mocker.MagicMock()
        bad_response = mocker.MagicMock(status_code=503)
        bad_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "503", request=mocker.MagicMock(), response=response
        )
        event_source.response = bad_response
        event_source.__aenter__ = mocker.AsyncMock(return_value=event_source)
        event_source.__aexit__ = mocker.AsyncMock(return_value=False)

        mocker.patch("src.worker.executors.sse.aconnect_sse", return_value=event_source)

        props = make_properties()
        executor = SseExecutor()

        with pytest.raises(ScheduleRunError) as exc_info:
            await executor.execute(CTX, props)

        assert exc_info.value.status_code == 503


class TestSseExecutorTimeout:
    async def test_effective_timeout_passed_through_when_set(self, mock_client, mocker):
        async def async_iter():
            return
            yield

        event_source = mocker.MagicMock()
        event_source.response = mocker.MagicMock(status_code=200)
        event_source.aiter_sse = async_iter
        event_source.__aenter__ = mocker.AsyncMock(return_value=event_source)
        event_source.__aexit__ = mocker.AsyncMock(return_value=False)

        mock_connect = mocker.patch("src.worker.executors.sse.aconnect_sse", return_value=event_source)

        ctx = RunContext(
            run_id=RUN_ID,
            schedule_id=SCHEDULE_ID,
            auth_header_name="Api-Key",
            auth_header_value=SecretStr("obo-token"),
            effective_timeout=20.0,
        )
        props = make_properties()
        executor = SseExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(ctx, props)

        assert mock_connect.call_args.kwargs["timeout"] == httpx.Timeout(20.0)

    async def test_no_timeout_kwarg_when_absent(self, mock_client, mocker):
        async def async_iter():
            return
            yield

        event_source = mocker.MagicMock()
        event_source.response = mocker.MagicMock(status_code=200)
        event_source.aiter_sse = async_iter
        event_source.__aenter__ = mocker.AsyncMock(return_value=event_source)
        event_source.__aexit__ = mocker.AsyncMock(return_value=False)

        mock_connect = mocker.patch("src.worker.executors.sse.aconnect_sse", return_value=event_source)

        props = make_properties()
        executor = SseExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, props)

        assert "timeout" not in mock_connect.call_args.kwargs
