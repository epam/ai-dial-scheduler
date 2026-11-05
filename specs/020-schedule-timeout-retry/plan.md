# Implementation Plan: Schedule Timeout & Retry

**Branch**: `020-schedule-timeout-retry` | **Date**: 2026-06-17 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/020-schedule-timeout-retry/spec.md`

## Summary

Add two optional, per-schedule execution controls — `timeout` (integer seconds) and `retry` (`{backoff_interval, retryable_errors}`) — that govern how each scheduled job runs. When absent, the system keeps today's behaviour: the default worker HTTP timeout and a single attempt. Both controls live on the existing `ScheduleProperties` schema, so they ride the existing `schedules.kwargs` JSONB column and the run `schedule_snapshot` to the worker with **no database migration**. The worker gains a retry/timeout orchestrator that wraps each attempt with a per-attempt total timeout (`asyncio.wait_for`), retries only on target-returned HTTP status codes listed in `retryable_errors`, waits the configured backoff between attempts, and emits structured logs for every attempt (labelling each retry and the final last-retry failure). Per-attempt detail lives in logs only — the run row persists just the final outcome, exactly as today (no DB or migration change).

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: FastAPI, APScheduler, SQLAlchemy/SQLModel, Pydantic v2, httpx, httpx-sse, openai (Azure), aidial-sdk (telemetry), structlog

**Storage**: PostgreSQL (via SQLAlchemy async). Reused columns: `schedules.kwargs` (JSONB) and `schedule_runs.result`/`schedule_snapshot` (JSONB). **No new columns, no migration.**

**Testing**: pytest + pytest-asyncio (`asyncio_mode = "auto"`), pytest-mock

**Target Platform**: Linux server — two independently deployable Docker services (`src/api` FastAPI, `src/worker` APScheduler)

**Project Type**: Web service (API + background worker) over a shared `src/lib`

**Performance Goals**: No throughput change. Retry waits are wall-clock sleeps inside an async job; they must not block the event loop (use `asyncio.sleep`).

**Constraints**: `async`/`await` for all I/O; no event-loop blocking; no `assert` in production; no `print()`; mypy clean; ruff clean (line-length 120). `lib/` must not import from `api/` or `worker/`.

**Scale/Scope**: Small, additive change. ~2 schema additions, 1 new worker module (retry/timeout orchestrator), executor + exception enrichment to carry HTTP status codes, and logging.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Reliability First | ✅ PASS | Feature is explicitly about proper retry/timeout handling. Retry waits use `asyncio.sleep` (no event-loop blocking). Timeout enforced per attempt via `asyncio.wait_for`. Every attempt outcome is logged; the run's final outcome is persisted; no data loss. |
| II. Observability (NON-NEGOTIABLE) | ✅ PASS (with deviation) | Each attempt, retry, backoff wait, and final failure emits a structured `logger` entry with `schedule_id`, `run_id`, attempt number, decision/reason. **Per product decision, per-attempt detail is recorded in logs only, not the DB** — a deliberate deviation from principle II's "persist all attempts"; the run row still persists the final outcome. Existing OTLP spans retained. No `print()`. |
| III. Test-First (NON-NEGOTIABLE) | ✅ PASS | TDD enforced in `/speckit-implement`: unit tests for retry decision logic, backoff sequencing, timeout, validation; integration tests for end-to-end retry/success/timeout; contract tests for the schema additions. Tests written and failing before implementation. |
| IV. Contract-Driven API | ✅ PASS | `timeout`/`retry` added to the Pydantic `ScheduleProperties` contract (request + response). Additive, non-breaking → no major version bump. Worker/API still exchange typed Pydantic models, not ad-hoc dicts. Contract captured in `contracts/`. |
| V. Simplicity & YAGNI | ✅ PASS | No migration; reuses existing JSONB carriers and repository methods. One small new module for the retry loop. No new abstraction layers. Functions stay within the 2–3 parameter rule by grouping data in Pydantic models. |

**Result**: PASS (initial). Re-evaluated after Phase 1 design — still PASS (see end of plan). No entries required in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/020-schedule-timeout-retry/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── schedule-properties.md   # Updated ScheduleProperties + RetryPolicy contract
└── checklists/
    └── requirements.md  # Spec quality checklist (from /speckit-specify)
```

