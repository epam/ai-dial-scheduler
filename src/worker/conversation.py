from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

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
    """Percent-encode one part of a conversation name so it stays a single, decodable segment.

    Encodes every character outside the RFC 3986 unreserved set (``safe=""``), then encodes the
    underscore as well. The extra step is load-bearing, not cosmetic: underscore *is* unreserved,
    so ``quote`` leaves it alone, and parts are joined by ``CONVERSATION_NAME_SEPARATOR`` (``__``).
    A deployment id such as ``applications/bkt/Untitled app 1101__0.0.1`` — a real id shape — would
    otherwise inject a fourth separator into a three-part name, so splitting the name back apart
    would silently yield the wrong pieces. Encoding ``_`` makes ``__`` impossible inside a part.

    The replacement cannot corrupt an input that already looks encoded: ``quote`` maps every literal
    ``%`` to ``%25`` first, so any underscore still present came from a literal underscore.

    Args:
        value: The raw deployment id or schedule display name.

    Returns:
        The encoded part; ``urllib.parse.unquote`` returns the exact original string.
    """
    return quote(value, safe="").replace("_", "%5F")


def _conversation_name(build_input: ConversationBuildInput) -> str:
    """Return the conversation display name ``{deployment}__{name}__{run-id}``.

    Every part is URL-encoded verbatim — no slugification, no truncation — so each decodes back to
    its exact original, and none can contain the ``__`` separator. The deployment id is the one
    configured on the schedule, never an application's resolved reference. The run id is encoded
    too: that is the identity function on a UUID, but it keeps the three-part split guarantee from
    resting on the shape of a value produced elsewhere.
    """
    segments = (
        encode_name_part(build_input.deployment_id),
        encode_name_part(build_input.display_name),
        encode_name_part(build_input.run_id),
    )
    return CONVERSATION_NAME_SEPARATOR.join(segments)


def conversation_resource_path(build_input: ConversationBuildInput) -> str:
    """Return the conversation storage path (``{bucket}/{folder...}/{name}``).

    This is the resource path DIAL uses to store and address the conversation, without the
    ``conversations/`` prefix. The final segment is the conversation name
    (``{deployment}__{name}__{run-id}``), which is path-safe because every part is percent-encoded:
    none can contribute a bare ``/``, so the name always occupies exactly one segment. The
    document's ``id`` and ``folderId`` are derived from it.
    """
    name = _conversation_name(build_input)
    return f"{build_input.bucket}/{SCHEDULER_CONVERSATION_FOLDER}/{build_input.schedule_id}/{name}"


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

    name = _conversation_name(build_input)
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
) -> None:
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
    """
    settings = get_settings()
    client = DialClient(HttpClientProvider().client, settings.dial_url, settings.worker_http.timeout_seconds)

    deployment = properties.payload.get("model")
    # payload is dict[str, Any] and this value is now path-facing, so a non-string would otherwise
    # surface as an opaque AttributeError/TypeError instead of naming the offending field.
    if not isinstance(deployment, str) or not deployment:
        raise ConversationCreationError("payload.model must be a non-empty string to create a conversation")

    is_application = deployment.startswith(APPLICATION_DEPLOYMENT_PREFIX)
    logger.info(
        "conversation_create_start",
        run_id=ctx.run_id,
        schedule_id=ctx.schedule_id,
        deployment_kind="application" if is_application else "model",
    )

    # Applications are still resolved to their underlying model reference: the reference is what
    # the conversation document records as its model, so the chat UI reopens the conversation
    # exactly as before. It is excluded from the storage path only -- see _conversation_name.
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
