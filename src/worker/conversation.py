from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote

from src.lib.clients.dial_client import DialClient
from src.lib.constants import (
    APPLICATION_DEPLOYMENT_PREFIX,
    CONVERSATION_ID_PREFIX,
    CONVERSATION_NAME_SEPARATOR,
    SCHEDULER_CONVERSATION_FOLDER,
)
from src.lib.db.engine import Database
from src.lib.exceptions.errors import ConversationCreationError
from src.lib.repositories.schedule_repository import ScheduleRepository
from src.lib.schemas.conversation import ConversationBuildInput, DialConversation, DialMessage, DialModel
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.lib.settings import get_settings
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


def encode_name_part(value: str) -> str:
    """Make one part of a conversation name safe to sit inside a single storage path segment.

    The stored name is kept *raw* — spaces, punctuation and non-ASCII characters are preserved
    literally, exactly as DIAL Chat stores its own conversation names. Percent-encoding belongs to
    the request URL, not to the resource name (see ``DialClient``), so only the two characters that
    a single path segment genuinely cannot carry are escaped: ``/`` and ``\\``. ``%`` is escaped
    first so the transformation stays reversible — a literal ``%`` in the input can never be
    confused with an escape this function introduced.

    Args:
        value: The raw deployment name part or schedule display name.

    Returns:
        The name part; ``urllib.parse.unquote`` returns the exact original string.
    """
    return value.replace("%", "%25").replace("/", "%2F").replace("\\", "%5C")


def _deployment_location(deployment_id: str) -> tuple[str, str]:
    """Split a deployment id into its folder prefix and its final name part.

    DIAL never percent-encodes a deployment id's separators into a single segment; it lets them
    stay real folders. ``applications/{bucket}/{app name}`` therefore contributes the folder path
    ``applications/{bucket}`` and the name part ``{app name}``. A model deployment id has no
    separator, so it yields an empty folder path and itself as the name part — one rule, no
    branching by deployment kind.

    Args:
        deployment_id: The deployment id configured on the schedule.

    Returns:
        ``(folder_path, name_part)``; ``folder_path`` is empty when the id has no separator.
    """
    prefix, separator, last = deployment_id.rpartition("/")
    return (prefix if separator else "", last)


def _conversation_leaf(build_input: ConversationBuildInput) -> str:
    """Return the final storage path segment ``{deployment-name}__{schedule-name}__{run-id}``.

    This is the conversation's address, not its title: the run id keeps every run of a schedule at
    its own path, and the deployment part keeps two schedules targeting different deployments apart.
    Parts are joined verbatim — no slugification, no truncation — and only path separators are
    escaped. The deployment part is the final segment of the id configured on the schedule (its
    folder segments live in the path instead), never an application's resolved reference.
    """
    _, deployment_name = _deployment_location(build_input.deployment_id)
    segments = (
        encode_name_part(deployment_name),
        encode_name_part(build_input.display_name),
        encode_name_part(build_input.run_id),
    )
    return CONVERSATION_NAME_SEPARATOR.join(segments)


def conversation_resource_path(build_input: ConversationBuildInput) -> str:
    """Return the conversation storage path (``{bucket}/{folder...}/{name}``).

    This is the resource path DIAL uses to store and address the conversation, without the
    ``conversations/`` prefix. It carries raw, unescaped characters; ``DialClient`` percent-encodes
    each segment when it builds the request URL. The deployment id's own folder segments are nested
    under the schedule folder, and the final segment is the conversation name. The document's
    ``id`` and ``folderId`` are derived from it.
    """
    deployment_folder, _ = _deployment_location(build_input.deployment_id)
    segments = [build_input.bucket, SCHEDULER_CONVERSATION_FOLDER, build_input.schedule_id]
    if deployment_folder:
        segments.append(deployment_folder)
    segments.append(_conversation_leaf(build_input))
    return "/".join(segments)


