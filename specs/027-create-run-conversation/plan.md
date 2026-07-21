# Implementation Plan: Create DIAL Conversation on Run Complete

**Branch**: `027-create-run-conversation` | **Date**: 2026-07-17 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/027-create-run-conversation/spec.md`

## Summary

Add an opt-in `create_conversation` boolean to chat completion schedule properties (default `False`, rejected for non-chat-completion targets). When a chat completion run succeeds and the flag is set, the worker builds a two-message DIAL conversation (user prompt from the run snapshot + assistant response from the stored result, including custom content) and writes it to the run owner's DIAL storage at `conversations/{bucket}/.scheduler/{schedule_id}/{run_id}`, using the run's existing OBO credentials. Model attribution differs by deployment kind: model deployments use the deployment id directly; application deployments are resolved via `GET /openai/applications/{app_id}` to obtain `reference`, which becomes the conversation model id. Chat completion result storage is unified so streaming and non-streaming runs both persist a single assembled `ChatCompletion` (via `chat.completions.stream` + `get_final_completion`), giving conversation creation one consistent result shape. Conversation creation is best-effort: failures are logged only and never change the run's success outcome.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: FastAPI, APScheduler, SQLAlchemy/SQLModel, Pydantic v2, `openai` (AsyncAzureOpenAI), httpx, AiDialSdk telemetry

**Storage**: PostgreSQL (schedules, schedule_runs). DIAL Core object storage for conversations (external, via HTTP)

**Testing**: pytest + pytest-asyncio (`asyncio_mode = "auto"`), pytest-mock

**Target Platform**: Linux server (containerized API + worker services)

**Project Type**: Web service (API service + background worker) sharing `src/lib`

**Performance Goals**: Not latency-critical; conversation creation is a post-run best-effort step off the run's critical path

**Constraints**: Conversation-creation failure MUST NOT fail a successful run (FR-013). No streamed content or credentials in logs (FR-015). Async/await for all I/O. No raw SQL. `create_conversation` lives inside `ScheduleProperties` (JSONB `kwargs`) → **no DB migration required**.

**Scale/Scope**: One conversation per successful opted-in chat completion run; single new DIAL client surface (bucket, application details, conversation write) plus a conversation builder and worker wiring.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|-----------|-----------|
| **I. Reliability First** | PASS — Conversation creation is isolated in try/except; failures logged, run success preserved (FR-013). Reuses shared httpx client and per-run timeout. New DIAL calls have explicit error handling mirroring `DialClient.get_obo_credentials`. |
| **II. Observability** | PASS — Structured `logger` events for conversation build/start/success/failure with run_id, schedule_id, deployment kind, reason code. No `print()`. Streaming diagnostics log event `type` only, never content (FR-015). Run persistence already records success/failure. |
| **III. Test-First** | PASS (enforced at implement) — TDD: unit tests for the property validator, deployment-kind detection, application-reference resolution, conversation builder (model vs app, content + custom_content, streaming vs non-streaming assembled result), and best-effort failure isolation. Integration tests for the DIAL client calls with mocked httpx. |
| **IV. Contract-Driven API** | PASS — `create_conversation` is added to the Pydantic `ScheduleProperties` schema (auto-reflected in OpenAPI) and surfaced in `ScheduleResponse.properties`. Internal worker/DIAL boundaries expressed as Pydantic models (conversation payload, application details), no ad-hoc dicts across service boundaries. |
| **V. Simplicity & YAGNI** | PASS — Flag rides existing `kwargs`/snapshot flow (no migration, no service changes). One new client with three GET/PUT methods; one builder module. No new abstraction layers. Unifying streaming/non-streaming into one assembled result *removes* branching in result handling. |

**Result**: PASS. No violations; Complexity Tracking table not required.

## Project Structure

### Documentation (this feature)

```text
specs/027-create-run-conversation/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── dial-conversation.schema.json    # DIAL conversation resource written via PUT
│   ├── schedule-properties.md           # create_conversation addition to ScheduleProperties
│   └── dial-client.md                   # New DialClient method contracts
├── checklists/
│   └── requirements.md  # Spec quality checklist (from /speckit-specify)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── api/
│   └── v1/endpoints/schedules.py        # No change required (properties flow through unchanged)
├── worker/
│   ├── jobs.py                          # MODIFY: after _persist_success, invoke best-effort conversation creation
│   ├── executors/
│   │   └── chat_completion.py           # MODIFY: use chat.completions.stream + get_final_completion; always store assembled ChatCompletion
│   └── conversation.py                  # NEW: conversation creation orchestration (build + resolve model + write)
└── lib/
    ├── schemas/
    │   ├── schedule.py                  # MODIFY: add create_conversation to ScheduleProperties + validator (chat_completion only)
    │   └── conversation.py              # NEW: Pydantic models — DialConversation, DialMessage, DialModel, ApplicationDetails
    ├── clients/
    │   └── dial_client.py               # MODIFY: add get_bucket, get_application, put_conversation
    ├── constants.py                     # MODIFY: add bucket/application/conversation path templates + scheduler folder segment
    └── exceptions/
        └── errors.py                    # MODIFY: add ConversationCreationError (worker-internal, non-fatal)

tests/
├── worker/
│   ├── executors/test_chat_completion.py   # MODIFY/ADD: assembled result for stream + non-stream
│   ├── test_conversation.py                # NEW: builder + orchestration unit tests
│   └── test_jobs.py                        # MODIFY: conversation invoked on success only; failure isolation
└── lib/
    ├── schemas/test_schedule.py            # MODIFY: create_conversation validation rules
    └── clients/test_dial_client.py         # MODIFY: get_bucket / get_application / put_conversation
```

**Structure Decision**: Existing web-service layout (`src/api`, `src/worker`, shared `src/lib`) per constitution v1.2.0. The feature is worker-centric: the flag is a `lib/schemas` change, DIAL access extends `lib/clients/dial_client.py`, conversation assembly is a new `worker/conversation.py` (worker-only orchestration that depends on `lib`), and the executor change stays in `worker/executors`. No `api/` changes beyond the schema being reflected automatically. `lib/` adds only schemas/client/constants and never imports from `api/` or `worker/`.

## Complexity Tracking

> No Constitution Check violations. Table intentionally omitted.
