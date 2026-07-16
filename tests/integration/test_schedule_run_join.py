"""Integration tests for schedule LEFT JOIN apscheduler_jobs and RESTRICT FK enforcement."""

from datetime import datetime, timezone
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.db.enums import ScheduleRunStatus
from src.lib.models.apscheduler_job import apscheduler_jobs
from src.lib.models.schedule import Schedule
from src.lib.models.schedule_run import ScheduleRun


pytestmark = pytest.mark.asyncio(loop_scope="module")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_schedule(sched_id: str) -> Schedule:
    return Schedule(
        id=sched_id,
        display_name="Test Schedule",
        created_by="test-user",
        service_id="dial-oauth",
        trigger={"type": "interval", "seconds": 60},
        kwargs={"arg": "value"},
    )


def _make_run(sched_id: str) -> ScheduleRun:
    return ScheduleRun(
        id=uuid.uuid4(),
        status=ScheduleRunStatus.SUCCESS,
        schedule_id=sched_id,
        schedule_snapshot={
            "display_name": "Test Schedule",
            "trigger": {"type": "interval", "seconds": 60},
            "kwargs": {"arg": "value"},
        },
    )


async def _cleanup(session: AsyncSession, sched_id: str) -> None:
    await session.execute(sa.delete(ScheduleRun).where(ScheduleRun.schedule_id == sched_id))
    await session.execute(sa.delete(Schedule).where(Schedule.id == sched_id))
    await session.execute(sa.delete(apscheduler_jobs).where(apscheduler_jobs.c.id == sched_id))
    await session.commit()


# ---------------------------------------------------------------------------
# US1: LEFT JOIN returns schedule metadata + next_run_time
# ---------------------------------------------------------------------------


class TestScheduleJoin:
    """US1: Query schedules with live next-run time via LEFT OUTER JOIN."""

    async def test_join_returns_next_run_time_when_engine_row_exists(self, session: AsyncSession) -> None:
        sched_id = "us1-active-job"
        try:
            session.add(_make_schedule(sched_id))
            await session.execute(apscheduler_jobs.insert().values(id=sched_id, next_run_time=1700000000.0))
            await session.commit()

            stmt = (
                sa.select(Schedule, apscheduler_jobs.c.next_run_time)
                .outerjoin(apscheduler_jobs, Schedule.id == apscheduler_jobs.c.id)
                .where(Schedule.id == sched_id)
            )
            row = (await session.execute(stmt)).one()
            assert row.Schedule.id == sched_id
            assert row.Schedule.display_name == "Test Schedule"
            assert row.next_run_time == pytest.approx(1700000000.0)
        finally:
            await _cleanup(session, sched_id)

    async def test_join_returns_null_next_run_time_when_engine_row_paused(self, session: AsyncSession) -> None:
        sched_id = "us1-paused-job"
        try:
            session.add(_make_schedule(sched_id))
            await session.execute(apscheduler_jobs.insert().values(id=sched_id, next_run_time=None))
            await session.commit()

            stmt = (
                sa.select(Schedule, apscheduler_jobs.c.next_run_time)
                .outerjoin(apscheduler_jobs, Schedule.id == apscheduler_jobs.c.id)
                .where(Schedule.id == sched_id)
            )
            row = (await session.execute(stmt)).one()
            assert row.Schedule.id == sched_id
            assert row.next_run_time is None
        finally:
            await _cleanup(session, sched_id)

    async def test_join_preserves_schedule_row_when_no_engine_row_exists(self, session: AsyncSession) -> None:
        """FR-006: LEFT JOIN must not drop schedule metadata rows when engine row is absent."""
        sched_id = "us1-no-engine-row"
        try:
            session.add(_make_schedule(sched_id))
            await session.commit()

            stmt = (
                sa.select(Schedule, apscheduler_jobs.c.next_run_time)
                .outerjoin(apscheduler_jobs, Schedule.id == apscheduler_jobs.c.id)
                .where(Schedule.id == sched_id)
            )
            row = (await session.execute(stmt)).one()
            assert row.Schedule.id == sched_id
            assert row.next_run_time is None
        finally:
            await _cleanup(session, sched_id)


# ---------------------------------------------------------------------------
# US2: Persist a complete history of schedule runs
# ---------------------------------------------------------------------------


