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
DEPLOYMENT_ID = "chathub-claude4"
DISPLAY_NAME = "hello"


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
        url="https://dial.example.com/openai/",
        payload={"model": "gpt-5.6-sol-2026-07-09-reasoning", "messages": [{"role": "user", "content": "hi"}]},
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


def make_build_input(**kwargs) -> ConversationBuildInput:
    defaults = dict(
        model_id=DEPLOYMENT_ID,
        bucket=BUCKET,
        schedule_id=SCHEDULE_ID,
        run_id=RUN_ID,
        display_name=DISPLAY_NAME,
    )
    defaults.update(kwargs)
    return ConversationBuildInput(**defaults)


_BUILD_INPUT = make_build_input()


class TestBuildConversationModel:
    def test_single_user_message_plus_assistant(self):
        conv = build_conversation(
            make_properties(),
            _result(),
            make_build_input(model_id="gpt-5.6-sol-2026-07-09-reasoning"),
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
                "model": "gpt-5.6-sol-2026-07-09-reasoning",
                "messages": [
                    {"role": "user", "content": "first question"},
                    {"role": "assistant", "content": "first answer"},
                    {"role": "user", "content": "follow up"},
                ],
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
                "model": "gpt-5.6-sol-2026-07-09-reasoning",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "hi"},
                ],
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
            make_build_input(model_id="gpt-5.6-sol-2026-07-09-reasoning"),
        )
        assert conv.model.id == "gpt-5.6-sol-2026-07-09-reasoning"
        assert conv.messages[1].model.id == "gpt-5.6-sol-2026-07-09-reasoning"

    def test_id_and_folder_follow_convention(self):
        conv = build_conversation(
            make_properties(),
            _result(),
            make_build_input(),
        )
        assert conv.id == f"conversations/{BUCKET}/.scheduler/{SCHEDULE_ID}/chathub-claude4__hello__{RUN_ID}"
        assert conv.folderId == f"conversations/{BUCKET}/.scheduler/{SCHEDULE_ID}"

    def test_name_follows_convention(self):
        conv = build_conversation(make_properties(), _result(), _BUILD_INPUT)
        assert conv.name == f"chathub-claude4__hello__{RUN_ID}"

    def test_id_final_segment_matches_name(self):
        # The conversation name is used as the final path segment (path-safe).
        conv = build_conversation(make_properties(), _result(), _BUILD_INPUT)
        assert conv.id.rsplit("/", 1)[1] == conv.name

    def test_folder_is_stable_and_messages_preserved(self):
        # The folder never includes the name; only the leaf segment carries it.
        conv = build_conversation(make_properties(), _result(), _BUILD_INPUT)
        assert conv.folderId == f"conversations/{BUCKET}/.scheduler/{SCHEDULE_ID}"
        assert len(conv.messages) == 2
        assert conv.messages[0].content == "hi"
        assert conv.messages[1].content == "Hi! How can I help?"

    def test_different_run_ids_produce_different_names(self):
        # SC-003: uniqueness guaranteed by the run id segment.
        conv_a = build_conversation(make_properties(), _result(), make_build_input(run_id="run-a"))
        conv_b = build_conversation(make_properties(), _result(), make_build_input(run_id="run-b"))
        assert conv_a.name != conv_b.name


class TestBuildConversationCustomContent:
    def test_assistant_custom_content_preserved(self):
        custom = {"state": {"tool_execution_history": [{"role": "assistant", "content": ""}]}}
        conv = build_conversation(
            make_properties(),
            _result(custom_content=custom),
            make_build_input(),
        )
        assert conv.messages[1].custom_content == custom

    def test_no_custom_content_when_absent(self):
        conv = build_conversation(
            make_properties(),
            _result(),
            make_build_input(),
        )
        assert conv.messages[1].custom_content is None


@pytest.fixture
def mock_schedule(mocker):
    """Patch the worker's DB session + ScheduleRepository so the display name resolves."""
    schedule = mocker.MagicMock()
    schedule.display_name = DISPLAY_NAME
    repo = mocker.MagicMock()
    repo.get_by_id = mocker.AsyncMock(return_value=schedule)
    mocker.patch("src.worker.conversation.ScheduleRepository", return_value=repo)

    session = mocker.MagicMock()
    session.__aenter__ = mocker.AsyncMock(return_value=session)
    session.__aexit__ = mocker.AsyncMock(return_value=None)
    database = mocker.MagicMock()
    database.sessionmaker.return_value = session
    mocker.patch("src.worker.conversation.Database", return_value=database)
    return repo


