"""Integration tests for schedule deletion against a real PostgreSQL database.

These cover the behaviours that cannot be proven with mocks: the ``ON DELETE RESTRICT``
foreign key that makes run-history loss impossible, the conditional-write concurrency
guarantee, and the fact that a soft-deleted schedule stays readable while dropping out of the
default list.
"""

from datetime import UTC, datetime
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.db.enums import ScheduleRunStatus
from src.lib.models.apscheduler_job import apscheduler_jobs
from src.lib.models.schedule import Schedule
from src.lib.models.schedule_run import ScheduleRun
from src.lib.repositories.schedule_repository import ScheduleRepository
from src.lib.repositories.schedule_run_repository import ScheduleRunRepository


pytestmark = pytest.mark.asyncio(loop_scope="module")

OWNER = "delete-owner@example.com"
OTHER = "someone-else@example.com"


def _make_schedule(sched_id: str, created_by: str = OWNER, is_deleted: bool = False) -> Schedule:
    return Schedule(
        id=sched_id,
        display_name=f"Schedule {sched_id}",
        created_by=created_by,
        service_id="dial-native",
        trigger={"cron": {"fields": {"hour": 9}}},
        kwargs={"target_type": "rest", "url": "https://example.com", "method": "POST"},
        is_deleted=is_deleted,
    )


def _make_run(sched_id: str, status: ScheduleRunStatus = ScheduleRunStatus.SUCCESS) -> ScheduleRun:
    return ScheduleRun(
        id=uuid.uuid4(),
        status=status,
        schedule_id=sched_id,
        schedule_snapshot={"display_name": "snapshot"},
    )


async def _cleanup(session: AsyncSession, *sched_ids: str) -> None:
    for sched_id in sched_ids:
        await session.execute(sa.delete(ScheduleRun).where(ScheduleRun.schedule_id == sched_id))
        await session.execute(sa.delete(Schedule).where(Schedule.id == sched_id))
        await session.execute(sa.delete(apscheduler_jobs).where(apscheduler_jobs.c.id == sched_id))
    await session.commit()


class TestIsDeletedColumn:
    async def test_new_schedule_defaults_to_not_deleted(self, session: AsyncSession) -> None:
        sched_id = f"col-{uuid.uuid4()}"
        try:
            session.add(_make_schedule(sched_id))
            await session.commit()

            stored = await session.get(Schedule, sched_id)
            assert stored is not None
            assert stored.is_deleted is False
        finally:
            await _cleanup(session, sched_id)

    async def test_column_is_not_nullable(self, session: AsyncSession) -> None:
        sched_id = f"col-null-{uuid.uuid4()}"
        try:
            await session.execute(
                sa.text(
                    "INSERT INTO schedules (id, display_name, created_by, service_id, trigger, kwargs, "
                    "created_at, updated_at) VALUES (:id, 'x', :owner, 'dial-native', '{}'::jsonb, "
                    "'{}'::jsonb, now(), now())"
                ),
                {"id": sched_id, "owner": OWNER},
            )
            await session.commit()

            # The server default must fill the column for inserts that omit it entirely.
            value = await session.scalar(sa.text("SELECT is_deleted FROM schedules WHERE id = :id"), {"id": sched_id})
            assert value is False
        finally:
            await _cleanup(session, sched_id)


class TestHardDeleteWithoutRuns:
    async def test_delete_owned_removes_row(self, session: AsyncSession) -> None:
        sched_id = f"hard-{uuid.uuid4()}"
        try:
            session.add(_make_schedule(sched_id))
            await session.commit()
            repo = ScheduleRepository(session)

            assert await repo.delete_owned(sched_id, OWNER) is True
            assert await session.get(Schedule, sched_id) is None
        finally:
            await _cleanup(session, sched_id)

    async def test_delete_owned_rejects_other_owner(self, session: AsyncSession) -> None:
        sched_id = f"hard-owner-{uuid.uuid4()}"
        try:
            session.add(_make_schedule(sched_id))
            await session.commit()
            repo = ScheduleRepository(session)

            assert await repo.delete_owned(sched_id, OTHER) is False
            assert await session.get(Schedule, sched_id) is not None
        finally:
            await _cleanup(session, sched_id)

    async def test_delete_owned_returns_false_for_unknown_id(self, session: AsyncSession) -> None:
        assert await ScheduleRepository(session).delete_owned(f"missing-{uuid.uuid4()}", OWNER) is False

    async def test_delete_owned_does_not_touch_other_schedules(self, session: AsyncSession) -> None:
        target, bystander = f"hard-a-{uuid.uuid4()}", f"hard-b-{uuid.uuid4()}"
        try:
            session.add(_make_schedule(target))
            session.add(_make_schedule(bystander))
            await session.commit()

            await ScheduleRepository(session).delete_owned(target, OWNER)

            assert await session.get(Schedule, bystander) is not None
        finally:
            await _cleanup(session, target, bystander)


