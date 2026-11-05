---
description: "Task list for Execute Schedules with Per-User Credentials"
---

# Tasks: Execute Schedules with Per-User Credentials

**Input**: Design documents from `specs/026-add-user-credentials-usage/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: INCLUDED — the project constitution (III. Test-First, NON-NEGOTIABLE) mandates TDD. Every test task MUST be written and confirmed failing before its implementation task.

**Organization**: Tasks are grouped by user story. US1 and US2 are both P1 (US2 extends the worker path added in US1). US3 is P2.

**Status**: All phases complete. Note: task descriptions below reference `tests/lib/...`, `tests/worker/...`, `tests/api/...` per the generic template convention; the actual repo layout is `tests/unit/lib/...`, `tests/unit/worker/...`, `tests/unit/api/...` (unit) and `tests/integration/...` — all work was done in the real paths.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1, US2, US3 (setup/foundational/polish have no story label)

## Path Conventions

Web service + worker with shared library: `src/api/`, `src/worker/`, `src/lib/`; tests mirror under `tests/`; migrations under `migrations/versions/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm a clean baseline before changes.

- [X] T001 Run `uv sync` and confirm the existing suite is green (`uv run pytest`, `uv run ruff check src tests`, `uv run mypy src`) so later diffs are attributable to this feature.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared building blocks required by every user story — settings, the `service_id` column + migration, credential schemas, the OBO client method, and worker plumbing.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Tests (write first, confirm failing)

- [X] T002 [P] Settings test in `tests/unit/lib/test_settings.py`: assert `scheduler_app_id == "dial_scheduler"` and `user_id_path_list == ["sub", "project"]` defaults.
- [X] T003 [P] Repository test in `tests/unit/lib/repositories/test_schedule_repository.py`: `get_by_id` returns the schedule (incl. `service_id`, `created_by`) regardless of owner, and `None` when absent.
- [X] T004 [P] DialClient test in `tests/unit/lib/clients/test_dial_client.py`: `get_obo_credentials` POSTs to `/v1/ops/external-service/obo-credentials` with `Api-Key`, body `url=applications/{scheduler_app_id}/external_services/{service_id}` and `owner_user_id`; parametrized BOTH external-service auth types per `dial/core/config-template.json` — API_KEY (`header_name="Api-Key"`) and OAUTH (`header_name="Authorization"`, value `"Bearer <token>"`); maps non-2xx, missing fields, and transport errors to a credentials failure carrying the response.
- [X] T005 [P] Migration test in `tests/unit/migrations/test_add_service_id.py`: after upgrade, pre-existing rows have `service_id == "dial-oauth"` and the column is NOT NULL.

### Implementation

- [X] T006 [P] Add `scheduler_app_id: str = "dial_scheduler"` and change `user_id_path_list` default to `["sub", "project"]` in `src/lib/settings.py`.
- [X] T007 [P] Add shared constants for the OBO call only (OBO path `/v1/ops/external-service/obo-credentials` and the `applications/{}/external_services/{}` url template) in `src/lib/constants.py`. No "default service id" constant added — `"dial-oauth"` is only a migration backfill value for existing rows (the API requires `service_id`; MCP receives it as docstring guidance text).
- [X] T008 [P] Created credential schemas `OboCredentialsRequest`, `OboCredentials` (`header_value: SecretStr`), and `RunError` (`stage: Literal["credentials","target"]`, `error_text`, `credentials_response`) in `src/lib/schemas/credentials.py`.
- [X] T009 [P] Added `service_id: str` column (NOT NULL) to the `Schedule` model in `src/lib/models/schedule.py`.
- [X] T010 Created Alembic migration `20260714_1000_c3f1a2b4d5e6_add_service_id_to_schedules.py` in `migrations/versions/`: add nullable column → backfill existing rows with the hardcoded literal `'dial-oauth'` (inline in the migration, not via an app constant) → set NOT NULL; `downgrade` drops it.
- [X] T011 [P] Added `auth_header_name: str` and `auth_header_value: SecretStr` fields to `RunContext` in `src/lib/schemas/run.py`.
- [X] T012 [P] Added `get_by_id(schedule_id) -> Schedule | None` (owner-agnostic PK lookup) to `ScheduleRepository` in `src/lib/repositories/schedule_repository.py`.
- [X] T013 [P] Added `OboCredentialsError` (carrying the DIAL response status/body) to `src/lib/exceptions/errors.py`.
- [X] T014 Added `get_obo_credentials(api_key, request: OboCredentialsRequest) -> OboCredentials` to `DialClient` in `src/lib/clients/dial_client.py`, authenticating with the scheduler service key, reusing the injected `httpx.AsyncClient`; raises `OboCredentialsError` on failure.

