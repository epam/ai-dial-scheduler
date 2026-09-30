import json
from typing import Any

import httpx

from src.lib.exceptions.errors import ScheduleRunError
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.lib.utils.logging import logger
from src.worker.clients import HttpClientProvider


class RestExecutor:
    async def execute(
        self, ctx: RunContext, credentials: OboCredentials, properties: ScheduleProperties
    ) -> dict[str, Any]:
        headers = {**properties.extra_headers, credentials.header_name: credentials.header_value.get_secret_value()}
        client = ctx.http_client or HttpClientProvider().client

        request_kwargs: dict[str, Any] = {
            "method": properties.method.value,  # type: ignore[union-attr]
            "url": properties.url,
            "json": properties.payload,
            "headers": headers,
        }
        if ctx.effective_timeout is not None:
            request_kwargs["timeout"] = httpx.Timeout(ctx.effective_timeout)

        try:
            response = await client.request(**request_kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ScheduleRunError(
                str(exc),
                schedule_id=ctx.schedule_id,
                run_id=ctx.run_id,
                status_code=exc.response.status_code,
            ) from exc
        except httpx.TransportError as exc:
            raise ScheduleRunError(str(exc), schedule_id=ctx.schedule_id, run_id=ctx.run_id) from exc

        try:
            body: Any = json.loads(response.text)
        except ValueError:
            body = response.text

        logger.debug("run_result", run_id=ctx.run_id, status=response.status_code, result=response.text)
        return {"status_code": response.status_code, "body": body}
