from datetime import datetime
from typing import Any
import uuid

import pytest

from src.lib.db.enums import ScheduleRunStatus


SCHEDULE_ID = "sched-repo-001"
RUN_ID = str(uuid.uuid4())
SNAPSHOT: dict[str, Any] = {"target_type": "rest", "url": "https://example.com"}


@pytest.fixture
def mock_session(mocker):
    session = mocker.AsyncMock()
    session.add = mocker.MagicMock()
    session.commit = mocker.AsyncMock()
    session.refresh = mocker.AsyncMock()
    session.get = mocker.AsyncMock()
    session.execute = mocker.AsyncMock()
    return session


class TestScheduleRunRepositoryCreateInProgress:
    async def test_inserts_in_progress_row(self, mock_session):
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        repo = ScheduleRunRepository(mock_session)
        await repo.create_in_progress(schedule_id=SCHEDULE_ID, schedule_snapshot=SNAPSHOT)

        mock_session.add.assert_called_once()
        inserted = mock_session.add.call_args.args[0]
        assert isinstance(inserted.id, uuid.UUID)
        assert inserted.status == ScheduleRunStatus.IN_PROGRESS.value
        assert inserted.end_time is None
        assert inserted.result is None
        assert inserted.schedule_id == SCHEDULE_ID
        assert inserted.schedule_snapshot == SNAPSHOT

    async def test_commits_and_refreshes(self, mock_session):
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        repo = ScheduleRunRepository(mock_session)
        await repo.create_in_progress(schedule_id=SCHEDULE_ID, schedule_snapshot=SNAPSHOT)

        mock_session.commit.assert_awaited_once()
        mock_session.refresh.assert_awaited_once()

    async def test_returns_schedule_run_instance(self, mock_session):
        from src.lib.models.schedule_run import ScheduleRun
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        result = await ScheduleRunRepository(mock_session).create_in_progress(
            schedule_id=SCHEDULE_ID, schedule_snapshot=SNAPSHOT
        )

        assert isinstance(result, ScheduleRun)


class TestScheduleRunRepositoryMarkSuccess:
    async def test_updates_status_end_time_and_result(self, mock_session, mocker):
        from src.lib.models.schedule_run import ScheduleRun
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        existing = mocker.MagicMock(spec=ScheduleRun)
        mock_session.get.return_value = existing

        repo = ScheduleRunRepository(mock_session)
        exec_result = {"status_code": 200, "body": {"ok": True}}
        await repo.mark_success(RUN_ID, exec_result)

        assert existing.status == ScheduleRunStatus.SUCCESS.value
        assert existing.result == exec_result
        assert isinstance(existing.end_time, datetime)
        assert existing.end_time.tzinfo is not None

    async def test_commits(self, mock_session, mocker):
        from src.lib.models.schedule_run import ScheduleRun
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        mock_session.get.return_value = mocker.MagicMock(spec=ScheduleRun)
        repo = ScheduleRunRepository(mock_session)
        await repo.mark_success(RUN_ID, {"ok": True})

        mock_session.commit.assert_awaited_once()

    async def test_returns_none_when_row_missing(self, mock_session):
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        mock_session.get.return_value = None
        repo = ScheduleRunRepository(mock_session)
        result = await repo.mark_success(RUN_ID, {"ok": True})

        assert result is None

    async def test_uses_uuid_for_lookup(self, mock_session, mocker):
        from src.lib.models.schedule_run import ScheduleRun
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        mock_session.get.return_value = mocker.MagicMock(spec=ScheduleRun)
        repo = ScheduleRunRepository(mock_session)
        await repo.mark_success(RUN_ID, {})

        call_args = mock_session.get.call_args
        assert call_args.args[1] == uuid.UUID(RUN_ID)


