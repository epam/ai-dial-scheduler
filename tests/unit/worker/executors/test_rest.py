import httpx
from pydantic import SecretStr
import pytest
from structlog.contextvars import bind_contextvars, clear_contextvars
import structlog.testing

from src.lib.db.enums import HttpMethod, TargetType
from src.lib.exceptions.errors import ScheduleRunError
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.worker.executors.rest import RestExecutor


RUN_ID = "run-001"
SCHEDULE_ID = "sched-001"
CTX = RunContext(
    run_id=RUN_ID,
    schedule_id=SCHEDULE_ID,
)
CREDS = OboCredentials(header_name="Api-Key", header_value=SecretStr("obo-token"))

# (header_name, header_value) shapes for both external-service auth types.
AUTH_TYPE_CASES = [
    ("Api-Key", "user-api-key"),
    ("Authorization", "Bearer user.oauth.token"),
]


def make_properties(**kwargs) -> ScheduleProperties:
    defaults = dict(
        target_type=TargetType.REST,
        method=HttpMethod.POST,
        url="https://example.com/api",
        payload={"key": "value"},
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
    response = mocker.MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.text = '{"ok": true}'
    response.raise_for_status = mocker.MagicMock()

    client = mocker.AsyncMock(spec=httpx.AsyncClient)
    client.request = mocker.AsyncMock(return_value=response)

    provider = mocker.MagicMock()
    provider.client = client
    mocker.patch("src.worker.executors.rest.HttpClientProvider", return_value=provider)
    return client


class TestRestExecutorHappyPath:
    async def test_makes_request_with_correct_method_url_payload(self, mock_client):
        props = make_properties()
        executor = RestExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, CREDS, props)

        mock_client.request.assert_called_once()
        call_kwargs = mock_client.request.call_args
        assert call_kwargs.kwargs["method"] == "POST"
        assert call_kwargs.kwargs["url"] == "https://example.com/api"
        assert call_kwargs.kwargs["json"] == {"key": "value"}

    @pytest.mark.parametrize("header_name, header_value", AUTH_TYPE_CASES)
    async def test_injects_obo_header_and_no_shared_key(self, mock_client, header_name, header_value):
        creds = OboCredentials(header_name=header_name, header_value=SecretStr(header_value))
        props = make_properties()
        executor = RestExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, creds, props)

        headers = mock_client.request.call_args.kwargs["headers"]
        assert headers[header_name] == header_value
        # The shared service key path is gone: only the caller's extra headers plus the OBO header.
        assert set(headers) == {header_name}

    async def test_obo_header_overrides_same_named_extra_header(self, mock_client):
        creds = OboCredentials(header_name="Api-Key", header_value=SecretStr("obo-wins"))
        props = make_properties(extra_headers={"Api-Key": "stale", "X-Custom": "val"})
        executor = RestExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, creds, props)

        headers = mock_client.request.call_args.kwargs["headers"]
        assert headers["Api-Key"] == "obo-wins"
        assert headers["X-Custom"] == "val"

    async def test_logs_one_record_on_success(self, mock_client):
        props = make_properties()
        executor = RestExecutor()

        with structlog.testing.capture_logs() as logs:
            await executor.execute(CTX, CREDS, props)

        result_logs = [entry for entry in logs if entry.get("event") == "run_result"]
        assert len(result_logs) == 1
        assert result_logs[0]["run_id"] == RUN_ID

    async def test_obo_secret_not_in_logs(self, mock_client):
        creds = OboCredentials(header_name="Api-Key", header_value=SecretStr("my-secret-key"))
        props = make_properties()
        executor = RestExecutor()

        with structlog.testing.capture_logs() as logs:
            await executor.execute(CTX, creds, props)

        for log in logs:
            assert "my-secret-key" not in str(log)


class TestRestExecutorReturnsResult:
    async def test_returns_status_code_and_parsed_json_body(self, mock_client, mocker):
        mock_client.request.return_value.status_code = 200
        mock_client.request.return_value.text = '{"ok": true}'
        mock_client.request.return_value.headers = {"content-type": "application/json"}

        props = make_properties()
        executor = RestExecutor()

        with structlog.testing.capture_logs():
            result = await executor.execute(CTX, CREDS, props)

        assert result["status_code"] == 200
        assert result["body"] == {"ok": True}

    async def test_returns_text_body_when_not_json(self, mock_client):
        mock_client.request.return_value.status_code = 200
        mock_client.request.return_value.text = "plain text response"
        mock_client.request.return_value.headers = {"content-type": "text/plain"}

        props = make_properties()
        executor = RestExecutor()

        with structlog.testing.capture_logs():
            result = await executor.execute(CTX, CREDS, props)

        assert result["status_code"] == 200
        assert result["body"] == "plain text response"


class TestRestExecutorFailurePath:
    async def test_non_2xx_raises_schedule_run_error(self, mock_client, mocker):
        mock_client.request.return_value.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=mocker.MagicMock(), response=mocker.MagicMock()
        )
        props = make_properties()
        executor = RestExecutor()

        with pytest.raises(ScheduleRunError) as exc_info:
            await executor.execute(CTX, CREDS, props)

        assert exc_info.value.run_id == RUN_ID
        assert exc_info.value.schedule_id == SCHEDULE_ID

    async def test_transport_error_raises_schedule_run_error(self, mock_client, mocker):
        mock_client.request.side_effect = httpx.TransportError("connection failed")
        props = make_properties()
        executor = RestExecutor()

        with pytest.raises(ScheduleRunError) as exc_info:
            await executor.execute(CTX, CREDS, props)

        assert exc_info.value.run_id == RUN_ID
        assert exc_info.value.schedule_id == SCHEDULE_ID

    async def test_http_status_error_captures_status_code(self, mock_client, mocker):
        response = mocker.MagicMock(spec=httpx.Response)
        response.status_code = 502
        mock_client.request.return_value.raise_for_status.side_effect = httpx.HTTPStatusError(
            "502", request=mocker.MagicMock(), response=response
        )
        props = make_properties()
        executor = RestExecutor()

        with pytest.raises(ScheduleRunError) as exc_info:
            await executor.execute(CTX, CREDS, props)

        assert exc_info.value.status_code == 502

    async def test_transport_error_has_no_status_code(self, mock_client):
        mock_client.request.side_effect = httpx.TransportError("connection failed")
        props = make_properties()
        executor = RestExecutor()

        with pytest.raises(ScheduleRunError) as exc_info:
            await executor.execute(CTX, CREDS, props)

        assert exc_info.value.status_code is None


class TestRestExecutorTimeout:
    async def test_effective_timeout_passed_through_when_set(self, mock_client):
        ctx = RunContext(
            run_id=RUN_ID,
            schedule_id=SCHEDULE_ID,
            effective_timeout=15.0,
        )
        props = make_properties()
        executor = RestExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(ctx, CREDS, props)

        assert "timeout" in mock_client.request.call_args.kwargs
        assert mock_client.request.call_args.kwargs["timeout"] == httpx.Timeout(15.0)

    async def test_no_timeout_kwarg_when_absent(self, mock_client):
        props = make_properties()
        executor = RestExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, CREDS, props)

        assert "timeout" not in mock_client.request.call_args.kwargs
