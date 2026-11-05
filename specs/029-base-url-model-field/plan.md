# Implementation Plan: Base URL and Payload Model for Chat Completion

**Branch**: `029-base-url-model-field` | **Date**: 2026-07-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/029-base-url-model-field/spec.md`

## Summary

Align the `chat_completion` target type with the existing `responses` model: use the schedule's
`properties.url` as a **base URL** (passed to `AsyncAzureOpenAI(base_url=...)`) instead of parsing a
per-deployment URL path, and take the deployment/model from `payload.model` instead of the URL path
segment. Validation is extended so `payload.model` is required for `chat_completion` too (it is
already required for `responses`). Conversation creation stops parsing the URL for the deployment and
reads it from `payload.model`. The URL-parsing helpers (`parse_deployment`, `extract_deployment_id`)
become unused and are removed. This rides the existing `kwargs`/snapshot flow — **no DB migration**.
This is a deliberate breaking change to the chat-completion configuration contract: schedules with a
deployment path in the URL and no `payload.model` will no longer validate (see spec Assumptions).

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: FastAPI, APScheduler, SQLAlchemy/SQLModel, Pydantic v2, `openai`
(AsyncAzureOpenAI — `chat.completions` resource with `base_url`), httpx, AiDialSdk telemetry

**Storage**: PostgreSQL (schedules, schedule_runs). Target type/properties live in the JSONB `kwargs`
column — no schema change.

**Testing**: pytest + pytest-asyncio (`asyncio_mode = "auto"`), pytest-mock

**Target Platform**: Linux server (containerized API + worker services)

**Project Type**: Web service (API service + background worker) sharing `src/lib`

**Performance Goals**: Not latency-critical; parity with the existing chat completion run path.

**Constraints**: Async/await for all I/O. No raw SQL. No streamed content or credentials in logs
(event-type discriminators only). Change rides existing `ScheduleProperties` (JSONB `kwargs`) → **no
DB migration**. `create_conversation` stays chat-completion-only.

**Scale/Scope**: One executor module changed (`chat_completion.py`), one validation-rule extension
(`schedule.py`), one conversation-orchestration change (`conversation.py`), removal of two now-unused
helpers (`dial.py`), and documentation/e2e updates. No new services, no new DB tables, no migration.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|-----------|-----------|
| **I. Reliability First** | PASS — Reuses the shared `execute_with_policy` timeout/retry orchestration and the shared httpx client (`HttpClientProvider`). Executor errors stay mapped to `ScheduleRunError` with status code, unchanged. Removing dead parsing helpers reduces failure surface. |
| **II. Observability** | PASS — Existing structured run/attempt logging unchanged; executor still logs stream event `type` only, never content. Conversation logging (`deployment_kind`) preserved, now driven by `payload.model`. No `print()`. |
| **III. Test-First** | PASS (enforced at implement) — TDD: unit tests for the extended `chat_completion` validation branch (`payload.model` required), the executor building `base_url`/`model` (stream + non-stream + `APIStatusError` mapping), and conversation model resolution from `payload.model` (model vs. application reference). |
| **IV. Contract-Driven API** | PASS — No new enum member; `ScheduleProperties` OpenAPI schema is unchanged in shape (the `payload.model` rule is a validation constraint on a free-form dict). Internal boundaries stay typed (`ScheduleProperties`, `RunContext`). The chat-completion **configuration contract** changes (breaking) — documented in `documentation/properties.md` and this plan. |
| **V. Simplicity & YAGNI** | PASS — Brings chat completion in line with the already-shipped responses pattern; net removal of code (two helpers + their tests). No new abstraction. |

**Result**: PASS. No violations; Complexity Tracking table not required.

## Project Structure

### Documentation (this feature)

```text
specs/029-base-url-model-field/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── schedule-properties.md      # chat_completion validation rules (payload.model required)
│   ├── chat-completion-executor.md # base_url + payload.model behavior + result shape contract
│   └── conversation-model.md       # model resolution from payload.model for conversation creation
├── checklists/
│   └── requirements.md  # Spec quality checklist (from /speckit-specify)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── lib/
│   ├── schemas/
│   │   └── schedule.py                 # MODIFY: require payload.model for chat_completion too
│   │                                   #   (extend the existing responses-only rule to both types)
│   └── utils/
│       └── dial.py                     # MODIFY: remove parse_deployment + extract_deployment_id
│                                       #   (now unused); keep strip_bearer_prefix
└── worker/
    ├── conversation.py                 # MODIFY: derive deployment from payload.model instead of
    │                                   #   extract_deployment_id(properties.url); keep application
    │                                   #   reference resolution + non-fatal failure behavior
    └── executors/
        └── chat_completion.py          # MODIFY: use base_url=properties.url (drop parse_deployment
                                        #   / azure_endpoint / azure_deployment); model comes from payload

tests/
├── unit/
│   ├── lib/
│   │   ├── schemas/test_schedule_schemas.py   # MODIFY: chat_completion requires payload.model
│   │   └── utils/test_dial.py                 # MODIFY: drop parse_deployment/extract_deployment_id tests
│   └── worker/
│       ├── executors/test_chat_completion.py  # MODIFY: base_url + model from payload; stream/non-stream
│       └── test_conversation.py               # MODIFY: model resolution from payload.model
└── e2e/
    └── *.http                                  # MODIFY: chat-completion example uses base URL + payload.model

documentation/
└── properties.md                       # MODIFY: chat_completion URL is a base URL; payload.model required
```

**Structure Decision**: Existing web-service layout (`src/api`, `src/worker`, shared `src/lib`) per
constitution v1.2.0. The feature is worker-centric: an executor change, a validation-rule extension,
a conversation-orchestration change, and removal of two now-unused helpers. No `api/` code changes
(the `ScheduleProperties` schema shape is unchanged); no DB migration (target properties live in
JSONB `kwargs`). `lib/` continues to import nothing from `api/` or `worker/`.

## Complexity Tracking

> No Constitution Check violations. Table intentionally omitted.
