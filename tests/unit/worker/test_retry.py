import asyncio

from pydantic import SecretStr
import pytest
from structlog.contextvars import clear_contextvars
import structlog.testing

from src.lib.db.enums import HttpMethod, TargetType
from src.lib.exceptions.errors import ScheduleRunError, StreamIdleTimeoutError
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext, RunTimeouts
from src.lib.schemas.schedule import RetryPolicy, ScheduleProperties
from src.worker.retry import execute_with_policy


CTX = RunContext(run_id="run-1", schedule_id="sched-1")
CREDS = OboCredentials(header_name="Api-Key", header_value=SecretStr("t"))


def _worker_http(mocker, **overrides):
    """WorkerHttpSettings stand-in carrying every bound the orchestrator reads."""
    defaults = dict(max_duration_seconds=300.0, streaming_max_duration_seconds=3600.0, idle_timeout_seconds=60.0)
    defaults.update(overrides)
    return mocker.MagicMock(**defaults)


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

    async def execute(self, ctx, credentials, properties):
        step = self.steps[self.calls]
        self.calls += 1
        if isinstance(step, BaseException):
            raise step
        return step


class SlowExecutor:
    def __init__(self, delay: float):
        self.delay = delay
        self.calls = 0

    async def execute(self, ctx, credentials, properties):
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
            outcome = await execute_with_policy(CTX, CREDS, props, executor)

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
            outcome = await execute_with_policy(CTX, CREDS, props, executor)

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
            await execute_with_policy(CTX, CREDS, props, executor)

        assert captured["timeout"] == 30.0

    async def test_uses_default_timeout_when_absent(self, mocker):
        captured = {}

        async def fake_wait_for(coro, timeout):
            captured["timeout"] = timeout
            return await coro

        mocker.patch("src.worker.retry.asyncio.wait_for", side_effect=fake_wait_for)
        mocker.patch(
            "src.worker.retry.get_settings",
            return_value=mocker.MagicMock(worker_http=_worker_http(mocker, max_duration_seconds=45.0)),
        )
        executor = ScriptedExecutor({"status_code": 200, "body": {}})
        props = make_properties()

        with structlog.testing.capture_logs():
            await execute_with_policy(CTX, CREDS, props, executor)

        assert captured["timeout"] == 45.0

    async def test_timeout_is_terminal_failure(self, mocker):
        mocker.patch(
            "src.worker.retry.get_settings",
            return_value=mocker.MagicMock(worker_http=_worker_http(mocker, max_duration_seconds=0.01)),
        )
        executor = SlowExecutor(delay=0.5)
        props = make_properties()

        with structlog.testing.capture_logs() as logs:
            outcome = await execute_with_policy(CTX, CREDS, props, executor)

        assert outcome.succeeded is False
        assert outcome.error is not None
        assert outcome.attempt_count == 1
        assert any(log.get("event") == "attempt_timed_out" for log in logs)

    async def test_timeout_not_retried_even_with_retry_policy(self, mocker):
        # Real asyncio.sleep is used here so wait_for actually times out; no backoff
        # sleep occurs because a timeout is terminal (not retried).
        mocker.patch(
            "src.worker.retry.get_settings",
            return_value=mocker.MagicMock(worker_http=_worker_http(mocker, max_duration_seconds=0.01)),
        )
        executor = SlowExecutor(delay=0.5)
        props = make_properties(retry=RetryPolicy(interval_sequence=[1, 2], retryable_errors=[500]))

        with structlog.testing.capture_logs():
            outcome = await execute_with_policy(CTX, CREDS, props, executor)

        assert outcome.succeeded is False
        assert executor.calls == 1


class TestEffectiveTimeoutThreading:
    async def test_sets_ctx_effective_timeout_from_properties(self):
        ctx = RunContext(run_id="run-2", schedule_id="sched-2")
        executor = ScriptedExecutor({"status_code": 200, "body": {}})
        props = make_properties(timeout=42)

        with structlog.testing.capture_logs():
            await execute_with_policy(ctx, CREDS, props, executor)

        assert ctx.effective_timeout == 42.0

    async def test_sets_ctx_effective_timeout_to_default(self, mocker):
        mocker.patch(
            "src.worker.retry.get_settings",
            return_value=mocker.MagicMock(worker_http=_worker_http(mocker, max_duration_seconds=45.0)),
        )
        ctx = RunContext(run_id="run-3", schedule_id="sched-3")
        executor = ScriptedExecutor({"status_code": 200, "body": {}})
        props = make_properties()

        with structlog.testing.capture_logs():
            await execute_with_policy(ctx, CREDS, props, executor)

        assert ctx.effective_timeout == 45.0

    async def test_decision_logs_carry_ids(self, no_sleep):
        executor = ScriptedExecutor(_run_error(500), {"status_code": 200, "body": {}})
        props = make_properties(retry=RetryPolicy(interval_sequence=[3], retryable_errors=[500]))

        with structlog.testing.capture_logs() as logs:
            await execute_with_policy(CTX, CREDS, props, executor)

        decision_logs = [log for log in logs if log.get("event") in ("attempt_start", "retry_scheduled")]
        assert decision_logs
        for log in decision_logs:
            assert log["schedule_id"] == CTX.schedule_id
            assert log["run_id"] == CTX.run_id