class TestScheduleRunPersistence:
    """US2: All status values and in-progress runs can be stored and read back."""

    @pytest.mark.parametrize("status", list(ScheduleRunStatus))
    async def test_all_status_values_can_be_stored(self, session: AsyncSession, status: ScheduleRunStatus) -> None:
        """SC-002: 100% fidelity for all supported status values."""
        sched_id = f"us2-status-{status.value}"
        try:
            session.add(_make_schedule(sched_id))
            await session.commit()

            run = ScheduleRun(
                id=uuid.uuid4(),
                status=status,
                schedule_id=sched_id,
                schedule_snapshot={"display_name": "Test", "trigger": {}, "kwargs": {}},
            )
            session.add(run)
            await session.commit()

            result = await session.execute(sa.select(ScheduleRun).where(ScheduleRun.id == run.id))
            fetched = result.scalar_one()
            assert fetched.status == status.value
        finally:
            await _cleanup(session, sched_id)

    async def test_in_progress_run_has_no_end_time(self, session: AsyncSession) -> None:
        """SC-002: in_progress runs must tolerate NULL end_time (FR-011)."""
        sched_id = "us2-in-progress"
        try:
            session.add(_make_schedule(sched_id))
            await session.commit()

            run = ScheduleRun(
                id=uuid.uuid4(),
                status=ScheduleRunStatus.IN_PROGRESS,
                end_time=None,
                result=None,
                schedule_id=sched_id,
                schedule_snapshot={"display_name": "Test", "trigger": {}, "kwargs": {}},
            )
            session.add(run)
            await session.commit()

            result = await session.execute(sa.select(ScheduleRun).where(ScheduleRun.id == run.id))
            fetched = result.scalar_one()
            assert fetched.status == ScheduleRunStatus.IN_PROGRESS
            assert fetched.end_time is None
            assert fetched.result is None
        finally:
            await _cleanup(session, sched_id)

    async def test_run_can_transition_to_success_with_end_time_and_result(self, session: AsyncSession) -> None:
        """SC-002: in_progress → success transition with end_time and result populated."""
        sched_id = "us2-transition"
        try:
            session.add(_make_schedule(sched_id))
            await session.commit()

            run = ScheduleRun(
                id=uuid.uuid4(),
                status=ScheduleRunStatus.IN_PROGRESS,
                schedule_id=sched_id,
                schedule_snapshot={"display_name": "Test", "trigger": {}, "kwargs": {}},
            )
            session.add(run)
            await session.commit()

            # Transition to success
            run.status = ScheduleRunStatus.SUCCESS
            run.end_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
            run.result = {"output": "ok"}
            session.add(run)
            await session.commit()

            result = await session.execute(sa.select(ScheduleRun).where(ScheduleRun.id == run.id))
            fetched = result.scalar_one()
            assert fetched.status == ScheduleRunStatus.SUCCESS
            assert fetched.end_time is not None
            assert fetched.result == {"output": "ok"}
        finally:
            await _cleanup(session, sched_id)

    async def test_check_constraint_rejects_invalid_status(self, session: AsyncSession) -> None:
        """FR-010: DB CHECK must reject any status value not in ScheduleRunStatus."""
        sched_id = "us2-bad-status"
        try:
            session.add(_make_schedule(sched_id))
            await session.commit()

            run = ScheduleRun(
                id=uuid.uuid4(),
                status="bogus_status",
                schedule_id=sched_id,
                schedule_snapshot={"display_name": "Test", "trigger": {}, "kwargs": {}},
            )
            session.add(run)
            with pytest.raises(sa.exc.IntegrityError):
                await session.flush()
            await session.rollback()
        finally:
            await _cleanup(session, sched_id)


# ---------------------------------------------------------------------------
# US3: ON DELETE RESTRICT protects run history
# ---------------------------------------------------------------------------


class TestScheduleDeleteProtection:
    """US3: Prevent deletion of a schedule that still has runs."""

    async def test_delete_schedule_with_runs_is_rejected(self, session: AsyncSession) -> None:
        """FR-014, SC-004: RESTRICT FK blocks deletion while runs exist."""
        sched_id = "us3-has-runs"
        try:
            session.add(_make_schedule(sched_id))
            await session.commit()

            session.add(_make_run(sched_id))
            await session.commit()

            with pytest.raises(sa.exc.IntegrityError):
                await session.execute(sa.delete(Schedule).where(Schedule.id == sched_id))
                await session.flush()

            await session.rollback()

            result = await session.execute(sa.select(Schedule).where(Schedule.id == sched_id))
            assert result.scalar_one_or_none() is not None
        finally:
            await _cleanup(session, sched_id)

    async def test_delete_schedule_with_no_runs_succeeds(self, session: AsyncSession) -> None:
        sched_id = "us3-no-runs"
        session.add(_make_schedule(sched_id))
        await session.commit()

        await session.execute(sa.delete(Schedule).where(Schedule.id == sched_id))
        await session.commit()

        result = await session.execute(sa.select(Schedule).where(Schedule.id == sched_id))
        assert result.scalar_one_or_none() is None

    async def test_snapshot_unchanged_after_schedule_edit(self, session: AsyncSession) -> None:
        """FR-012, SC-003: Run snapshot is immutable w.r.t. later schedule edits."""
        sched_id = "us3-snapshot-immutable"
        try:
            schedule = _make_schedule(sched_id)
            session.add(schedule)
            await session.commit()

            run = ScheduleRun(
                id=uuid.uuid4(),
                status=ScheduleRunStatus.SUCCESS,
                schedule_id=sched_id,
                schedule_snapshot={
                    "display_name": "Original Name",
                    "trigger": {"type": "interval", "seconds": 60},
                    "kwargs": {"arg": "value"},
                },
            )
            session.add(run)
            await session.commit()

            schedule.display_name = "Updated Name"
            session.add(schedule)
            await session.commit()

            result = await session.execute(sa.select(ScheduleRun).where(ScheduleRun.id == run.id))
            fetched_run = result.scalar_one()
            assert fetched_run.schedule_snapshot["display_name"] == "Original Name"
        finally:
            await _cleanup(session, sched_id)