@pytest.fixture
def mock_dial(mocker, mock_schedule):
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

    async def test_name_uses_fetched_display_name_and_resolved_deployment(self, mock_dial, mock_schedule):
        with structlog.testing.capture_logs():
            await create_run_conversation(make_ctx(), make_creds(), make_properties(), _result())

        mock_schedule.get_by_id.assert_awaited_once_with(SCHEDULE_ID)
        written = mock_dial.put_conversation.call_args.kwargs["conversation"]
        # For a model deployment the resolved model id equals the payload model (verbatim segment).
        assert written.name == f"gpt-5.6-sol-2026-07-09-reasoning__{DISPLAY_NAME}__{RUN_ID}"

    async def test_bucket_failure_raises_conversation_error(self, mock_dial):
        mock_dial.get_bucket.side_effect = ConversationCreationError("bucket down")

        with structlog.testing.capture_logs():
            with pytest.raises(ConversationCreationError):
                await create_run_conversation(make_ctx(), make_creds(), make_properties(), _result())

        mock_dial.put_conversation.assert_not_awaited()


_APP_MODEL = "applications/bkt/Untitled app 1101__0.0.1"


def _app_payload() -> dict:
    return {"model": _APP_MODEL, "messages": [{"role": "user", "content": "hi"}]}


class TestConversationNameEdgeInputs:
    # US2 / SC-004: edge display names never break naming and stay unique via the run id.
    @pytest.mark.parametrize(
        ("display_name", "expected_segment"),
        [
            ("Привет", "privet"),  # non-Latin is transliterated, not dropped
            ("a" * 80, "a" * 64),  # capped at CONVERSATION_NAME_SEGMENT_MAX_LENGTH
        ],
    )
    def test_build_conversation_handles_edge_display_names(self, display_name, expected_segment):
        conv = build_conversation(make_properties(), _result(), make_build_input(display_name=display_name))
        assert conv.name == f"chathub-claude4__{expected_segment}__{RUN_ID}"
        # The run id segment always survives verbatim.
        assert conv.name.rsplit("__", 1)[1] == RUN_ID

    @pytest.mark.parametrize("display_name", ["Привет", "a" * 80])
    async def test_create_run_conversation_never_raises_on_edge_names(self, mock_dial, mock_schedule, display_name):
        mock_schedule.get_by_id.return_value.display_name = display_name
        with structlog.testing.capture_logs():
            await create_run_conversation(make_ctx(), make_creds(), make_properties(), _result())
        mock_dial.put_conversation.assert_awaited_once()


class TestCreateRunConversationApplication:
    async def test_application_deployment_resolves_reference(self, mocker, mock_dial):
        from src.lib.schemas.conversation import ApplicationDetails

        mock_dial.get_application.return_value = ApplicationDetails(reference="c9e5decf-1284-421e-ab84-c790beea837d")

        with structlog.testing.capture_logs():
            await create_run_conversation(make_ctx(), make_creds(), make_properties(payload=_app_payload()), _result())

        mock_dial.get_application.assert_awaited_once()
        assert mock_dial.get_application.call_args.args[1] == _APP_MODEL
        written = mock_dial.put_conversation.call_args.kwargs["conversation"]
        assert written.model.id == "c9e5decf-1284-421e-ab84-c790beea837d"
        # The name's deployment segment uses the resolved app reference (same as content), not the app path.
        assert written.name == f"c9e5decf-1284-421e-ab84-c790beea837d__{DISPLAY_NAME}__{RUN_ID}"

    async def test_application_reference_failure_raises(self, mock_dial):
        mock_dial.get_application.side_effect = ConversationCreationError("no reference")

        with structlog.testing.capture_logs():
            with pytest.raises(ConversationCreationError):
                await create_run_conversation(
                    make_ctx(), make_creds(), make_properties(payload=_app_payload()), _result()
                )

        mock_dial.put_conversation.assert_not_awaited()


class TestCreateRunConversationModelResolution:
    async def test_model_taken_from_payload(self, mock_dial):
        with structlog.testing.capture_logs():
            await create_run_conversation(
                make_ctx(),
                make_creds(),
                make_properties(payload={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}),
                _result(),
            )

        mock_dial.get_application.assert_not_awaited()
        written = mock_dial.put_conversation.call_args.kwargs["conversation"]
        assert written.model.id == "gpt-4o"

    async def test_missing_model_raises_conversation_error(self, mock_dial):
        # Construct properties whose payload lacks a model, bypassing schema validation
        # (validation normally guarantees presence) to exercise the executor's guard.
        props = make_properties()
        object.__setattr__(props, "payload", {"messages": [{"role": "user", "content": "hi"}]})

        with structlog.testing.capture_logs():
            with pytest.raises(ConversationCreationError):
                await create_run_conversation(make_ctx(), make_creds(), props, _result())

        mock_dial.put_conversation.assert_not_awaited()
