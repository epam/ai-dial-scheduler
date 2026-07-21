"""Unit tests for the run-completion conversation builder and orchestration."""

from pydantic import SecretStr
import pytest
import structlog.testing

from src.lib.db.enums import TargetType
from src.lib.exceptions.errors import ConversationCreationError
from src.lib.schemas.conversation import ConversationBuildInput, DialConversation
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.worker.conversation import build_conversation, create_run_conversation


RUN_ID = "run-conv-001"
SCHEDULE_ID = "sched-conv-001"
BUCKET = "bkt-xyz"


def make_ctx(**kwargs) -> RunContext:
    defaults = dict(run_id=RUN_ID, schedule_id=SCHEDULE_ID)
    defaults.update(kwargs)
    return RunContext(**defaults)


def make_creds(**kwargs) -> OboCredentials:
    defaults = dict(header_name="Api-Key", header_value=SecretStr("owner-token"))
    defaults.update(kwargs)
    return OboCredentials(**defaults)


def make_properties(**kwargs) -> ScheduleProperties:
    defaults = dict(
        target_type=TargetType.CHAT_COMPLETION,
        url="https://dial.example.com/openai/deployments/gpt-5.6-sol-2026-07-09-reasoning/chat/completions",
        payload={"messages": [{"role": "user", "content": "hi"}]},
        api_version="2024-02-01",
        create_conversation=True,
    )
    defaults.update(kwargs)
    return ScheduleProperties(**defaults)


def _result(content: str = "Hi! How can I help?", custom_content=None) -> dict:
    message = {"role": "assistant", "content": content}
    if custom_content is not None:
        message["custom_content"] = custom_content
    return {"result": {"id": "cmpl-1", "choices": [{"index": 0, "message": message}]}}


_BUILD_INPUT = ConversationBuildInput(model_id="m", bucket=BUCKET, schedule_id=SCHEDULE_ID, run_id=RUN_ID)


class TestBuildConversationModel:
    def test_single_user_message_plus_assistant(self):
        conv = build_conversation(
            make_properties(),
            _result(),
            ConversationBuildInput(
                model_id="gpt-5.6-sol-2026-07-09-reasoning", bucket=BUCKET, schedule_id=SCHEDULE_ID, run_id=RUN_ID
            ),
        )
        assert isinstance(conv, DialConversation)
        assert len(conv.messages) == 2
        assert conv.messages[0].role == "user"
        assert conv.messages[0].content == "hi"
        assert conv.messages[1].role == "assistant"
        assert conv.messages[1].content == "Hi! How can I help?"

    def test_conversation_history_preserved(self):
        props = make_properties(
            payload={
                "messages": [
                    {"role": "user", "content": "first question"},
                    {"role": "assistant", "content": "first answer"},
                    {"role": "user", "content": "follow up"},
                ]
            }
        )
        conv = build_conversation(props, _result(), _BUILD_INPUT)
        assert len(conv.messages) == 4
        assert conv.messages[0].role == "user"
        assert conv.messages[0].content == "first question"
        assert conv.messages[1].role == "assistant"
        assert conv.messages[1].content == "first answer"
        assert conv.messages[2].role == "user"
        assert conv.messages[2].content == "follow up"
        assert conv.messages[3].role == "assistant"

    def test_system_message_included_in_history(self):
        props = make_properties(
            payload={
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "hi"},
                ]
            }
        )
        conv = build_conversation(props, _result(), _BUILD_INPUT)
        assert len(conv.messages) == 3
        assert conv.messages[0].role == "system"
        assert conv.messages[0].content == "You are a helpful assistant."
        assert conv.messages[1].role == "user"

    def test_model_id_used(self):
        conv = build_conversation(
            make_properties(),
            _result(),
            ConversationBuildInput(
                model_id="gpt-5.6-sol-2026-07-09-reasoning", bucket=BUCKET, schedule_id=SCHEDULE_ID, run_id=RUN_ID
            ),
        )
        assert conv.model.id == "gpt-5.6-sol-2026-07-09-reasoning"
        assert conv.messages[1].model.id == "gpt-5.6-sol-2026-07-09-reasoning"

    def test_id_and_folder_follow_convention(self):
        conv = build_conversation(
            make_properties(),
            _result(),
            ConversationBuildInput(model_id="m", bucket=BUCKET, schedule_id=SCHEDULE_ID, run_id=RUN_ID),
        )
        assert conv.id == f"conversations/{BUCKET}/.scheduler/{SCHEDULE_ID}/{RUN_ID}"
        assert conv.folderId == f"conversations/{BUCKET}/.scheduler/{SCHEDULE_ID}"

    def test_name_is_run_id(self):
        conv = build_conversation(make_properties(), _result(), _BUILD_INPUT)
        assert conv.name == RUN_ID


