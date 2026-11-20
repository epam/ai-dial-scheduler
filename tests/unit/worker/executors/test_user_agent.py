"""End-to-end check that every executor puts the scheduler User-Agent on the wire.

Unlike the per-executor tests, nothing here mocks the executor's collaborators: requests flow
through the real httpx client (and the real OpenAI SDK) into a MockTransport, which is where a
library silently overriding the header would show up.
"""

from collections.abc import AsyncIterator

import httpx
from pydantic import SecretStr
import pytest
import structlog.testing

from src.lib.constants import USER_AGENT_HEADER
from src.lib.db.enums import HttpMethod, TargetType
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.lib.utils.user_agent import get_user_agent
from src.worker.executors.chat_completion import ChatCompletionExecutor
from src.worker.executors.responses import ResponsesExecutor
from src.worker.executors.rest import RestExecutor
from src.worker.executors.sse import SseExecutor


CREDS = OboCredentials(header_name="Api-Key", header_value=SecretStr("obo-token"))
SPOOFED_HEADERS = {"user-agent": "spoofed/1.0", "X-Custom": "kept"}

CHAT_COMPLETION_BODY = {
    "id": "cmpl-1",
    "object": "chat.completion",
    "created": 0,
    "model": "gpt-4",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
}
RESPONSES_BODY = {
    "id": "resp-1",
    "object": "response",
    "created_at": 0,
    "model": "gpt-5",
    "status": "completed",
    "output": [],
    "parallel_tool_calls": False,
    "tool_choice": "auto",
    "tools": [],
}


def _respond(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/chat/completions"):
        return httpx.Response(200, json=CHAT_COMPLETION_BODY)
    if request.url.path.endswith("/responses"):
        return httpx.Response(200, json=RESPONSES_BODY)
    if request.url.path.endswith("/stream"):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=b"data: hi\n\n")
    return httpx.Response(200, json={"ok": True})


@pytest.fixture
def captured() -> list[httpx.Request]:
    return []


@pytest.fixture
async def ctx(captured: list[httpx.Request]) -> AsyncIterator[RunContext]:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _respond(request)

    # Mirrors create_http_client: the User-Agent is a client-level default header.
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), headers={USER_AGENT_HEADER: get_user_agent()}
    ) as client:
        yield RunContext(run_id="run-ua", schedule_id="sched-ua", http_client=client)


EXECUTOR_CASES = [
    pytest.param(
        RestExecutor(),
        dict(target_type=TargetType.REST, method=HttpMethod.POST, url="https://core.example.com/api"),
        id="rest",
    ),
    pytest.param(
        SseExecutor(),
        dict(target_type=TargetType.SSE, method=HttpMethod.POST, url="https://core.example.com/stream"),
        id="sse",
    ),
    pytest.param(
        ChatCompletionExecutor(),
        dict(
            target_type=TargetType.CHAT_COMPLETION,
            url="https://core.example.com/openai/",
            payload={"model": "gpt-4", "messages": [{"role": "user", "content": "hello"}]},
            api_version="2024-02-01",
        ),
        id="chat_completion",
    ),
    pytest.param(
        ResponsesExecutor(),
        dict(
            target_type=TargetType.RESPONSES,
            url="https://core.example.com/openai/v1/",
            payload={"model": "gpt-5", "input": "hello"},
            api_version="2025-01-01-preview",
        ),
        id="responses",
    ),
]


@pytest.mark.parametrize(("executor", "props_kwargs"), EXECUTOR_CASES)
async def test_sends_scheduler_user_agent(executor, props_kwargs, ctx, captured):
    with structlog.testing.capture_logs():
        await executor.execute(ctx, CREDS, ScheduleProperties(**props_kwargs))

    assert len(captured) == 1
    assert captured[0].headers.get_list(USER_AGENT_HEADER) == [get_user_agent()]


@pytest.mark.parametrize(("executor", "props_kwargs"), EXECUTOR_CASES)
async def test_user_supplied_user_agent_cannot_override(executor, props_kwargs, ctx, captured):
    props = ScheduleProperties(**props_kwargs, extra_headers=SPOOFED_HEADERS)

    with structlog.testing.capture_logs():
        await executor.execute(ctx, CREDS, props)

    request = captured[0]
    assert request.headers.get_list(USER_AGENT_HEADER) == [get_user_agent()]
    assert request.headers["X-Custom"] == "kept"