class TestForeignKeyProtectsRunHistory:
    @pytest.mark.parametrize(
        "status",
        [
            ScheduleRunStatus.SUCCESS,
            ScheduleRunStatus.ERROR,
            ScheduleRunStatus.IN_PROGRESS,
            ScheduleRunStatus.MISSED,
        ],
    )
    async def test_hard_delete_is_blocked_by_restrict_for_any_run_status(
        self, session: AsyncSession, status: ScheduleRunStatus
    ) -> None:
        """The database refuses to destroy run history even if application logic is wrong."""
        sched_id = f"fk-{uuid.uuid4()}"
        try:
            session.add(_make_schedule(sched_id))
            await session.commit()
            session.add(_make_run(sched_id, status))
            await session.commit()

            with pytest.raises(IntegrityError):
                await ScheduleRepository(session).delete_owned(sched_id, OWNER)
            await session.rollback()

            assert await session.get(Schedule, sched_id) is not None
        finally:
            await _cleanup(session, sched_id)

    async def test_soft_delete_preserves_every_run(self, session: AsyncSession) -> None:
        sched_id = f"fk-soft-{uuid.uuid4()}"
        try:
            session.add(_make_schedule(sched_id))
            await session.commit()
            for status in (ScheduleRunStatus.SUCCESS, ScheduleRunStatus.ERROR, ScheduleRunStatus.IN_PROGRESS):
                session.add(_make_run(sched_id, status))
            await session.commit()

            assert await ScheduleRepository(session).mark_deleted(sched_id, OWNER) is True

            total, runs = await ScheduleRunRepository(session).list_for_schedule(sched_id, limit=10, offset=0)
            assert total == 3
            assert len(runs) == 3
        finally:
            await _cleanup(session, sched_id)


class TestSoftDelete:
    async def test_mark_deleted_sets_flag_and_advances_updated_at(self, session: AsyncSession) -> None:
        sched_id = f"soft-{uuid.uuid4()}"
        try:
            schedule = _make_schedule(sched_id)
            schedule.updated_at = datetime(2020, 1, 1, tzinfo=UTC)
            session.add(schedule)
            await session.commit()
            before = (await session.get(Schedule, sched_id)).updated_at  # type: ignore[union-attr]

            assert await ScheduleRepository(session).mark_deleted(sched_id, OWNER) is True

            session.expire_all()
            stored = await session.get(Schedule, sched_id)
            assert stored is not None
            assert stored.is_deleted is True
            assert stored.updated_at > before
        finally:
            await _cleanup(session, sched_id)

    async def test_second_mark_deleted_returns_false(self, session: AsyncSession) -> None:
        """Only one of two concurrent deletes may win (the conditional-write guarantee)."""
        sched_id = f"soft-twice-{uuid.uuid4()}"
        try:
            session.add(_make_schedule(sched_id))
            await session.commit()
            repo = ScheduleRepository(session)

            assert await repo.mark_deleted(sched_id, OWNER) is True
            assert await repo.mark_deleted(sched_id, OWNER) is False
        finally:
            await _cleanup(session, sched_id)

    async def test_mark_deleted_rejects_other_owner(self, session: AsyncSession) -> None:
        sched_id = f"soft-owner-{uuid.uuid4()}"
        try:
            session.add(_make_schedule(sched_id))
            await session.commit()

            assert await ScheduleRepository(session).mark_deleted(sched_id, OTHER) is False

            session.expire_all()
            stored = await session.get(Schedule, sched_id)
            assert stored is not None and stored.is_deleted is False
        finally:
            await _cleanup(session, sched_id)

    async def test_delete_owned_refuses_already_soft_deleted_row(self, session: AsyncSession) -> None:
        sched_id = f"soft-then-hard-{uuid.uuid4()}"
        try:
            session.add(_make_schedule(sched_id, is_deleted=True))
            await session.commit()

            assert await ScheduleRepository(session).delete_owned(sched_id, OWNER) is False
            assert await session.get(Schedule, sched_id) is not None
        finally:
            await _cleanup(session, sched_id)


class TestRaceFallbackOnRealSession:
    """The hard→soft fallback must work on a real session, not just against mocks.

    PostgreSQL aborts the whole transaction when a statement fails, so the follow-up UPDATE
    after the FK-blocked DELETE only succeeds if the failed transaction is rolled back first.
    A run appearing between the count and the delete is simulated by forcing the count to 0
    while a run really exists.
    """

    async def test_soft_deletes_and_preserves_the_new_run(self, session: AsyncSession) -> None:
        from unittest.mock import MagicMock

        from src.lib.services.schedule_service import ScheduleService

        sched_id = f"race-{uuid.uuid4()}"
        try:
            session.add(_make_schedule(sched_id))
            await session.commit()
            session.add(_make_run(sched_id))
            await session.commit()

            run_repo = ScheduleRunRepository(session)
            real_count = run_repo.count_for_schedule
            observed: list[int] = []

            async def stale_then_real(target_id: str) -> int:
                """Report 0 on the first call, then delegate to the real query.

                The second call runs *after* the FK-blocked DELETE and its rollback, so this
                also proves the session is usable for reads again, not just for the UPDATE.
                """
                count = 0 if not observed else await real_count(target_id)
                observed.append(count)
                return count

            run_repo.count_for_schedule = stale_then_real  # type: ignore[assignment]
            service = ScheduleService(ScheduleRepository(session), run_repo, MagicMock())

            await service.delete(sched_id, OWNER)

            assert observed == [0, 1], "the fallback must re-count against the database"
            session.expire_all()
            stored = await session.get(Schedule, sched_id)
            assert stored is not None, "the schedule must survive as a soft delete"
            assert stored.is_deleted is True
            total, _ = await ScheduleRunRepository(session).list_for_schedule(sched_id, limit=10, offset=0)
            assert total == 1, "the concurrently created run must be preserved"
        finally:
            await session.rollback()
            await _cleanup(session, sched_id)


