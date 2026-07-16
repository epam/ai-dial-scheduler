from typing import Any
from urllib.parse import urlsplit

from openai import APIStatusError, AsyncAzureOpenAI
from openai.types.chat import ChatCompletion

from src.lib.exceptions.errors import ScheduleRunError
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.lib.utils.logging import logger
from src.worker.clients import HttpClientProvider


_BEARER_PREFIX = "Bearer "


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
    async def execute(self, ctx: RunContext, properties: ScheduleProperties) -> dict[str, Any]:
        # AsyncAzureOpenAI emits the auth header from api_key; pass the per-user OBO value,
        # stripping a leading "Bearer " (OAUTH-type services) so only the raw token is sent.
        api_key = ctx.auth_header_value.get_secret_value()
        if api_key.startswith(_BEARER_PREFIX):
            api_key = api_key[len(_BEARER_PREFIX) :]
        try:
            azure_endpoint, azure_deployment = _parse_deployment(properties.url)
            client = AsyncAzureOpenAI(
                azure_endpoint=azure_endpoint,
                api_key=api_key,
                api_version=properties.api_version,
                azure_deployment=azure_deployment,
                http_client=HttpClientProvider().client,
            )
            create_kwargs: dict[str, Any] = {**properties.payload, "extra_headers": properties.extra_headers}
            if ctx.effective_timeout is not None:
                create_kwargs["timeout"] = ctx.effective_timeout
            response = await client.chat.completions.create(**create_kwargs)
            if isinstance(response, ChatCompletion):
                logger.info("run_result", run_id=ctx.run_id, result=str(response))
                return {"result": response.model_dump()}
            else:
                chunks = []
                async for chunk in response:
                    logger.info("stream_chunk", run_id=ctx.run_id, chunk=str(chunk))
                    chunks.append(chunk.model_dump())
                return {"chunks": chunks}
        except APIStatusError as exc:
            raise ScheduleRunError(
                str(exc), schedule_id=ctx.schedule_id, run_id=ctx.run_id, status_code=exc.status_code
            ) from exc
        except Exception as exc:
            raise ScheduleRunError(str(exc), schedule_id=ctx.schedule_id, run_id=ctx.run_id) from exc
