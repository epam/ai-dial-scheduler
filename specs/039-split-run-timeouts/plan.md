# Implementation Plan: Split Run Execution Timeouts

**Branch**: `039-split-run-timeouts` | **Date**: 2026-10-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/039-split-run-timeouts/spec.md`

**Revision note**: This plan supersedes the 2026-10-08 draft, which specified a mandatory 300 s ceiling and implemented the idle bound as an httpx `read` timeout. Both were reversed after the draft was written: the operator rejected a non-resetting ceiling as the primary control, and the DIAL SDK heartbeat was found to reset a transport-level read timeout, defeating the idle bound entirely. See research.md R2 and R3.

## Summary

Separate the single per-attempt `timeout` into two independent controls: an **absolute ceiling** on total attempt duration (the existing `timeout`, unchanged in meaning, now defaulting to an hour and acting only as an emergency backstop) and a new optional **`idle_timeout`** bounding the gap between consecutive stream events.

The idle bound is enforced at the **event layer** — `asyncio.timeout(idle)` wrapped around `anext()` on the stream iterator — not at the transport layer. This is the central correction over the previous draft: the DIAL SDK emits SSE heartbeat pings exactly when the response is idle, so a socket-level read timeout is reset by the very silence it is meant to detect, while an SSE comment is discarded by the parser and never becomes an event.

Both fields live on the existing `ScheduleProperties`, so they ride `schedules.kwargs` (JSONB) and the run `schedule_snapshot` to the worker with **no database migration**. The orchestrator resolves both and threads them through `RunContext`; each executor applies the event-level bound on its streaming path and selects its HTTP read timeout by whether the request streams. The same change replaces the bare-float timeout arguments that currently discard the configured per-phase connect timeout.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: FastAPI, APScheduler, SQLAlchemy/SQLModel, Pydantic v2, httpx, httpx-sse, openai (Azure), aidial-sdk (telemetry), structlog

**Storage**: PostgreSQL (via SQLAlchemy async). Reused columns: `schedules.kwargs` (JSONB) and `schedule_runs.schedule_snapshot` (JSONB). **No new columns, no migration.**

**Testing**: pytest + pytest-asyncio (`asyncio_mode = "auto"`), pytest-mock

**Target Platform**: Linux server — two independently deployable Docker services (`src/api` FastAPI, `src/worker` APScheduler)

**Project Type**: Web service (API + background worker) over a shared `src/lib`

**Performance Goals**: No throughput change. Raising the ceiling lengthens the worst-case lifetime of a single in-flight attempt; it does not change concurrency, which stays bounded by `job_max_instances` and the httpx connection limits. The event-level idle bound adds one `asyncio.timeout` context per stream event, which is negligible against network and model latency.

**Constraints**: `async`/`await` for all I/O; no event-loop blocking; no `assert` in production; no `print()`; mypy clean; ruff clean (line-length 120). `lib/` must not import from `api/` or `worker/`. On-demand runs execute inside the API process and remain subject to `DRAIN_TIMEOUT_SECONDS = 30.0` (`src/lib/services/run_launcher.py:19`), so a ceiling above 30 s does not survive a rollout for that path. `job_max_instances: 1` (`src/lib/settings.py:129`) means an attempt that never terminates blocks all later firings of its schedule — this is what forbids removing the ceiling entirely.

**Scale/Scope**: Small, additive change. One new schema field, two new settings per HTTP settings class, two new `RunContext` fields, one shared executor helper, an event-level idle wrapper on three streaming paths, and a read-timeout branch in each of the four executors.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Reliability First | ✅ PASS | Directly repairs a reliability defect: a healthy long-running stream is currently killed mid-flight. Stall detection is not merely preserved but repaired — the previous mechanism could not fire on a heartbeating target at all, whereas an event-level bound can. The ceiling is retained specifically to prevent the `job_max_instances: 1` jam. Fixing the discarded `connect` phase restores fast failure on an unreachable host (5 s instead of the full hour). |
| II. Observability (NON-NEGOTIABLE) | ✅ PASS | Both resolved values are added to the attempt-start payload. The two expiries remain distinguishable: the ceiling surfaces from the `asyncio.TimeoutError` branch in `retry.py:96-110`; an idle expiry is raised inside the executor as a `ScheduleRunError` naming stream inactivity and the exceeded bound. Per-attempt detail stays logs-only, consistent with 020's recorded deviation (020 R5). |
| III. Test-First (NON-NEGOTIABLE) | ✅ PASS | Every change is driven by a failing test first. Two governing regression tests fail against current `development`: (a) a streaming executor emitting events for longer than `idle_timeout` but less than `timeout` must succeed; (b) a stream that delivers only heartbeat pings must be abandoned at `idle_timeout` — this one would also fail against the superseded transport-level design, which is why it is the discriminating test. |
| IV. Contract-Driven API | ✅ PASS | `idle_timeout` is an optional addition to the `ScheduleProperties` contract → additive, non-breaking, no major version bump. `timeout` keeps its existing semantics and validation. Both fields gain `Field(description=...)`, which is what MCP clients receive as tool-input guidance. Contract captured in `contracts/schedule-properties.md`. |
| V. Simplicity & YAGNI | ⚠️ PASS (with note) | Adds a second timeout concept, which is irreducible: one number provably cannot express both semantics (research.md R1). Kept minimal by reusing `timeout`/`effective_timeout` rather than renaming, by extracting the `httpx.Timeout` construction into a single shared helper instead of repeating it four times, and by deciding "does this target stream" once on `ScheduleProperties.is_streaming` rather than in each caller. Two pieces of conditional logic are introduced — the streaming/non-streaming branch (justified in R6) and the event-level idle wrapper (R2). `RunTimeouts` groups three floats that would otherwise push `BackgroundRunLauncher.__init__` past the parameter limit; see Complexity Tracking. |

**Result**: PASS. One entry in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/039-split-run-timeouts/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── contracts/
    └── schedule-properties.md   # ScheduleProperties + idle_timeout contract
```

