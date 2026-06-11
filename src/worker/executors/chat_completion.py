from typing import Any
from urllib.parse import urlsplit

from openai import AsyncAzureOpenAI
from openai.types.chat import ChatCompletion

from src.lib.exceptions.errors import ScheduleRunError
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.lib.settings import get_settings
from src.lib.utils.logging import logger


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
        settings = get_settings()
        try:
            azure_endpoint, azure_deployment = _parse_deployment(properties.url)
            client = AsyncAzureOpenAI(
                azure_endpoint=azure_endpoint,
                api_key=settings.dial_api_key.get_secret_value(),
                api_version=properties.api_version,
                azure_deployment=azure_deployment,
            )
            response = await client.chat.completions.create(
                **properties.payload,
                extra_headers=properties.extra_headers,
            )
            if isinstance(response, ChatCompletion):
                logger.info("run_result", run_id=ctx.run_id, result=str(response))
                return {"result": response.model_dump()}
            else:
                chunks = []
                async for chunk in response:
                    logger.info("stream_chunk", run_id=ctx.run_id, chunk=str(chunk))
                    chunks.append(chunk.model_dump())
                return {"chunks": chunks}
        except Exception as exc:
            raise ScheduleRunError(str(exc), schedule_id=ctx.schedule_id, run_id=ctx.run_id) from exc
