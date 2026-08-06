"""Unit tests for the run-completion conversation builder and orchestration."""

from urllib.parse import unquote

from pydantic import SecretStr
import pytest
import structlog.testing

from src.lib.constants import CONVERSATION_NAME_SEPARATOR
from src.lib.db.enums import TargetType
from src.lib.exceptions.errors import ConversationCreationError
from src.lib.schemas.conversation import ApplicationDetails, ConversationBuildInput, DialConversation
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.worker.conversation import build_conversation, create_run_conversation, encode_name_part


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
    """Build a ConversationBuildInput, defaulting deployment_id to model_id (the model-backed case).

    Application-backed cases must pass both explicitly, since that is precisely where the two differ.
    """
    defaults = dict(
        model_id=DEPLOYMENT_ID,
        bucket=BUCKET,
        schedule_id=SCHEDULE_ID,
        run_id=RUN_ID,
        display_name=DISPLAY_NAME,
    )
    defaults.update(kwargs)
    defaults.setdefault("deployment_id", defaults["model_id"])
    return ConversationBuildInput(**defaults)


_BUILD_INPUT = make_build_input()


class TestEncodeNamePart:
    """FR-004/FR-007/FR-008: the encoding primitive shared by every name part."""

    @pytest.mark.parametrize(
        "value",
        [
            "gpt-4o",
            "applications/bkt/Untitled app 1101__0.0.1",
            "Привет",
            "Daily Report Sync",
            "a__b/c",
            "100% done",
            "",
            "_leading_and_trailing_",
            "a" * 80,
        ],
    )
    def test_round_trip_is_exact(self, value):
        assert unquote(encode_name_part(value)) == value

    @pytest.mark.parametrize(
        "value",
        ["a__b", "applications/bkt/Untitled app 1101__0.0.1", "_", "__", "x_y"],
    )
    def test_never_emits_the_segment_separator(self, value):
        # Underscore is RFC 3986 unreserved, so quote() alone would leave "__" intact and
        # a deployment id containing it would inject a false separator (research R-002).
        assert "__" not in encode_name_part(value)
        assert "_" not in encode_name_part(value)

    def test_reserved_characters_are_percent_encoded(self):
        encoded = encode_name_part("applications/bkt/my app")
        assert "/" not in encoded
        assert " " not in encoded
        assert encoded == "applications%2Fbkt%2Fmy%20app"

    def test_underscore_encoded_as_percent_5f(self):
        assert encode_name_part("a_b") == "a%5Fb"

    def test_exact_output_for_characters_javascript_encodes_differently(self):
        # Pins the contract's byte-level output. JavaScript's encodeURIComponent leaves !'()*
        # literal, so any consumer mirroring this rule in JS must encode those five explicitly
        # or it will compute a different -- and non-existent -- path.
        assert encode_name_part("a!'()*b~-._") == "a%21%27%28%29%2Ab~-.%5F"


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


_APP_DEPLOYMENT = "applications/bkt/Untitled app 1101__0.0.1"
_APP_REFERENCE = "c9e5decf-1284-421e-ab84-c790beea837d"


def _app_build_input(**kwargs) -> ConversationBuildInput:
    defaults = dict(deployment_id=_APP_DEPLOYMENT, model_id=_APP_REFERENCE)
    defaults.update(kwargs)
    return make_build_input(**defaults)


class TestPathUsesDeploymentIdNotReference:
    """US1: the path identifies the scheduled deployment, never an application's reference."""

    def test_application_path_carries_deployment_id(self):
        conv = build_conversation(make_properties(), _result(), _app_build_input())
        assert unquote(conv.name.split("__")[0]) == _APP_DEPLOYMENT

    def test_application_reference_absent_from_name_and_id(self):
        # FR-003/SC-001: the reference must not leak into the storage location in any form.
        conv = build_conversation(make_properties(), _result(), _app_build_input())
        assert _APP_REFERENCE not in conv.name
        assert _APP_REFERENCE not in conv.id
        assert _APP_REFERENCE not in conv.folderId

    def test_reference_still_recorded_in_document(self):
        # FR-010/SC-007: reopening an application conversation must behave exactly as before.
        conv = build_conversation(make_properties(), _result(), _app_build_input())
        assert conv.model.id == _APP_REFERENCE
        assert all(message.model.id == _APP_REFERENCE for message in conv.messages)

    def test_two_apps_sharing_a_backing_model_are_distinguishable(self):
        conv_a = build_conversation(
            make_properties(), _result(), _app_build_input(deployment_id="applications/bkt/app-a")
        )
        conv_b = build_conversation(
            make_properties(), _result(), _app_build_input(deployment_id="applications/bkt/app-b")
        )
        assert conv_a.model.id == conv_b.model.id == _APP_REFERENCE
        assert conv_a.name != conv_b.name
        assert conv_a.id != conv_b.id

    def test_model_deployment_uses_the_same_rule(self):
        # FR-002: one rule, no branch by deployment kind.
        conv = build_conversation(make_properties(), _result(), make_build_input(model_id="gpt-4o"))
        assert unquote(conv.name.split("__")[0]) == "gpt-4o"
        assert conv.model.id == "gpt-4o"


