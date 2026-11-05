"""Integration tests for job executors using in-process echo endpoints.

REST and SSE executors are tested by patching httpx.AsyncClient to route
through ASGI transport, so requests land on the real echo FastAPI router
without a network hop.

ChatCompletionExecutor requires AsyncAzureOpenAI which manages its own
internal httpx client, so the DIAL echo app is served by a real uvicorn
server on a random port.
"""

import asyncio
import socket

from aidial_sdk import DIALApp
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient as _RealAsyncClient
from pydantic import SecretStr
import pytest
import structlog.testing
import uvicorn

from src.api.v1.echo_chat import EchoChatCompletion
from src.api.v1.endpoints.echo import router as echo_router
from src.lib.db.enums import HttpMethod, TargetType
from src.lib.exceptions.errors import ScheduleRunError
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _rest_sse_app() -> FastAPI:
    app = FastAPI()
    app.include_router(echo_router)
    return app


def _chat_dial_app() -> DIALApp:
    app = DIALApp(dial_url="http://dial", add_healthcheck=False)
    app.add_chat_completion("echo", EchoChatCompletion())
    return app


def _make_asgi_client_factory(app: FastAPI):
    """Return a drop-in replacement for httpx.AsyncClient that routes through ASGI.

    Uses _RealAsyncClient (bound before any mock patches) to avoid recursion
    when httpx.AsyncClient is patched on the shared httpx module.
    """

    def factory(*_args, **_kwargs) -> _RealAsyncClient:
        return _RealAsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    return factory


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


_CREDS = OboCredentials(header_name="Api-Key", header_value=SecretStr("test-key"))


def _make_ctx(run_id: str, schedule_id: str = "s1") -> RunContext:
    return RunContext(run_id=run_id, schedule_id=schedule_id)


