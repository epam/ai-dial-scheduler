"""Unit tests for DIAL conversation schemas (Phase 2 foundational)."""

from pydantic import ValidationError
import pytest

from src.lib.schemas.conversation import (
    ApplicationDetails,
    DialConversation,
    DialMessage,
    DialModel,
)


class TestDialModel:
    def test_holds_id(self):
        model = DialModel(id="gpt-5.6-sol-2026-07-09-reasoning")
        assert model.id == "gpt-5.6-sol-2026-07-09-reasoning"


class TestDialMessage:
    def test_user_message_defaults(self):
        msg = DialMessage(role="user", content="hi", model=DialModel(id="m"))
        assert msg.role == "user"
        assert msg.content == "hi"
        assert msg.custom_content is None

    def test_assistant_message_with_custom_content(self):
        msg = DialMessage(
            role="assistant",
            content="Hi! How can I help?",
            model=DialModel(id="m"),
            custom_content={"state": {"responses_output": []}},
        )
        assert msg.custom_content == {"state": {"responses_output": []}}

    def test_content_defaults_to_empty(self):
        msg = DialMessage(role="assistant", model=DialModel(id="m"))
        assert msg.content == ""


class TestDialConversation:
    def test_two_message_shape(self):
        conv = DialConversation(
            id="conversations/bkt/.scheduler/sched-1/run-1",
            name="hi",
            model=DialModel(id="m"),
            folderId="conversations/bkt/.scheduler/sched-1",
            messages=[
                DialMessage(role="user", content="hi", model=DialModel(id="m")),
                DialMessage(role="assistant", content="hello", model=DialModel(id="m")),
            ],
            updatedAt=1784286243253,
        )
        assert len(conv.messages) == 2
        assert conv.messages[0].role == "user"
        assert conv.messages[1].role == "assistant"

    def test_id_and_folder_format(self):
        conv = DialConversation(
            id="conversations/bkt/.scheduler/sched-1/run-1",
            name="hi",
            model=DialModel(id="m"),
            folderId="conversations/bkt/.scheduler/sched-1",
            messages=[
                DialMessage(role="user", content="hi", model=DialModel(id="m")),
                DialMessage(role="assistant", content="hello", model=DialModel(id="m")),
            ],
            updatedAt=1,
        )
        assert conv.id.startswith("conversations/bkt/.scheduler/sched-1/")
        assert conv.folderId == "conversations/bkt/.scheduler/sched-1"

    def test_defaults(self):
        conv = DialConversation(
            id="conversations/bkt/.scheduler/s/r",
            name="hi",
            model=DialModel(id="m"),
            folderId="conversations/bkt/.scheduler/s",
            messages=[
                DialMessage(role="user", content="hi", model=DialModel(id="m")),
                DialMessage(role="assistant", content="hello", model=DialModel(id="m")),
            ],
            updatedAt=1,
        )
        assert conv.prompt == ""
        assert conv.temperature == 1
        assert conv.selectedAddons == []
        assert conv.responseFormat == "Markdown"

    def test_model_dump_uses_dial_field_names(self):
        conv = DialConversation(
            id="conversations/bkt/.scheduler/s/r",
            name="hi",
            model=DialModel(id="m"),
            folderId="conversations/bkt/.scheduler/s",
            messages=[
                DialMessage(role="user", content="hi", model=DialModel(id="m")),
                DialMessage(role="assistant", content="hello", model=DialModel(id="m")),
            ],
            updatedAt=1,
        )
        dumped = conv.model_dump()
        assert "folderId" in dumped
        assert "responseFormat" in dumped
        assert "updatedAt" in dumped


class TestApplicationDetails:
    def test_parses_reference(self):
        details = ApplicationDetails(reference="c9e5decf-1284-421e-ab84-c790beea837d")
        assert details.reference == "c9e5decf-1284-421e-ab84-c790beea837d"

    def test_ignores_extra_fields(self):
        details = ApplicationDetails.model_validate(
            {"reference": "ref-1", "display_name": "Untitled app", "status": "succeeded"}
        )
        assert details.reference == "ref-1"

    def test_missing_reference_raises(self):
        with pytest.raises(ValidationError):
            ApplicationDetails.model_validate({"display_name": "no ref"})
