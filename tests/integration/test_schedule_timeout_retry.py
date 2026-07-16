"""Integration tests for per-schedule timeout/retry against a real DB.

Attempt detail is reflected in logs only; the persisted run row stores just the final
outcome (success payload or error_text), never per-attempt history.
"""

from pydantic import SecretStr
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
import structlog.testing

from src.lib.db.enums import HttpMethod, ScheduleRunStatus, TargetType
from src.lib.exceptions.errors import ScheduleRunError
from src.lib.models.schedule import Schedule
from src.lib.models.schedule_run import ScheduleRun
from src.lib.schemas.credentials import OboCredentials


pytestmark = pytest.mark.asyncio(loop_scope="module")


@pytest.fixture(autouse=True)
def mock_obo_credentials(mocker):
    """Stub the DIAL OBO credentials call so these tests never hit a real DIAL instance.

    Schedule owner/service resolution still goes through the real seeded DB row.
    """
    creds = OboCredentials(header_name="Api-Key", header_value=SecretStr("obo-token"))
    dial_client = mocker.MagicMock()
    dial_client.get_obo_credentials = mocker.AsyncMock(return_value=creds)
    mocker.patch("src.worker.jobs.DialClient", return_value=dial_client)
    mocker.patch("src.worker.jobs.HttpClientProvider")


RETRY_SUCCESS_ID = "sched-retry-success-001"
RETRY_EXHAUST_ID = "sched-retry-exhaust-001"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def seeded_schedules(engine):
    rows = [
        Schedule(
            id=RETRY_SUCCESS_ID,
            display_name="retry success",
            created_by="test",
            service_id="dial-oauth",
            trigger={},
            kwargs={},
        ),
        Schedule(
            id=RETRY_EXHAUST_ID,
            display_name="retry exhaust",
            created_by="test",
            service_id="dial-oauth",
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
            sa.delete(ScheduleRun).where(ScheduleRun.schedule_id.in_([RETRY_SUCCESS_ID, RETRY_EXHAUST_ID]))
        )
        await session.execute(
            sa.delete(Schedule).where(Schedule.id.in_([RETRY_SUCCESS_ID, RETRY_EXHAUST_ID]))  # type: ignore[attr-defined]
        )
        await session.commit()


async def _get_run_rows(session: AsyncSession, schedule_id: str) -> list[ScheduleRun]:
    result = await session.execute(select(ScheduleRun).where(ScheduleRun.schedule_id == schedule_id))
    return list(result.scalars().all())


class TestRetryRunPersistence:
    async def test_retry_then_success_persists_final_result_only(self, engine, seeded_schedules, mocker):
        from src.lib.db.engine import Database
        from src.worker.jobs import execute_scheduled_job

        sessionmaker_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        db = Database()
        db._engine = engine
        db._sessionmaker = sessionmaker_factory

        mocker.patch("src.worker.retry.asyncio.sleep", new=mocker.AsyncMock())
        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(
            side_effect=[
                ScheduleRunError("500", schedule_id=RETRY_SUCCESS_ID, run_id="r", status_code=500),
                {"status_code": 200, "body": {"ok": True}},
            ]
        )
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs() as logs:
            await execute_scheduled_job(
                schedule_id=RETRY_SUCCESS_ID,
                target_type=TargetType.REST,
                method=HttpMethod.POST,
                url="https://example.com/api",
                payload={},
                extra_headers={},
                retry={"interval_sequence": [2], "retryable_errors": [500]},
            )

        async with sessionmaker_factory() as session:
            rows = await _get_run_rows(session, RETRY_SUCCESS_ID)

        assert len(rows) == 1
        row = rows[0]
        assert row.status == ScheduleRunStatus.SUCCESS.value
        # Final executor payload persisted; no per-attempt history in the DB.
        assert row.result == {"status_code": 200, "body": {"ok": True}}
        assert "attempts" not in row.result
        # Attempts are reflected only in logs.
        assert any(log.get("event") == "retry_scheduled" for log in logs)
        assert any(log.get("event") == "run_complete" and log.get("attempts") == 2 for log in logs)

        db._engine = None
        db._sessionmaker = None

    async def test_exhausted_retries_persists_error_only(self, engine, seeded_schedules, mocker):
        from src.lib.db.engine import Database
        from src.worker.jobs import execute_scheduled_job

        sessionmaker_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        db = Database()
        db._engine = engine
        db._sessionmaker = sessionmaker_factory

        mocker.patch("src.worker.retry.asyncio.sleep", new=mocker.AsyncMock())
        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(
            side_effect=lambda ctx, _: (_ for _ in ()).throw(
                ScheduleRunError("502", schedule_id=ctx.schedule_id, run_id=ctx.run_id, status_code=502)
            )
        )
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs() as logs:
            with pytest.raises(ScheduleRunError):
                await execute_scheduled_job(
                    schedule_id=RETRY_EXHAUST_ID,
                    target_type=TargetType.REST,
                    method=HttpMethod.POST,
                    url="https://example.com/api",
                    payload={},
                    extra_headers={},
                    retry={"interval_sequence": [1, 1, 1], "retryable_errors": [502]},
                )

        async with sessionmaker_factory() as session:
            rows = await _get_run_rows(session, RETRY_EXHAUST_ID)

        assert len(rows) == 1
        row = rows[0]
        assert row.status == ScheduleRunStatus.ERROR.value
        assert row.result is not None
        assert "error_text" in row.result
        assert "attempts" not in row.result
        # 1 initial + 3 retries reflected in logs only.
        assert any(log.get("event") == "last_retry_failed" for log in logs)
        assert any(log.get("event") == "run_failed" and log.get("attempts") == 4 for log in logs)

        db._engine = None
        db._sessionmaker = None