class TestCeilingResolution:
    async def test_prefers_context_timeouts_over_settings(self, mocker):
        """On-demand runs carry the API process bounds; the worker's must not win."""
        mocker.patch("src.worker.retry.get_settings", return_value=mocker.MagicMock(worker_http=_worker_http(mocker)))
        ctx = RunContext(
            run_id="run-4",
            schedule_id="sched-4",
            timeouts=RunTimeouts(max_duration=120.0, streaming_max_duration=240.0, idle_timeout=20.0),
        )

        with structlog.testing.capture_logs():
            await execute_with_policy(ctx, CREDS, make_properties(), ScriptedExecutor({"status_code": 200, "body": {}}))

        assert ctx.effective_timeout == 120.0
        assert ctx.effective_idle_timeout == 20.0

    async def test_schedule_timeout_wins_over_context_default(self, mocker):
        ctx = RunContext(
            run_id="run-5",
            schedule_id="sched-5",
            timeouts=RunTimeouts(max_duration=120.0, streaming_max_duration=240.0, idle_timeout=20.0),
        )

        with structlog.testing.capture_logs():
            await execute_with_policy(
                ctx, CREDS, make_properties(timeout=42), ScriptedExecutor({"status_code": 200, "body": {}})
            )

        assert ctx.effective_timeout == 42.0

    async def test_non_streaming_target_gets_the_tighter_ceiling(self, mocker):
        """A rest target has no progress signal, so its only instrument stays tight."""
        mocker.patch("src.worker.retry.get_settings", return_value=mocker.MagicMock(worker_http=_worker_http(mocker)))
        ctx = RunContext(run_id="run-11", schedule_id="sched-11")

        with structlog.testing.capture_logs():
            await execute_with_policy(ctx, CREDS, make_properties(), ScriptedExecutor({"status_code": 200, "body": {}}))

        assert ctx.effective_timeout == 300.0

    async def test_streaming_target_earns_the_longer_ceiling(self, mocker):
        mocker.patch("src.worker.retry.get_settings", return_value=mocker.MagicMock(worker_http=_worker_http(mocker)))
        ctx = RunContext(run_id="run-12", schedule_id="sched-12")
        props = ScheduleProperties(
            target_type=TargetType.SSE,
            method=HttpMethod.POST,
            url="https://example.com/stream",
            payload={},
        )

        with structlog.testing.capture_logs():
            await execute_with_policy(ctx, CREDS, props, ScriptedExecutor({"events": []}))

        assert ctx.effective_timeout == 3600.0


class TestIdleTimeoutReporting:
    async def test_idle_expiry_logs_its_own_event(self):
        """FR-011: a ceiling expiry and an idle expiry must be distinguishable in logs without
        reading the message text."""
        executor = ScriptedExecutor(
            StreamIdleTimeoutError("stream produced no event for 60s", schedule_id="sched-1", run_id="run-1")
        )

        with structlog.testing.capture_logs() as logs:
            outcome = await execute_with_policy(CTX, CREDS, make_properties(), executor)

        assert outcome.succeeded is False
        events = [log.get("event") for log in logs]
        assert "attempt_idle_timed_out" in events
        assert "attempt_timed_out" not in events
        assert "attempt_failed" not in events

    async def test_idle_expiry_is_not_retried(self, no_sleep):
        executor = ScriptedExecutor(
            StreamIdleTimeoutError("stream produced no event for 60s", schedule_id="sched-1", run_id="run-1"),
            {"status_code": 200, "body": {}},
        )
        props = make_properties(retry=RetryPolicy(interval_sequence=[1], retryable_errors=[500]))

        with structlog.testing.capture_logs():
            outcome = await execute_with_policy(CTX, CREDS, props, executor)

        assert outcome.succeeded is False
        assert executor.calls == 1


