import httpx
import pytest
from structlog.contextvars import bind_contextvars, clear_contextvars
import structlog.testing

from src.lib.db.enums import HttpMethod, TargetType
from src.lib.exceptions.errors import ScheduleRunError
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.worker.executors.rest import RestExecutor


RUN_ID = "run-001"
SCHEDULE_ID = "sched-001"
CTX = RunContext(run_id=RUN_ID, schedule_id=SCHEDULE_ID)


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
    client.__aenter__ = mocker.AsyncMock(return_value=client)
    client.__aexit__ = mocker.AsyncMock(return_value=False)

    mocker.patch("src.worker.executors.rest.httpx.AsyncClient", return_value=client)
    return client


class TestRestExecutorHappyPath:
    async def test_makes_request_with_correct_method_url_payload(self, mock_client):
        props = make_properties()
        executor = RestExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, props)

        mock_client.request.assert_called_once()
        call_kwargs = mock_client.request.call_args
        assert call_kwargs.kwargs["method"] == "POST"
        assert call_kwargs.kwargs["url"] == "https://example.com/api"
        assert call_kwargs.kwargs["json"] == {"key": "value"}

    async def test_injects_api_key_header(self, mock_client, mocker):
        mocker.patch(
            "src.worker.executors.rest.get_settings",
            return_value=mocker.MagicMock(
                dial_api_key=mocker.MagicMock(get_secret_value=lambda: "secret-key"),
                http_timeout_seconds=30.0,
            ),
        )
        props = make_properties()
        executor = RestExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, props)

        call_kwargs = mock_client.request.call_args
        assert call_kwargs.kwargs["headers"]["Api-Key"] == "secret-key"

    async def test_extra_headers_win_on_collision(self, mock_client, mocker):
        mocker.patch(
            "src.worker.executors.rest.get_settings",
            return_value=mocker.MagicMock(
                dial_api_key=mocker.MagicMock(get_secret_value=lambda: "default-key"),
                http_timeout_seconds=30.0,
            ),
        )
        props = make_properties(extra_headers={"Api-Key": "override-key", "X-Custom": "val"})
        executor = RestExecutor()

        with structlog.testing.capture_logs():
            await executor.execute(CTX, props)

        call_kwargs = mock_client.request.call_args
        headers = call_kwargs.kwargs["headers"]
        assert headers["Api-Key"] == "override-key"
        assert headers["X-Custom"] == "val"

    async def test_logs_one_record_on_success(self, mock_client):
        props = make_properties()
        executor = RestExecutor()

        with structlog.testing.capture_logs() as logs:
            await executor.execute(CTX, props)

        result_logs = [entry for entry in logs if entry.get("event") == "run_result"]
        assert len(result_logs) == 1
        assert result_logs[0]["run_id"] == RUN_ID

    async def test_dial_api_key_not_in_logs(self, mock_client, mocker):
        mocker.patch(
            "src.worker.executors.rest.get_settings",
            return_value=mocker.MagicMock(
                dial_api_key=mocker.MagicMock(get_secret_value=lambda: "my-secret-key"),
                http_timeout_seconds=30.0,
            ),
        )
        props = make_properties()
        executor = RestExecutor()

        with structlog.testing.capture_logs() as logs:
            await executor.execute(CTX, props)

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
            result = await executor.execute(CTX, props)

        assert result["status_code"] == 200
        assert result["body"] == {"ok": True}

    async def test_returns_text_body_when_not_json(self, mock_client):
        mock_client.request.return_value.status_code = 200
        mock_client.request.return_value.text = "plain text response"
        mock_client.request.return_value.headers = {"content-type": "text/plain"}

        props = make_properties()
        executor = RestExecutor()

        with structlog.testing.capture_logs():
            result = await executor.execute(CTX, props)

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
            await executor.execute(CTX, props)

        assert exc_info.value.run_id == RUN_ID
        assert exc_info.value.schedule_id == SCHEDULE_ID

    async def test_transport_error_raises_schedule_run_error(self, mock_client, mocker):
        mock_client.request.side_effect = httpx.TransportError("connection failed")
        props = make_properties()
        executor = RestExecutor()

        with pytest.raises(ScheduleRunError) as exc_info:
            await executor.execute(CTX, props)

        assert exc_info.value.run_id == RUN_ID
        assert exc_info.value.schedule_id == SCHEDULE_ID
