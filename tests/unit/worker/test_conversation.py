"""Unit tests for the run-completion conversation builder and orchestration."""

from urllib.parse import unquote

from pydantic import SecretStr
import pytest
import structlog.testing

from src.lib.constants import CONVERSATION_ID_PREFIX, CONVERSATION_NAME_SEPARATOR
from src.lib.db.enums import TargetType
from src.lib.exceptions.errors import ConversationCreationError
from src.lib.schemas.conversation import (
    ApplicationDetails,
    ConversationBuildInput,
    ConversationLocation,
    DialConversation,
)
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.lib.utils.conversation_header import encode_header_value
from src.worker.conversation import (
    build_conversation,
    conversation_id_for,
    conversation_resource_path,
    create_run_conversation,
    deployment_kind,
    encode_name_part,
    resolve_conversation_location,
)


RUN_ID = "run-conv-001"
SCHEDULE_ID = "sched-conv-001"
BUCKET = "bkt-xyz"
DEPLOYMENT_ID = "chathub-claude4"
DISPLAY_NAME = "hello"
MODEL_DEPLOYMENT = "gpt-5.6-sol-2026-07-09-reasoning"


def make_ctx(**kwargs) -> RunContext:
    # Credential resolution records the display name on the context before conversation work runs.
    defaults = dict(run_id=RUN_ID, schedule_id=SCHEDULE_ID, schedule_display_name=DISPLAY_NAME)
    defaults.update(kwargs)
    return RunContext(**defaults)


def make_location(**kwargs) -> ConversationLocation:
    defaults = dict(
        deployment_id=MODEL_DEPLOYMENT,
        bucket=BUCKET,
        schedule_id=SCHEDULE_ID,
        run_id=RUN_ID,
        display_name=DISPLAY_NAME,
    )
    defaults.update(kwargs)
    return ConversationLocation(**defaults)


def make_located_ctx(**location_overrides) -> RunContext:
    """A run context whose conversation location was resolved before the target request."""
    return make_ctx(conversation_location=make_location(**location_overrides))


def make_creds(**kwargs) -> OboCredentials:
    defaults = dict(header_name="Api-Key", header_value=SecretStr("owner-token"))
    defaults.update(kwargs)
    return OboCredentials(**defaults)