class TestIdleTimeoutResolution:
    async def test_sets_ctx_idle_timeout_from_properties(self):
        ctx = RunContext(run_id="run-7", schedule_id="sched-7")

        with structlog.testing.capture_logs():
            await execute_with_policy(
                ctx, CREDS, make_properties(idle_timeout=15), ScriptedExecutor({"status_code": 200, "body": {}})
            )

        assert ctx.effective_idle_timeout == 15.0

    async def test_sets_ctx_idle_timeout_to_default(self, mocker):
        mocker.patch(
            "src.worker.retry.get_settings",
            return_value=mocker.MagicMock(worker_http=_worker_http(mocker)),
        )
        ctx = RunContext(run_id="run-8", schedule_id="sched-8")

        with structlog.testing.capture_logs():
            await execute_with_policy(ctx, CREDS, make_properties(), ScriptedExecutor({"status_code": 200, "body": {}}))

        assert ctx.effective_idle_timeout == 60.0

    async def test_bounds_are_resolved_independently(self, mocker):
        mocker.patch(
            "src.worker.retry.get_settings",
            return_value=mocker.MagicMock(worker_http=_worker_http(mocker)),
        )
        ctx = RunContext(run_id="run-9", schedule_id="sched-9")

        with structlog.testing.capture_logs():
            await execute_with_policy(
                ctx, CREDS, make_properties(timeout=1800), ScriptedExecutor({"status_code": 200, "body": {}})
            )

        assert ctx.effective_timeout == 1800.0
        assert ctx.effective_idle_timeout == 60.0

    async def test_idle_bound_does_not_cap_the_attempt(self, mocker):
        """The idle bound is enforced inside streaming executors, never by wait_for."""
        captured = {}

        async def fake_wait_for(coro, timeout):
            captured["timeout"] = timeout
            return await coro

        mocker.patch("src.worker.retry.asyncio.wait_for", side_effect=fake_wait_for)
        ctx = RunContext(run_id="run-10", schedule_id="sched-10")

        with structlog.testing.capture_logs():
            await execute_with_policy(
                ctx,
                CREDS,
                make_properties(timeout=1800, idle_timeout=15),
                ScriptedExecutor({"status_code": 200, "body": {}}),
            )

        assert captured["timeout"] == 1800.0

    async def test_attempt_start_logs_both_bounds(self):
        props = make_properties(timeout=1800, idle_timeout=90)

        with structlog.testing.capture_logs() as logs:
            await execute_with_policy(CTX, CREDS, props, ScriptedExecutor({"status_code": 200, "body": {}}))

        starts = [log for log in logs if log.get("event") == "attempt_start"]
        assert starts[0]["timeout"] == 1800.0
        assert starts[0]["idle_timeout"] == 90.0


class TestRetry:
    async def test_retry_then_success(self, no_sleep):
        executor = ScriptedExecutor(_run_error(500), {"status_code": 200, "body": {}})
        props = make_properties(retry=RetryPolicy(interval_sequence=[2, 5], retryable_errors=[500]))

        with structlog.testing.capture_logs() as logs:
            outcome = await execute_with_policy(CTX, CREDS, props, executor)

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
            outcome = await execute_with_policy(CTX, CREDS, props, executor)

        assert outcome.succeeded is False
        assert executor.calls == 4
        assert outcome.attempt_count == 4
        assert [c.args[0] for c in no_sleep.await_args_list] == [2, 5, 8]
        assert any(log.get("event") == "last_retry_failed" for log in logs)

    async def test_non_retryable_status_immediate_failure(self, no_sleep):
        executor = ScriptedExecutor(_run_error(403))
        props = make_properties(retry=RetryPolicy(interval_sequence=[2, 5], retryable_errors=[500]))

        with structlog.testing.capture_logs() as logs:
            outcome = await execute_with_policy(CTX, CREDS, props, executor)

        assert outcome.succeeded is False
        assert executor.calls == 1
        no_sleep.assert_not_awaited()
        assert any(log.get("event") == "attempt_failed_non_retryable" for log in logs)

    async def test_retries_count_equals_backoff_length(self, no_sleep):
        executor = ScriptedExecutor(*([_run_error(500)] * 3))
        props = make_properties(retry=RetryPolicy(interval_sequence=[1, 1], retryable_errors=[500]))

        with structlog.testing.capture_logs():
            outcome = await execute_with_policy(CTX, CREDS, props, executor)

        assert outcome.succeeded is False
        assert executor.calls == 3  # 1 initial + 2 retries
        assert no_sleep.await_count == 2

    async def test_missing_status_code_not_retried(self, no_sleep):
        executor = ScriptedExecutor(_run_error(None))
        props = make_properties(retry=RetryPolicy(interval_sequence=[2], retryable_errors=[500]))

        with structlog.testing.capture_logs():
            outcome = await execute_with_policy(CTX, CREDS, props, executor)

        assert outcome.succeeded is False
        assert executor.calls == 1
        no_sleep.assert_not_awaited()