class TestNameSegmentsSurviveReservedCharacters:
    """US2/US3: adversarial deployment ids and schedule names stay one decodable segment."""

    def test_slashes_do_not_split_the_final_segment(self):
        conv = build_conversation(make_properties(), _result(), _app_build_input())
        assert "/" not in conv.name
        assert conv.id.rsplit("/", 1)[0] == conv.folderId
        assert conv.id.rsplit("/", 1)[1] == conv.name

    @pytest.mark.parametrize(
        ("deployment_id", "display_name"),
        [
            (_APP_DEPLOYMENT, "hello"),
            ("gpt-4o", "a__b/c"),
            ("app__1", "name__2"),
            ("Привет-модель", "Привет"),
            ("gpt-4o", "a" * 80),
            # Spec edge case: empty / whitespace-only name must not make the parts ambiguous.
            ("gpt-4o", ""),
            ("gpt-4o", "   "),
            # Input that already looks percent-encoded must not be double-decoded on the way back.
            ("a%5Fb", "%2F%2E%2E"),
            # Spec edge case: traversal-shaped values stay inert.
            ("..", ".."),
            (".", "."),
        ],
    )
    def test_name_always_splits_into_exactly_three_parts(self, deployment_id, display_name):
        conv = build_conversation(
            make_properties(),
            _result(),
            make_build_input(deployment_id=deployment_id, model_id=deployment_id, display_name=display_name),
        )
        parts = conv.name.split("__")
        assert len(parts) == 3
        assert unquote(parts[0]) == deployment_id
        assert unquote(parts[1]) == display_name
        assert parts[2] == RUN_ID

    @pytest.mark.parametrize(("deployment_id", "display_name"), [("..", ".."), (".", "."), ("", "")])
    def test_final_segment_is_never_a_traversal_token(self, deployment_id, display_name):
        # quote() leaves "." alone, so the segment is only inert because the "__" separators are
        # always present. That invariant is what makes traversal unreachable -- pin it explicitly
        # rather than leaving the safety incidental.
        conv = build_conversation(
            make_properties(),
            _result(),
            make_build_input(deployment_id=deployment_id, model_id="gpt-4o", display_name=display_name),
        )
        leaf = conv.id.rsplit("/", 1)[1]
        assert leaf not in {".", ".."}
        assert CONVERSATION_NAME_SEPARATOR in leaf

    def test_name_matches_final_path_segment_for_adversarial_input(self):
        # FR-009: display name and final location segment must not diverge.
        conv = build_conversation(make_properties(), _result(), _app_build_input(display_name="Daily Report / Sync"))
        assert conv.id.rsplit("/", 1)[1] == conv.name


