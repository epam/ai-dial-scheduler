from src.lib.db.enums import TargetType
from src.worker.executors.base import Executor
from src.worker.executors.chat_completion import ChatCompletionExecutor
from src.worker.executors.rest import RestExecutor
from src.worker.executors.sse import SseExecutor


_EXECUTOR_MAP: dict[TargetType, Executor] = {
    TargetType.REST: RestExecutor(),
    TargetType.SSE: SseExecutor(),
    TargetType.CHAT_COMPLETION: ChatCompletionExecutor(),
}


def resolve_executor(target_type: TargetType) -> Executor:
    return _EXECUTOR_MAP[target_type]