def build_conversation(
    properties: ScheduleProperties, result: dict[str, Any], build_input: ConversationBuildInput
) -> DialConversation:
    """Build a DIAL conversation from a completed run.

    The full message history from the schedule payload is included (system prompt goes
    into ``DialConversation.prompt``; all other messages become ``DialMessage`` entries).
    The assembled assistant response is appended last. The document's ``name`` is the schedule's
    display name; the three-part ``{deployment}__{name}__{run-id}`` form is the storage path leaf.

    Args:
        properties: The run's schedule properties (source of messages, temperature).
        result: The stored run result (assembled completion under ``result``).
        build_input: Resolved deployment id (path-facing) and model id (document-facing),
            storage bucket, and run/schedule ids.

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

    # The title is the schedule's display name alone -- it is what the user reads in DIAL Chat, so it
    # carries no deployment or run id noise and needs no escaping (only the path leaf is a segment).
    # Runs stay distinguishable through the storage path, which keeps the three-part leaf.
    name = build_input.display_name
    conversation_id = f"{CONVERSATION_ID_PREFIX}{conversation_resource_path(build_input)}"
    folder_id = conversation_id.rsplit("/", 1)[0]

    return DialConversation(
        id=conversation_id,
        name=name,
        model=model,
        temperature=temperature,
        folderId=folder_id,
        messages=[*history, assistant_message],
        updatedAt=int(datetime.now(UTC).timestamp() * 1000),
    )


async def create_run_conversation(
    ctx: RunContext, credentials: OboCredentials, properties: ScheduleProperties, result: dict[str, Any]
) -> str:
    """Create a DIAL conversation for a completed chat completion run.

    Resolves the owner's bucket and, for applications, the underlying model reference, builds the
    conversation, and writes it to DIAL storage using the run owner's credentials. The storage path
    is keyed on the deployment configured on the schedule; the resolved reference is recorded only
    inside the document. Raises ``ConversationCreationError`` on any failure; the caller treats
    this as non-fatal.

    Args:
        ctx: The run context (run/schedule ids).
        credentials: The run owner's on-behalf-of credentials.
        properties: The run's schedule properties.
        result: The stored run result.

    Returns:
        The addressable DIAL conversation id (``conversations/...``) of the created conversation.
    """
    settings = get_settings()
    client = DialClient(ctx.http_client or HttpClientProvider().client, settings.dial_url)

    raw_deployment = properties.payload.get("model")
    # payload is dict[str, Any] and this value is now path-facing, so a non-string would otherwise
    # surface as an opaque AttributeError/TypeError instead of naming the offending field.
    if not isinstance(raw_deployment, str) or not raw_deployment:
        raise ConversationCreationError("payload.model must be a non-empty string to create a conversation")

    # DIAL hands out application deployment ids in their URL form, already percent-encoded
    # ("applications/{bucket}/Planning%20Assistant__0.0.1"). Everything downstream -- the name parts
    # and the resource path -- works on raw names and is escaped once by DialClient, so the id is
    # decoded back to its raw form here. Skipping this escapes the id twice: the application lookup
    # asks for "Planning%2520Assistant__0.0.1" and 404s, and the conversation is stored under the
    # literal escapes instead of its name. Model deployment ids carry no escapes, so this is a no-op
    # for them.
    deployment = unquote(raw_deployment)

    is_application = deployment.startswith(APPLICATION_DEPLOYMENT_PREFIX)
    logger.info(
        "conversation_create_start",
        run_id=ctx.run_id,
        schedule_id=ctx.schedule_id,
        deployment_kind="application" if is_application else "model",
    )

    # Applications are still resolved to their underlying model reference: the reference is what
    # the conversation document records as its model, so the chat UI reopens the conversation
    # exactly as before. It is excluded from the storage path only -- see _conversation_leaf.
    if is_application:
        details = await client.get_application(credentials, deployment)
        model_id = details.reference
    else:
        model_id = deployment

    async with Database().sessionmaker() as session:
        schedule = await ScheduleRepository(session).get_by_id(ctx.schedule_id)
    if schedule is None:
        raise ConversationCreationError(f"schedule not found: {ctx.schedule_id}")

    bucket = await client.get_bucket(credentials)
    build_input = ConversationBuildInput(
        deployment_id=deployment,
        model_id=model_id,
        bucket=bucket,
        schedule_id=ctx.schedule_id,
        run_id=ctx.run_id,
        display_name=schedule.display_name,
    )
    conversation = build_conversation(properties, result, build_input)
    # Derived from the document rather than recomputed, so the PUT target and the stored id can
    # never drift apart.
    resource_path = conversation.id.removeprefix(CONVERSATION_ID_PREFIX)
    await client.put_conversation(credentials=credentials, path=resource_path, conversation=conversation)
    logger.info("conversation_create_succeeded", run_id=ctx.run_id, schedule_id=ctx.schedule_id)
    return conversation.id
