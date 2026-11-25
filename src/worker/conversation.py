from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote

from src.lib.clients.dial_client import DialClient
from src.lib.constants import (
    APPLICATION_DEPLOYMENT_PREFIX,
    CONVERSATION_ID_PREFIX,
    CONVERSATION_NAME_SEPARATOR,
    MAX_CONVERSATION_ID_HEADER_BYTES,
    SCHEDULER_CONVERSATION_FOLDER,
)
from src.lib.db.engine import Database
from src.lib.exceptions.errors import ConversationCreationError
from src.lib.repositories.schedule_repository import ScheduleRepository
from src.lib.schemas.conversation import (
    ConversationBuildInput,
    ConversationLocation,
    DialConversation,
    DialMessage,
    DialModel,
)
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.lib.settings import get_settings
from src.lib.utils.conversation_header import encode_header_value
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


def _conversation_leaf(location: ConversationLocation) -> str:
    """Return the final storage path segment ``{deployment-name}__{schedule-name}__{run-id}``.

    This is the conversation's address, not its title: the run id keeps every run of a schedule at
    its own path, and the deployment part keeps two schedules targeting different deployments apart.
    Parts are joined verbatim — no slugification, no truncation — and only path separators are
    escaped. The deployment part is the final segment of the id configured on the schedule (its
    folder segments live in the path instead), never an application's resolved reference.
    """
    _, deployment_name = _deployment_location(location.deployment_id)
    segments = (
        encode_name_part(deployment_name),
        encode_name_part(location.display_name),
        encode_name_part(location.run_id),
    )
    return CONVERSATION_NAME_SEPARATOR.join(segments)


def conversation_resource_path(location: ConversationLocation) -> str:
    """Return the conversation storage path (``{bucket}/{folder...}/{name}``).

    This is the resource path DIAL uses to store and address the conversation, without the
    ``conversations/`` prefix. It carries raw, unescaped characters; ``DialClient`` percent-encodes
    each segment when it builds the request URL. The deployment id's own folder segments are nested
    under the schedule folder, and the final segment is the conversation name. The document's
    ``id`` and ``folderId`` are derived from it.
    """
    deployment_folder, _ = _deployment_location(location.deployment_id)
    segments = [location.bucket, SCHEDULER_CONVERSATION_FOLDER, location.schedule_id]
    if deployment_folder:
        segments.append(deployment_folder)
    segments.append(_conversation_leaf(location))
    return "/".join(segments)


def deployment_kind(deployment_id: str) -> str:
    """Return ``"application"`` for an ``applications/...`` deployment id, otherwise ``"model"``."""
    return "application" if deployment_id.startswith(APPLICATION_DEPLOYMENT_PREFIX) else "model"


def conversation_id_for(location: ConversationLocation) -> str:
    """Return the addressable conversation id (``conversations/{resource path}``) for a location.

    The single source for the ``X-CONVERSATION-ID`` header value, the stored document ``id`` and
    the PUT target, so the three can never drift apart.

    Args:
        location: The run's resolved conversation location.

    Returns:
        The conversation id, raw (not percent-encoded).
    """
    return f"{CONVERSATION_ID_PREFIX}{conversation_resource_path(location)}"


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
    conversation_id = conversation_id_for(build_input)
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


async def resolve_conversation_location(
    ctx: RunContext, credentials: OboCredentials, properties: ScheduleProperties
) -> ConversationLocation:
    """Resolve where a run's conversation will live, before the target request is sent.

    Loads the schedule's display name and the owner's bucket so the conversation id can be sent as
    ``X-CONVERSATION-ID`` on every attempt and the conversation saved at that same location after
    the run. Raises ``ConversationCreationError`` when any input is missing; the caller treats this
    as non-fatal.

    Args:
        ctx: The run context (run/schedule ids).
        credentials: The run owner's on-behalf-of credentials.
        properties: The run's schedule properties (source of ``payload.model``).

    Returns:
        The run's conversation location.

    Raises:
        ConversationCreationError: On an unusable ``payload.model``, a missing schedule, a failed
            bucket lookup, or a conversation id too long to send as a header.
    """
    raw_deployment = properties.payload.get("model")
    # payload is dict[str, Any] and this value is path-facing, so a non-string would otherwise
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

    async with Database().sessionmaker() as session:
        schedule = await ScheduleRepository(session).get_by_id(ctx.schedule_id)
    if schedule is None:
        raise ConversationCreationError(f"schedule not found: {ctx.schedule_id}")

    client = DialClient(ctx.http_client or HttpClientProvider().client, get_settings().dial_url)
    bucket = await client.get_bucket(credentials)
    location = ConversationLocation(
        deployment_id=deployment,
        bucket=bucket,
        schedule_id=ctx.schedule_id,
        run_id=ctx.run_id,
        display_name=schedule.display_name,
    )
    # display_name has no length limit; an oversized header would make the target reject the run.
    if len(encode_header_value(conversation_id_for(location))) > MAX_CONVERSATION_ID_HEADER_BYTES:
        raise ConversationCreationError(
            f"conversation id exceeds {MAX_CONVERSATION_ID_HEADER_BYTES} bytes as a request header"
        )
    return location


async def create_run_conversation(
    ctx: RunContext, credentials: OboCredentials, properties: ScheduleProperties, result: dict[str, Any]
) -> str:
    """Create a DIAL conversation for a completed chat completion run.

    Saves the conversation at the location resolved before the run (``ctx.conversation_location``),
    so its id matches the ``X-CONVERSATION-ID`` the target request carried. For applications the
    underlying model reference is resolved here, since it is only recorded inside the document; the
    storage path stays keyed on the deployment configured on the schedule. Raises
    ``ConversationCreationError`` on any failure; the caller treats this as non-fatal.

    Args:
        ctx: The run context carrying the resolved conversation location.
        credentials: The run owner's on-behalf-of credentials.
        properties: The run's schedule properties.
        result: The stored run result.

    Returns:
        The addressable DIAL conversation id (``conversations/...``) of the created conversation.
    """
    location = ctx.conversation_location
    if location is None:
        # Callers skip conversation creation without a location; this guards direct use.
        raise ConversationCreationError("conversation location not resolved")

    client = DialClient(ctx.http_client or HttpClientProvider().client, get_settings().dial_url)
    kind = deployment_kind(location.deployment_id)
    logger.info("conversation_create_start", run_id=ctx.run_id, schedule_id=ctx.schedule_id, deployment_kind=kind)

    # Applications are still resolved to their underlying model reference: the reference is what
    # the conversation document records as its model, so the chat UI reopens the conversation
    # exactly as before. It is excluded from the storage path only -- see _conversation_leaf.
    if kind == "application":
        details = await client.get_application(credentials, location.deployment_id)
        model_id = details.reference
    else:
        model_id = location.deployment_id

    build_input = ConversationBuildInput(**location.model_dump(), model_id=model_id)
    conversation = build_conversation(properties, result, build_input)
    # Derived from the document rather than recomputed, so the PUT target and the stored id can
    # never drift apart.
    resource_path = conversation.id.removeprefix(CONVERSATION_ID_PREFIX)
    await client.put_conversation(credentials=credentials, path=resource_path, conversation=conversation)
    logger.info("conversation_create_succeeded", run_id=ctx.run_id, schedule_id=ctx.schedule_id)
    return conversation.id
