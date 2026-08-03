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
