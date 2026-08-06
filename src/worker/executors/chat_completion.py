from typing import Any

import httpx
from openai import APIStatusError, AsyncAzureOpenAI, OpenAIError

from src.lib.exceptions.errors import ScheduleRunError
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.lib.utils.dial import strip_bearer_prefix
from src.lib.utils.logging import logger
from src.worker.clients import HttpClientProvider


class ChatCompletionExecutor:
    async def execute(
        self, ctx: RunContext, credentials: OboCredentials, properties: ScheduleProperties
    ) -> dict[str, Any]:
        # AsyncAzureOpenAI emits the auth header from api_key; pass the per-user OBO value,
        # stripping a leading "Bearer " (OAUTH-type services) so only the raw token is sent.
        api_key = strip_bearer_prefix(credentials.header_value.get_secret_value())
        try:
            # ``url`` is the base URL; the deployment is selected by ``payload.model`` (sent in the
            # request body by the SDK). Validation guarantees ``payload.model`` is present.
            client = AsyncAzureOpenAI(
                base_url=properties.url,
                api_key=api_key,
                api_version=properties.api_version,
                http_client=HttpClientProvider().client,
            )
            # ``stream`` is controlled by DIAL via the payload flag (defaults to False). When
            # streaming, ``.stream()`` assembles a single ChatCompletion via
            # ``get_final_completion()``; otherwise ``.create()`` returns one directly. Either
            # way the stored result shape is identical.
            create_kwargs: dict[str, Any] = {**properties.payload, "extra_headers": properties.extra_headers}
            stream_enabled = bool(create_kwargs.pop("stream", None))
            if ctx.effective_timeout is not None:
                create_kwargs["timeout"] = ctx.effective_timeout
            if stream_enabled:
                async with client.chat.completions.stream(**create_kwargs) as stream:
                    async for stream_event in stream:
                        # Event type discriminator only — never the streamed content, which may be sensitive.
                        logger.debug("stream_event", run_id=ctx.run_id, event_type=getattr(stream_event, "type", None))
                    result = await stream.get_final_completion()
            else:
                result = await client.chat.completions.create(**create_kwargs)
            return {"result": result.model_dump()}
        except APIStatusError as exc:
            raise ScheduleRunError(
                str(exc), schedule_id=ctx.schedule_id, run_id=ctx.run_id, status_code=exc.status_code
            ) from exc
        # Deliberately narrow: SDK/transport failures and payload-shape errors (``payload`` is
        # free-form, so a bad key raises TypeError and a bad value ValueError) are remote/user
        # faults and become run errors. Anything else is a bug in this service and must escape
        # to the job boundary, which logs it at error level with a traceback.
        except (OpenAIError, httpx.TransportError, TypeError, ValueError) as exc:
            raise ScheduleRunError(str(exc), schedule_id=ctx.schedule_id, run_id=ctx.run_id) from exc