`checklists/requirements.md` is produced by `/speckit-checklist`, and `tasks.md` by `/speckit-tasks`; neither is an output of this command.

### Source Code (repository root)

```text
src/
├── lib/
│   ├── settings.py              # CHANGE: max_duration_seconds + streaming_max_duration_seconds + idle_timeout_seconds
│   │                            #         on both HTTP settings classes; timeout_seconds REMOVED
│   ├── clients/
│   │   └── dial_client.py       # CHANGE: no timeout parameter; requests inherit the client's per-phase timeouts
│   ├── schemas/
│   │   ├── schedule.py          # CHANGE: idle_timeout field, validator, cross-field validator, descriptions
│   │   └── run.py               # CHANGE: RunContext gains timeouts + effective_idle_timeout; default_timeout REMOVED
│   └── services/
│       └── run_launcher.py      # CHANGE: accept and thread the API process RunTimeouts; default_timeout REMOVED
├── api/
│   ├── app.py                   # CHANGE: pass api_http RunTimeouts to BackgroundRunLauncher
│   ├── dependencies/
│   │   └── auth.py              # CHANGE: DialClient built without a timeout
│   ├── mcp/
│   │   └── backing_client.py    # CHANGE: drop the ignored positional default from httpx.Timeout
│   └── v1/endpoints/
│       └── schedules.py         # CHANGE: _SCHEDULE_EXAMPLES gain both timeout fields (reaches MCP agents)
└── worker/
    ├── clients.py               # CHANGE: drop the ignored positional default from httpx.Timeout
    ├── retry.py                 # CHANGE: resolve both values; wait_for stays on the ceiling; log both
    ├── jobs.py                  # CHANGE: DialClient built without a timeout (credential resolution)
    ├── conversation.py          # CHANGE: same, conversation writes are not the target call
    └── executors/
        ├── base.py              # NEW: build_timeout(client, read_seconds) helper + iterate_with_idle(...) wrapper
        ├── rest.py              # CHANGE: non-streaming -> read timeout is the ceiling
        ├── sse.py               # CHANGE: always streaming -> event-level idle around aiter_sse()
        ├── chat_completion.py   # CHANGE: branch on stream_enabled; event-level idle on the streaming branch
        └── responses.py         # CHANGE: branch on stream_enabled; event-level idle on the streaming branch

tests/
├── unit/lib/
│   ├── test_settings.py                     # EXTEND: defaults + env override, both HTTP settings classes
│   ├── schemas/test_schedule_schemas.py     # EXTEND: TestScheduleTimeout, cross-field rule, round-trip
│   └── services/test_run_launcher.py        # EXTEND: new default threaded onto RunContext
├── unit/worker/
│   ├── test_retry.py                        # EXTEND: resolution precedence + the streaming regression test
│   └── executors/test_*.py                  # EXTEND: Test<Name>ExecutorTimeout classes, per-phase assertions,
│                                            #         heartbeat-only stream abandoned at idle_timeout
└── integration/
    └── test_schedule_timeout_retry.py       # VERIFIED UNCHANGED: covers retry paths only,
                                             #   asserts no timeout defaults
```