class TestDisplayNamePreservedNotSlugified:
    """US3: the schedule name reaches the path verbatim, never flattened."""

    def test_case_and_spaces_preserved(self):
        conv = build_conversation(make_properties(), _result(), make_build_input(display_name="Daily Report Sync"))
        name_part = conv.name.split("__")[1]
        assert unquote(name_part) == "Daily Report Sync"
        assert name_part == "Daily%20Report%20Sync"

    def test_names_differing_only_by_case_and_punctuation_do_not_collide(self):
        conv_a = build_conversation(make_properties(), _result(), make_build_input(display_name="Weekly Sync"))
        conv_b = build_conversation(make_properties(), _result(), make_build_input(display_name="weekly-sync"))
        assert conv_a.name != conv_b.name

    def test_non_latin_name_is_not_transliterated(self):
        conv = build_conversation(make_properties(), _result(), make_build_input(display_name="Привет"))
        assert "privet" not in conv.name
        assert unquote(conv.name.split("__")[1]) == "Привет"

    def test_long_name_is_not_truncated(self):
        long_name = "a" * 80
        conv = build_conversation(make_properties(), _result(), make_build_input(display_name=long_name))
        assert unquote(conv.name.split("__")[1]) == long_name


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
    # SC-003 / SC-004: edge display names round-trip exactly instead of being flattened.
    @pytest.mark.parametrize(
        ("display_name", "expected_segment"),
        [
            # Previously transliterated to "privet"; now preserved.
            ("Привет", "%D0%9F%D1%80%D0%B8%D0%B2%D0%B5%D1%82"),
            # Previously capped at 64 characters; now kept in full.
            ("a" * 80, "a" * 80),
        ],
    )
    def test_build_conversation_handles_edge_display_names(self, display_name, expected_segment):
        conv = build_conversation(make_properties(), _result(), make_build_input(display_name=display_name))
        assert conv.name == f"chathub-claude4__{expected_segment}__{RUN_ID}"
        assert unquote(conv.name.split("__")[1]) == display_name
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
        mock_dial.get_application.return_value = ApplicationDetails(reference="c9e5decf-1284-421e-ab84-c790beea837d")

        with structlog.testing.capture_logs():
            await create_run_conversation(make_ctx(), make_creds(), make_properties(payload=_app_payload()), _result())

        # The application lookup is retained: its reference is what the document reopens against.
        mock_dial.get_application.assert_awaited_once()
        assert mock_dial.get_application.call_args.args[1] == _APP_MODEL
        written = mock_dial.put_conversation.call_args.kwargs["conversation"]
        assert written.model.id == "c9e5decf-1284-421e-ab84-c790beea837d"
        # ...but the name's deployment part is the application id itself, encoded -- not the reference.
        assert written.name == f"applications%2Fbkt%2FUntitled%20app%201101%5F%5F0.0.1__{DISPLAY_NAME}__{RUN_ID}"
        assert unquote(written.name.split("__")[0]) == _APP_MODEL
        assert "c9e5decf-1284-421e-ab84-c790beea837d" not in written.name

    async def test_written_path_is_encoded_and_single_segment(self, mock_dial):
        mock_dial.get_application.return_value = ApplicationDetails(reference="c9e5decf-1284-421e-ab84-c790beea837d")

        with structlog.testing.capture_logs():
            await create_run_conversation(make_ctx(), make_creds(), make_properties(payload=_app_payload()), _result())

        path = mock_dial.put_conversation.call_args.kwargs["path"]
        expected_leaf = f"applications%2Fbkt%2FUntitled%20app%201101%5F%5F0.0.1__{DISPLAY_NAME}__{RUN_ID}"
        assert path == f"{BUCKET}/.scheduler/{SCHEDULE_ID}/{expected_leaf}"
        # Four segments exactly: the encoded application id contributed no extra separator.
        assert len(path.split("/")) == 4

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

    @pytest.mark.parametrize("bad_model", [123, ["gpt-4o"], {"id": "gpt-4o"}, ""])
    async def test_non_string_model_raises_named_conversation_error(self, mock_dial, bad_model):
        # payload is dict[str, Any]; the value is path-facing now, so it must fail with a named
        # error rather than an opaque AttributeError/TypeError from startswith()/quote().
        props = make_properties()
        object.__setattr__(props, "payload", {"model": bad_model, "messages": []})

        with structlog.testing.capture_logs():
            with pytest.raises(ConversationCreationError, match="payload.model"):
                await create_run_conversation(make_ctx(), make_creds(), props, _result())

        mock_dial.put_conversation.assert_not_awaited()

    async def test_missing_model_raises_conversation_error(self, mock_dial):
        # Construct properties whose payload lacks a model, bypassing schema validation
        # (validation normally guarantees presence) to exercise the executor's guard.
        props = make_properties()
        object.__setattr__(props, "payload", {"messages": [{"role": "user", "content": "hi"}]})

        with structlog.testing.capture_logs():
            with pytest.raises(ConversationCreationError):
                await create_run_conversation(make_ctx(), make_creds(), props, _result())

        mock_dial.put_conversation.assert_not_awaited()