**Checkpoint**: Column exists and is migrated; settings, schemas, repository lookup, and OBO client method are available and unit-tested. 513+ unit tests passing.

---

## Phase 3: User Story 1 - Schedule runs with the creator's credentials (Priority: P1) 🎯 MVP

**Goal**: A schedule executes with OBO credentials fetched for its owner and service; the shared `Api-Key` is no longer sent on the target request.

**Independent Test**: With a stubbed OBO endpoint returning `{"header_name":"Api-Key","header_value":"user-token"}`, a schedule row with `service_id="dial-oauth"` executes; the outgoing target request carries `Api-Key: user-token` (not the shared key) and the run is `success`.

### Tests (write first, confirm failing)

- [X] T015 [P] [US1] Unit test in `tests/unit/worker/executors/test_rest.py`: `RestExecutor` sends the OBO header from `RunContext` and NO shared `settings.dial_api_key`; OBO header overrides a same-named `extra_headers` entry. Parametrized BOTH auth-type shapes (API_KEY `Api-Key` / OAUTH `Authorization: Bearer`).
- [X] T016 [P] [US1] Unit test in `tests/unit/worker/executors/test_sse.py`: same expectations for `SseExecutor`, both auth-type shapes.
- [X] T017 [P] [US1] Unit test in `tests/unit/worker/executors/test_chat_completion.py`: `ChatCompletionExecutor` sets `AsyncAzureOpenAI.api_key` to the OBO `header_value` with a leading `"Bearer "` stripped, and never uses the shared `settings.dial_api_key`; parametrized both auth-type shapes.
- [X] T018 [P] [US1] Unit test in `tests/unit/worker/test_jobs.py`: success path resolves owner+service via `get_by_id`, calls `get_obo_credentials`, injects header into `RunContext`, then runs the executor and marks success.
- [X] T019 [P] [US1] Integration coverage: extended `tests/integration/test_worker_dispatch.py` (real `AsyncIOScheduler` end-to-end fire, credentials mocked) and `tests/integration/test_echo_endpoints.py` (real REST/SSE/chat_completion executors driven with an OBO-shaped `RunContext`) rather than a single new file — together these prove the target receives the per-user header end-to-end.

### Implementation

- [X] T020 [P] [US1] Updated `RestExecutor` in `src/worker/executors/rest.py`: headers built as `{**extra_headers, ctx.auth_header_name: ctx.auth_header_value.get_secret_value()}`; removed `settings.dial_api_key` injection.
- [X] T021 [P] [US1] Updated `SseExecutor` in `src/worker/executors/sse.py`: same header change; removed shared key.
- [X] T022 [P] [US1] Updated `ChatCompletionExecutor` in `src/worker/executors/chat_completion.py`: `AsyncAzureOpenAI(api_key=...)` set to `ctx.auth_header_value.get_secret_value()`, stripping a leading `"Bearer "` prefix if present; `properties.extra_headers` still forwarded; shared-key usage removed.
- [X] T023 [US1] Updated `_execute_scheduled_job_inner` in `src/worker/jobs.py`: after creating the in-progress run and validating properties, `_resolve_credentials` loads the schedule via `ScheduleRepository.get_by_id`, builds `OboCredentialsRequest`, calls `DialClient.get_obo_credentials`, populates `RunContext.auth_header_name/value` on success, then proceeds to `execute_with_policy`.
- [X] T024 [US1] Added structured logging (`credentials_fetch_start`/`credentials_fetch_succeeded`) in `src/worker/jobs.py`, keyed by `schedule_id`/`run_id`/`service_id`/`owner_user_id`; `header_value` confirmed never logged (secret-hygiene tests + grep review).

**Checkpoint**: MVP — schedules execute with per-user credentials; no shared key on target requests. Verified against a real DIAL/Keycloak stack (`tests/integration/test_real_dial_credentials.py`).

---

## Phase 4: User Story 2 - Missing or invalid credentials fail the run clearly (Priority: P1)