def make_properties(**kwargs) -> ScheduleProperties:
    defaults = dict(
        target_type=TargetType.CHAT_COMPLETION,
        url="https://dial.example.com/openai/",
        payload={"model": MODEL_DEPLOYMENT, "messages": [{"role": "user", "content": "hi"}]},
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


def leaf(conv: DialConversation) -> str:
    """Return the conversation's final storage path segment (the three-part path leaf)."""
    return conv.id.rsplit("/", 1)[1]


class TestEncodeNamePart:
    """FR-007: the name-part primitive keeps names raw but path-safe."""

    @pytest.mark.parametrize(
        "value",
        [
            "gpt-4o",
            "Untitled app 1101__0.0.1",
            "Привет",
            "Daily Report Sync",
            "a__b/c",
            "100% done",
            "",
            "_leading_and_trailing_",
            "hi! how are you?",
            "a" * 80,
        ],
    )
    def test_round_trip_is_exact(self, value):
        assert unquote(encode_name_part(value)) == value

    @pytest.mark.parametrize(
        "value",
        ["Daily Report Sync", "hi! how are you?", "Привет", "a_b", "100%"],
    )
    def test_never_emits_a_path_separator(self, value):
        # A name part must occupy exactly one storage path segment.
        assert "/" not in encode_name_part(value)
        assert "\\" not in encode_name_part(value)

    def test_characters_dial_stores_literally_are_left_alone(self):
        # DIAL Chat stores "Planning Assistant__0.0.1__hi! how are you?__{uuid}" raw and escapes
        # only in the request URL; spaces, punctuation and underscores must survive untouched.
        assert encode_name_part("Planning Assistant__0.0.1") == "Planning Assistant__0.0.1"
        assert encode_name_part("hi! how are you?") == "hi! how are you?"
        assert encode_name_part("Привет") == "Привет"

    def test_only_separators_are_escaped(self):
        assert encode_name_part("a/b") == "a%2Fb"
        assert encode_name_part("a\\b") == "a%5Cb"

    def test_literal_percent_escaped_first_so_the_round_trip_holds(self):
        # Without escaping "%" first, the input "%2F" would decode back to "/".
        assert encode_name_part("%2F") == "%252F"
        assert unquote(encode_name_part("%2F")) == "%2F"


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

    def test_name_is_the_schedule_display_name(self):
        # The document title is what the user reads in DIAL Chat: the display name, nothing else.
        conv = build_conversation(make_properties(), _result(), _BUILD_INPUT)
        assert conv.name == DISPLAY_NAME

    def test_path_leaf_keeps_the_three_part_convention(self):
        conv = build_conversation(make_properties(), _result(), _BUILD_INPUT)
        assert leaf(conv) == f"chathub-claude4__hello__{RUN_ID}"

    def test_name_and_path_leaf_are_independent(self):
        # The title carries no deployment or run id; only the address does.
        conv = build_conversation(make_properties(), _result(), _BUILD_INPUT)
        assert conv.name != leaf(conv)
        assert RUN_ID not in conv.name
        assert DEPLOYMENT_ID not in conv.name

    def test_folder_is_stable_and_messages_preserved(self):
        # The folder never includes the name; only the leaf segment carries it.
        conv = build_conversation(make_properties(), _result(), _BUILD_INPUT)
        assert conv.folderId == f"conversations/{BUCKET}/.scheduler/{SCHEDULE_ID}"
        assert len(conv.messages) == 2
        assert conv.messages[0].content == "hi"
        assert conv.messages[1].content == "Hi! How can I help?"

    def test_different_run_ids_produce_different_paths(self):
        # SC-003: uniqueness guaranteed by the run id segment of the path leaf.
        conv_a = build_conversation(make_properties(), _result(), make_build_input(run_id="run-a"))
        conv_b = build_conversation(make_properties(), _result(), make_build_input(run_id="run-b"))
        assert leaf(conv_a) != leaf(conv_b)
        assert conv_a.id != conv_b.id
        # Runs of one schedule intentionally share a title.
        assert conv_a.name == conv_b.name == DISPLAY_NAME


_APP_DEPLOYMENT = "applications/bkt/Untitled app 1101__0.0.1"
_APP_REFERENCE = "c9e5decf-1284-421e-ab84-c790beea837d"


def _app_build_input(**kwargs) -> ConversationBuildInput:
    defaults = dict(deployment_id=_APP_DEPLOYMENT, model_id=_APP_REFERENCE)
    defaults.update(kwargs)
    return make_build_input(**defaults)


class TestPathUsesDeploymentIdNotReference:
    """US1: the path identifies the scheduled deployment, never an application's reference."""

    def test_application_path_carries_deployment_id(self):
        # The id's folder segments nest under the schedule folder and its final segment opens the
        # name -- the whole deployment id is still recoverable from the location, as in DIAL Chat.
        conv = build_conversation(make_properties(), _result(), _app_build_input())
        assert conv.folderId == f"conversations/{BUCKET}/.scheduler/{SCHEDULE_ID}/applications/bkt"
        assert leaf(conv) == f"Untitled app 1101__0.0.1__{DISPLAY_NAME}__{RUN_ID}"

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
        assert leaf(conv_a) != leaf(conv_b)
        assert conv_a.id != conv_b.id

    def test_model_deployment_uses_the_same_rule(self):
        # FR-002: one rule, no branch by deployment kind. A model id has no separator, so it
        # contributes no folder and opens the path leaf by itself.
        conv = build_conversation(make_properties(), _result(), make_build_input(model_id="gpt-4o"))
        assert conv.folderId == f"conversations/{BUCKET}/.scheduler/{SCHEDULE_ID}"
        assert leaf(conv) == f"gpt-4o__{DISPLAY_NAME}__{RUN_ID}"
        assert conv.model.id == "gpt-4o"


class TestNameSegmentsSurviveReservedCharacters:
    """US2/US3: adversarial deployment ids and schedule names stay one decodable segment."""

    def test_slashes_do_not_split_the_final_segment(self):
        conv = build_conversation(make_properties(), _result(), _app_build_input())
        assert "/" not in leaf(conv)
        assert conv.id.rsplit("/", 1)[0] == conv.folderId

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
    def test_leaf_is_always_one_decodable_segment(self, deployment_id, display_name):
        conv = build_conversation(
            make_properties(),
            _result(),
            make_build_input(deployment_id=deployment_id, model_id=deployment_id, display_name=display_name),
        )
        # The deployment id's own folders are path segments; only its final part joins the leaf.
        _, _, deployment_name = deployment_id.rpartition("/")
        assert "/" not in leaf(conv)
        assert unquote(leaf(conv)) == CONVERSATION_NAME_SEPARATOR.join((deployment_name, display_name, RUN_ID))
        # The title is never escaped -- it is a document field, not a path segment.
        assert conv.name == display_name

    @pytest.mark.parametrize(("deployment_id", "display_name"), [("..", ".."), (".", "."), ("", "")])
    def test_final_segment_is_never_a_traversal_token(self, deployment_id, display_name):
        # Name parts are stored raw, so "." survives verbatim: the segment is inert only because
        # the "__" separators are always present. Pin that invariant rather than leaving the
        # safety incidental.
        conv = build_conversation(
            make_properties(),
            _result(),
            make_build_input(deployment_id=deployment_id, model_id="gpt-4o", display_name=display_name),
        )
        leaf = conv.id.rsplit("/", 1)[1]
        assert leaf not in {".", ".."}
        assert CONVERSATION_NAME_SEPARATOR in leaf

    def test_display_name_reaches_the_path_leaf_escaped_and_the_title_raw(self):
        # FR-009: a display name containing a separator must not split the location segment, while
        # the title keeps the character literally.
        conv = build_conversation(make_properties(), _result(), _app_build_input(display_name="Daily Report / Sync"))
        assert "/" not in leaf(conv)
        assert unquote(leaf(conv).split("__")[-2]) == "Daily Report / Sync"
        assert conv.name == "Daily Report / Sync"


class TestDisplayNamePreservedNotSlugified:
    """US3: the schedule name reaches the title and the path verbatim, never flattened."""

    def test_case_and_spaces_preserved(self):
        conv = build_conversation(make_properties(), _result(), make_build_input(display_name="Daily Report Sync"))
        assert conv.name == "Daily Report Sync"
        assert leaf(conv).split("__")[1] == "Daily Report Sync"

    def test_names_differing_only_by_case_and_punctuation_do_not_collide(self):
        conv_a = build_conversation(make_properties(), _result(), make_build_input(display_name="Weekly Sync"))
        conv_b = build_conversation(make_properties(), _result(), make_build_input(display_name="weekly-sync"))
        assert conv_a.name != conv_b.name
        assert leaf(conv_a) != leaf(conv_b)

    def test_non_latin_name_is_not_transliterated(self):
        conv = build_conversation(make_properties(), _result(), make_build_input(display_name="Привет"))
        assert conv.name == "Привет"
        assert unquote(leaf(conv).split("__")[1]) == "Привет"

    def test_long_name_is_not_truncated(self):
        long_name = "a" * 80
        conv = build_conversation(make_properties(), _result(), make_build_input(display_name=long_name))
        assert conv.name == long_name
        assert unquote(leaf(conv).split("__")[1]) == long_name


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
        ),
    )
    return dial_client


