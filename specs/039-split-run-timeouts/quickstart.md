# Quickstart: Validating Split Run Execution Timeouts

How to prove this feature works end to end. Details of the fields live in [contracts/schedule-properties.md](./contracts/schedule-properties.md); the models and resolution order are in [data-model.md](./data-model.md); the reasoning is in [research.md](./research.md).

## Prerequisites

```bash
uv sync
```

A PostgreSQL instance reachable per `.env` (never read or commit it), and `uv run alembic upgrade head` already applied. **This feature adds no migration**, so `alembic check` must report nothing pending after the change.

## Quality gates

These must all pass before the feature is considered done (Constitution, Development Workflow):

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest --cov=src
```

## The two discriminating tests

Write these first and confirm they fail against `development` (Constitution III). Together they are what distinguishes this design from both the current code and the superseded draft.

### 1. A healthy long stream completes

A streaming executor emits events continuously for longer than `idle_timeout`, with no single gap exceeding it, and finishes under the ceiling.

- **Against current `development`**: fails — the attempt is cut at the single 60 s bound.
- **Expected after the change**: the run succeeds and its result is persisted.

This is the incident (SC-001, SC-002).

### 2. A heartbeating but hung stream is abandoned

The transport stays alive — heartbeat ping comments keep arriving — but no parsed event is delivered for longer than `idle_timeout`.

- **Against current `development`**: fails.
- **Against the superseded transport-level design**: also fails, because pings reset a socket read timeout.
- **Expected after the change**: the attempt is abandoned at the idle bound with a failure naming stream inactivity.

This test is the reason the idle bound is measured on events rather than reads (research.md R2). If it passes trivially, check that the fixture actually emits SSE comments rather than events.

## Supporting unit coverage

| Area | File | What to assert |
|------|------|----------------|
| Settings | `tests/unit/lib/test_settings.py` | All three new fields default to 600.0 / 3600.0 / 60.0 on both HTTP settings classes, each honours its env var, and the streaming ceiling exceeds the general one; a leftover `*_HTTP_TIMEOUT_SECONDS` is ignored and does not affect the run defaults |
| DIAL client | `tests/unit/lib/clients/test_dial_client.py` | Requests pass no per-request `timeout`, so the shared client's per-phase timeouts apply |
| Schema | `tests/unit/lib/schemas/test_schedule_schemas.py` | `idle_timeout` defaults to `None`; positive accepted; zero and negative rejected; `idle_timeout > timeout` rejected only when both set; `idle_timeout` alone accepted; JSON round-trip preserves both |
| Resolution | `tests/unit/worker/test_retry.py` | Precedence for both bounds (property → context `RunTimeouts` → settings); a streaming target gets the longer ceiling and a non-streaming one the tighter; both values appear in the attempt-start log; an idle expiry logs `attempt_idle_timed_out` and is not retried |
| Executors | `tests/unit/worker/executors/test_*.py` | Per-phase timeout assertions: `read` is the ceiling for non-streaming and `connect` is preserved at 5.0 — not overwritten by the ceiling; streaming paths apply the event-level idle bound |
| Launcher | `tests/unit/lib/services/test_run_launcher.py` | The API ceiling default is threaded onto `RunContext` |
| Integration | `tests/integration/test_schedule_timeout_retry.py` | Unaffected in practice: its two cases cover retry-then-success and exhausted-retries, and assert no timeout defaults. Re-run it anyway, since it exercises the orchestrator path end to end |

The connect-phase assertion is easy to omit and is the one that catches a regression to `httpx.Timeout(x)` with a single positional argument.

## Manual verification against the live incident

The authoritative check is schedule `7e2bf7c0-b1bb-4cee-b291-54a338c7500c` ("Weekly USD Exchange Rate Report"), which fails on every run today.

1. Deploy the worker with defaults (`WORKER_HTTP_STREAMING_MAX_DURATION_SECONDS=3600`, `WORKER_HTTP_IDLE_TIMEOUT_SECONDS=60`).
2. Trigger the schedule on demand rather than waiting for its cron firing.
3. Watch the run through the run-detail endpoint and the structured logs.

**Expected**: the run reaches a terminal success state and persists a result.

**Record the wall-clock duration.** No successful end-to-end run of this schedule has ever been observed, so the hour-long ceiling is sized by argument rather than measurement. One completed run turns that into evidence and is tracked as an open item in [plan.md](./plan.md).

Note that an on-demand run executes in the API process under `DRAIN_TIMEOUT_SECONDS = 30.0`. If the API pod restarts mid-run the attempt dies at 30 s regardless of any ceiling — so verify on a stable pod, or use the cron path for a long run.

## Reading the logs

Both resolved bounds appear in the attempt-start entry, so a single log line shows what the attempt was actually running under. The two expiries are distinguishable without reading source (SC-006):

- **Ceiling** — `attempt_timed_out`, message `execution timed out after {n}s`.
- **Idle** — `attempt_idle_timed_out`, message `stream produced no event for {n}s`.

Both are distinct event names, so neither requires message parsing.

Filter on `run_id` or `schedule_id`; both are bound to contextvars in `jobs.py`. Per-attempt detail is logs-only by design (020 R5), so the run row shows only the final outcome.

## Rollback

No migration and no schema removal, so rollback is a deploy of the previous image. Schedules that have `idle_timeout` stored in `kwargs` keep the key, and the previous version reconstructs `ScheduleProperties(**kwargs)` without it: `ScheduleProperties` sets no `model_config`, so Pydantic v2's default `extra="ignore"` applies and the unknown key is dropped rather than raising. A mixed-version window is therefore safe.

The quickest mitigation without a rollback is raising `WORKER_HTTP_IDLE_TIMEOUT_SECONDS`, which loosens stall detection without touching the ceiling.
