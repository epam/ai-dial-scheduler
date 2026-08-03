from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from src.lib.db.enums import ScheduleSortField, SortDirection
from src.lib.models.schedule import Schedule
from src.lib.repositories.schedule_repository import ScheduleRepository
from src.lib.schemas.ordering import ScheduleOrdering


def make_schedule(
    schedule_id: str = "sched-1",
    created_by: str = "mock_user_id",
    service_id: str = "dial-oauth",
) -> Schedule:
    return Schedule(
        id=schedule_id,
        display_name="Test Schedule",
        created_by=created_by,
        service_id=service_id,
        trigger={"cron": {"fields": {"hour": 9}}},
        kwargs={"target_type": "chat_completion", "url": "https://example.com"},
    )


class TestScheduleRepositoryGetById:
    @pytest.mark.asyncio
    async def test_returns_schedule_regardless_of_owner(self):
        session = AsyncMock()
        schedule = make_schedule(created_by="someone-else", service_id="dial-api-key")
        session.get = AsyncMock(return_value=schedule)
        repo = ScheduleRepository(session)

        result = await repo.get_by_id("sched-1")

        assert result is schedule
        assert result.service_id == "dial-api-key"
        assert result.created_by == "someone-else"
        session.get.assert_awaited_once_with(Schedule, "sched-1")

    @pytest.mark.asyncio
    async def test_returns_none_when_absent(self):
        session = AsyncMock()
        session.get = AsyncMock(return_value=None)
        repo = ScheduleRepository(session)

        assert await repo.get_by_id("missing") is None


class TestScheduleRepositoryCreate:
    @pytest.mark.asyncio
    async def test_create_inserts_and_commits(self):
        session = AsyncMock()
        session.add = MagicMock()
        session.refresh = AsyncMock()
        repo = ScheduleRepository(session)
        schedule = make_schedule()

        result = await repo.create(schedule)

        session.add.assert_called_once_with(schedule)
        session.commit.assert_awaited_once()
        assert result is schedule

    @pytest.mark.asyncio
    async def test_delete_removes_row(self):
        session = AsyncMock()
        schedule = make_schedule()
        session.get = AsyncMock(return_value=schedule)
        repo = ScheduleRepository(session)

        await repo.delete("sched-1")

        session.delete.assert_awaited_once_with(schedule)
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_noop_when_not_found(self):
        session = AsyncMock()
        session.get = AsyncMock(return_value=None)
        repo = ScheduleRepository(session)

        await repo.delete("nonexistent")

        session.delete.assert_not_awaited()