**Goal**: When OBO credentials cannot be obtained, the run is marked `error` with the credentials-lookup response, the target request is not sent, and the failure is distinguishable from a target error.

**Independent Test**: With the OBO stub returning 404, a run is recorded `error` with `result.stage == "credentials"` and `result.credentials_response` holding the DIAL response; the target executor is never invoked and the retry budget is untouched.

### Tests (write first, confirm failing)

- [X] T025 [P] [US2] Repository test in `tests/unit/lib/repositories/test_schedule_run_repository.py` pattern extended via `mark_credentials_error`: the credential-error result stores `stage="credentials"`, `error_text`, and `credentials_response`; target errors keep the existing plain `{"error_text": ...}` shape via `mark_error`.
- [X] T026 [P] [US2] Unit tests in `tests/unit/worker/test_jobs.py` (`TestExecuteScheduledJobCredentialFailure`): OBO failure marks the run `error` (credentials stage), does NOT call the executor, and does NOT invoke `execute_with_policy`/retries; a missing schedule row and a schedule-lookup DB exception also yield a credentials error.
- [X] T027 [P] [US2] Integration coverage: covered via the unit-level `TestExecuteScheduledJobCredentialFailure` suite (full DB session mocking of the credentials path) rather than a separate DB-backed integration file — the credential failure branch does not depend on DB-specific behavior beyond what `ScheduleRunRepository.mark_credentials_error` already exercises against a real DB in other lifecycle integration tests.

### Implementation

- [X] T028 [US2] Added `mark_credentials_error(run_id, error: RunError)` to `ScheduleRunRepository` in `src/lib/repositories/schedule_run_repository.py`, storing `result = error.model_dump()`; existing `mark_error` unchanged for target failures.
- [X] T029 [US2] Extended `_resolve_credentials`/`_execute_scheduled_job_inner` in `src/worker/jobs.py`: on `OboCredentialsError`, a missing schedule, or a schedule-lookup exception, persists the credentials error via `_persist_credentials_error` and raises before the executor is ever resolved/called; logs `credentials_fetch_failed` without the secret.

**Checkpoint**: Credential failures are terminal, diagnosable, and never fall back to the shared key.

---

## Phase 5: User Story 3 - Owners specify which external service a schedule uses (Priority: P2)

**Goal**: The API requires `service_id` on create/update and returns it; MCP tool guidance defaults clients to `dial-oauth`.

**Independent Test**: `POST /schedules/` without `service_id` → 422; with `service_id` it persists and appears in the response and list; MCP/OpenAPI examples include `service_id`.

### Tests (write first, confirm failing)

- [X] T030 [P] [US3] Endpoint tests in `tests/unit/api/v1/endpoints/test_schedules.py` and `tests/integration/test_schedules_api.py`: create/update without `service_id` → 422; with it, response and list items include `service_id`.
- [X] T031 [P] [US3] Service tests in `tests/unit/lib/services/test_schedule_service.py` (`TestScheduleServiceServiceId`): `create`/`update` persist `service_id`, restore it on job-registration failure, and `_to_response`/`list` return it.

### Implementation

- [X] T032 [P] [US3] Added required `service_id: str` (non-empty validator) to `ScheduleRequest`, and `service_id` to `ScheduleResponse` and `ScheduleListItem` in `src/lib/schemas/schedule.py`.
- [X] T033 [US3] Updated `ScheduleService.create`/`update`/`_to_response`/`list` in `src/lib/services/schedule_service.py` to persist and surface `service_id` (including restore-on-failure); updated `ScheduleRepository.update` to accept `service_id`.
- [X] T034 [P] [US3] Updated `src/api/v1/endpoints/schedules.py`: added `service_id` to `_SCHEDULE_EXAMPLES`, and docstring guidance ("use `service_id: \"dial-oauth\"` unless another external service is required") on create/update, flowing into OpenAPI/MCP.