class TestLogging:
    async def test_attempt_start_logged_with_metadata(self, no_sleep):
        executor = ScriptedExecutor(_run_error(500), {"status_code": 200, "body": {}})
        props = make_properties(timeout=12, retry=RetryPolicy(interval_sequence=[3], retryable_errors=[500]))

        with structlog.testing.capture_logs() as logs:
            await execute_with_policy(CTX, CREDS, props, executor)

        starts = [log for log in logs if log.get("event") == "attempt_start"]
        assert len(starts) == 2
        assert starts[0]["is_retry"] is False
        assert starts[0]["timeout"] == 12.0
        assert starts[1]["is_retry"] is True

    async def test_retry_scheduled_logged_with_backoff_and_status(self, no_sleep):
        executor = ScriptedExecutor(_run_error(502), {"status_code": 200, "body": {}})
        props = make_properties(retry=RetryPolicy(interval_sequence=[7], retryable_errors=[502]))

        with structlog.testing.capture_logs() as logs:
            await execute_with_policy(CTX, CREDS, props, executor)

        scheduled = [log for log in logs if log.get("event") == "retry_scheduled"]
        assert len(scheduled) == 1
        assert scheduled[0]["backoff_seconds"] == 7
        assert scheduled[0]["status_code"] == 502


class TestFailureLogLevel:
    """Every failure reaching the orchestrator comes from outside the scheduler, so none of
    them are error level — error stays reserved for the scheduler's own faults."""

    async def test_timed_out_logged_as_warning(self, mocker):
        mocker.patch(
            "src.worker.retry.get_settings",
            return_value=mocker.MagicMock(worker_http=_worker_http(mocker, max_duration_seconds=0.01)),
        )
        props = make_properties()

        with structlog.testing.capture_logs() as logs:
            await execute_with_policy(CTX, CREDS, props, SlowExecutor(delay=0.5))

        timed_out = [log for log in logs if log.get("event") == "attempt_timed_out"]
        assert len(timed_out) == 1
        assert timed_out[0]["log_level"] == "warning"

    @pytest.mark.parametrize("status_code", [404, 500, None])
    async def test_attempt_failed_logged_as_warning(self, status_code):
        props = make_properties()

        with structlog.testing.capture_logs() as logs:
            await execute_with_policy(CTX, CREDS, props, ScriptedExecutor(_run_error(status_code)))

        failed = [log for log in logs if log.get("event") == "attempt_failed"]
        assert len(failed) == 1
        assert failed[0]["log_level"] == "warning"

    @pytest.mark.parametrize("status_code", [403, 503])
    async def test_attempt_failed_non_retryable_logged_as_warning(self, status_code, no_sleep):
        props = make_properties(retry=RetryPolicy(interval_sequence=[2], retryable_errors=[429]))

        with structlog.testing.capture_logs() as logs:
            await execute_with_policy(CTX, CREDS, props, ScriptedExecutor(_run_error(status_code)))

        failed = [log for log in logs if log.get("event") == "attempt_failed_non_retryable"]
        assert len(failed) == 1
        assert failed[0]["log_level"] == "warning"

    async def test_last_retry_failed_logged_as_warning(self, no_sleep):
        executor = ScriptedExecutor(_run_error(500), _run_error(500))
        props = make_properties(retry=RetryPolicy(interval_sequence=[2], retryable_errors=[500]))

        with structlog.testing.capture_logs() as logs:
            await execute_with_policy(CTX, CREDS, props, executor)

        failed = [log for log in logs if log.get("event") == "last_retry_failed"]
        assert len(failed) == 1
        assert failed[0]["log_level"] == "warning"

    async def test_no_error_level_logs_emitted(self, no_sleep):
        executor = ScriptedExecutor(_run_error(500), _run_error(500))
        props = make_properties(retry=RetryPolicy(interval_sequence=[2], retryable_errors=[500]))

        with structlog.testing.capture_logs() as logs:
            await execute_with_policy(CTX, CREDS, props, executor)

        assert [log for log in logs if log.get("log_level") == "error"] == []
