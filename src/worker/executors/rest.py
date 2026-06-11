import httpx

from src.lib.constants import API_KEY_HEADER
from src.lib.exceptions.errors import ScheduleRunError
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.lib.settings import get_settings
from src.lib.utils.logging import logger


class RestExecutor:
    async def execute(self, ctx: RunContext, properties: ScheduleProperties) -> None:
        settings = get_settings()
        headers = {API_KEY_HEADER: settings.dial_api_key.get_secret_value(), **properties.extra_headers}

        try:
            async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
                response = await client.request(
                    method=properties.method.value,  # type: ignore[union-attr]
                    url=properties.url,
                    json=properties.payload,
                    headers=headers,
                )
                response.raise_for_status()
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            raise ScheduleRunError(str(exc), schedule_id=ctx.schedule_id, run_id=ctx.run_id) from exc

        logger.info("run_result", run_id=ctx.run_id, status=response.status_code, result=response.text)
