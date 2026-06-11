from typing import Protocol

from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties


class Executor(Protocol):
    async def execute(self, ctx: RunContext, properties: ScheduleProperties) -> None: ...
