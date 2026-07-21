from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DialModel(BaseModel):
    """Model reference used by a DIAL conversation and its messages."""

    id: str


class DialMessage(BaseModel):
    """A single conversation message in the DIAL Chat shape.

    Assistant messages may carry ``custom_content`` (tool execution history, stages)
    copied verbatim from the completion's ``choices[0].message.custom_content``.
    """

    role: str
    content: str = ""
    model: DialModel
    custom_content: dict[str, Any] | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    responseId: str | None = None  # noqa: N815 — DIAL Chat field name


class DialConversation(BaseModel):
    """A DIAL conversation document written after a successful chat completion run.

    Field names mirror the DIAL Chat conversation resource (``folderId``, ``updatedAt``,
    ``responseFormat``) so the stored document opens and renders like a manually created
    conversation.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    model: DialModel
    prompt: str = ""
    temperature: float | None = None
    folderId: str  # noqa: N815 — DIAL Chat field name
    messages: list[DialMessage]
    selectedAddons: list[str] = Field(default_factory=list)  # noqa: N815 — DIAL Chat field name
    updatedAt: int  # noqa: N815 — DIAL Chat field name
    responseFormat: str = "Markdown"  # noqa: N815 — DIAL Chat field name


class ConversationBuildInput(BaseModel):
    """Resolved inputs for assembling a conversation document.

    Groups the values derived during orchestration (model id, storage location, ids) so the
    builder keeps a small parameter list.
    """

    model_id: str
    bucket: str
    schedule_id: str
    run_id: str


class ApplicationDetails(BaseModel):
    """Subset of the DIAL application details response.

    Only ``reference`` (the underlying model reference) is consumed; other fields are
    ignored so the schema tolerates the full application payload.
    """

    model_config = ConfigDict(extra="ignore")

    reference: str
