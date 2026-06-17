import asyncio

import pytest
from structlog.contextvars import clear_contextvars
import structlog.testing

from src.lib.db.enums import HttpMethod, TargetType
from src.lib.exceptions.errors import ScheduleRunError
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import RetryPolicy, ScheduleProperties
from src.worker.retry import execute_with_policy


CTX = RunContext(run_id="run-1", schedule_id="sched-1")


def make_properties(**kwargs) -> ScheduleProperties:
    defaults = dict(
        target_type=TargetType.REST,
        method=HttpMethod.POST,
        url="https://example.com/api",
        payload={},
        extra_headers={},
    )
    defaults.update(kwargs)
    return ScheduleProperties(**defaults)


class ScriptedExecutor:
    """Executor that returns/raises a scripted step per attempt."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = 0

    async def execute(self, ctx, properties):
        step = self.steps[self.calls]
        self.calls += 1
        if isinstance(step, BaseException):
            raise step
        return step


class SlowExecutor:
    def __init__(self, delay: float):
        self.delay = delay
        self.calls = 0

    async def execute(self, ctx, properties):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return {"status_code": 200, "body": {}}


def _run_error(status_code: int | None) -> ScheduleRunError:
    return ScheduleRunError("boom", schedule_id=CTX.schedule_id, run_id=CTX.run_id, status_code=status_code)


@pytest.fixture(autouse=True)
def reset_context():
    clear_contextvars()
    yield
    clear_contextvars()


@pytest.fixture
def no_sleep(mocker):
    return mocker.patch("src.worker.retry.asyncio.sleep", new=mocker.AsyncMock())


class TestSingleAttempt:
    async def test_success_no_retry(self):
        executor = ScriptedExecutor({"status_code": 200, "body": {"ok": True}})
        props = make_properties()

        with structlog.testing.capture_logs() as logs:
            outcome = await execute_with_policy(CTX, props, executor)

        assert outcome.succeeded is True
        assert outcome.result == {"status_code": 200, "body": {"ok": True}}
        assert outcome.error is None
        assert outcome.attempt_count == 1
        succeeded = [log for log in logs if log.get("event") == "attempt_succeeded"]
        assert len(succeeded) == 1
        assert succeeded[0]["is_retry"] is False

    async def test_failure_no_retry_policy_single_attempt(self):
        executor = ScriptedExecutor(_run_error(500))
        props = make_properties()

        with structlog.testing.capture_logs() as logs:
            outcome = await execute_with_policy(CTX, props, executor)

        assert outcome.succeeded is False
        assert outcome.error is not None
        assert executor.calls == 1
        assert outcome.attempt_count == 1
        failed = [log for log in logs if log.get("event") == "attempt_failed"]
        assert len(failed) == 1
        assert failed[0]["status_code"] == 500
        assert failed[0]["reason"] == "no_retry_policy"


class TestTimeout:
    async def test_uses_schedule_timeout(self, mocker):
        captured = {}

        async def fake_wait_for(coro, timeout):
            captured["timeout"] = timeout
            return await coro

        mocker.patch("src.worker.retry.asyncio.wait_for", side_effect=fake_wait_for)
        executor = ScriptedExecutor({"status_code": 200, "body": {}})
        props = make_properties(timeout=30)

        with structlog.testing.capture_logs():
            await execute_with_policy(CTX, props, executor)

        assert captured["timeout"] == 30.0

    async def test_uses_default_timeout_when_absent(self, mocker):
        captured = {}

        async def fake_wait_for(coro, timeout):
            captured["timeout"] = timeout
            return await coro

        mocker.patch("src.worker.retry.asyncio.wait_for", side_effect=fake_wait_for)
        mocker.patch(
            "src.worker.retry.get_settings",
            return_value=mocker.MagicMock(worker_http=mocker.MagicMock(timeout_seconds=45.0)),
        )
        executor = ScriptedExecutor({"status_code": 200, "body": {}})
        props = make_properties()

        with structlog.testing.capture_logs():
            await execute_with_policy(CTX, props, executor)

        assert captured["timeout"] == 45.0

    async def test_timeout_is_terminal_failure(self, mocker):
        mocker.patch(
            "src.worker.retry.get_settings",
            return_value=mocker.MagicMock(worker_http=mocker.MagicMock(timeout_seconds=0.01)),
        )
        executor = SlowExecutor(delay=0.5)
        props = make_properties()

        with structlog.testing.capture_logs() as logs:
            outcome = await execute_with_policy(CTX, props, executor)

        assert outcome.succeeded is False
        assert outcome.error is not None
        assert outcome.attempt_count == 1
        assert any(log.get("event") == "attempt_timed_out" for log in logs)

    async def test_timeout_not_retried_even_with_retry_policy(self, mocker):
        # Real asyncio.sleep is used here so wait_for actually times out; no backoff
        # sleep occurs because a timeout is terminal (not retried).
        mocker.patch(
            "src.worker.retry.get_settings",
            return_value=mocker.MagicMock(worker_http=mocker.MagicMock(timeout_seconds=0.01)),
        )
        executor = SlowExecutor(delay=0.5)
        props = make_properties(retry=RetryPolicy(interval_sequence=[1, 2], retryable_errors=[500]))

        with structlog.testing.capture_logs():
            outcome = await execute_with_policy(CTX, props, executor)

        assert outcome.succeeded is False
        assert executor.calls == 1


class TestEffectiveTimeoutThreading:
    async def test_sets_ctx_effective_timeout_from_properties(self):
        ctx = RunContext(run_id="run-2", schedule_id="sched-2")
        executor = ScriptedExecutor({"status_code": 200, "body": {}})
        props = make_properties(timeout=42)

        with structlog.testing.capture_logs():
            await execute_with_policy(ctx, props, executor)

        assert ctx.effective_timeout == 42.0

    async def test_sets_ctx_effective_timeout_to_default(self, mocker):
        mocker.patch(
            "src.worker.retry.get_settings",
            return_value=mocker.MagicMock(worker_http=mocker.MagicMock(timeout_seconds=45.0)),
        )
        ctx = RunContext(run_id="run-3", schedule_id="sched-3")
        executor = ScriptedExecutor({"status_code": 200, "body": {}})
        props = make_properties()

        with structlog.testing.capture_logs():
            await execute_with_policy(ctx, props, executor)

        assert ctx.effective_timeout == 45.0

    async def test_decision_logs_carry_ids(self, no_sleep):
        executor = ScriptedExecutor(_run_error(500), {"status_code": 200, "body": {}})
        props = make_properties(retry=RetryPolicy(interval_sequence=[3], retryable_errors=[500]))

        with structlog.testing.capture_logs() as logs:
            await execute_with_policy(CTX, props, executor)

        decision_logs = [log for log in logs if log.get("event") in ("attempt_start", "retry_scheduled")]
        assert decision_logs
        for log in decision_logs:
            assert log["schedule_id"] == CTX.schedule_id
            assert log["run_id"] == CTX.run_id


class TestRetry:
    async def test_retry_then_success(self, no_sleep):
        executor = ScriptedExecutor(_run_error(500), {"status_code": 200, "body": {}})
        props = make_properties(retry=RetryPolicy(interval_sequence=[2, 5], retryable_errors=[500]))

        with structlog.testing.capture_logs() as logs:
            outcome = await execute_with_policy(CTX, props, executor)

        assert outcome.succeeded is True
        assert executor.calls == 2
        assert outcome.attempt_count == 2
        succeeded = [log for log in logs if log.get("event") == "attempt_succeeded"]
        assert succeeded[-1]["is_retry"] is True
        no_sleep.assert_awaited_once_with(2)

    async def test_exhausted_retries(self, no_sleep):
        executor = ScriptedExecutor(_run_error(500), _run_error(500), _run_error(500), _run_error(500))
        props = make_properties(retry=RetryPolicy(interval_sequence=[2, 5, 8], retryable_errors=[500, 502]))

        with structlog.testing.capture_logs() as logs:
            outcome = await execute_with_policy(CTX, props, executor)

        assert outcome.succeeded is False
        assert executor.calls == 4
        assert outcome.attempt_count == 4
        assert [c.args[0] for c in no_sleep.await_args_list] == [2, 5, 8]
        assert any(log.get("event") == "last_retry_failed" for log in logs)

    async def test_non_retryable_status_immediate_failure(self, no_sleep):
        executor = ScriptedExecutor(_run_error(403))
        props = make_properties(retry=RetryPolicy(interval_sequence=[2, 5], retryable_errors=[500]))

        with structlog.testing.capture_logs() as logs:
            outcome = await execute_with_policy(CTX, props, executor)

        assert outcome.succeeded is False
        assert executor.calls == 1
        no_sleep.assert_not_awaited()
        assert any(log.get("event") == "attempt_failed_non_retryable" for log in logs)

    async def test_retries_count_equals_backoff_length(self, no_sleep):
        executor = ScriptedExecutor(*([_run_error(500)] * 3))
        props = make_properties(retry=RetryPolicy(interval_sequence=[1, 1], retryable_errors=[500]))

        with structlog.testing.capture_logs():
            outcome = await execute_with_policy(CTX, props, executor)

        assert outcome.succeeded is False
        assert executor.calls == 3  # 1 initial + 2 retries
        assert no_sleep.await_count == 2

    async def test_missing_status_code_not_retried(self, no_sleep):
        executor = ScriptedExecutor(_run_error(None))
        props = make_properties(retry=RetryPolicy(interval_sequence=[2], retryable_errors=[500]))

        with structlog.testing.capture_logs():
            outcome = await execute_with_policy(CTX, props, executor)

        assert outcome.succeeded is False
        assert executor.calls == 1
        no_sleep.assert_not_awaited()


class TestLogging:
    async def test_attempt_start_logged_with_metadata(self, no_sleep):
        executor = ScriptedExecutor(_run_error(500), {"status_code": 200, "body": {}})
        props = make_properties(timeout=12, retry=RetryPolicy(interval_sequence=[3], retryable_errors=[500]))

        with structlog.testing.capture_logs() as logs:
            await execute_with_policy(CTX, props, executor)

        starts = [log for log in logs if log.get("event") == "attempt_start"]
        assert len(starts) == 2
        assert starts[0]["is_retry"] is False
        assert starts[0]["timeout"] == 12.0
        assert starts[1]["is_retry"] is True

    async def test_retry_scheduled_logged_with_backoff_and_status(self, no_sleep):
        executor = ScriptedExecutor(_run_error(502), {"status_code": 200, "body": {}})
        props = make_properties(retry=RetryPolicy(interval_sequence=[7], retryable_errors=[502]))

        with structlog.testing.capture_logs() as logs:
            await execute_with_policy(CTX, props, executor)

        scheduled = [log for log in logs if log.get("event") == "retry_scheduled"]
        assert len(scheduled) == 1
        assert scheduled[0]["backoff_seconds"] == 7
        assert scheduled[0]["status_code"] == 502