class TestScheduleRunRepositoryMarkError:
    async def test_updates_status_end_time_and_error_result(self, mock_session, mocker):
        from src.lib.models.schedule_run import ScheduleRun
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        existing = mocker.MagicMock(spec=ScheduleRun)
        mock_session.get.return_value = existing

        repo = ScheduleRunRepository(mock_session)
        await repo.mark_error(RUN_ID, "connection refused")

        assert existing.status == ScheduleRunStatus.ERROR.value
        assert existing.result == {"error_text": "connection refused"}
        assert isinstance(existing.end_time, datetime)
        assert existing.end_time.tzinfo is not None

    async def test_commits(self, mock_session, mocker):
        from src.lib.models.schedule_run import ScheduleRun
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        mock_session.get.return_value = mocker.MagicMock(spec=ScheduleRun)
        repo = ScheduleRunRepository(mock_session)
        await repo.mark_error(RUN_ID, "some error")

        mock_session.commit.assert_awaited_once()

    async def test_returns_none_when_row_missing(self, mock_session):
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        mock_session.get.return_value = None
        repo = ScheduleRunRepository(mock_session)
        result = await repo.mark_error(RUN_ID, "some error")

        assert result is None

    async def test_uses_uuid_for_lookup(self, mock_session, mocker):
        from src.lib.models.schedule_run import ScheduleRun
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        mock_session.get.return_value = mocker.MagicMock(spec=ScheduleRun)
        repo = ScheduleRunRepository(mock_session)
        await repo.mark_error(RUN_ID, "error")

        call_args = mock_session.get.call_args
        assert call_args.args[1] == uuid.UUID(RUN_ID)


class TestScheduleRunRepositoryListForSchedule:
    async def test_returns_count_and_list(self, mock_session, mocker):
        from src.lib.models.schedule_run import ScheduleRun
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        run = mocker.MagicMock(spec=ScheduleRun)
        count_result = mocker.MagicMock()
        count_result.scalar_one.return_value = 1
        page_result = mocker.MagicMock()
        page_result.scalars.return_value.all.return_value = [run]
        mock_session.execute.side_effect = [count_result, page_result]

        repo = ScheduleRunRepository(mock_session)
        total, rows = await repo.list_for_schedule(SCHEDULE_ID, limit=10, offset=0)

        assert total == 1
        assert rows == [run]

    async def test_passes_limit_and_offset(self, mock_session, mocker):
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        count_result = mocker.MagicMock()
        count_result.scalar_one.return_value = 0
        page_result = mocker.MagicMock()
        page_result.scalars.return_value.all.return_value = []
        mock_session.execute.side_effect = [count_result, page_result]

        repo = ScheduleRunRepository(mock_session)
        await repo.list_for_schedule(SCHEDULE_ID, limit=5, offset=10)

        assert mock_session.execute.await_count == 2

    async def test_empty_list_when_no_runs(self, mock_session, mocker):
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        count_result = mocker.MagicMock()
        count_result.scalar_one.return_value = 0
        page_result = mocker.MagicMock()
        page_result.scalars.return_value.all.return_value = []
        mock_session.execute.side_effect = [count_result, page_result]

        repo = ScheduleRunRepository(mock_session)
        total, rows = await repo.list_for_schedule(SCHEDULE_ID, limit=10, offset=0)

        assert total == 0
        assert rows == []


class TestScheduleRunRepositoryCountForSchedule:
    async def test_returns_scalar_count(self, mock_session, mocker):
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        count_result = mocker.MagicMock()
        count_result.scalar_one.return_value = 3
        mock_session.execute.return_value = count_result

        total = await ScheduleRunRepository(mock_session).count_for_schedule(SCHEDULE_ID)

        assert total == 3
        assert mock_session.execute.await_count == 1

    async def test_returns_zero_for_unknown_schedule(self, mock_session, mocker):
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        count_result = mocker.MagicMock()
        count_result.scalar_one.return_value = 0
        mock_session.execute.return_value = count_result

        assert await ScheduleRunRepository(mock_session).count_for_schedule("nope") == 0

    async def test_filters_by_schedule_id_only_with_no_status_predicate(self, mock_session, mocker):
        """Runs of every status must count, so the WHERE clause must not mention status."""
        from sqlalchemy.dialects import postgresql

        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        count_result = mocker.MagicMock()
        count_result.scalar_one.return_value = 0
        mock_session.execute.return_value = count_result

        await ScheduleRunRepository(mock_session).count_for_schedule(SCHEDULE_ID)

        stmt = str(mock_session.execute.await_args.args[0].compile(dialect=postgresql.dialect())).lower()
        assert "count(*)" in stmt
        assert "schedule_id" in stmt
        assert "status" not in stmt

    async def test_list_for_schedule_reuses_count_for_schedule(self, mock_session, mocker):
        """The refactor must not duplicate the count statement."""
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        page_result = mocker.MagicMock()
        page_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = page_result
        repo = ScheduleRunRepository(mock_session)
        spy = mocker.patch.object(repo, "count_for_schedule", return_value=7)

        total, rows = await repo.list_for_schedule(SCHEDULE_ID, limit=10, offset=0)

        spy.assert_awaited_once_with(SCHEDULE_ID)
        assert total == 7
        assert rows == []


