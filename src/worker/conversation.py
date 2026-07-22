from datetime import UTC, datetime
from typing import Any

from src.lib.clients.dial_client import DialClient
from src.lib.constants import APPLICATION_DEPLOYMENT_PREFIX, SCHEDULER_CONVERSATION_FOLDER
from src.lib.exceptions.errors import ConversationCreationError
from src.lib.schemas.conversation import ConversationBuildInput, DialConversation, DialMessage, DialModel
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.lib.settings import get_settings
from src.lib.utils.dial import extract_deployment_id
from src.lib.utils.logging import logger
from src.worker.clients import HttpClientProvider


def _payload_temperature(properties: ScheduleProperties) -> float:
    """Return the temperature from the payload, or ``1`` when not provided."""
    value = properties.payload.get("temperature")
    return value if isinstance(value, (int, float)) else 1


def _assistant_message(result: dict[str, Any]) -> dict[str, Any]:
    """Return the assistant message dict from an assembled completion result."""
    completion = result.get("result", {})
    choices = completion.get("choices", []) if isinstance(completion, dict) else []
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message", {})
        if isinstance(message, dict):
            return message
    return {}


def _build_history_messages(
    properties: ScheduleProperties, model: DialModel, settings: dict[str, Any]
) -> list[DialMessage]:
    """Return all payload messages as DialMessage objects in original order."""
    return [
        DialMessage(
            role=msg["role"],
            content=msg.get("content", "") if isinstance(msg.get("content"), str) else "",
            model=model,
            settings=settings,
        )
        for msg in properties.payload.get("messages", [])
        if isinstance(msg, dict) and msg.get("role")
    ]


def conversation_resource_path(build_input: ConversationBuildInput) -> str:
    """Return the conversation storage path (``{bucket}/{folder...}/{run_id}``).

    This is the resource path DIAL uses to store and address the conversation, without the
    ``conversations/`` prefix. The document's ``id`` and ``folderId`` are derived from it.
    """
    return f"{build_input.bucket}/{SCHEDULER_CONVERSATION_FOLDER}/{build_input.schedule_id}/{build_input.run_id}"


def build_conversation(
    properties: ScheduleProperties, result: dict[str, Any], build_input: ConversationBuildInput
) -> DialConversation:
    """Build a DIAL conversation from a completed run.

    The full message history from the schedule payload is included (system prompt goes
    into ``DialConversation.prompt``; all other messages become ``DialMessage`` entries).
    The assembled assistant response is appended last.

    Args:
        properties: The run's schedule properties (source of messages, temperature).
        result: The stored run result (assembled completion under ``result``).
        build_input: Resolved model id, storage bucket, and run/schedule ids.

    Returns:
        The assembled conversation document.
    """
    model = DialModel(id=build_input.model_id)
    temperature = _payload_temperature(properties)
    settings: dict[str, Any] = {"prompt": "", "selectedAddons": [], "temperature": temperature}

    history = _build_history_messages(properties, model, settings)

    assistant = _assistant_message(result)
    custom_content = assistant.get("custom_content")
    response_id = assistant.get("responseId") or assistant.get("id")
    assistant_message = DialMessage(
        role="assistant",
        content=assistant.get("content") or "",
        model=model,
        custom_content=custom_content if isinstance(custom_content, dict) else None,
        responseId=response_id if isinstance(response_id, str) else None,
        settings=settings,
    )

    resource_path = conversation_resource_path(build_input)
    conversation_id = f"conversations/{resource_path}"
    folder_id = conversation_id.rsplit("/", 1)[0]

    return DialConversation(
        id=conversation_id,
        name=build_input.run_id,
        model=model,
        temperature=temperature,
        folderId=folder_id,
        messages=[*history, assistant_message],
        updatedAt=int(datetime.now(UTC).timestamp() * 1000),
    )


async def create_run_conversation(
    ctx: RunContext, credentials: OboCredentials, properties: ScheduleProperties, result: dict[str, Any]
) -> None:
    """Create a DIAL conversation for a completed chat completion run.

    Resolves the owner's bucket and the conversation model id, builds the conversation, and
    writes it to DIAL storage using the run owner's credentials. Raises
    ``ConversationCreationError`` on any failure; the caller treats this as non-fatal.

    Args:
        ctx: The run context (run/schedule ids).
        credentials: The run owner's on-behalf-of credentials.
        properties: The run's schedule properties.
        result: The stored run result.
    """
    settings = get_settings()
    client = DialClient(HttpClientProvider().client, settings.dial_url, settings.worker_http.timeout_seconds)

    try:
        deployment = extract_deployment_id(properties.url)
    except ValueError as exc:
        raise ConversationCreationError(str(exc)) from exc

    is_application = deployment.startswith(APPLICATION_DEPLOYMENT_PREFIX)
    logger.info(
        "conversation_create_start",
        run_id=ctx.run_id,
        schedule_id=ctx.schedule_id,
        deployment_kind="application" if is_application else "model",
    )

    if is_application:
        details = await client.get_application(credentials, deployment)
        model_id = details.reference
    else:
        model_id = deployment

    bucket = await client.get_bucket(credentials)
    build_input = ConversationBuildInput(
        model_id=model_id, bucket=bucket, schedule_id=ctx.schedule_id, run_id=ctx.run_id
    )
    conversation = build_conversation(properties, result, build_input)
    resource_path = conversation_resource_path(build_input)
    await client.put_conversation(credentials=credentials, path=resource_path, conversation=conversation)
    logger.info("conversation_create_succeeded", run_id=ctx.run_id, schedule_id=ctx.schedule_id)