_APP_MODEL = "applications/bkt/Untitled app 1101__0.0.1"


def _app_payload() -> dict:
    return {"model": _APP_MODEL, "messages": [{"role": "user", "content": "hi"}]}


class TestResolveConversationLocation:
    """The location is resolved once, before the target request, without touching the conversation."""

    async def test_returns_the_full_location(self, mock_dial):
        location = await resolve_conversation_location(make_ctx(), make_creds(), make_properties())

        assert location == make_location()

    async def test_percent_encoded_application_id_is_decoded(self, mock_dial):
        # DIAL hands out application ids in URL form; everything downstream works on raw names.
        props = make_properties(payload={"model": f"applications/{BUCKET}/Planning%20Assistant__0.0.1", "messages": []})

        location = await resolve_conversation_location(make_ctx(), make_creds(), props)

        assert location.deployment_id == f"applications/{BUCKET}/Planning Assistant__0.0.1"

    async def test_neither_looks_up_the_application_nor_writes(self, mock_dial):
        await resolve_conversation_location(make_ctx(), make_creds(), make_properties(payload=_app_payload()))

        mock_dial.get_application.assert_not_awaited()
        mock_dial.put_conversation.assert_not_awaited()

    async def test_bucket_is_fetched_once_with_the_owner_credentials(self, mock_dial):
        creds = make_creds(header_name="Authorization", header_value=SecretStr("Bearer owner-token"))

        await resolve_conversation_location(make_ctx(), creds, make_properties())

        mock_dial.get_bucket.assert_awaited_once()
        assert mock_dial.get_bucket.call_args.args[0] is creds

    async def test_display_name_comes_from_the_run_context(self, mock_dial):
        location = await resolve_conversation_location(
            make_ctx(schedule_display_name="Щоденний звіт"), make_creds(), make_properties()
        )

        assert location.display_name == "Щоденний звіт"

    def test_does_not_read_the_schedule_again(self):
        # Credential resolution already loaded the row; a second read could disagree with it.
        import src.worker.conversation as conversation_module

        assert not hasattr(conversation_module, "ScheduleRepository")
        assert not hasattr(conversation_module, "Database")

    async def test_bucket_failure_raises(self, mock_dial):
        mock_dial.get_bucket.side_effect = ConversationCreationError("bucket request failed with status 503")

        with pytest.raises(ConversationCreationError, match="bucket"):
            await resolve_conversation_location(make_ctx(), make_creds(), make_properties())

    async def test_display_name_not_recorded_raises_before_the_bucket_lookup(self, mock_dial):
        with pytest.raises(ConversationCreationError, match=f"schedule display name not loaded: {SCHEDULE_ID}"):
            await resolve_conversation_location(make_ctx(schedule_display_name=None), make_creds(), make_properties())

        mock_dial.get_bucket.assert_not_awaited()

    @pytest.mark.parametrize("bad_model", [123, ["gpt-4o"], {"id": "gpt-4o"}, ""])
    async def test_non_string_model_raises_named_conversation_error(self, mock_dial, bad_model):
        # payload is dict[str, Any]; the value is path-facing, so it must fail with a named
        # error rather than an opaque AttributeError/TypeError from startswith()/unquote().
        props = make_properties()
        object.__setattr__(props, "payload", {"model": bad_model, "messages": []})

        with pytest.raises(ConversationCreationError, match="payload.model"):
            await resolve_conversation_location(make_ctx(), make_creds(), props)

    async def test_missing_model_raises_conversation_error(self, mock_dial):
        # Bypass schema validation (which normally guarantees presence) to exercise the guard.
        props = make_properties()
        object.__setattr__(props, "payload", {"messages": [{"role": "user", "content": "hi"}]})

        with pytest.raises(ConversationCreationError, match="payload.model"):
            await resolve_conversation_location(make_ctx(), make_creds(), props)