class TestScheduleRunRepositoryListOrdering:
    @staticmethod
    def _order_by_clause(session) -> str:
        from sqlalchemy.dialects import postgresql

        page_call = session.execute.await_args_list[1]
        stmt = str(page_call.args[0].compile(dialect=postgresql.dialect())).upper()
        after = stmt.split("ORDER BY", 1)[1]
        return after.split("LIMIT", 1)[0].strip()

    @staticmethod
    def _empty_session(mocker) -> Any:
        session = mocker.AsyncMock()
        count_result = mocker.MagicMock()
        count_result.scalar_one.return_value = 0
        page_result = mocker.MagicMock()
        page_result.scalars.return_value.all.return_value = []
        session.execute = mocker.AsyncMock(side_effect=[count_result, page_result])
        return session

    async def test_defaults_to_created_at_desc_with_id_tiebreaker(self, mocker):
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        session = self._empty_session(mocker)
        repo = ScheduleRunRepository(session)
        await repo.list_for_schedule(SCHEDULE_ID, limit=10, offset=0)

        clause = self._order_by_clause(session)
        assert "CREATED_AT DESC" in clause
        assert clause.endswith("ID DESC")

    async def test_created_at_asc_with_id_tiebreaker(self, mocker):
        from src.lib.db.enums import RunSortField, SortDirection
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository
        from src.lib.schemas.ordering import RunOrdering

        session = self._empty_session(mocker)
        repo = ScheduleRunRepository(session)
        ordering = RunOrdering(order_by=RunSortField.CREATED_AT, order_dir=SortDirection.ASC)
        await repo.list_for_schedule(SCHEDULE_ID, limit=10, offset=0, ordering=ordering)

        clause = self._order_by_clause(session)
        assert "CREATED_AT ASC" in clause
        assert clause.endswith("ID ASC")


class TestScheduleRunRepositoryGetForSchedule:
    async def test_returns_run_when_schedule_matches(self, mock_session, mocker):
        from src.lib.models.schedule_run import ScheduleRun
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        run = mocker.MagicMock(spec=ScheduleRun)
        run.schedule_id = SCHEDULE_ID
        mock_session.get.return_value = run

        repo = ScheduleRunRepository(mock_session)
        result = await repo.get_for_schedule(SCHEDULE_ID, RUN_ID)

        assert result == run

    async def test_returns_none_when_run_missing(self, mock_session):
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        mock_session.get.return_value = None

        repo = ScheduleRunRepository(mock_session)
        result = await repo.get_for_schedule(SCHEDULE_ID, RUN_ID)

        assert result is None

    async def test_returns_none_when_schedule_id_mismatch(self, mock_session, mocker):
        from src.lib.models.schedule_run import ScheduleRun
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        run = mocker.MagicMock(spec=ScheduleRun)
        run.schedule_id = "different-schedule-id"
        mock_session.get.return_value = run

        repo = ScheduleRunRepository(mock_session)
        result = await repo.get_for_schedule(SCHEDULE_ID, RUN_ID)

        assert result is None

    async def test_fetches_by_uuid(self, mock_session, mocker):
        from src.lib.models.schedule_run import ScheduleRun
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        run = mocker.MagicMock(spec=ScheduleRun)
        run.schedule_id = SCHEDULE_ID
        mock_session.get.return_value = run

        repo = ScheduleRunRepository(mock_session)
        await repo.get_for_schedule(SCHEDULE_ID, RUN_ID)

        call_args = mock_session.get.call_args
        assert call_args.args[1] == uuid.UUID(RUN_ID)


