# Implementation Plan: Conversation ID Header and Scheduler Request Identity

**Branch**: `feature/scheduler-user-agent` (user decision: shared with the User-Agent change, commit `f94816b`) | **Date**: 2026-11-25 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/043-conversation-id-header/spec.md`

## Summary

For `chat_completion` runs with `create_conversation: true`, resolve the run's DIAL conversation location (owner bucket,
display name, deployment, schedule id, run id) **once, before** the target request, keep it on `RunContext`, and send the
derived conversation id as `X-CONVERSATION-ID` on every attempt — percent-encoding everything outside printable ASCII and
replacing any caller-supplied value. After a successful run the conversation is saved at that same location, so the header,
the stored document and `schedule_runs.conversation_id` always agree and DIAL Admin shows the run call and any DIAL Chat
continuation as one session. If the location cannot be resolved, the request goes out without the header, a single warning is
logged, the conversation is skipped, and the run outcome is unchanged. The scheduler User-Agent (`f94816b`) is already
implemented and is only verified and documented here. Logs keep `trace_id`/`span_id` and gain `conversation_id` via the log
context. No schema change, no API change.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: httpx 0.28 (shared clients), openai 1.x `AsyncAzureOpenAI`, structlog, OpenTelemetry via aidial-sdk
telemetry, Pydantic v2. No new dependency (`urllib.parse` from stdlib for encoding).

**Storage**: PostgreSQL — unchanged; no migration.

**Testing**: pytest, pytest-asyncio, pytest-mock (unit). No integration test needed: no repository or schema change.

**Target Platform**: Linux containers (API service and worker, same image)

**Project Type**: web-service with background worker

**Performance Goals**: At most one extra DIAL round-trip *before* the target request for opted-in chat completion runs — the
bucket lookup, which today happens after the run anyway (net calls per run unchanged).

**Constraints**: A run must never fail because of this feature (header encoding, bucket failure, missing schedule). Header
value must equal `schedule_runs.conversation_id` for ASCII names. No secrets in logs.

**Scale/Scope**: ~7 source files (2 new small modules/models), 4–5 test modules, 2 docs files.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle / constraint | Status | Notes |
|---|---|---|
| I. Reliability First | ✅ | Location resolution wrapped in a best-effort guard (R5); encoding prevents `UnicodeEncodeError`/header injection (R2); no blocking I/O added; resolved once, not per attempt. |
| II. Observability | ✅ | New events `conversation_id_resolved` / `conversation_id_resolve_failed` / `conversation_create_skipped` with `run_id`, `schedule_id`, reason code; `trace_id`/`span_id` preserved; `conversation_id` bound to log context ([contracts/log-events.md](contracts/log-events.md)). Telemetry init unchanged. |
| III. Test-First | ✅ | Tasks will start with failing unit tests for encoding, resolver, `execute_run` flow and executor headers. |
| IV. Contract-Driven API | ✅ | No REST schema change. Outbound header contract and log contract committed under `contracts/`. Internal data passed via typed Pydantic models (`ConversationLocation`, `RunContext`). |
| V. Simplicity & YAGNI | ✅ | No hooks/contextvar magic for headers (R3); reuse of `RunContext`; new functions ≤ 3 params. `ConversationBuildInput` subclasses `ConversationLocation` instead of a parallel model. |
| Layer rules (`lib` ↛ `api`/`worker`) | ✅ | New helpers/models live in `src/lib/utils` and `src/lib/schemas`; path logic stays in `src/worker/conversation.py`. |
| Security (no secrets in logs) | ✅ | Reasons come from `ConversationCreationError` texts (status/transport only); OBO header values never logged. |
| Workflow: branch `NNN-short-description` | ⚠️ deviation | Work is done on `feature/scheduler-user-agent` at the user's explicit request so the User-Agent change and this feature ship together — see Complexity Tracking. |

**Post-design re-check (after Phase 1)**: unchanged — all ✅, one documented workflow deviation.

## Project Structure

### Documentation (this feature)

```text
specs/043-conversation-id-header/
├── plan.md               # This file
├── spec.md
├── research.md           # Phase 0
├── data-model.md         # Phase 1
├── quickstart.md         # Phase 1
├── contracts/
│   ├── outbound-headers.md
│   └── log-events.md
├── checklists/requirements.md
└── tasks.md              # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
src/
├── lib/
│   ├── constants.py                    # + CONVERSATION_ID_HEADER
│   ├── schemas/
│   │   ├── conversation.py             # + ConversationLocation; ConversationBuildInput(ConversationLocation) + model_id
│   │   └── run.py                      # RunContext + conversation_location
│   └── utils/
│       ├── conversation_header.py      # NEW: encode_header_value, with_conversation_id
│       └── user_agent.py               # (f94816b) unchanged
└── worker/
    ├── conversation.py                 # + resolve_conversation_location, conversation_id_for;
    │                                   #   create_run_conversation reuses ctx.conversation_location (no bucket/schedule lookup)
    ├── jobs.py                         # execute_run: resolve before execute_with_policy, bind log context;
    │                                   #   _maybe_create_conversation: skip + info when location is None
    └── executors/
        └── chat_completion.py          # extra_headers = with_conversation_id(strip_user_agent(...), id)