### Source Code (repository root)

```text
src/
├── lib/
│   ├── schemas/
│   │   └── schedule.py          # CHANGE: add RetryPolicy model; add timeout & retry to ScheduleProperties + validators
│   ├── exceptions/
│   │   └── errors.py            # CHANGE: ScheduleRunError carries optional status_code
│   ├── repositories/
│   │   └── schedule_run_repository.py  # UNCHANGED: final-outcome persistence only (no attempt history)
│   ├── settings.py              # READ: worker_http.timeout_seconds is the default timeout
│   └── constants.py             # CHANGE (if needed): log event-name constants
└── worker/
    ├── retry.py                 # NEW: per-attempt timeout + retry orchestration (the core of this feature)
    ├── jobs.py                  # CHANGE: delegate execution to the orchestrator; persist final outcome only
    └── executors/
        ├── rest.py              # CHANGE: capture HTTP status code on error; apply per-attempt timeout
        ├── sse.py               # CHANGE: capture HTTP status code on error; apply per-attempt timeout
        └── chat_completion.py   # CHANGE: capture status code from OpenAI error; apply per-attempt timeout

tests/
├── lib/
│   ├── schemas/test_schedule.py             # NEW/EXTEND: validation of timeout & retry
│   └── repositories/test_schedule_run_repository.py  # UNCHANGED behaviour (final-outcome persistence)
└── worker/
    ├── test_retry.py                        # NEW: retry decision, backoff sequencing, timeout (unit)
    ├── test_jobs.py                         # EXTEND: orchestration + persistence wiring
    └── executors/test_*                     # EXTEND: status-code capture + timeout passthrough
```

**Structure Decision**: Existing web-service layout (`src/api` + `src/worker` + shared `src/lib`). All schema/validation/exception/persistence changes go in `src/lib` (shared); all execution-behaviour changes (timeout enforcement, retry loop, status-code capture) go in `src/worker`. No API endpoint logic changes — the additions flow through the existing `ScheduleRequest.properties` → `kwargs` → worker path.

## Key Design Decisions (see research.md for rationale)

1. **`timeout` and `retry` live on `ScheduleProperties`** (not a new top-level request field). They automatically serialize into `schedules.kwargs`, into the run `schedule_snapshot`, into `ScheduleResponse.properties`, and are reconstructed in the worker via the existing `ScheduleProperties(**kwargs)` call. No endpoint or service-layer change required.
2. **No DB migration and no run-schema change.** `timeout`/`retry` persist in the existing `schedules.kwargs` JSONB; the run `result` is unchanged (final outcome only). Per-attempt detail is logs-only.
3. **One `ScheduleRun` per firing, persisting only the final outcome** (run `result` shape unchanged). Per-attempt detail is emitted to **structured logs only** and is intentionally **not** written to the database (explicit product decision). The orchestrator returns an in-memory `attempt_count` used solely for the run-summary log line.
4. **Timeout is enforced per attempt as a total wall-clock cap** via `asyncio.wait_for` in the orchestrator, and also passed to the underlying HTTP/SDK call so longer-than-default timeouts are honoured. Default when unset = `worker_http.timeout_seconds`.
5. **Retry triggers only on target-returned HTTP status codes** in `retryable_errors`. To match, `ScheduleRunError` is enriched with an optional `status_code`; executors populate it from `httpx.HTTPStatusError` / OpenAI status errors. Timeouts and transport errors carry no status code and are therefore terminal (per spec Assumptions).
6. **Backoff list defines both count and waits**: `len(backoff_interval)` retries; before retry *i* the orchestrator `asyncio.sleep(backoff_interval[i])`.

## Complexity Tracking

No constitution violations. No entries required.
