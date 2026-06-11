import httpx
import httpx_sse
from httpx_sse import aconnect_sse

from src.lib.constants import API_KEY_HEADER
from src.lib.exceptions.errors import ScheduleRunError
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.lib.settings import get_settings
from src.lib.utils.logging import logger


class SseExecutor:
    async def execute(self, ctx: RunContext, properties: ScheduleProperties) -> None:
        settings = get_settings()
        headers = {API_KEY_HEADER: settings.dial_api_key.get_secret_value(), **properties.extra_headers}

        try:
            async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
                async with aconnect_sse(
                    client,
                    properties.method.value,  # type: ignore[union-attr]
                    properties.url,
                    json=properties.payload,
                    headers=headers,
                ) as event_source:
                    if not (200 <= event_source.response.status_code < 300):
                        event_source.response.raise_for_status()
                    async for event in event_source.aiter_sse():
                        logger.info("sse_event", run_id=ctx.run_id, **{event.event: event.data})
        except (httpx.HTTPStatusError, httpx.TransportError, httpx_sse.SSEError) as exc:
            raise ScheduleRunError(str(exc), schedule_id=ctx.schedule_id, run_id=ctx.run_id) from exc
