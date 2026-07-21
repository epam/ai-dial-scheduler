from typing import Any, Protocol

from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties


class Executor(Protocol):
    async def execute(
        self, ctx: RunContext, credentials: OboCredentials, properties: ScheduleProperties
    ) -> dict[str, Any]: ...
