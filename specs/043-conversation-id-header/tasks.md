---

description: "Task list for Conversation ID Header and Scheduler Request Identity"
---

# Tasks: Conversation ID Header and Scheduler Request Identity

**Input**: Design documents from `/specs/043-conversation-id-header/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/outbound-headers.md, contracts/log-events.md, quickstart.md

**Branch**: `feature/scheduler-user-agent` — do NOT create or switch branches (User-Agent already implemented in commit `f94816b`).

**Tests**: REQUIRED — the constitution mandates TDD (Principle III). Every test task must be written first and observed failing
before its implementation task.

**Organization**: Tasks are grouped by user story (spec.md) so each story can be implemented and verified independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story label (US1–US5) — only in user story phases

## Path Conventions

Single project: `src/`, `tests/` at repository root. Unit tests mirror `src/` under `tests/unit/`.

---

## Phase 1: Setup

**Purpose**: Confirm a green baseline on the branch before changing anything.

- [X] T001 Confirm branch is `feature/scheduler-user-agent` and HEAD contains `f94816b` (`git log --oneline -3`); run `make lint` and `make test` and record that both pass before any change (baseline for regressions; no file changes)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Constant, typed models and pure header helpers that every story uses.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Tests (write first, must fail)

- [X] T002 [P] Create `tests/unit/lib/utils/test_conversation_header.py` with failing tests for `encode_header_value(value: str) -> str` from `src/lib/utils/conversation_header.py`: (a) printable ASCII incl. space, `/`, `%`, `_` returned unchanged (e.g. `"conversations/b/.scheduler/s/gpt-4__Daily report__r"`); (b) `"Щоденний звіт"` → `"%D0%A9%D0%BE%D0%B4%D0%B5%D0%BD%D0%BD%D0%B8%D0%B9 %D0%B7%D0%B2%D1%96%D1%82"`; (c) emoji `"📈"` → `"%F0%9F%93%88"`; (d) control chars `"a\nb\tc\x7f"` → `"a%0Ab%09c%7F"`; (e) literal `"50%25 done"` unchanged; (f) result always satisfies `value.isascii()` and contains no char < 0x20 or == 0x7F; (g) idempotent on ASCII-only output
- [X] T003 [P] Add failing tests to `tests/unit/lib/utils/test_conversation_header.py` for `with_conversation_id(headers: dict[str, str], conversation_id: str | None) -> dict[str, str]`: (a) `None` → returns an equal copy, input dict not mutated, caller `x-conversation-id` kept; (b) id given, no caller header → adds exactly `{"X-CONVERSATION-ID": <encoded id>}` and keeps other headers; (c) caller keys `X-CONVERSATION-ID`, `x-conversation-id`, `X-Conversation-Id` are all removed and only the scheduler value remains (exactly one key matching case-insensitively); (d) value is `encode_header_value(conversation_id)` for a Cyrillic id; (e) input dict not mutated
- [X] T004 [P] Add failing tests in `tests/unit/lib/schemas/test_conversation.py` for `ConversationLocation` (fields `deployment_id: str`, `bucket: str`, `schedule_id: str`, `run_id: str`, `display_name: str`, all required) and that `ConversationBuildInput` is a subclass of `ConversationLocation` adding required `model_id: str` and still accepts the existing keyword set (`deployment_id, model_id, bucket, schedule_id, run_id, display_name`); and in `tests/unit/lib/schemas/test_run.py` that `RunContext(run_id=..., schedule_id=...)` has `conversation_location is None` by default and accepts a `ConversationLocation`

### Implementation

- [X] T005 Add `CONVERSATION_ID_HEADER = "X-CONVERSATION-ID"` to `src/lib/constants.py` next to `USER_AGENT_HEADER`, with a one-line comment: DIAL Core/Admin key sessions on it
- [X] T006 [P] Create `src/lib/utils/conversation_header.py` implementing `encode_header_value` as `urllib.parse.quote(value, safe=<all chars 0x20–0x7E>)` (module-level constant for the safe set) and `with_conversation_id` (copy without any key whose `.lower() == CONVERSATION_ID_HEADER.lower()`, then add `CONVERSATION_ID_HEADER: encode_header_value(id)`; `None` → plain copy). Google-style docstrings explaining: httpx encodes header values as ASCII (non-ASCII would fail the run), `%`/`/`/`\` already escaped in name parts so `%` stays literal, and case-insensitive removal because httpx keeps differently-cased duplicates. Makes T002–T003 pass
- [X] T007 [P] In `src/lib/schemas/conversation.py` add `ConversationLocation(BaseModel)` with fields `deployment_id: str`, `bucket: str`, `schedule_id: str`, `run_id: str`, `display_name: str` (docstring: path-facing inputs resolved once before the target request and reused when the conversation is saved) and change `ConversationBuildInput` to `class ConversationBuildInput(ConversationLocation)` declaring only `model_id: str` (keep its existing docstring about deployment_id vs model_id)
- [X] T008 In `src/lib/schemas/run.py` add `conversation_location: ConversationLocation | None = None` to `RunContext` (import from `src.lib.schemas.conversation`) and extend the class docstring: set once per run by `execute_run` for chat completion schedules with `create_conversation`, read by the chat completion executor and conversation creation. Makes T004 pass (depends on T007)

**Checkpoint**: `uv run pytest tests/unit/lib` green; `uv run mypy src` green.

---

## Phase 3: User Story 1 — Find a scheduled run's call in DIAL Admin by conversation (Priority: P1) 🎯 MVP

**Goal**: For `chat_completion` + `create_conversation: true`, the target request carries `X-CONVERSATION-ID` equal to the
run's `conversation_id`, on every attempt, for scheduled and on-demand runs; the conversation is saved at that same location.

**Independent Test**: Unit-run `execute_run` for such a schedule with mocked DIAL/DB/OpenAI: the executor receives
`extra_headers["X-CONVERSATION-ID"] == ` the id later passed to `set_conversation_id`, and `put_conversation` uses the same path.

### Tests (write first, must fail)

- [X] T009 [P] [US1] In `tests/unit/worker/test_conversation.py` add `class TestResolveConversationLocation` for `resolve_conversation_location(ctx, credentials, properties) -> ConversationLocation` (reuse fixtures `mock_dial`, `mock_schedule`): (a) returns location with `bucket == BUCKET`, `display_name == DISPLAY_NAME`, `schedule_id`/`run_id` from ctx, `deployment_id == payload.model`; (b) percent-encoded application id `applications/{bucket}/Planning%20Assistant__0.0.1` yields raw `applications/{bucket}/Planning Assistant__0.0.1`; (c) does NOT call `get_application` or `put_conversation`; (d) calls `get_bucket` exactly once with the given credentials. Also add `TestConversationIdFor`: `conversation_id_for(location)` == `CONVERSATION_ID_PREFIX + conversation_resource_path(location)` and equals `build_conversation(...).id` for the same inputs
- [X] T010 [P] [US1] In `tests/unit/worker/test_conversation.py` rework `TestCreateRunConversationOrchestration`, `TestCreateRunConversationApplication`, `TestCreateRunConversationPercentEncodedDeployment`, `TestCreateRunConversationModelResolution` and `TestConversationNameEdgeInputs` so they pass a ctx whose `conversation_location` is set (add helper `make_located_ctx(**location_overrides)` building `ConversationLocation` from the module constants), and assert: `create_run_conversation` does NOT call `get_bucket` nor `ScheduleRepository.get_by_id`; returned id == `conversation_id_for(ctx.conversation_location)`; PUT path == returned id without `conversations/`; application deployments still resolve `model_id` via `get_application` post-run. Keep `test_returned_id_contains_run_id` and drift test semantics
- [X] T011 [P] [US1] In `tests/unit/worker/executors/test_chat_completion.py` add `class TestConversationIdHeader`: (a) ctx with `conversation_location` → `create(...)`/`stream(...)` kwargs `extra_headers["X-CONVERSATION-ID"] == encode_header_value(conversation_id_for(loc))`, for both streaming and non-streaming; (b) ctx without location → no `X-CONVERSATION-ID` added and caller-supplied `x-conversation-id` passes through unchanged; (c) with location and caller `x-Conversation-ID: manual` in `properties.extra_headers` → exactly one key matching case-insensitively, value is the scheduler's; (d) `User-Agent` in extra_headers still stripped (regression of `f94816b`)
- [X] T012 [P] [US1] In `tests/unit/worker/test_jobs.py` add `class TestConversationLocationInExecuteRun` (patch `src.worker.jobs.resolve_conversation_location`, `src.worker.jobs.create_run_conversation`, executor via `resolve_executor`): (a) chat_completion + `create_conversation: true` → resolver awaited once before `execute_with_policy`, and the ctx passed to the executor has `conversation_location` set; (b) retry policy with two retryable 503 attempts then success → every executor call sees the same `conversation_location` object and resolver awaited exactly once; (c) `create_conversation: false`, and target types `responses`, `rest`, `sse` → resolver never awaited, `ctx.conversation_location is None`; (d) on success `create_run_conversation` is awaited with the same ctx (location reused) and `set_conversation_id` receives its return value; (e) on target failure the header was still present (executor saw location) and no conversation is created
- [X] T013 [P] [US1] In `tests/unit/lib/services/test_run_launcher.py` add a test that `BackgroundRunLauncher` executes via `execute_run` with a `RunContext` whose `conversation_location` starts as `None` (resolution happens inside `execute_run`), proving on-demand runs share the same path

### Implementation

- [X] T014 [US1] In `src/worker/conversation.py` add `resolve_conversation_location(ctx: RunContext, credentials: OboCredentials, properties: ScheduleProperties) -> ConversationLocation`: move from `create_run_conversation` the `payload.model` validation (raise `ConversationCreationError("payload.model must be a non-empty string to create a conversation")`), `unquote`, schedule lookup via `ScheduleRepository.get_by_id` (raise `ConversationCreationError(f"schedule not found: {ctx.schedule_id}")`), and `client.get_bucket(credentials)`; return `ConversationLocation(...)`. Add `conversation_id_for(location: ConversationLocation) -> str` returning `f"{CONVERSATION_ID_PREFIX}{conversation_resource_path(location)}"`; retype `conversation_resource_path` and `_conversation_leaf` to accept `ConversationLocation`; make `build_conversation` use `conversation_id_for(build_input)`. Makes T009 pass
- [X] T015 [US1] In `src/worker/conversation.py` change `create_run_conversation(ctx, credentials, properties, result)` to read `ctx.conversation_location` (raise `ConversationCreationError("conversation location not resolved")` if `None` — defensive, callers guard it), keep `deployment_kind` logging and `get_application` model resolution, build `ConversationBuildInput(**location.model_dump(), model_id=model_id)`, PUT at `conversation.id.removeprefix(CONVERSATION_ID_PREFIX)`; remove its bucket and schedule lookups; update docstring. Makes T010 pass (depends on T014)
- [X] T016 [US1] In `src/worker/executors/chat_completion.py` build `extra_headers` as `with_conversation_id(strip_user_agent(properties.extra_headers), conversation_id_for(ctx.conversation_location) if ctx.conversation_location else None)`; importing `conversation_id_for` from `src.worker.conversation` is allowed (same layer). Do not touch `responses.py`, `rest.py`, `sse.py`. Makes T011 pass (depends on T006, T014)
- [X] T017 [US1] In `src/worker/jobs.py` add `async def _resolve_conversation_location(ctx: RunContext, credentials: OboCredentials, properties: ScheduleProperties) -> None` that returns immediately unless `properties.target_type == TargetType.CHAT_COMPLETION and properties.create_conversation`; otherwise awaits `resolve_conversation_location`, sets `ctx.conversation_location`, and logs info `conversation_id_resolved` with `run_id`, `schedule_id`, `deployment_kind` (`"application"` if `deployment_id.startswith(APPLICATION_DEPLOYMENT_PREFIX)` else `"model"`). Call it in `execute_run` right after `credentials = await _resolve_credentials(ctx)` and before `resolve_executor`/`execute_with_policy`. (Failure handling added in US2.) Makes T012(a–e) and T013 pass (depends on T008, T014–T016)

**Checkpoint**: US1 tests green; for an ASCII display name the header equals the stored `conversation_id`.

---

## Phase 4: User Story 2 — A missing bucket never breaks a run (Priority: P1)

**Goal**: Any failure resolving the location → request sent without the header, one warning, conversation skipped with an info
log, `conversation_id` stays NULL, run outcome unchanged.

**Independent Test**: Make `get_bucket` raise `ConversationCreationError`; `execute_run` still dispatches, persists success,
logs exactly one `conversation_id_resolve_failed` and one `conversation_create_skipped`, never calls `put_conversation` or
`set_conversation_id`.

### Tests (write first, must fail)

- [X] T018 [P] [US2] In `tests/unit/worker/test_jobs.py` add `class TestConversationLocationFailure` with `structlog.testing.capture_logs()`: for each failure — resolver raising `ConversationCreationError("bucket request failed with status 503")`, `ConversationCreationError("bucket response missing 'bucket'")`, `ConversationCreationError("payload.model must be a non-empty string …")`, `ConversationCreationError("schedule not found: …")`, and an unexpected `RuntimeError("boom")` — assert: executor still called once with `ctx.conversation_location is None`; run persisted via `mark_success` when the target succeeds (and `mark_error` path unchanged when it fails); exactly one log event `conversation_id_resolve_failed` at level `warning` with `run_id`, `schedule_id`, `reason` equal to the exception text; no log value contains the OBO token `"obo-token"`; `create_run_conversation` not awaited; one info event `conversation_create_skipped` with `reason == "conversation_id_unresolved"` only when the run succeeded; `set_conversation_id` never called
- [X] T019 [P] [US2] In `tests/unit/worker/executors/test_chat_completion.py` add a test that with `ctx.conversation_location = None` the request kwargs contain no key equal to `X-CONVERSATION-ID` case-insensitively unless the caller supplied it (header omitted, never empty string)

### Implementation

- [X] T020 [US2] In `src/worker/jobs.py` wrap the body of `_resolve_conversation_location` in `try/except Exception as exc` → `logger.warning("conversation_id_resolve_failed", run_id=..., schedule_id=..., reason=str(exc))` and leave `ctx.conversation_location = None` (comment: best-effort like `_maybe_create_conversation`; a run must never fail because of conversation work). Makes T018 dispatch/log assertions pass
- [X] T021 [US2] In `src/worker/jobs.py` `_maybe_create_conversation`: after the existing target-type/`create_conversation` guard, if `ctx.conversation_location is None` log info `conversation_create_skipped` with `run_id`, `schedule_id`, `reason="conversation_id_unresolved"` and return; update its docstring. Makes T018 skip assertions pass; existing `_maybe_create_conversation` tests in `tests/unit/worker/test_jobs.py` must set `ctx.conversation_location` in their `_ctx()` helpers so they keep exercising the create path

**Checkpoint**: US1 + US2 green; no path where conversation work changes the run status.

---

## Phase 5: User Story 3 — Non-ASCII display names are safe (Priority: P1)

**Goal**: Cyrillic, emoji, control characters and literal `%` in display names never fail a run; header is printable ASCII.

**Independent Test**: Through the real `ChatCompletionExecutor` with a real `httpx.MockTransport`-backed client, a run for
display name `"Щоденний звіт 📈"` succeeds and the sent header is ASCII and matches the encoded stored id.

### Tests (write first, must fail if T006/T016 were bypassed)

- [X] T022 [P] [US3] In `tests/unit/worker/executors/test_chat_completion.py` add `class TestConversationHeaderOnTheWire` using a real `httpx.AsyncClient(transport=httpx.MockTransport(handler))` passed via `RunContext.http_client` (no `AsyncAzureOpenAI` mock) returning a minimal valid chat completion JSON; parametrize display names `"Daily report"`, `"Щоденний звіт"`, `"📈 KPI"`, `"50% done"`, `"line1\nline2"`: assert the run returns a result (no `ScheduleRunError`), the captured request header `x-conversation-id` is ASCII-only, contains no `\n`/`\r`, and `urllib.parse.unquote(header) == urllib.parse.unquote(conversation_id_for(location))` for names without literal `%`, and `header == conversation_id_for(location)` for `"Daily report"`
- [X] T023 [P] [US3] In `tests/unit/worker/test_conversation.py` add a test that for display name `"50% done/Q1\\x"` the stored id from `build_conversation` contains `50%25 done%2FQ1%5Cx` and `encode_header_value(conversation_id_for(location))` leaves that segment unchanged (no double encoding of `%`)

### Implementation

- [X] T024 [US3] Fix any failure surfaced by T022–T023 in `src/lib/utils/conversation_header.py` or `src/worker/executors/chat_completion.py` only (no other files); if none, no code change — record "no change needed" in the task's commit message

**Checkpoint**: P1 stories (US1–US3) complete — MVP shippable.

---

## Phase 6: User Story 4 — Scheduler traffic is identifiable (Priority: P2)

**Goal**: Verify and document the User-Agent behaviour implemented in `f94816b`; close coverage gaps for the calls this
feature moved.

**Independent Test**: Every outbound request (bucket, OBO credentials, PUT, all four target types) carries
`User-Agent: ai-dial-scheduler/<version>`; caller User-Agent ignored.

- [X] T025 [P] [US4] Review `tests/unit/lib/utils/test_user_agent.py`, `tests/unit/lib/clients/test_http_client.py`, `tests/unit/worker/executors/test_user_agent.py` against spec FR-011/FR-012 and add any missing case in those files: a `DialClient.get_bucket` call made through `create_http_client(...)` with `httpx.MockTransport` sends `User-Agent: ai-dial-scheduler/<version>` (bucket is now called before the target request); all four executors ignore `user-agent`/`USER-AGENT` in `extra_headers`
- [X] T026 [US4] If T025 reveals a gap, fix it in `src/lib/clients/http_client.py` or the affected executor only; otherwise no code change

**Checkpoint**: FR-011/FR-012 covered by tests.

---

## Phase 7: User Story 5 — Run logs and traces are correlated (Priority: P2)

**Goal**: Once resolved, `conversation_id` is on every later log entry of the run; new events carry `trace_id`/`span_id`.

**Independent Test**: With an SDK `TracerProvider` and an active span, `execute_run` logs `conversation_id_resolve_failed` (or
`conversation_id_resolved` and later events) with `trace_id`, `span_id`, and — when resolved — `conversation_id`.

### Tests (write first, must fail)

- [X] T027 [P] [US5] In `tests/unit/worker/test_jobs.py` add `class TestConversationLogContext`: (a) after successful resolution, events emitted later in the run (`attempt_start`/`run_complete`, captured with `structlog.testing.capture_logs()` plus `structlog.contextvars.merge_contextvars` in the processor chain, or by asserting `structlog.contextvars.get_contextvars()["conversation_id"]` inside a patched executor) carry `conversation_id == conversation_id_for(location)` (raw, unencoded); (b) after resolution failure, no `conversation_id` is bound
- [X] T028 [P] [US5] In `tests/unit/lib/utils/test_logging.py`, add a test that renders a `conversation_id_resolve_failed` warning through `LoggingConfig`'s structured formatter (or calls `_add_trace_span_ids`) inside `opentelemetry.sdk.trace.TracerProvider().get_tracer("t").start_as_current_span("job:schedule:x")` and asserts the output has 32-hex `trace_id` and 16-hex `span_id`

### Implementation

- [X] T029 [US5] In `src/worker/jobs.py` `_resolve_conversation_location`, after setting `ctx.conversation_location`, call `bind_contextvars(conversation_id=conversation_id_for(ctx.conversation_location))` (import `conversation_id_for` from `src.worker.conversation`). No change to `src/lib/utils/logging.py`. Makes T027 pass; T028 should pass without code change (existing processor) — if not, fix only `src/lib/utils/logging.py`

**Checkpoint**: All user stories complete.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T030 [P] Update `CONFIGURATION.md` (section near `create_conversation` / `extra_headers`, after the User-Agent bullet added in `f94816b`): chat completion runs with `create_conversation: true` send `X-CONVERSATION-ID` = the run's `conversation_id` (percent-encoded outside printable ASCII), it replaces any caller `X-CONVERSATION-ID`, other target types do not send it, and if the owner's bucket cannot be resolved the header is omitted, a warning is logged and no conversation is saved
- [X] T031 [P] Update `documentation/properties.md` `create_conversation` and `extra_headers` bullets with the same rules (precedence over caller value; omitted on resolution failure; conversation then not saved)
- [X] T032 Run `uv run ruff format src tests`, `uv run ruff check src tests`, `uv run mypy src tests`; fix all findings
- [X] T033 Run `make lint` and `make test` (full suite with coverage); fix failures; confirm new modules `src/lib/utils/conversation_header.py` and changed lines in `src/worker/conversation.py`, `src/worker/jobs.py`, `src/worker/executors/chat_completion.py` are covered
- [X] T034 Spawn `code-review-agent` (no main-agent context) on the branch diff vs `development` (commits `f94816b..HEAD` plus working tree); save its report to `specs/043-conversation-id-header/code-review-report.md`; address comments and repeat until no open issues (constitution SDLC)
- [X] T035 Validate `specs/043-conversation-id-header/quickstart.md` §1 (automated checks); the manual §2–§3 scenarios are optional and their behaviour is covered by the §1 unit tests
- [ ] T036 After deployment to dev, execute quickstart §4 (Tempo, ADAS ClickHouse, Loki, DIAL Admin, Cyrillic continuation), including whether DIAL Chat's continuation `chat_id` matches the scheduler's encoded value for a non-ASCII name
- [ ] T037 [P] File follow-ups outside this repo: ADAS classifier rule mapping `ai-dial-scheduler/*` to a `scheduler` client type; deployment fix for duplicated OTEL log export (two logging handlers)

---

## Phase 9: PR review follow-ups

**Purpose**: Address PR review comments (items not related to header encoding).

- [X] T038 [P] Add `without_headers` in `src/lib/utils/headers.py` (case-insensitive drop) with tests in `tests/unit/lib/utils/test_headers.py`; reimplement `strip_user_agent` on top of it
- [X] T039 Drop caller auth headers in any casing: `http_request_headers` (REST/SSE: OBO header name, `User-Agent`) and `sdk_extra_headers` (chat completion/responses: OBO header name, `api-key`, `Authorization`, `User-Agent`) in `src/worker/executors/base.py`, used by all four executors; on-the-wire tests in `tests/unit/worker/executors/test_auth_headers.py`
- [X] T040 Bound location resolution with `asyncio.timeout(CONVERSATION_LOCATION_TIMEOUT_SECONDS)` (10 s) in `src/worker/jobs.py`; timeout → `conversation_id_resolve_failed` with `reason="timed out after …"`; test in `tests/unit/worker/test_jobs.py`
- [X] T041 Over-long encoded id (> 4096 bytes): keep the location and save the conversation, omit only the header (`RunContext.conversation_id` unset, `conversation_id_header_omitted` warning); move the check from `resolve_conversation_location` to `execute_run`; executor reads `ctx.conversation_id`
- [X] T042 Record `display_name` on `RunContext.schedule_display_name` in `_resolve_credentials`; `resolve_conversation_location` reads it and no longer queries the database
- [X] T043 [P] Document in `CONFIGURATION.md` and `documentation/properties.md`: auth-header dropping, 10 s bound, header-only omission over 4096 bytes, and that non-DIAL targets receive the header (owner bucket id + display name)
- [X] T044 Update spec, contracts, research (R2a, R4, R9–R11), data model and quickstart for T038–T043

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** → **Foundational (Phase 2)** → user stories.
- **US1 (Phase 3)** depends on Phase 2. It is the MVP and the base for US2, US3 and US5 (they extend `_resolve_conversation_location` / the executor built in US1).
- **US2 (Phase 4)** depends on US1 (T017).
- **US3 (Phase 5)** depends on US1 (T016) — tests can be written in parallel with US2.
- **US4 (Phase 6)** independent of US1–US3 (User-Agent already implemented); can run any time after Phase 1.
- **US5 (Phase 7)** depends on US1 (T017); independent of US2/US3.
- **Polish (Phase 8)** after all desired stories; T036/T037 after deployment.

### Within Each Story

- Tests first, confirmed failing → implementation → story checkpoint.
- `src/worker/conversation.py` tasks (T014 → T015) before executor/jobs tasks (T016, T017).
- `src/worker/jobs.py` edits are sequential: T017 → T020 → T021 → T029.

### Parallel Opportunities

- Phase 2: T002/T003 (same new test file — write together), T004 in parallel; T006 and T007 in parallel; T008 after T007.
- US1 tests T009, T010, T011, T012, T013 — different files except T009/T010 (same file, write sequentially or together).
- US2 T018 ∥ T019; US3 T022 ∥ T023; US4 T025 ∥ any US1–US3 task; US5 T027 ∥ T028.
- Polish T030 ∥ T031 ∥ T037.

## Parallel Example: User Story 1

```text
# Write failing tests together (different files):
T011 tests/unit/worker/executors/test_chat_completion.py  — header present/absent/precedence
T012 tests/unit/worker/test_jobs.py                       — resolve once, before attempts, reused for PUT
T013 tests/unit/lib/services/test_run_launcher.py         — on-demand path
T009+T010 tests/unit/worker/test_conversation.py          — resolver + create_run_conversation reuse

# Then implement in order:
T014 → T015 (src/worker/conversation.py) → T016 (executor) → T017 (jobs)
```

## Implementation Strategy

### MVP First (US1 + US2 + US3 — all P1)

1. Phase 1 → Phase 2.
2. US1 → validate header == `conversation_id` (unit).
3. US2 → validate failure isolation. **Do not ship US1 without US2 and US3**: without them a bucket error or a non-ASCII name could fail runs.
4. US3 → validate encoding on the wire.
5. STOP and VALIDATE: `make lint`, `make test`.

### Incremental Delivery

- P1 bundle (US1–US3) → US5 (log correlation) → US4 (coverage of existing User-Agent) → Polish → deploy → dev verification (T036).

### Notes

- Never log OBO header values, prompts or responses.
- Keep `__init__.py` files empty; use absolute imports; functions ≤ 3 parameters.
- Commit after each phase checkpoint on `feature/scheduler-user-agent`; commit messages end with the attribution line from the session.