tests/unit/
├── lib/utils/test_conversation_header.py   # NEW
├── worker/test_conversation.py             # resolver + create_run_conversation with location
├── worker/test_jobs.py                     # execute_run flow, skip path, retries, log context, trace ids
└── worker/executors/test_chat_completion.py# header present/absent, precedence, encoding; responses/rest/sse untouched

CONFIGURATION.md, documentation/properties.md   # create_conversation + extra_headers docs
```

**Structure Decision**: Existing single-repo layout (`src/api`, `src/worker`, `src/lib`). Pure helpers and models go to
`src/lib`; conversation path logic and orchestration stay in `src/worker`. `CHANGELOG.md` is frozen at 0.3.1 (releases are
documented on GitHub), so it is not updated.

## Implementation Approach

1. **Header helpers (TDD)** — `encode_header_value` (`quote(value, safe=printable ASCII)`) and `with_conversation_id`
   (case-insensitive replace). Constant `CONVERSATION_ID_HEADER`.
2. **Models** — `ConversationLocation`; `ConversationBuildInput` subclasses it with `model_id`; `RunContext.conversation_location`.
3. **Conversation module split**
   - `resolve_conversation_location(ctx, credentials, properties) -> ConversationLocation`: validate/`unquote` `payload.model`,
     load schedule (display name), `get_bucket`; raises `ConversationCreationError` on any missing input.
   - `conversation_id_for(location) -> str`: `CONVERSATION_ID_PREFIX + conversation_resource_path(location)`; used by
     `build_conversation` for the document id.
   - `create_run_conversation(ctx, credentials, properties, result)`: reads `ctx.conversation_location`; resolves `model_id`
     (application reference when needed); builds and PUTs; returns the id. No bucket or schedule lookup any more.
4. **Orchestration (`execute_run`)** — after `_resolve_credentials`, when `chat_completion` + `create_conversation`:
   `_resolve_conversation_location(ctx, credentials, properties)` (best-effort wrapper: success → set
   `ctx.conversation_location`, `bind_contextvars(conversation_id=…)`, info `conversation_id_resolved`; any exception → warning
   `conversation_id_resolve_failed`). `_maybe_create_conversation` returns early with info `conversation_create_skipped` when the
   location is `None`.
5. **Executor** — `ChatCompletionExecutor` builds `extra_headers` as
   `with_conversation_id(strip_user_agent(properties.extra_headers), conversation_id_for(loc) if loc else None)`.
   `responses`/`rest`/`sse` unchanged.
6. **Docs** — `CONFIGURATION.md` and `documentation/properties.md`: header, precedence, encoding, failure behaviour.
7. **Quality gates** — `make lint`, `make test`; code-review-agent loop per constitution SDLC.
8. **Dev verification** — [quickstart.md §4](quickstart.md), including the non-ASCII DIAL Chat continuation check.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Pre-request bucket failure loses a conversation that a post-run lookup might have saved | Same failure probability as today; logged as warning + skip; accepted by user to keep run and session aligned. |
| DIAL Chat encodes non-ASCII ids differently on continuation → split sessions for such names | Non-blocking; verified post-deploy (quickstart §4.5); encoding rule can be adjusted in one helper. |
| Existing tests rely on `create_run_conversation` doing its own bucket/schedule lookup | Update `test_conversation.py` orchestration tests to set `ctx.conversation_location`; resolver gets its own tests. |
| Scheduler calls still classified as `client_type = chat` in ADAS | External follow-up: ADAS classifier rule for `ai-dial-scheduler/*`. |

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Branch is `feature/scheduler-user-agent`, not `043-conversation-id-header` | User asked to implement on the existing User-Agent branch so both request-identity changes ship and are verified together | A separate `043-…` branch would split one reviewable change across two PRs touching the same executors |
