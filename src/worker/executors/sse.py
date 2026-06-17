from typing import Any

import httpx
import httpx_sse
from httpx_sse import aconnect_sse

from src.lib.constants import API_KEY_HEADER
from src.lib.exceptions.errors import ScheduleRunError
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.lib.settings import get_settings
from src.lib.utils.logging import logger
from src.worker.clients import HttpClientProvider


class SseExecutor:
    async def execute(self, ctx: RunContext, properties: ScheduleProperties) -> dict[str, Any]:
        settings = get_settings()
        headers = {API_KEY_HEADER: settings.dial_api_key.get_secret_value(), **properties.extra_headers}
        events: list[dict[str, str]] = []
        client = HttpClientProvider().client

        connect_kwargs: dict[str, Any] = {"json": properties.payload, "headers": headers}
        if ctx.effective_timeout is not None:
            connect_kwargs["timeout"] = httpx.Timeout(ctx.effective_timeout)

        try:
            async with aconnect_sse(
                client,
                properties.method.value,  # type: ignore[union-attr]
                properties.url,
                **connect_kwargs,
            ) as event_source:
                if not (200 <= event_source.response.status_code < 300):
                    event_source.response.raise_for_status()
                async for event in event_source.aiter_sse():
                    logger.info("sse_event", run_id=ctx.run_id, **{event.event: event.data})
                    events.append({"event": event.event, "data": event.data})
        except httpx.HTTPStatusError as exc:
            raise ScheduleRunError(
                str(exc),
                schedule_id=ctx.schedule_id,
                run_id=ctx.run_id,
                status_code=exc.response.status_code,
            ) from exc
        except (httpx.TransportError, httpx_sse.SSEError) as exc:
            raise ScheduleRunError(str(exc), schedule_id=ctx.schedule_id, run_id=ctx.run_id) from exc

        return {"events": events}