@pytest.fixture
async def dial_server():
    """Run the DIAL echo chat completion app on a random port."""
    port = _free_port()
    config = uvicorn.Config(_chat_dial_app(), host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        await asyncio.sleep(0.01)
        if server.started:
            break
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    await task


# ---------------------------------------------------------------------------
# REST executor
# ---------------------------------------------------------------------------


class TestRestExecutorWithEchoEndpoint:
    @pytest.fixture(autouse=True)
    def patch_rest(self, mocker):
        mocker.patch(
            "src.worker.executors.rest.httpx.AsyncClient",
            side_effect=_make_asgi_client_factory(_rest_sse_app()),
        )

    async def test_success_logs_run_result(self) -> None:
        from src.worker.executors.rest import RestExecutor

        ctx = _make_ctx("r1")
        props = ScheduleProperties(
            target_type=TargetType.REST,
            method=HttpMethod.POST,
            url="http://test/echo/rest",
            payload={"success": True},
            extra_headers={},
        )
        with structlog.testing.capture_logs() as logs:
            await RestExecutor().execute(ctx, _CREDS, props)

        result_logs = [e for e in logs if e.get("event") == "run_result"]
        assert len(result_logs) == 1
        assert result_logs[0]["status"] == 200

    async def test_error_response_raises_schedule_run_error(self) -> None:
        from src.worker.executors.rest import RestExecutor

        ctx = _make_ctx("r2")
        props = ScheduleProperties(
            target_type=TargetType.REST,
            method=HttpMethod.POST,
            url="http://test/echo/rest",
            payload={"success": False},
            extra_headers={},
        )
        with pytest.raises(ScheduleRunError) as exc_info:
            await RestExecutor().execute(ctx, _CREDS, props)

        assert exc_info.value.run_id == "r2"
        assert exc_info.value.schedule_id == "s1"

    async def test_sleep_payload_is_respected(self) -> None:
        from src.worker.executors.rest import RestExecutor

        ctx = _make_ctx("r3")
        props = ScheduleProperties(
            target_type=TargetType.REST,
            method=HttpMethod.POST,
            url="http://test/echo/rest",
            payload={"success": True, "sleep": 0.05},
            extra_headers={},
        )
        with structlog.testing.capture_logs():
            await RestExecutor().execute(ctx, _CREDS, props)  # should not raise or hang


# ---------------------------------------------------------------------------
# SSE executor
# ---------------------------------------------------------------------------


class TestSseExecutorWithEchoEndpoint:
    @pytest.fixture(autouse=True)
    def patch_sse(self, mocker):
        mocker.patch(
            "src.worker.executors.sse.httpx.AsyncClient",
            side_effect=_make_asgi_client_factory(_rest_sse_app()),
        )

    async def test_streams_one_log_per_event(self) -> None:
        from src.worker.executors.sse import SseExecutor

        ctx = _make_ctx("r4")
        props = ScheduleProperties(
            target_type=TargetType.SSE,
            method=HttpMethod.POST,
            url="http://test/echo/sse",
            payload={"chunks": 4},
            extra_headers={},
        )
        with structlog.testing.capture_logs() as logs:
            await SseExecutor().execute(ctx, _CREDS, props)

        sse_logs = [e for e in logs if e.get("event") == "sse_event"]
        assert len(sse_logs) == 4

    async def test_error_response_raises_schedule_run_error(self) -> None:
        from src.worker.executors.sse import SseExecutor

        ctx = _make_ctx("r5")
        props = ScheduleProperties(
            target_type=TargetType.SSE,
            method=HttpMethod.POST,
            url="http://test/echo/sse",
            payload={"success": False},
            extra_headers={},
        )
        with pytest.raises(ScheduleRunError) as exc_info:
            await SseExecutor().execute(ctx, _CREDS, props)

        assert exc_info.value.run_id == "r5"

    async def test_zero_chunk_stream_succeeds_with_no_logs(self) -> None:
        from src.worker.executors.sse import SseExecutor

        ctx = _make_ctx("r6")
        props = ScheduleProperties(
            target_type=TargetType.SSE,
            method=HttpMethod.POST,
            url="http://test/echo/sse",
            payload={"chunks": 0},
            extra_headers={},
        )
        with structlog.testing.capture_logs() as logs:
            await SseExecutor().execute(ctx, _CREDS, props)

        assert not any(e.get("event") == "sse_event" for e in logs)


# ---------------------------------------------------------------------------
# Chat completion executor
# ---------------------------------------------------------------------------


class TestChatCompletionExecutorWithEchoEndpoint:
    @pytest.fixture(autouse=True)
    async def patch_chat(self, mocker):
        async with _RealAsyncClient() as real_client:
            provider = mocker.MagicMock()
            provider.client = real_client
            mocker.patch("src.worker.executors.chat_completion.HttpClientProvider", return_value=provider)
            yield

    async def test_streaming_returns_assembled_result(self, dial_server: str) -> None:
        from src.worker.executors.chat_completion import ChatCompletionExecutor

        ctx = _make_ctx("r7")
        props = ScheduleProperties(
            target_type=TargetType.CHAT_COMPLETION,
            url=f"{dial_server}/openai/deployments/echo",
            payload={"messages": [{"role": "user", "content": "hi"}], "model": "echo", "stream": True},
            extra_headers={},
            api_version="2024-02-01",
        )
        with structlog.testing.capture_logs() as logs:
            result = await ChatCompletionExecutor().execute(ctx, _CREDS, props)

        # Streaming schedules assemble a single completion via get_final_completion(),
        # so the stored result has the same shape as a non-streaming run.
        assert "result" in result
        assert result["result"]["choices"][0]["message"]["role"] == "assistant"
        # Stream events log only their type discriminator, never streamed content.
        event_logs = [e for e in logs if e.get("event") == "stream_event"]
        assert len(event_logs) >= 1

    async def test_non_streaming_returns_assembled_result(self, dial_server: str) -> None:
        from src.worker.executors.chat_completion import ChatCompletionExecutor

        ctx = _make_ctx("r8")
        props = ScheduleProperties(
            target_type=TargetType.CHAT_COMPLETION,
            url=f"{dial_server}/openai/deployments/echo",
            payload={"messages": [{"role": "user", "content": "hi"}], "model": "echo", "stream": False},
            extra_headers={},
            api_version="2024-02-01",
        )
        with structlog.testing.capture_logs():
            result = await ChatCompletionExecutor().execute(ctx, _CREDS, props)

        assert "result" in result
        assert result["result"]["choices"][0]["message"]["role"] == "assistant"