**Structure Decision**: Existing web-service layout. Schema, settings and context changes go in `src/lib` (shared); all timeout-selection and idle-enforcement behaviour stays in `src/worker`. No endpoint logic changes — the new field flows through the existing `ScheduleRequest.properties` → `kwargs` → worker path, exactly as 020 established.

## Key Design Decisions (see research.md for rationale)

1. **`timeout` keeps meaning "absolute ceiling"; `idle_timeout` is the new field.** Reinterpreting `timeout` as the idle value would silently raise the real ceiling of every schedule that sets it explicitly. Operator decision, 2026-10-07. (R1)

2. **Idle is measured on parsed events, not socket reads.** `asyncio.timeout(idle)` around `anext()`. A transport-level read timeout is reset by DIAL SDK heartbeat pings — the exact condition it is meant to detect — so the previous draft's mechanism could never fire on a heartbeating target. (R2)

3. **The ceiling is an emergency backstop, and only streaming earns the long one.** Default 3600 s for streaming targets, 600 s for everything else. Removing the ceiling entirely is unsafe because `job_max_instances: 1` means a never-ending attempt permanently blocks its schedule. But a non-streaming target has no idle bound watching it and no progress signal of any kind, so an hour-long setting on its only instrument would let a silently hung host hold a worker slot and a pooled connection for that hour. (R3)

4. **The idle default is 60 s and is the only measured number here.** Trace `573e59c09029866fcd77627aa08836e9`: worst observed gap 21.80 s, second 7.49 s, remainder 2–3 s → ×2.7 margin. (R4)

5. **Six new settings, three per process**, grouped into one `RunTimeouts` model for transport. `max_duration_seconds = 600.0`, `streaming_max_duration_seconds = 3600.0` and `idle_timeout_seconds = 60.0` on both `ApiHttpSettings` and `WorkerHttpSettings`, so the two processes can diverge — on-demand runs die at the 30 s drain regardless. All three must be threaded per-process: a bound that is configurable in name but resolved from the worker's settings in fact is worse than no setting at all. The existing `timeout_seconds` is **not** reused as the idle default: it backed the httpx client defaults and every `DialClient` call, and the production mitigation had already moved it to 600. (R5)

6. **The HTTP read timeout is the ceiling for non-streaming requests.** A non-streaming call has no idle period, so applying the idle bound would cap `rest` and `stream: false` targets and deny them their ceiling. Whether a target streams is decided in one place — `ScheduleProperties.is_streaming` — which both the orchestrator (to pick the ceiling default) and the two SDK executors (to pick the call shape) read, rather than each re-deriving it. (R6)

7. **Executors build an explicit `httpx.Timeout` via one shared helper.** Passing a bare float — or `httpx.Timeout(x)` with a single positional — expands to all four phases and discards the configured `connect=5.0`. With an hour-long ceiling, an unreachable host would otherwise hang for an hour. (R7)