class TestScheduleRepositoryUpdate:
    @pytest.mark.asyncio
    async def test_update_owned_row_commits(self):
        session = AsyncMock()
        session.add = MagicMock()
        schedule = make_schedule()
        session.get = AsyncMock(return_value=schedule)
        session.refresh = AsyncMock()
        repo = ScheduleRepository(session)

        result = await repo.update(
            "sched-1",
            "mock_user_id",
            display_name="New Name",
            service_id="dial-oauth",
            trigger={"date": "2026-06-12T09:00:00Z"},
            kwargs={"target_type": "rest", "url": "https://example.com", "method": "POST"},
        )

        session.commit.assert_awaited_once()
        assert result is not None
        assert result.display_name == "New Name"
        assert result.service_id == "dial-oauth"

    @pytest.mark.asyncio
    async def test_update_sets_metadata_when_provided(self):
        session = AsyncMock()
        session.add = MagicMock()
        schedule = make_schedule()
        session.get = AsyncMock(return_value=schedule)
        session.refresh = AsyncMock()
        repo = ScheduleRepository(session)

        result = await repo.update(
            "sched-1",
            "mock_user_id",
            display_name="New Name",
            service_id="dial-oauth",
            trigger={},
            kwargs={},
            metadata={"tenant": "hcscd"},
        )

        assert result is not None
        assert schedule.schedule_metadata == {"tenant": "hcscd"}

    @pytest.mark.asyncio
    async def test_update_leaves_metadata_unchanged_when_omitted(self):
        session = AsyncMock()
        session.add = MagicMock()
        schedule = make_schedule()
        schedule.schedule_metadata = {"tenant": "keep"}
        session.get = AsyncMock(return_value=schedule)
        session.refresh = AsyncMock()
        repo = ScheduleRepository(session)

        await repo.update(
            "sched-1",
            "mock_user_id",
            display_name="New Name",
            service_id="dial-oauth",
            trigger={},
            kwargs={},
        )

        assert schedule.schedule_metadata == {"tenant": "keep"}

    @pytest.mark.asyncio
    async def test_update_returns_none_for_missing_id(self):
        session = AsyncMock()
        session.get = AsyncMock(return_value=None)
        repo = ScheduleRepository(session)

        result = await repo.update(
            "nonexistent",
            "mock_user_id",
            display_name="New Name",
            service_id="dial-oauth",
            trigger={},
            kwargs={},
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_update_returns_none_for_wrong_owner(self):
        session = AsyncMock()
        schedule = make_schedule(created_by="other_user")
        session.get = AsyncMock(return_value=schedule)
        repo = ScheduleRepository(session)

        result = await repo.update(
            "sched-1",
            "mock_user_id",
            display_name="New Name",
            service_id="dial-oauth",
            trigger={},
            kwargs={},
        )

        assert result is None


class TestScheduleRepositoryDescription:
    @pytest.mark.asyncio
    async def test_update_sets_description(self):
        session = AsyncMock()
        session.add = MagicMock()
        schedule = make_schedule()
        session.get = AsyncMock(return_value=schedule)
        session.refresh = AsyncMock()
        repo = ScheduleRepository(session)

        await repo.update(
            "sched-1",
            "mock_user_id",
            display_name="New Name",
            service_id="dial-oauth",
            trigger={},
            kwargs={},
            description="fresh notes",
        )

        assert schedule.description == "fresh notes"

    @pytest.mark.asyncio
    async def test_update_replaces_description_with_none(self):
        session = AsyncMock()
        session.add = MagicMock()
        schedule = make_schedule()
        schedule.description = "old notes"
        session.get = AsyncMock(return_value=schedule)
        session.refresh = AsyncMock()
        repo = ScheduleRepository(session)

        await repo.update(
            "sched-1",
            "mock_user_id",
            display_name="New Name",
            service_id="dial-oauth",
            trigger={},
            kwargs={},
            description=None,
        )

        assert schedule.description is None


class TestScheduleRepositoryNameFilter:
    @staticmethod
    def _compiled_statements(session) -> list[str]:
        dialect = postgresql.dialect()
        return [str(call.args[0].compile(dialect=dialect)) for call in session.execute.await_args_list]

    @pytest.mark.asyncio
    async def test_name_filter_adds_ilike_predicate(self):
        session = AsyncMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        page_result = MagicMock()
        page_result.__iter__ = MagicMock(return_value=iter([]))
        session.execute = AsyncMock(side_effect=[count_result, page_result])

        repo = ScheduleRepository(session)
        await repo.list("mock_user_id", limit=10, offset=0, name_filter="report")

        statements = self._compiled_statements(session)
        assert len(statements) == 2
        for stmt in statements:
            assert "ILIKE" in stmt.upper()
            assert "display_name" in stmt

    @pytest.mark.asyncio
    async def test_no_name_filter_has_no_ilike(self):
        session = AsyncMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        page_result = MagicMock()
        page_result.__iter__ = MagicMock(return_value=iter([]))
        session.execute = AsyncMock(side_effect=[count_result, page_result])

        repo = ScheduleRepository(session)
        await repo.list("mock_user_id", limit=10, offset=0)

        for stmt in self._compiled_statements(session):
            assert "ILIKE" not in stmt.upper()

    @pytest.mark.asyncio
    async def test_name_filter_composes_with_metadata(self):
        session = AsyncMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        page_result = MagicMock()
        page_result.__iter__ = MagicMock(return_value=iter([]))
        session.execute = AsyncMock(side_effect=[count_result, page_result])

        repo = ScheduleRepository(session)
        await repo.list(
            "mock_user_id",
            limit=10,
            offset=0,
            metadata_filter={"tenant": "hcscd"},
            name_filter="report",
        )

        for stmt in self._compiled_statements(session):
            assert "ILIKE" in stmt.upper()
            assert "@>" in stmt


class TestScheduleRepositoryGet:
    @pytest.mark.asyncio
    async def test_get_returns_schedule_with_next_run_time(self):
        session = AsyncMock()
        schedule = make_schedule()
        epoch = 1749721200.0
        mock_row = (schedule, epoch)
        mock_result = MagicMock()
        mock_result.first.return_value = mock_row
        session.execute = AsyncMock(return_value=mock_result)

        repo = ScheduleRepository(session)
        result = await repo.get("sched-1", "mock_user_id")

        assert result is not None
        returned_schedule, next_run_time = result
        assert returned_schedule is schedule
        assert next_run_time is not None
        assert next_run_time.tzinfo is not None

    @pytest.mark.asyncio
    async def test_get_returns_none_when_not_found(self):
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.first.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        repo = ScheduleRepository(session)
        result = await repo.get("nonexistent", "mock_user_id")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_returns_none_next_run_time_when_no_job(self):
        session = AsyncMock()
        schedule = make_schedule()
        mock_row = (schedule, None)
        mock_result = MagicMock()
        mock_result.first.return_value = mock_row
        session.execute = AsyncMock(return_value=mock_result)

        repo = ScheduleRepository(session)
        result = await repo.get("sched-1", "mock_user_id")

        assert result is not None
        _, next_run_time = result
        assert next_run_time is None


class TestScheduleRepositoryList:
    @pytest.mark.asyncio
    async def test_list_returns_count_and_page(self):
        session = AsyncMock()
        schedule = make_schedule()
        epoch = 1749721200.0

        count_result = MagicMock()
        count_result.scalar_one.return_value = 5
        page_result = MagicMock()
        page_result.__iter__ = MagicMock(return_value=iter([(schedule, epoch)]))
        session.execute = AsyncMock(side_effect=[count_result, page_result])

        repo = ScheduleRepository(session)
        total, rows = await repo.list("mock_user_id", limit=10, offset=0)

        assert total == 5
        assert len(rows) == 1
        assert rows[0][0] is schedule
        assert rows[0][1] is not None

    @staticmethod
    def _compiled_statements(session) -> list[str]:
        dialect = postgresql.dialect()
        return [str(call.args[0].compile(dialect=dialect)) for call in session.execute.await_args_list]

    @pytest.mark.asyncio
    async def test_list_applies_metadata_containment_filter(self):
        session = AsyncMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        page_result = MagicMock()
        page_result.__iter__ = MagicMock(return_value=iter([]))
        session.execute = AsyncMock(side_effect=[count_result, page_result])

        repo = ScheduleRepository(session)
        await repo.list("mock_user_id", limit=10, offset=0, metadata_filter={"tenant": "hcscd"})

        # Both the count and page statements must carry the JSONB containment predicate.
        statements = self._compiled_statements(session)
        assert len(statements) == 2
        for stmt in statements:
            assert "@>" in stmt
            assert "metadata" in stmt

    @pytest.mark.asyncio
    async def test_list_without_filter_has_no_containment(self):
        session = AsyncMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        page_result = MagicMock()
        page_result.__iter__ = MagicMock(return_value=iter([]))
        session.execute = AsyncMock(side_effect=[count_result, page_result])

        repo = ScheduleRepository(session)
        await repo.list("mock_user_id", limit=10, offset=0)

        for stmt in self._compiled_statements(session):
            assert "@>" not in stmt

    @pytest.mark.asyncio
    async def test_list_empty_filter_has_no_containment(self):
        session = AsyncMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        page_result = MagicMock()
        page_result.__iter__ = MagicMock(return_value=iter([]))
        session.execute = AsyncMock(side_effect=[count_result, page_result])

        repo = ScheduleRepository(session)
        await repo.list("mock_user_id", limit=10, offset=0, metadata_filter={})

        for stmt in self._compiled_statements(session):
            assert "@>" not in stmt

    @pytest.mark.asyncio
    async def test_list_next_run_time_none_when_no_job(self):
        session = AsyncMock()
        schedule = make_schedule()

        count_result = MagicMock()
        count_result.scalar_one.return_value = 1
        page_result = MagicMock()
        page_result.__iter__ = MagicMock(return_value=iter([(schedule, None)]))
        session.execute = AsyncMock(side_effect=[count_result, page_result])

        repo = ScheduleRepository(session)
        _, rows = await repo.list("mock_user_id", limit=10, offset=0)

        assert rows[0][1] is None


class TestScheduleRepositoryOrdering:
    @staticmethod
    def _page_statement(session) -> str:
        """Compile the second execute() call (the page query) to a SQL string."""
        dialect = postgresql.dialect()
        page_call = session.execute.await_args_list[1]
        return str(page_call.args[0].compile(dialect=dialect))

    @classmethod
    def _order_by_clause(cls, session) -> str:
        """Return the uppercased ORDER BY clause of the page statement, without LIMIT/OFFSET."""
        stmt = cls._page_statement(session).upper()
        after = stmt.split("ORDER BY", 1)[1]
        return after.split("LIMIT", 1)[0].strip()

    @staticmethod
    def _empty_session() -> AsyncMock:
        session = AsyncMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        page_result = MagicMock()
        page_result.__iter__ = MagicMock(return_value=iter([]))
        session.execute = AsyncMock(side_effect=[count_result, page_result])
        return session

    @pytest.mark.asyncio
    async def test_default_orders_by_created_at_desc_with_id_tiebreaker(self):
        session = self._empty_session()
        repo = ScheduleRepository(session)
        await repo.list("mock_user_id", limit=10, offset=0)

        clause = self._order_by_clause(session)
        assert "CREATED_AT DESC" in clause
        assert clause.endswith("ID DESC")

    @pytest.mark.asyncio
    async def test_created_at_asc_with_id_tiebreaker(self):
        session = self._empty_session()
        repo = ScheduleRepository(session)
        ordering = ScheduleOrdering(order_by=ScheduleSortField.CREATED_AT, order_dir=SortDirection.ASC)
        await repo.list("mock_user_id", limit=10, offset=0, ordering=ordering)

        clause = self._order_by_clause(session)
        assert "CREATED_AT ASC" in clause
        assert clause.endswith("ID ASC")

    @pytest.mark.asyncio
    async def test_created_at_desc_with_id_tiebreaker(self):
        session = self._empty_session()
        repo = ScheduleRepository(session)
        ordering = ScheduleOrdering(order_by=ScheduleSortField.CREATED_AT, order_dir=SortDirection.DESC)
        await repo.list("mock_user_id", limit=10, offset=0, ordering=ordering)

        clause = self._order_by_clause(session)
        assert "CREATED_AT DESC" in clause
        assert clause.endswith("ID DESC")

    @pytest.mark.asyncio
    async def test_next_run_time_asc_nulls_last_with_id_tiebreaker(self):
        session = self._empty_session()
        repo = ScheduleRepository(session)
        ordering = ScheduleOrdering(order_by=ScheduleSortField.NEXT_RUN_TIME, order_dir=SortDirection.ASC)
        await repo.list("mock_user_id", limit=10, offset=0, ordering=ordering)

        clause = self._order_by_clause(session)
        assert "NEXT_RUN_TIME ASC NULLS LAST" in clause
        assert clause.endswith("ID ASC")

    @pytest.mark.asyncio
    async def test_next_run_time_desc_nulls_last_with_id_tiebreaker(self):
        session = self._empty_session()
        repo = ScheduleRepository(session)
        ordering = ScheduleOrdering(order_by=ScheduleSortField.NEXT_RUN_TIME, order_dir=SortDirection.DESC)
        await repo.list("mock_user_id", limit=10, offset=0, ordering=ordering)

        clause = self._order_by_clause(session)
        assert "NEXT_RUN_TIME DESC NULLS LAST" in clause
        assert clause.endswith("ID DESC")

    @pytest.mark.asyncio
    async def test_name_asc_case_insensitive_with_id_tiebreaker(self):
        session = self._empty_session()
        repo = ScheduleRepository(session)
        ordering = ScheduleOrdering(order_by=ScheduleSortField.NAME, order_dir=SortDirection.ASC)
        await repo.list("mock_user_id", limit=10, offset=0, ordering=ordering)

        clause = self._order_by_clause(session)
        assert "LOWER(" in clause
        assert "DISPLAY_NAME" in clause
        assert clause.endswith("ID ASC")

    @pytest.mark.asyncio
    async def test_name_desc_case_insensitive_with_id_tiebreaker(self):
        session = self._empty_session()
        repo = ScheduleRepository(session)
        ordering = ScheduleOrdering(order_by=ScheduleSortField.NAME, order_dir=SortDirection.DESC)
        await repo.list("mock_user_id", limit=10, offset=0, ordering=ordering)

        clause = self._order_by_clause(session)
        assert "LOWER(" in clause
        assert "DISPLAY_NAME" in clause
        assert clause.endswith("ID DESC")
