import pytest

from src.lib.db.enums import TargetType
from src.worker.dispatcher import resolve_executor
from src.worker.executors.chat_completion import ChatCompletionExecutor
from src.worker.executors.responses import ResponsesExecutor
from src.worker.executors.rest import RestExecutor
from src.worker.executors.sse import SseExecutor


class TestDispatcher:
    def test_resolves_rest_executor(self):
        executor = resolve_executor(TargetType.REST)
        assert isinstance(executor, RestExecutor)

    def test_resolves_sse_executor(self):
        executor = resolve_executor(TargetType.SSE)
        assert isinstance(executor, SseExecutor)

    def test_resolves_chat_completion_executor(self):
        executor = resolve_executor(TargetType.CHAT_COMPLETION)
        assert isinstance(executor, ChatCompletionExecutor)

    def test_resolves_responses_executor(self):
        executor = resolve_executor(TargetType.RESPONSES)
        assert isinstance(executor, ResponsesExecutor)

    def test_unknown_type_raises(self):
        with pytest.raises((KeyError, ValueError)):
            resolve_executor("unknown_type")  # type: ignore[arg-type]
