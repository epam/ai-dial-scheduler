from typing import Any
from urllib.parse import urlsplit

from openai import APIStatusError, AsyncAzureOpenAI

from src.lib.exceptions.errors import ScheduleRunError
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.lib.utils.dial import strip_bearer_prefix
from src.lib.utils.logging import logger
from src.worker.clients import HttpClientProvider


def _parse_deployment(url: str) -> tuple[str, str]:
    """Return (azure_endpoint, azure_deployment) from a deployment URL."""
    parsed = urlsplit(url)
    endpoint = f"{parsed.scheme}://{parsed.netloc}"
    parts = parsed.path.split("/")
    try:
        idx = parts.index("deployments")
        deployment = parts[idx + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Cannot extract deployment from URL: {url}") from exc
    return endpoint, deployment


class ChatCompletionExecutor:
    async def execute(
        self, ctx: RunContext, credentials: OboCredentials, properties: ScheduleProperties
    ) -> dict[str, Any]:
        # AsyncAzureOpenAI emits the auth header from api_key; pass the per-user OBO value,
        # stripping a leading "Bearer " (OAUTH-type services) so only the raw token is sent.
        api_key = strip_bearer_prefix(credentials.header_value.get_secret_value())
        try:
            azure_endpoint, azure_deployment = _parse_deployment(properties.url)
            client = AsyncAzureOpenAI(
                azure_endpoint=azure_endpoint,
                api_key=api_key,
                api_version=properties.api_version,
                azure_deployment=azure_deployment,
                http_client=HttpClientProvider().client,
            )
            # ``stream`` is controlled by DIAL via the payload flag; ``.stream()`` assembles a
            # single ChatCompletion in both cases via ``get_final_completion()``, so the stored
            # result shape is identical for streaming and non-streaming schedules.
            create_kwargs: dict[str, Any] = {**properties.payload, "extra_headers": properties.extra_headers}
            create_kwargs.pop("stream", None)
            if ctx.effective_timeout is not None:
                create_kwargs["timeout"] = ctx.effective_timeout
            async with client.chat.completions.stream(**create_kwargs) as stream:
                async for stream_event in stream:
                    # Event type discriminator only — never the streamed content, which may be sensitive.
                    logger.debug("stream_event", run_id=ctx.run_id, event_type=getattr(stream_event, "type", None))
                result = await stream.get_final_completion()
            return {"result": result.model_dump()}
        except APIStatusError as exc:
            raise ScheduleRunError(
                str(exc), schedule_id=ctx.schedule_id, run_id=ctx.run_id, status_code=exc.status_code
            ) from exc
        except Exception as exc:
            raise ScheduleRunError(str(exc), schedule_id=ctx.schedule_id, run_id=ctx.run_id) from exc
