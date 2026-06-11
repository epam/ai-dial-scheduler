import pytest
import structlog.testing

from src.worker.jobs import execute_scheduled_job


class TestExecuteScheduledJob:
    @pytest.mark.asyncio
    async def test_logs_schedule_id_and_properties(self):
        with structlog.testing.capture_logs() as captured:
            await execute_scheduled_job(
                schedule_id="test-id-123",
                target_type="chat_completion",
                url="https://example.com",
            )

        assert len(captured) >= 1
        log = captured[0]
        assert log["schedule_id"] == "test-id-123"
        assert "executed_at" in log

    @pytest.mark.asyncio
    async def test_accepts_arbitrary_kwargs(self):
        with structlog.testing.capture_logs() as captured:
            await execute_scheduled_job(
                schedule_id="abc",
                foo="bar",
                baz=42,
            )
        assert captured[0]["schedule_id"] == "abc"