class TestSoftDeletedRemainsReadable:
    async def test_get_still_returns_soft_deleted_schedule(self, session: AsyncSession) -> None:
        """FR-016 depends on `get` not filtering is_deleted — pin it so it cannot regress."""
        sched_id = f"read-{uuid.uuid4()}"
        try:
            session.add(_make_schedule(sched_id, is_deleted=True))
            await session.commit()

            row = await ScheduleRepository(session).get(sched_id, OWNER)

            assert row is not None
            assert row[0].is_deleted is True
            assert row[1] is None
        finally:
            await _cleanup(session, sched_id)

    async def test_run_history_of_soft_deleted_schedule_is_listable(self, session: AsyncSession) -> None:
        sched_id = f"read-runs-{uuid.uuid4()}"
        try:
            session.add(_make_schedule(sched_id))
            await session.commit()
            session.add(_make_run(sched_id))
            await session.commit()
            await ScheduleRepository(session).mark_deleted(sched_id, OWNER)

            total, runs = await ScheduleRunRepository(session).list_for_schedule(sched_id, limit=10, offset=0)

            assert total == 1
            assert runs[0].schedule_id == sched_id
        finally:
            await _cleanup(session, sched_id)


class TestListDeletedFilter:
    async def test_default_excludes_soft_deleted(self, session: AsyncSession) -> None:
        live, deleted = f"list-live-{uuid.uuid4()}", f"list-del-{uuid.uuid4()}"
        try:
            session.add(_make_schedule(live))
            session.add(_make_schedule(deleted, is_deleted=True))
            await session.commit()

            total, rows = await ScheduleRepository(session).list(OWNER, limit=50, offset=0)

            ids = {row[0].id for row in rows}
            assert live in ids
            assert deleted not in ids
            assert total == len(rows)
        finally:
            await _cleanup(session, live, deleted)

    async def test_is_deleted_true_returns_only_deleted(self, session: AsyncSession) -> None:
        live, deleted = f"list2-live-{uuid.uuid4()}", f"list2-del-{uuid.uuid4()}"
        try:
            session.add(_make_schedule(live))
            session.add(_make_schedule(deleted, is_deleted=True))
            await session.commit()

            _, rows = await ScheduleRepository(session).list(OWNER, limit=50, offset=0, is_deleted=True)

            ids = {row[0].id for row in rows}
            assert deleted in ids
            assert live not in ids
        finally:
            await _cleanup(session, live, deleted)

    async def test_count_reflects_the_filter(self, session: AsyncSession) -> None:
        live, deleted = f"list3-live-{uuid.uuid4()}", f"list3-del-{uuid.uuid4()}"
        owner = f"count-owner-{uuid.uuid4()}"
        try:
            session.add(_make_schedule(live, created_by=owner))
            session.add(_make_schedule(deleted, created_by=owner, is_deleted=True))
            await session.commit()
            repo = ScheduleRepository(session)

            live_total, _ = await repo.list(owner, limit=50, offset=0)
            deleted_total, _ = await repo.list(owner, limit=50, offset=0, is_deleted=True)

            assert live_total == 1
            assert deleted_total == 1
        finally:
            await _cleanup(session, live, deleted)

    async def test_filter_composes_with_name_filter(self, session: AsyncSession) -> None:
        owner = f"compose-owner-{uuid.uuid4()}"
        match, nomatch = f"compose-a-{uuid.uuid4()}", f"compose-b-{uuid.uuid4()}"
        try:
            wanted = _make_schedule(match, created_by=owner, is_deleted=True)
            wanted.display_name = "Nightly digest"
            other = _make_schedule(nomatch, created_by=owner, is_deleted=True)
            other.display_name = "Hourly ping"
            session.add(wanted)
            session.add(other)
            await session.commit()

            total, rows = await ScheduleRepository(session).list(
                owner, limit=50, offset=0, name_filter="digest", is_deleted=True
            )

            assert total == 1
            assert rows[0][0].id == match
        finally:
            await _cleanup(session, match, nomatch)