8. **A cross-field validator rejects `idle_timeout > timeout`**, only when both are explicitly set. Comparing an explicit field against a process default would make validity depend on deployment configuration. (R8)

9. **Executors read both resolved values from `RunContext` only**, never from `properties`, preserving the single-source-of-truth rule established by 020's code review.

10. **An idle expiry gets its own exception and log event.** `StreamIdleTimeoutError` (a `ScheduleRunError` subclass) is logged by the orchestrator as `attempt_idle_timed_out`, so it is mechanically distinguishable from a ceiling expiry (`attempt_timed_out`) and from any other transport failure — all of which otherwise log identically as `attempt_failed` with `reason=no_status_code`. (R9)

11. **No DB migration.** Both fields persist in the existing `schedules.kwargs` JSONB and the response models are `dict[str, Any]`, per 020 R1/R2.

12. **`timeout_seconds` is removed.** After the split it bounded no run: httpx ignored it in every fully-specified `httpx.Timeout`, and its only live use replaced `DialClient`'s per-phase timeouts with a flat 600 s — including the user-info lookup on every API request. `DialClient` now inherits the client's timeouts; `RunContext.default_timeout` and the launcher's `default_timeout` parameter go with it. A deployment still setting the variable starts normally (`extra="ignore"`). (R11)

## Complexity Tracking

| Violation | Why needed | Simpler alternative rejected because |
|-----------|------------|--------------------------------------|
| `RunTimeouts`, a model introduced to carry three floats that could have been three parameters | The API process must supply its own ceiling, streaming ceiling and idle bound to `BackgroundRunLauncher`, which would otherwise reach five parameters — over the Principle V limit — with four adjacent positional floats that are trivial to transpose | Separate parameters were the first implementation and were abandoned once the third value appeared. The model also enforces that the three travel together, which is precisely the defect an earlier revision shipped: the ceiling was threaded per-process and the idle bound silently was not, leaving `API_HTTP_IDLE_TIMEOUT_SECONDS` defined, tested, documented and inert |

The ⚠️ note on Principle V in the Constitution Check is a justification, not a violation: the second timeout concept is irreducible, and its cost is contained to one shared helper, one boolean property, and one iterator wrapper.

## Open Items

- **No successful end-to-end run of the affected schedule has ever been measured.** The streaming ceiling is sized by argument, not observation. One completed run would confirm the hour is generous rather than merely large.
- **Connection-pool arithmetic under the longer ceiling.** `max_connections = 200` per process; an attempt now holds its connection for up to an hour instead of a minute. Pool exhaustion surfaces as `PoolTimeout`, which carries no status code and is therefore non-retryable, so it would spread to unrelated schedules. Not modelled here; worth a bound on concurrent long-running schedules if the streaming population grows.
- **SDK-level event filtering (streaming chat/responses only).** The idle wrapper sits above the SDK's own parser, which drops frames whose `object` is not the expected chunk type — Azure's asynchronous-filter frames among them. A run of such frames would read as silence to the idle bound, the inverse of the heartbeat problem one layer up. `sse.py` does not have this asymmetry. Flagged as a risk to confirm against a real DIAL deployment, not a known defect.

## Resolved during implementation

- **`httpx.AsyncClient.timeout` phase access** — verified on the pinned httpx 0.28.1: `timeout` is a property and `httpx.Timeout` accepts all four phases as keywords. `build_timeout` is sound.
- **Disposition of `fix/change-api-and-worker-timeout`** (2026-10-12) — the stopgap merged to `development` first, raising `timeout_seconds` from 60 to 600, and this feature landed on top of it via a merge. Its intent, a 600 s budget for runs, is carried forward by raising `max_duration_seconds` to 600. Its side effect, a flat 600 s on every `DialClient` phase, is reversed by removing `timeout_seconds` entirely. (R3 revision, R11)