class TestDeploymentKind:
    @pytest.mark.parametrize(
        ("deployment_id", "expected"), [(_APP_MODEL, "application"), (MODEL_DEPLOYMENT, "model"), ("apps/x", "model")]
    )
    def test_classifies_by_prefix(self, deployment_id, expected):
        assert deployment_kind(deployment_id) == expected


class TestConversationIdFor:
    def test_is_the_prefixed_resource_path(self):
        location = make_location()
        assert conversation_id_for(location) == f"{CONVERSATION_ID_PREFIX}{conversation_resource_path(location)}"

    @pytest.mark.parametrize(
        ("deployment_id", "display_name"),
        [(MODEL_DEPLOYMENT, DISPLAY_NAME), (_APP_MODEL, "Daily / Report"), ("gpt-4o", "Щоденний звіт 📈")],
    )
    def test_equals_the_built_document_id(self, deployment_id, display_name):
        location = make_location(deployment_id=deployment_id, display_name=display_name)
        build_input = ConversationBuildInput(**location.model_dump(), model_id="ref")

        conv = build_conversation(make_properties(), _result(), build_input)

        assert conversation_id_for(location) == conv.id


class TestCreateRunConversationOrchestration:
    async def test_reuses_the_resolved_location_without_lookups(self, mock_dial):
        with structlog.testing.capture_logs():
            await create_run_conversation(make_located_ctx(), make_creds(), make_properties(), _result())

        mock_dial.get_bucket.assert_not_awaited()
        mock_dial.put_conversation.assert_awaited_once()
        written = mock_dial.put_conversation.call_args.kwargs["conversation"]
        assert f"/{BUCKET}/" in written.id

    async def test_returns_the_id_derived_from_the_location(self, mock_dial):
        ctx = make_located_ctx()
        with structlog.testing.capture_logs():
            result = await create_run_conversation(ctx, make_creds(), make_properties(), _result())

        assert result == conversation_id_for(ctx.conversation_location)
        assert result.startswith("conversations/")

    async def test_returned_id_matches_put_path(self, mock_dial):
        """The returned id must be the same id used for the PUT (no drift)."""
        with structlog.testing.capture_logs():
            returned_id = await create_run_conversation(make_located_ctx(), make_creds(), make_properties(), _result())

        put_path = mock_dial.put_conversation.call_args.kwargs["path"]
        assert returned_id == f"{CONVERSATION_ID_PREFIX}{put_path}"

    async def test_returned_id_contains_run_id(self, mock_dial):
        """The returned id embeds the run_id so each run gets a unique path."""
        with structlog.testing.capture_logs():
            result = await create_run_conversation(make_located_ctx(), make_creds(), make_properties(), _result())

        assert RUN_ID in result

    async def test_model_deployment_skips_get_application(self, mock_dial):
        with structlog.testing.capture_logs():
            await create_run_conversation(make_located_ctx(), make_creds(), make_properties(), _result())

        mock_dial.get_application.assert_not_awaited()
        written = mock_dial.put_conversation.call_args.kwargs["conversation"]
        assert written.model.id == MODEL_DEPLOYMENT

    async def test_user_credential_passed_to_put(self, mock_dial):
        creds = make_creds(header_name="Authorization", header_value=SecretStr("Bearer owner-token"))
        with structlog.testing.capture_logs():
            await create_run_conversation(make_located_ctx(), creds, make_properties(), _result())

        assert mock_dial.put_conversation.call_args.kwargs["credentials"] is creds

    async def test_secret_not_logged(self, mock_dial):
        creds = make_creds(header_value=SecretStr("super-secret"))
        with structlog.testing.capture_logs() as logs:
            await create_run_conversation(make_located_ctx(), creds, make_properties(), _result())

        for log in logs:
            assert "super-secret" not in str(log)

    async def test_name_and_leaf_come_from_the_location(self, mock_dial):
        with structlog.testing.capture_logs():
            await create_run_conversation(
                make_located_ctx(display_name="Weekly"), make_creds(), make_properties(), _result()
            )

        written = mock_dial.put_conversation.call_args.kwargs["conversation"]
        assert written.name == "Weekly"
        assert leaf(written) == f"{MODEL_DEPLOYMENT}__Weekly__{RUN_ID}"

    async def test_unresolved_location_raises_without_writing(self, mock_dial):
        with structlog.testing.capture_logs():
            with pytest.raises(ConversationCreationError, match="conversation location not resolved"):
                await create_run_conversation(make_ctx(), make_creds(), make_properties(), _result())

        mock_dial.put_conversation.assert_not_awaited()

    async def test_put_failure_raises(self, mock_dial):
        mock_dial.put_conversation.side_effect = ConversationCreationError("put failed")

        with structlog.testing.capture_logs():
            with pytest.raises(ConversationCreationError):
                await create_run_conversation(make_located_ctx(), make_creds(), make_properties(), _result())


