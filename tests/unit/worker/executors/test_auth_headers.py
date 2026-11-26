"""The OBO identity cannot be duplicated or overridden through ``extra_headers`` in any casing.

A plain dict merge (ours, or the OpenAI SDK's) is case-sensitive, and httpx keeps differently-cased
keys as separate header lines, so a caller-supplied ``authorization`` / ``api-key`` would otherwise be
sent next to the injected value. Requests go through the real httpx client (and the real SDK) into a
MockTransport, which is where such a duplicate would show up.
"""

from collections.abc import AsyncIterator

import httpx
from pydantic import SecretStr
import pytest
import structlog.testing

from src.lib.db.enums import HttpMethod, TargetType
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.worker.executors.base import http_request_headers, sdk_extra_headers
from src.worker.executors.chat_completion import ChatCompletionExecutor
from src.worker.executors.responses import ResponsesExecutor
from src.worker.executors.rest import RestExecutor
from src.worker.executors.sse import SseExecutor
from tests.unit.worker.executors.test_user_agent import _respond


BEARER_CREDS = OboCredentials(header_name="Authorization", header_value=SecretStr("Bearer obo-token"))
API_KEY_CREDS = OboCredentials(header_name="Api-Key", header_value=SecretStr("obo-token"))


@pytest.fixture
def captured() -> list[httpx.Request]:
    return []


@pytest.fixture
async def ctx(captured: list[httpx.Request]) -> AsyncIterator[RunContext]:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _respond(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        yield RunContext(run_id="run-auth", schedule_id="sched-auth", http_client=client)


DIRECT_HTTP_CASES = [
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
]

SDK_CASES = [
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


@pytest.mark.parametrize("caller_name", ["authorization", "AUTHORIZATION", "Authorization"])
@pytest.mark.parametrize(("executor", "props_kwargs"), DIRECT_HTTP_CASES)
async def test_direct_http_sends_only_the_obo_auth_header(executor, props_kwargs, caller_name, ctx, captured):
    props = ScheduleProperties(**props_kwargs, extra_headers={caller_name: "Bearer other", "X-Custom": "kept"})

    with structlog.testing.capture_logs():
        await executor.execute(ctx, BEARER_CREDS, props)

    request = captured[0]
    assert request.headers.get_list("authorization") == ["Bearer obo-token"]
    assert request.headers["X-Custom"] == "kept"


@pytest.mark.parametrize(
    "caller_headers",
    [{"api-key": "other"}, {"API-KEY": "other"}, {"Authorization": "Bearer other"}, {"authorization": "Bearer other"}],
)
@pytest.mark.parametrize(("executor", "props_kwargs"), SDK_CASES)
async def test_sdk_sends_only_the_obo_api_key(executor, props_kwargs, caller_headers, ctx, captured):
    props = ScheduleProperties(**props_kwargs, extra_headers={**caller_headers, "X-Custom": "kept"})

    with structlog.testing.capture_logs():
        await executor.execute(ctx, API_KEY_CREDS, props)

    request = captured[0]
    assert request.headers.get_list("api-key") == ["obo-token"]
    # The SDK itself also sends the OBO token as a bearer; only the caller's copy must be gone.
    assert request.headers.get_list("authorization") in ([], ["Bearer obo-token"])
    assert not [value for _, value in request.headers.multi_items() if "other" in value]
    assert request.headers["X-Custom"] == "kept"


class TestHeaderHelpers:
    def test_http_request_headers_keeps_the_obo_value_once(self):
        props = ScheduleProperties(
            target_type=TargetType.REST,
            method=HttpMethod.POST,
            url="https://core.example.com/api",
            extra_headers={"authorization": "Bearer other", "user-agent": "spoofed", "X-Custom": "kept"},
        )

        assert http_request_headers(BEARER_CREDS, props) == {
            "X-Custom": "kept",
            "Authorization": "Bearer obo-token",
        }

    def test_sdk_extra_headers_drops_auth_obo_name_and_user_agent(self):
        props = ScheduleProperties(
            target_type=TargetType.CHAT_COMPLETION,
            url="https://core.example.com/openai/",
            payload={"model": "gpt-4", "messages": []},
            api_version="2024-02-01",
            extra_headers={
                "API-KEY": "other",
                "authorization": "Bearer other",
                "User-Agent": "spoofed",
                "X-Custom": "kept",
            },
        )

        assert sdk_extra_headers(API_KEY_CREDS, props) == {"X-Custom": "kept"}