**Checkpoint**: Owners select the service via the API; MCP clients are guided to `dial-oauth`. Verified via 104+ integration tests against a real DB.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T035 [P] Secret-hygiene review: grepped worker/executor/client code for `auth_header_value` usage (always via `.get_secret_value()`) and all `logger.*` calls in `src/worker/jobs.py`/`dial_client.py` — confirmed the OBO secret is never logged or persisted; backed by `*_not_in_logs` tests in each executor's test suite.
- [X] T036 [P] Real-DIAL integration test (opt-in, requires the local `dial/docker-compose.yml` stack) in `tests/integration/test_real_dial_credentials.py`: uses `scripts/dial_sign_in.py` to sign the test user in to `applications/dial_scheduler/external_services/dial-oauth`, then calls the real `DialClient.get_obo_credentials` (no mocking) and asserts a valid header is returned; skips automatically when the stack is unreachable. Run against the actual local stack — confirmed DIAL returns `{"header_name": "Authorization", "header_value": "Bearer <token>", "expires_at": ...}` for `dial-oauth`, validating the `OboCredentials` contract end-to-end.
- [X] T037 [P] [Docs] Added `service_id` to every create/update request body in `tests/helpers/verify.http` (8 request bodies), including one example using `dial-api-key`, plus an explanatory comment on `service_id` semantics and the sign-in prerequisite.
- [X] T038 [P] [Docs] Updated `README.md`: new "Per-User Credentials (external-service sign-in)" section documenting per-user execution, the required `service_id` field, both external-service auth types, and the `scripts/dial_sign_in.py` helper + prerequisite; updated `USER_ID_PATH_LIST` default and added `SCHEDULER_APP_ID` to both the API and Worker env-var tables.
- [X] T039 Ran quality gates: `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src` — all pass with zero findings.
- [X] T040 Ran full suite with coverage: 515 unit tests + 105 integration tests (104 DB-backed + 1 real-DIAL) all passing; overall unit coverage 91%, with the new credential-failure branches in `jobs.py` specifically covered by dedicated tests.
- [X] T041 Quickstart validation executed live: created/updated schedules via the real API requiring `service_id`; ran the real-DIAL credentials test proving the happy-path OBO fetch; unit/integration suites cover the missing-credentials and secret-hygiene scenarios end-to-end.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: none.
- **Foundational (Phase 2)**: after Setup — BLOCKS all user stories.
- **US1 (Phase 3)**: after Foundational.
- **US2 (Phase 4)**: after US1 (extends `src/worker/jobs.py` from T023) and Foundational.
- **US3 (Phase 5)**: after Foundational; independent of US1/US2 (touches API/schemas/service only) — can run in parallel with US1/US2.
- **Polish (Phase 6)**: after all desired stories.

### Within Each Story

- Tests written and failing before implementation (Constitution III).
- Models/schemas before services; services before endpoints; core before integration.

### Parallel Opportunities

- Foundational tests T002–T005 in parallel; implementation T006–T009, T011–T013 in parallel ([P]); T010 after T009; T014 after T008/T013.
- US1 tests T015–T019 in parallel; executor impls T020–T022 in parallel; T023 after them; T024 after T023.
- US3 (Phase 5) can be developed in parallel with US1/US2 by a second developer once Foundational is done.

---

## Parallel Example: Foundational

```bash
# Tests first (parallel):
Task: "Settings test in tests/unit/lib/test_settings.py"
Task: "Repository get_by_id test in tests/unit/lib/repositories/test_schedule_repository.py"
Task: "DialClient OBO test in tests/unit/lib/clients/test_dial_client.py"
Task: "Migration backfill test in tests/unit/migrations/test_add_service_id.py"

# Then independent implementation files (parallel):
Task: "Settings changes in src/lib/settings.py"
Task: "Constants in src/lib/constants.py"
Task: "Credential schemas in src/lib/schemas/credentials.py"
Task: "service_id column in src/lib/models/schedule.py"
Task: "RunContext fields in src/lib/schemas/run.py"
```

---

## Implementation Strategy

### MVP First (US1)

1. Phase 1 Setup → 2. Phase 2 Foundational (CRITICAL) → 3. Phase 3 US1 → **STOP and validate** per-user execution → demo.

### Incremental Delivery

1. Setup + Foundational → foundation ready.
2. US1 (per-user execution) → validate → the MVP.
3. US2 (fail-loud on missing credentials) → validate.
4. US3 (service selection API + MCP guidance) → validate.
5. Polish (quality gates, coverage, quickstart).

### Notes

- [P] = different files, no incomplete dependencies.
- `src/worker/jobs.py` is edited by US1 (T023/T024) and US2 (T029) — sequence US2 after US1.
- Commit after each task or logical group; verify tests fail before implementing.