class TestConversationNameEdgeInputs:
    # SC-003 / SC-004: edge display names round-trip exactly instead of being flattened.
    @pytest.mark.parametrize(
        ("display_name", "expected_segment"),
        [
            # Previously transliterated to "privet"; now preserved.
            ("Привет", "Привет"),
            # Previously capped at 64 characters; now kept in full.
            ("a" * 80, "a" * 80),
        ],
    )
    def test_build_conversation_handles_edge_display_names(self, display_name, expected_segment):
        conv = build_conversation(make_properties(), _result(), make_build_input(display_name=display_name))
        assert conv.name == display_name
        assert leaf(conv) == f"chathub-claude4__{expected_segment}__{RUN_ID}"
        # The run id segment always survives verbatim.
        assert leaf(conv).rsplit("__", 1)[1] == RUN_ID

    @pytest.mark.parametrize("display_name", ["Привет", "a" * 80])
    async def test_create_run_conversation_never_raises_on_edge_names(self, mock_dial, display_name):
        with structlog.testing.capture_logs():
            await create_run_conversation(
                make_located_ctx(display_name=display_name), make_creds(), make_properties(), _result()
            )
        mock_dial.put_conversation.assert_awaited_once()


class TestCreateRunConversationApplication:
    async def test_application_deployment_resolves_reference(self, mock_dial):
        mock_dial.get_application.return_value = ApplicationDetails(reference="c9e5decf-1284-421e-ab84-c790beea837d")

        with structlog.testing.capture_logs():
            await create_run_conversation(
                make_located_ctx(deployment_id=_APP_MODEL),
                make_creds(),
                make_properties(payload=_app_payload()),
                _result(),
            )

        # The application lookup is retained post-run: its reference is what the document reopens against.
        mock_dial.get_application.assert_awaited_once()
        assert mock_dial.get_application.call_args.args[1] == _APP_MODEL
        written = mock_dial.put_conversation.call_args.kwargs["conversation"]
        assert written.model.id == "c9e5decf-1284-421e-ab84-c790beea837d"
        assert written.name == DISPLAY_NAME
        # ...but the leaf's deployment part is the application's own name -- not the reference.
        assert leaf(written) == f"Untitled app 1101__0.0.1__{DISPLAY_NAME}__{RUN_ID}"
        assert "c9e5decf-1284-421e-ab84-c790beea837d" not in written.name
        assert "c9e5decf-1284-421e-ab84-c790beea837d" not in written.id

    async def test_written_path_nests_the_application_folders(self, mock_dial):
        mock_dial.get_application.return_value = ApplicationDetails(reference="c9e5decf-1284-421e-ab84-c790beea837d")

        with structlog.testing.capture_logs():
            await create_run_conversation(
                make_located_ctx(deployment_id=_APP_MODEL),
                make_creds(),
                make_properties(payload=_app_payload()),
                _result(),
            )

        path = mock_dial.put_conversation.call_args.kwargs["path"]
        expected_leaf = f"Untitled app 1101__0.0.1__{DISPLAY_NAME}__{RUN_ID}"
        # Mirrors DIAL Chat: the application id's folders are real segments, never a %2F blob.
        assert path == f"{BUCKET}/.scheduler/{SCHEDULE_ID}/applications/bkt/{expected_leaf}"
        assert "%2F" not in path

    async def test_application_reference_failure_raises(self, mock_dial):
        mock_dial.get_application.side_effect = ConversationCreationError("no reference")

        with structlog.testing.capture_logs():
            with pytest.raises(ConversationCreationError):
                await create_run_conversation(
                    make_located_ctx(deployment_id=_APP_MODEL),
                    make_creds(),
                    make_properties(payload=_app_payload()),
                    _result(),
                )

        mock_dial.put_conversation.assert_not_awaited()


