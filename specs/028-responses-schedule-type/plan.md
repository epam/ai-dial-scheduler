# Implementation Plan: Responses Schedule Type

**Branch**: `028-responses-schedule-type` | **Date**: 2026-07-21 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/028-responses-schedule-type/spec.md`

## Summary

Add a new schedule `target_type` value `responses` that behaves exactly like `chat_completion` for everything that is not interaction-specific (cron/date triggering, timeout, retry, run tracking, pause/resume, metadata, OBO authorization), but invokes the deployment's **Responses API** (`client.responses.stream()` / `client.responses.create()`) instead of the Chat Completions API. The new type reuses the existing `ScheduleProperties` validation (no `method`, `api_version` required, default non-streaming), so **no DB migration is required** — the change rides the existing `kwargs`/snapshot flow. A new `ResponsesExecutor` is registered in the dispatcher; it assembles a single final Response object for both streaming and non-streaming runs (via `responses.stream()` + `get_final_response()`), giving one consistent stored result shape. The `create_conversation` option remains chat-completion-only and is rejected for `responses` (conversation creation is out of scope for this feature).

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: FastAPI, APScheduler, SQLAlchemy/SQLModel, Pydantic v2, `openai` (AsyncAzureOpenAI — `responses` resource), httpx, AiDialSdk telemetry

**Storage**: PostgreSQL (schedules, schedule_runs). Target type/properties are stored in the JSONB `kwargs` column — no schema change.

**Testing**: pytest + pytest-asyncio (`asyncio_mode = "auto"`), pytest-mock

**Target Platform**: Linux server (containerized API + worker services)

**Project Type**: Web service (API service + background worker) sharing `src/lib`

**Performance Goals**: Not latency-critical; parity with existing chat completion run path.

**Constraints**: Async/await for all I/O. No raw SQL. No streamed content or credentials in logs (event-type discriminators only, per FR-010). `responses` rides existing `ScheduleProperties` (JSONB `kwargs`) → **no DB migration**. `create_conversation` must stay chat-completion-only.

**Scale/Scope**: One new `TargetType` enum member, one new executor module registered in the dispatcher, validation-rule extension so chat-completion rules apply to `responses`, and documentation/e2e updates. No new services, no new DB tables.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|-----------|-----------|
| **I. Reliability First** | PASS — Reuses the shared `execute_with_policy` timeout/retry orchestration and the shared httpx client (`HttpClientProvider`). Executor errors mapped to `ScheduleRunError` with status code (mirrors `ChatCompletionExecutor`), so retry eligibility and failure recording are identical. |
| **II. Observability** | PASS — Reuses existing structured run/attempt logging; the executor logs stream event `type` only, never content (FR-010). Runs persisted (success/error) through the existing repository path. No `print()`. |
| **III. Test-First** | PASS (enforced at implement) — TDD: unit tests for the `responses` validation branch (method rejected, api_version required, stream default, create_conversation rejected), dispatcher resolution, and the executor's assembled result for stream + non-stream + `APIStatusError` mapping. |
| **IV. Contract-Driven API** | PASS — `responses` is added to the `TargetType` enum, auto-reflected in the OpenAPI `ScheduleProperties` schema; the executor `Protocol` contract is unchanged. Internal boundaries stay typed (Pydantic `ScheduleProperties`, `RunContext`). |
| **V. Simplicity & YAGNI** | PASS — Smallest change that satisfies the spec: one enum value, one executor mirroring the existing one, one dispatcher entry, and validation reuse. No new abstraction; the existing `Executor` protocol already supports adding an implementation. |

**Result**: PASS. No violations; Complexity Tracking table not required.

## Project Structure

### Documentation (this feature)

```text
specs/028-responses-schedule-type/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── schedule-properties.md    # responses target_type validation rules
│   └── responses-executor.md     # ResponsesExecutor behavior + result shape contract
├── checklists/
│   └── requirements.md  # Spec quality checklist (from /speckit-specify)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── lib/
│   ├── db/
│   │   └── enums.py                    # MODIFY: add RESPONSES = "responses" to TargetType
│   └── schemas/
│       └── schedule.py                 # MODIFY: apply chat_completion validation branch to responses
│                                       #   (no method, api_version required, default stream=False);
│                                       #   keep create_conversation restricted to chat_completion only
└── worker/
    ├── dispatcher.py                   # MODIFY: register TargetType.RESPONSES -> ResponsesExecutor()
    └── executors/
        └── responses.py                # NEW: ResponsesExecutor — responses.stream() + get_final_response()

tests/
├── unit/
│   ├── lib/
│   │   └── schemas/test_schedule_schemas.py   # MODIFY: responses validation parity + create_conversation rejection
│   └── worker/
│       ├── test_dispatcher.py                 # MODIFY: responses resolves to ResponsesExecutor
│       └── executors/test_responses.py        # NEW: assembled result (stream + non-stream), APIStatusError mapping
└── e2e/
    └── *.http                                  # ADD: create-responses-schedule example (mirrors chat completion .http)

documentation/
└── properties.md                       # MODIFY: add "responses" to target_type list + note interaction difference
```

**Structure Decision**: Existing web-service layout (`src/api`, `src/worker`, shared `src/lib`) per constitution v1.2.0. The feature is worker-centric and additive: a new `TargetType` value in `lib/db/enums.py`, a validation-branch extension in `lib/schemas/schedule.py`, a new executor in `worker/executors/responses.py`, and one dispatcher registration. No `api/` code changes (the enum addition is reflected automatically in the OpenAPI schema); no DB migration (target type/properties live in JSONB `kwargs`). `lib/` continues to import nothing from `api/` or `worker/`.

## Complexity Tracking

> No Constitution Check violations. Table intentionally omitted.
