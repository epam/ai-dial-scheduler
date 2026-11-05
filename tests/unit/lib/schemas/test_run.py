"""Unit tests for run schemas — conversation_id field (feature 036)."""

from datetime import UTC, datetime
import uuid

from src.lib.db.enums import ScheduleRunStatus


class TestRunListItemConversationId:
    """RunListItem serialises conversation_id correctly (data-model §6 cases 3, 4, 5)."""

    def test_serialises_set_conversation_id(self):
        from src.lib.schemas.run import RunListItem

        item = RunListItem(
            id=uuid.uuid4(),
            status=ScheduleRunStatus.SUCCESS,
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC),
            conversation_id="conversations/bkt/.scheduler/sched-1/gpt-4o__hello__run-1",
        )
        assert item.conversation_id == "conversations/bkt/.scheduler/sched-1/gpt-4o__hello__run-1"

    def test_serialises_none_when_not_set(self):
        from src.lib.schemas.run import RunListItem

        item = RunListItem(
            id=uuid.uuid4(),
            status=ScheduleRunStatus.SUCCESS,
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC),
        )
        assert item.conversation_id is None

    def test_serialises_none_when_explicitly_none(self):
        from src.lib.schemas.run import RunListItem

        item = RunListItem(
            id=uuid.uuid4(),
            status=ScheduleRunStatus.SUCCESS,
            start_time=datetime.now(UTC),
            end_time=None,
            conversation_id=None,
        )
        assert item.conversation_id is None

    def test_no_validation_error_when_conversation_id_missing(self):
        """Backward compatibility: constructing without conversation_id is valid."""
        from src.lib.schemas.run import RunListItem

        item = RunListItem(
            id=uuid.uuid4(),
            status=ScheduleRunStatus.SUCCESS,
            start_time=datetime.now(UTC),
            end_time=None,
        )
        assert item.conversation_id is None

    def test_model_dump_includes_conversation_id_field(self):
        from src.lib.schemas.run import RunListItem

        item = RunListItem(
            id=uuid.uuid4(),
            status=ScheduleRunStatus.SUCCESS,
            start_time=datetime.now(UTC),
            end_time=None,
            conversation_id="conversations/bkt/.scheduler/sched/model__name__run",
        )
        dumped = item.model_dump()
        assert "conversation_id" in dumped
        assert dumped["conversation_id"] == "conversations/bkt/.scheduler/sched/model__name__run"

    def test_model_dump_conversation_id_is_none_when_not_set(self):
        from src.lib.schemas.run import RunListItem

        item = RunListItem(
            id=uuid.uuid4(),
            status=ScheduleRunStatus.SUCCESS,
            start_time=datetime.now(UTC),
            end_time=None,
        )
        dumped = item.model_dump()
        assert "conversation_id" in dumped
        assert dumped["conversation_id"] is None


class TestRunResponseConversationId:
    """RunResponse serialises conversation_id correctly (data-model §6 cases 3, 4, 5)."""

    def _schedule(self):
        from src.lib.schemas.run import RunScheduleSummary

        return RunScheduleSummary(
            display_name="Test",
            trigger={"cron": {"fields": {"hour": 9}}},
            properties={"target_type": "chat_completion"},
        )

    def test_serialises_set_conversation_id(self):
        from src.lib.schemas.run import RunResponse

        resp = RunResponse(
            id=uuid.uuid4(),
            status=ScheduleRunStatus.SUCCESS,
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC),
            result={"status_code": 200},
            conversation_id="conversations/bkt/.scheduler/sched-1/gpt-4o__hello__run-1",
            schedule=self._schedule(),
        )
        assert resp.conversation_id == "conversations/bkt/.scheduler/sched-1/gpt-4o__hello__run-1"

    def test_serialises_none_when_not_set(self):
        from src.lib.schemas.run import RunResponse

        resp = RunResponse(
            id=uuid.uuid4(),
            status=ScheduleRunStatus.SUCCESS,
            start_time=datetime.now(UTC),
            end_time=None,
            result=None,
            schedule=self._schedule(),
        )
        assert resp.conversation_id is None

    def test_no_validation_error_when_conversation_id_missing(self):
        """Backward compatibility: constructing without conversation_id is valid."""
        from src.lib.schemas.run import RunResponse

        resp = RunResponse(
            id=uuid.uuid4(),
            status=ScheduleRunStatus.SUCCESS,
            start_time=datetime.now(UTC),
            end_time=None,
            result=None,
            schedule=self._schedule(),
        )
        assert resp.conversation_id is None

    def test_model_dump_includes_conversation_id_field(self):
        from src.lib.schemas.run import RunResponse

        resp = RunResponse(
            id=uuid.uuid4(),
            status=ScheduleRunStatus.SUCCESS,
            start_time=datetime.now(UTC),
            end_time=None,
            result=None,
            conversation_id="conversations/bkt/.scheduler/sched/model__name__run",
            schedule=self._schedule(),
        )
        dumped = resp.model_dump()
        assert "conversation_id" in dumped
        assert dumped["conversation_id"] == "conversations/bkt/.scheduler/sched/model__name__run"

    def test_model_dump_conversation_id_is_none_when_not_set(self):
        from src.lib.schemas.run import RunResponse

        resp = RunResponse(
            id=uuid.uuid4(),
            status=ScheduleRunStatus.SUCCESS,
            start_time=datetime.now(UTC),
            end_time=None,
            result=None,
            schedule=self._schedule(),
        )
        dumped = resp.model_dump()
        assert "conversation_id" in dumped
        assert dumped["conversation_id"] is None