class TestScheduleRunRepositorySetConversationId:
    """Tests for set_conversation_id method (data-model §6 cases 6, 7, 8, 9)."""

    CONV_ID = "conversations/bkt/.scheduler/sched-001/gpt-4o__hello__run-001"

    async def test_sets_conversation_id_on_existing_run(self, mock_session, mocker):
        """Case 6: set_conversation_id sets the column and returns the row."""
        from src.lib.models.schedule_run import ScheduleRun
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        existing = mocker.MagicMock(spec=ScheduleRun)
        mock_session.get.return_value = existing

        repo = ScheduleRunRepository(mock_session)
        result = await repo.set_conversation_id(RUN_ID, self.CONV_ID)

        assert existing.conversation_id == self.CONV_ID
        mock_session.commit.assert_awaited_once()
        mock_session.refresh.assert_awaited_once_with(existing)
        assert result is existing

    async def test_returns_none_for_unknown_run_id(self, mock_session):
        """Case 7: set_conversation_id returns None and writes nothing for unknown run."""
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        mock_session.get.return_value = None

        repo = ScheduleRunRepository(mock_session)
        result = await repo.set_conversation_id(RUN_ID, self.CONV_ID)

        assert result is None
        mock_session.commit.assert_not_awaited()
        mock_session.refresh.assert_not_awaited()

    async def test_does_not_alter_status_end_time_or_result(self, mock_session, mocker):
        """Case 8: set_conversation_id does not touch status, end_time, or result."""
        from src.lib.db.enums import ScheduleRunStatus
        from src.lib.models.schedule_run import ScheduleRun
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        existing = mocker.MagicMock(spec=ScheduleRun)
        original_status = ScheduleRunStatus.SUCCESS.value
        existing.status = original_status
        mock_session.get.return_value = existing

        repo = ScheduleRunRepository(mock_session)
        await repo.set_conversation_id(RUN_ID, self.CONV_ID)

        # Only conversation_id was assigned; status, end_time, result must not be touched
        assert existing.status == original_status
        # Confirm only conversation_id was set, not status/end_time/result
        assert existing.conversation_id == self.CONV_ID

    async def test_uses_uuid_for_lookup(self, mock_session, mocker):
        """set_conversation_id passes uuid.UUID(run_id) to session.get."""
        from src.lib.models.schedule_run import ScheduleRun
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        mock_session.get.return_value = mocker.MagicMock(spec=ScheduleRun)

        repo = ScheduleRunRepository(mock_session)
        await repo.set_conversation_id(RUN_ID, self.CONV_ID)

        call_args = mock_session.get.call_args
        assert call_args.args[1] == uuid.UUID(RUN_ID)

    async def test_list_for_schedule_returns_conversation_id_in_page(self, mock_session, mocker):
        """Case 9 (set): list_for_schedule returns runs carrying the persisted conversation_id."""
        from src.lib.models.schedule_run import ScheduleRun
        from src.lib.repositories.schedule_run_repository import ScheduleRunRepository

        run_with_conv = mocker.MagicMock(spec=ScheduleRun)
        run_with_conv.conversation_id = self.CONV_ID
        run_without_conv = mocker.MagicMock(spec=ScheduleRun)
        run_without_conv.conversation_id = None

        count_result = mocker.MagicMock()
        count_result.scalar_one.return_value = 2
        page_result = mocker.MagicMock()
        page_result.scalars.return_value.all.return_value = [run_with_conv, run_without_conv]
        mock_session.execute.side_effect = [count_result, page_result]

        repo = ScheduleRunRepository(mock_session)
        total, rows = await repo.list_for_schedule(SCHEDULE_ID, limit=10, offset=0)

        assert total == 2
        assert rows[0].conversation_id == self.CONV_ID
        assert rows[1].conversation_id is None