class TestBuildConversationCustomContent:
    def test_assistant_custom_content_preserved(self):
        custom = {"state": {"tool_execution_history": [{"role": "assistant", "content": ""}]}}
        conv = build_conversation(
            make_properties(),
            _result(custom_content=custom),
            ConversationBuildInput(model_id="m", bucket=BUCKET, schedule_id=SCHEDULE_ID, run_id=RUN_ID),
        )
        assert conv.messages[1].custom_content == custom

    def test_no_custom_content_when_absent(self):
        conv = build_conversation(
            make_properties(),
            _result(),
            ConversationBuildInput(model_id="m", bucket=BUCKET, schedule_id=SCHEDULE_ID, run_id=RUN_ID),
        )
        assert conv.messages[1].custom_content is None


@pytest.fixture
def mock_dial(mocker):
    dial_client = mocker.MagicMock()
    dial_client.get_bucket = mocker.AsyncMock(return_value=BUCKET)
    dial_client.get_application = mocker.AsyncMock()
    dial_client.put_conversation = mocker.AsyncMock()
    mocker.patch("src.worker.conversation.DialClient", return_value=dial_client)
    mocker.patch("src.worker.conversation.HttpClientProvider")
    mocker.patch(
        "src.worker.conversation.get_settings",
        return_value=mocker.MagicMock(
            dial_url="https://dial.example.com",
            worker_http=mocker.MagicMock(timeout_seconds=60.0),
        ),
    )
    return dial_client


class TestCreateRunConversationOrchestration:
    async def test_resolves_bucket_then_writes(self, mock_dial):
        with structlog.testing.capture_logs():
            await create_run_conversation(make_ctx(), make_creds(), make_properties(), _result())

        mock_dial.get_bucket.assert_awaited_once()
        mock_dial.put_conversation.assert_awaited_once()
        # Conversation id embeds the bucket returned by get_bucket.
        written = mock_dial.put_conversation.call_args.kwargs["conversation"]
        assert f"/{BUCKET}/" in written.id

    async def test_model_deployment_skips_get_application(self, mock_dial):
        with structlog.testing.capture_logs():
            await create_run_conversation(make_ctx(), make_creds(), make_properties(), _result())

        mock_dial.get_application.assert_not_awaited()
        written = mock_dial.put_conversation.call_args.kwargs["conversation"]
        assert written.model.id == "gpt-5.6-sol-2026-07-09-reasoning"

    async def test_user_credential_passed_to_calls(self, mock_dial):
        creds = make_creds(header_name="Authorization", header_value=SecretStr("Bearer owner-token"))
        with structlog.testing.capture_logs():
            await create_run_conversation(make_ctx(), creds, make_properties(), _result())

        assert mock_dial.get_bucket.call_args.args[0] is creds
        assert mock_dial.put_conversation.call_args.kwargs["credentials"] is creds

    async def test_secret_not_logged(self, mock_dial):
        creds = make_creds(header_value=SecretStr("super-secret"))
        with structlog.testing.capture_logs() as logs:
            await create_run_conversation(make_ctx(), creds, make_properties(), _result())

        for log in logs:
            assert "super-secret" not in str(log)

    async def test_bucket_failure_raises_conversation_error(self, mock_dial):
        mock_dial.get_bucket.side_effect = ConversationCreationError("bucket down")

        with structlog.testing.capture_logs():
            with pytest.raises(ConversationCreationError):
                await create_run_conversation(make_ctx(), make_creds(), make_properties(), _result())

        mock_dial.put_conversation.assert_not_awaited()


class TestCreateRunConversationApplication:
    async def test_application_deployment_resolves_reference(self, mocker, mock_dial):
        from src.lib.schemas.conversation import ApplicationDetails

        mock_dial.get_application.return_value = ApplicationDetails(reference="c9e5decf-1284-421e-ab84-c790beea837d")
        app_url = (
            "https://dial.example.com/openai/deployments/applications/bkt/Untitled app 1101__0.0.1/chat/completions"
        )

        with structlog.testing.capture_logs():
            await create_run_conversation(make_ctx(), make_creds(), make_properties(url=app_url), _result())

        mock_dial.get_application.assert_awaited_once()
        written = mock_dial.put_conversation.call_args.kwargs["conversation"]
        assert written.model.id == "c9e5decf-1284-421e-ab84-c790beea837d"

    async def test_application_reference_failure_raises(self, mock_dial):
        mock_dial.get_application.side_effect = ConversationCreationError("no reference")
        app_url = (
            "https://dial.example.com/openai/deployments/applications/bkt/Untitled app 1101__0.0.1/chat/completions"
        )

        with structlog.testing.capture_logs():
            with pytest.raises(ConversationCreationError):
                await create_run_conversation(make_ctx(), make_creds(), make_properties(url=app_url), _result())

        mock_dial.put_conversation.assert_not_awaited()
