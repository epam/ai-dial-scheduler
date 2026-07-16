"""Integration tests for ScheduleRepository against a real PostgreSQL database.

Requires TEST_DB_URL to be set; all tests are skipped otherwise.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.lib.models.apscheduler_job import apscheduler_jobs
from src.lib.models.schedule import Schedule
from src.lib.repositories.schedule_repository import ScheduleRepository


pytestmark = pytest.mark.asyncio(loop_scope="module")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_schedule(sched_id: str | None = None, *, created_by: str = "test-user") -> Schedule:
    return Schedule(
        id=sched_id or str(uuid.uuid4()),
        display_name="Test Schedule",
        created_by=created_by,
        service_id="dial-oauth",
        trigger={"cron": {"fields": {"hour": 9}}},
        kwargs={"target_type": "chat_completion", "url": "https://example.com"},
    )


@pytest_asyncio.fixture(loop_scope="module")
async def sess(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


async def _delete_schedules(session: AsyncSession, *ids: str) -> None:
    schedules_table = Schedule.__table__  # type: ignore[attr-defined]
    await session.execute(sa.delete(apscheduler_jobs).where(apscheduler_jobs.c.id.in_(ids)))  # type: ignore[union-attr]
    await session.execute(sa.delete(Schedule).where(schedules_table.c.id.in_(ids)))
    await session.commit()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestScheduleRepositoryCreate:
    async def test_create_persists_row_and_returns_model(self, sess: AsyncSession) -> None:
        schedule = _make_schedule()
        repo = ScheduleRepository(sess)
        result = await repo.create(schedule)

        try:
            assert result.id == schedule.id
            assert result.display_name == "Test Schedule"
            assert result.created_by == "test-user"
            assert result.created_at is not None
            assert result.updated_at is not None

            fetched = await sess.get(Schedule, schedule.id)
            assert fetched is not None
        finally:
            await _delete_schedules(sess, schedule.id)

    async def test_create_stores_jsonb_fields(self, sess: AsyncSession) -> None:
        schedule = _make_schedule()
        repo = ScheduleRepository(sess)
        result = await repo.create(schedule)

        try:
            assert result.trigger == {"cron": {"fields": {"hour": 9}}}
            assert result.kwargs["target_type"] == "chat_completion"
            assert result.kwargs["url"] == "https://example.com"
        finally:
            await _delete_schedules(sess, schedule.id)


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


class TestScheduleRepositoryGet:
    async def test_get_returns_none_next_run_time_without_job_row(self, sess: AsyncSession) -> None:
        schedule = _make_schedule()
        repo = ScheduleRepository(sess)
        await repo.create(schedule)

        try:
            row = await repo.get(schedule.id, "test-user")
            assert row is not None
            fetched, next_run_time = row
            assert fetched.id == schedule.id
            assert next_run_time is None
        finally:
            await _delete_schedules(sess, schedule.id)

    async def test_get_returns_utc_datetime_when_job_row_exists(self, sess: AsyncSession) -> None:
        schedule = _make_schedule()
        repo = ScheduleRepository(sess)
        await repo.create(schedule)

        epoch = 1_700_000_000.0
        await sess.execute(apscheduler_jobs.insert().values(id=schedule.id, next_run_time=epoch))
        await sess.commit()

        try:
            row = await repo.get(schedule.id, "test-user")
            assert row is not None
            _, next_run_time = row
            assert next_run_time is not None
            assert next_run_time.tzinfo == UTC
            assert next_run_time == datetime.fromtimestamp(epoch, tz=UTC)
        finally:
            await _delete_schedules(sess, schedule.id)

    async def test_get_with_wrong_owner_returns_none(self, sess: AsyncSession) -> None:
        schedule = _make_schedule()
        repo = ScheduleRepository(sess)
        await repo.create(schedule)

        try:
            result = await repo.get(schedule.id, "other-user")
            assert result is None
        finally:
            await _delete_schedules(sess, schedule.id)

    async def test_get_nonexistent_returns_none(self, sess: AsyncSession) -> None:
        repo = ScheduleRepository(sess)
        result = await repo.get(str(uuid.uuid4()), "test-user")
        assert result is None


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


class TestScheduleRepositoryUpdate:
    async def test_update_changes_fields(self, sess: AsyncSession) -> None:
        schedule = _make_schedule()
        repo = ScheduleRepository(sess)
        await repo.create(schedule)

        try:
            updated = await repo.update(
                schedule.id,
                "test-user",
                display_name="Updated Name",
                service_id="dial-oauth",
                trigger={"date": "2026-12-01T09:00:00"},
                kwargs={"target_type": "rest", "url": "https://new.example.com", "method": "POST"},
            )
            assert updated is not None
            assert updated.display_name == "Updated Name"
            assert updated.trigger == {"date": "2026-12-01T09:00:00"}
        finally:
            await _delete_schedules(sess, schedule.id)

    async def test_update_bumps_updated_at(self, sess: AsyncSession) -> None:
        schedule = _make_schedule()
        repo = ScheduleRepository(sess)
        created = await repo.create(schedule)
        original_updated_at = created.updated_at

        try:
            updated = await repo.update(
                schedule.id,
                "test-user",
                display_name="New Name",
                service_id="dial-oauth",
                trigger=schedule.trigger,
                kwargs=schedule.kwargs,
            )
            assert updated is not None
            assert updated.updated_at >= original_updated_at
        finally:
            await _delete_schedules(sess, schedule.id)

    async def test_update_with_explicit_updated_at_restores_timestamp(self, sess: AsyncSession) -> None:
        """C3: Compensating transaction must restore the exact prior updated_at."""
        schedule = _make_schedule()
        repo = ScheduleRepository(sess)
        created = await repo.create(schedule)
        prior_updated_at = created.updated_at

        try:
            restored = await repo.update(
                schedule.id,
                "test-user",
                display_name="Restored Name",
                service_id="dial-oauth",
                trigger=schedule.trigger,
                kwargs=schedule.kwargs,
                updated_at=prior_updated_at,
            )
            assert restored is not None
            assert restored.updated_at == prior_updated_at
        finally:
            await _delete_schedules(sess, schedule.id)

    async def test_update_with_wrong_owner_returns_none(self, sess: AsyncSession) -> None:
        schedule = _make_schedule()
        repo = ScheduleRepository(sess)
        await repo.create(schedule)

        try:
            result = await repo.update(
                schedule.id,
                "other-user",
                display_name="Hijacked",
                service_id="dial-oauth",
                trigger=schedule.trigger,
                kwargs=schedule.kwargs,
            )
            assert result is None
        finally:
            await _delete_schedules(sess, schedule.id)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestScheduleRepositoryDelete:
    async def test_delete_removes_row(self, sess: AsyncSession) -> None:
        schedule = _make_schedule()
        repo = ScheduleRepository(sess)
        await repo.create(schedule)

        await repo.delete(schedule.id)

        fetched = await sess.get(Schedule, schedule.id)
        assert fetched is None

    async def test_delete_nonexistent_is_a_no_op(self, sess: AsyncSession) -> None:
        repo = ScheduleRepository(sess)
        await repo.delete(str(uuid.uuid4()))  # must not raise


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class TestScheduleRepositoryList:
    async def test_list_returns_count_and_rows(self, sess: AsyncSession) -> None:
        ids = [str(uuid.uuid4()) for _ in range(3)]
        repo = ScheduleRepository(sess)
        for sid in ids:
            await repo.create(_make_schedule(sid))

        try:
            total, rows = await repo.list("test-user", limit=10, offset=0)
            assert total >= 3
            returned_ids = {r[0].id for r in rows}
            for sid in ids:
                assert sid in returned_ids
        finally:
            await _delete_schedules(sess, *ids)

    async def test_list_excludes_other_owners(self, sess: AsyncSession) -> None:
        own_id = str(uuid.uuid4())
        other_id = str(uuid.uuid4())
        repo = ScheduleRepository(sess)
        await repo.create(_make_schedule(own_id, created_by="alice"))
        await repo.create(_make_schedule(other_id, created_by="bob"))

        try:
            total, rows = await repo.list("alice", limit=10, offset=0)
            returned_ids = {r[0].id for r in rows}
            assert own_id in returned_ids
            assert other_id not in returned_ids
        finally:
            await _delete_schedules(sess, own_id, other_id)

    async def test_list_pagination_respects_limit(self, sess: AsyncSession) -> None:
        ids = [str(uuid.uuid4()) for _ in range(5)]
        repo = ScheduleRepository(sess)
        for sid in ids:
            await repo.create(_make_schedule(sid, created_by="paginate-user"))

        try:
            total, rows = await repo.list("paginate-user", limit=2, offset=0)
            assert total == 5
            assert len(rows) == 2
        finally:
            await _delete_schedules(sess, *ids)

    async def test_list_pagination_offset(self, sess: AsyncSession) -> None:
        ids = [str(uuid.uuid4()) for _ in range(4)]
        repo = ScheduleRepository(sess)
        for sid in ids:
            await repo.create(_make_schedule(sid, created_by="offset-user"))

        try:
            _, first_page = await repo.list("offset-user", limit=2, offset=0)
            _, second_page = await repo.list("offset-user", limit=2, offset=2)
            first_ids = {r[0].id for r in first_page}
            second_ids = {r[0].id for r in second_page}
            assert first_ids.isdisjoint(second_ids)
        finally:
            await _delete_schedules(sess, *ids)

    async def test_list_returns_null_next_run_time_without_job_row(self, sess: AsyncSession) -> None:
        sid = str(uuid.uuid4())
        repo = ScheduleRepository(sess)
        await repo.create(_make_schedule(sid))

        try:
            _, rows = await repo.list("test-user", limit=10, offset=0)
            row = next(r for r in rows if r[0].id == sid)
            assert row[1] is None
        finally:
            await _delete_schedules(sess, sid)

    async def test_list_returns_datetime_next_run_time_with_job_row(self, sess: AsyncSession) -> None:
        sid = str(uuid.uuid4())
        repo = ScheduleRepository(sess)
        await repo.create(_make_schedule(sid))
        epoch = 1_700_000_000.0
        await sess.execute(apscheduler_jobs.insert().values(id=sid, next_run_time=epoch))
        await sess.commit()

        try:
            _, rows = await repo.list("test-user", limit=10, offset=0)
            row = next(r for r in rows if r[0].id == sid)
            assert row[1] == datetime.fromtimestamp(epoch, tz=UTC)
        finally:
            await _delete_schedules(sess, sid)