class TestCreateRunConversationPercentEncodedDeployment:
    """DIAL stores ``payload.model`` in its URL form, so the id arrives already percent-encoded."""

    # Verbatim from a real schedule whose runs logged "application details request failed with
    # status 404": the space in the application name reaches the scheduler as %20.
    _ENCODED_APP_MODEL = "applications/bkt/Planning%20Assistant__0.0.1"

    def _encoded_properties(self) -> ScheduleProperties:
        return make_properties(
            payload={"model": self._ENCODED_APP_MODEL, "messages": [{"role": "user", "content": "hi"}]}
        )

    async def _resolve_then_create(self) -> None:
        props = self._encoded_properties()
        ctx = make_ctx()
        ctx.conversation_location = await resolve_conversation_location(ctx, make_creds(), props)
        with structlog.testing.capture_logs():
            await create_run_conversation(ctx, make_creds(), props, _result())

    async def test_application_is_looked_up_by_its_decoded_id(self, mock_dial):
        mock_dial.get_application.return_value = ApplicationDetails(reference="c9e5decf-1284-421e-ab84-c790beea837d")

        await self._resolve_then_create()

        # DialClient escapes the id itself; handing it the encoded form would ask DIAL for
        # "Planning%2520Assistant__0.0.1" and 404.
        assert mock_dial.get_application.call_args.args[1] == "applications/bkt/Planning Assistant__0.0.1"

    async def test_name_and_path_carry_the_decoded_application_name(self, mock_dial):
        mock_dial.get_application.return_value = ApplicationDetails(reference="c9e5decf-1284-421e-ab84-c790beea837d")

        await self._resolve_then_create()

        written = mock_dial.put_conversation.call_args.kwargs["conversation"]
        path = mock_dial.put_conversation.call_args.kwargs["path"]
        expected_leaf = f"Planning Assistant__0.0.1__{DISPLAY_NAME}__{RUN_ID}"
        assert written.name == DISPLAY_NAME
        assert leaf(written) == expected_leaf
        assert path == f"{BUCKET}/.scheduler/{SCHEDULE_ID}/applications/bkt/{expected_leaf}"
        # The escape must not survive into the stored path -- it would display literally in DIAL Chat.
        assert "%20" not in written.id
        assert "%25" not in written.id

    async def test_model_deployment_id_is_unaffected(self, mock_dial):
        with structlog.testing.capture_logs():
            await create_run_conversation(make_located_ctx(), make_creds(), make_properties(), _result())

        written = mock_dial.put_conversation.call_args.kwargs["conversation"]
        assert written.model.id == MODEL_DEPLOYMENT
        assert leaf(written).split("__")[0] == MODEL_DEPLOYMENT


class TestCreateRunConversationModelResolution:
    async def test_model_taken_from_location(self, mock_dial):
        with structlog.testing.capture_logs():
            await create_run_conversation(
                make_located_ctx(deployment_id="gpt-4o"),
                make_creds(),
                make_properties(payload={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}),
                _result(),
            )

        mock_dial.get_application.assert_not_awaited()
        written = mock_dial.put_conversation.call_args.kwargs["conversation"]
        assert written.model.id == "gpt-4o"


class TestHeaderEncodingOfStoredId:
    """US3: name-part escapes in the stored id are sent literally, never double-encoded."""

    def test_literal_percent_and_separators_are_not_double_encoded(self):
        location = make_location(display_name="50% done/Q1\\x")
        build_input = ConversationBuildInput(**location.model_dump(), model_id=MODEL_DEPLOYMENT)

        stored_id = build_conversation(make_properties(), _result(), build_input).id

        assert "50%25 done%2FQ1%5Cx" in stored_id
        assert encode_header_value(conversation_id_for(location)) == stored_id
