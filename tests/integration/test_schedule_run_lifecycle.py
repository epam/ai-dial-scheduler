"""Integration tests for the full schedule run lifecycle (in_progress → success/error)."""

from pydantic import SecretStr
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
import structlog.testing

from src.lib.db.enums import HttpMethod, ScheduleRunStatus, TargetType
from src.lib.models.schedule import Schedule
from src.lib.models.schedule_run import ScheduleRun
from src.lib.schemas.credentials import OboCredentials


pytestmark = pytest.mark.asyncio(loop_scope="module")


SCHEDULE_ID = "sched-lifecycle-001"
FAIL_SCHEDULE_ID = "sched-lifecycle-fail-001"


@pytest.fixture(autouse=True)
def mock_obo_credentials(mocker):
    """Stub the DIAL OBO credentials call so these tests never hit a real DIAL instance."""
    creds = OboCredentials(header_name="Api-Key", header_value=SecretStr("obo-token"))
    dial_client = mocker.MagicMock()
    dial_client.get_obo_credentials = mocker.AsyncMock(return_value=creds)
    mocker.patch("src.worker.jobs.DialClient", return_value=dial_client)
    mocker.patch("src.worker.jobs.HttpClientProvider")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def seeded_schedules(engine):
    """Insert Schedule rows required by FK constraint; remove them after the module."""
    rows = [
        Schedule(
            id=SCHEDULE_ID,
            display_name="Lifecycle test schedule",
            created_by="test",
            service_id="dial-native",
            trigger={},
            kwargs={},
        ),
        Schedule(
            id=FAIL_SCHEDULE_ID,
            display_name="Lifecycle fail schedule",
            created_by="test",
            service_id="dial-native",
            trigger={},
            kwargs={},
        ),
    ]
    async with AsyncSession(engine, expire_on_commit=False) as session:
        for row in rows:
            session.add(row)
        await session.commit()

    yield

    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.execute(
            sa.delete(ScheduleRun).where(ScheduleRun.schedule_id.in_([SCHEDULE_ID, FAIL_SCHEDULE_ID]))
        )
        await session.execute(
            sa.delete(Schedule).where(Schedule.id.in_([SCHEDULE_ID, FAIL_SCHEDULE_ID]))  # type: ignore[attr-defined]
        )
        await session.commit()


async def _get_run_rows(session: AsyncSession, schedule_id: str) -> list[ScheduleRun]:
    result = await session.execute(select(ScheduleRun).where(ScheduleRun.schedule_id == schedule_id))
    return list(result.scalars().all())


class TestSuccessRunLifecycle:
    async def test_run_goes_from_in_progress_to_success(self, engine, seeded_schedules, mocker):
        from src.lib.db.engine import Database
        from src.worker.jobs import execute_scheduled_job

        sessionmaker_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

        db = Database()
        db._engine = engine
        db._sessionmaker = sessionmaker_factory

        result_payload = {"status_code": 200, "body": {"ok": True}}
        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(return_value=result_payload)
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs() as logs:
            await execute_scheduled_job(
                schedule_id=SCHEDULE_ID,
                target_type=TargetType.REST,
                method=HttpMethod.POST,
                url="https://example.com/api",
                payload={},
                extra_headers={},
            )

        async with sessionmaker_factory() as session:
            rows = await _get_run_rows(session, SCHEDULE_ID)

        assert len(rows) == 1
        row = rows[0]
        assert row.status == ScheduleRunStatus.SUCCESS.value
        assert row.end_time is not None
        assert row.result == result_payload
        assert any(log.get("event") == "run_recorded_success" for log in logs)

        db._engine = None
        db._sessionmaker = None


class TestFailureRunLifecycle:
    async def test_run_goes_from_in_progress_to_error(self, engine, seeded_schedules, mocker):
        from src.lib.db.engine import Database
        from src.lib.exceptions.errors import ScheduleRunError
        from src.worker.jobs import execute_scheduled_job

        sessionmaker_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

        db = Database()
        db._engine = engine
        db._sessionmaker = sessionmaker_factory

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(
            side_effect=lambda ctx, _: (_ for _ in ()).throw(
                ScheduleRunError("transport error", schedule_id=ctx.schedule_id, run_id=ctx.run_id)
            )
        )
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs() as logs:
            with pytest.raises(ScheduleRunError):
                await execute_scheduled_job(
                    schedule_id=FAIL_SCHEDULE_ID,
                    target_type=TargetType.REST,
                    method=HttpMethod.POST,
                    url="https://example.com/fail",
                    payload={},
                    extra_headers={},
                )

        async with sessionmaker_factory() as session:
            rows = await _get_run_rows(session, FAIL_SCHEDULE_ID)

        assert len(rows) == 1
        row = rows[0]
        assert row.status == ScheduleRunStatus.ERROR.value
        assert row.end_time is not None
        assert row.result is not None
        assert "error_text" in row.result
        assert any(log.get("event") == "run_recorded_error" for log in logs)

        db._engine = None
        db._sessionmaker = None
