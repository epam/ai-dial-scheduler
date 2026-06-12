# Tasks: User Identity Authentication for Schedule Endpoints

**Input**: Design documents from `/specs/011-user-id-auth/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: INCLUDED — the project constitution mandates Test-First (TDD, NON-NEGOTIABLE).
Every test task MUST be written and confirmed FAILING before its implementation task.

**Organization**: Tasks are grouped by user story (US1=P1 happy path, US2=P1 rejection,
US3=P2 configurable fallback) to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 / US2 / US3 (Setup, Foundational, Polish have no story label)

## Path Conventions

Web service layout (per plan.md): API service in `src/api/`, shared logic in `src/lib/`,
tests mirror `src/` under `tests/`. All `__init__.py` files MUST be empty (constitution).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the new package/test directories required by later phases.

- [X] T001 [P] Create `src/lib/clients/` package with an empty `src/lib/clients/__init__.py`
- [X] T002 [P] Create empty test package files: `tests/unit/lib/utils/__init__.py`, `tests/unit/lib/clients/__init__.py`, `tests/unit/api/dependencies/__init__.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Configuration and exception types required by all three user stories.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 [P] Add `user_id_path_list: list[str] = ["userClaims.mail.0", "project"]` to the `Settings` class in `src/lib/settings.py`
- [X] T004 [P] Add `UserIdNotResolvedError` and `DialServiceError` (subclasses of `SchedulerError`) to `src/lib/exceptions/errors.py` with Google-style docstrings

**Checkpoint**: Settings expose the candidate-path list and the domain exceptions exist —
user stories can now begin.

---

## Phase 3: User Story 1 - Operate on my own schedules with my identity (Priority: P1) 🎯 MVP

**Goal**: Replace `MOCK_USER_ID` on all six schedule/run endpoints with a real identity
resolved from DIAL `GET /v1/info`, so each endpoint operates and scopes data under the
caller's resolved `user_id`.

**Independent Test**: With DIAL mocked to return `{"userClaims": {"mail": ["alice@example.com"]}}`,
create a schedule with `Api-Key: alice-key` and confirm `created_by == "alice@example.com"`;
confirm a second identity sees disjoint schedules.

### Tests for User Story 1 (write FIRST, confirm FAILING) ⚠️

- [X] T005 [P] [US1] Unit test happy-path extraction (first candidate path resolves a non-empty string) in `tests/unit/lib/utils/test_user_id.py`
- [X] T006 [P] [US1] Unit test `DialClient.get_user_info` success (2xx JSON → dict, sends `Api-Key` header to `{dial_url}/v1/info`, uses injected httpx client) in `tests/unit/lib/clients/test_dial_client.py`
- [X] T007 [P] [US1] Unit test `get_user_id` success path returns resolved id (DIAL client + extractor mocked) in `tests/unit/api/dependencies/test_auth.py`

### Implementation for User Story 1

- [X] T008 [P] [US1] Implement `extract_user_id(data, paths) -> str | None` (dotted keys, digit segments as list indices, first non-empty stripped string wins) in `src/lib/utils/user_id.py`
- [X] T009 [US1] Implement `DialClient.__init__(http_client, base_url)` and `get_user_info(api_key)` success path (`GET {base_url}/v1/info`, `Api-Key` header, `settings.http_timeout_seconds`) in `src/lib/clients/dial_client.py` (uses constant `API_KEY_HEADER`)
- [X] T010 [US1] Implement `get_dial_client` (reads `request.app.state.app_client` + `settings.dial_url`) and `get_user_id` success path plus `UserIdDep = Annotated[str, Depends(get_user_id)]` in `src/api/dependencies/auth.py` (depends on T008, T009)
- [X] T011 [US1] Inject `UserIdDep` into all four routes in `src/api/v1/endpoints/schedules.py`, passing the resolved id to `service.create/update/list/get` and removing the `MOCK_USER_ID` import/usage (depends on T010)
- [X] T012 [US1] Inject `UserIdDep` into both routes in `src/api/v1/endpoints/runs.py`, passing the resolved id to `service.list_runs/get_run` and removing the `MOCK_USER_ID` import/usage (depends on T010)
- [X] T013 [P] [US1] Update `tests/unit/api/v1/endpoints/test_schedules.py` to override `get_user_id` with a fixed id and assert that id reaches the service (replace `"mock_user_id"` assertions) (depends on T011)
- [X] T014 [P] [US1] Update `tests/unit/api/v1/endpoints/test_runs.py` to override `get_user_id` with a fixed id and assert that id reaches the service (depends on T012)
- [X] T015 [US1] Add structured success/resolution logging (reason-free success entry, NO secret values) in `get_user_id` / `DialClient` via `src.lib.utils.logging.logger` (depends on T010)
- [X] T016 [US1] Integration test proving per-identity isolation (two mocked DIAL identities see disjoint schedules) in `tests/integration/test_user_id_auth.py`

**Checkpoint**: All six endpoints resolve identity from DIAL and scope data to it; MVP usable.

---

## Phase 4: User Story 2 - Reject callers whose identity cannot be established (Priority: P1)

**Goal**: Reject requests with missing/invalid credentials or unresolvable identity rather than
silently proceeding — missing key / DIAL-rejected / no-identity → 401; DIAL transport/upstream
error → 502; no schedule data read or written on any rejection.

**Independent Test**: Call any endpoint with no `Api-Key` (→401), with a DIAL-rejected key
(→401), with a DIAL response yielding no identity (→401), and with DIAL unreachable (→502);
confirm no service/data operation occurs in each case.

### Tests for User Story 2 (write FIRST, confirm FAILING) ⚠️

- [X] T017 [P] [US2] Unit tests for `DialClient` error mapping (401/403 → `UserIdNotResolvedError`; other non-2xx → `DialServiceError`; transport error → `DialServiceError`; non-JSON 2xx → `{}`; `Api-Key` value never logged) in `tests/unit/lib/clients/test_dial_client.py`
- [X] T018 [P] [US2] Unit tests for `get_user_id` rejection branches (missing/empty key → 401 with no DIAL call; `UserIdNotResolvedError` → 401; `None` identity → 401; `DialServiceError` → 502; service never called) in `tests/unit/api/dependencies/test_auth.py`

### Implementation for User Story 2

- [X] T019 [US2] Implement `DialClient` error mapping (catch `httpx.HTTPStatusError`/`TransportError`, map 401/403 vs other non-2xx vs transport; tolerate non-JSON body) in `src/lib/clients/dial_client.py` (depends on T009)
- [X] T020 [US2] Implement `get_user_id` rejection mapping with reason-code logging (`missing_api_key`, `dial_rejected`, `no_identity`, `dial_unavailable`) raising `HTTPException(401)` / `HTTPException(502)` in `src/api/dependencies/auth.py` (depends on T010, T019)
- [X] T021 [P] [US2] Integration test: missing `Api-Key` → 401 and no schedule row created; DIAL-rejected key → 401 in `tests/integration/test_user_id_auth.py` (depends on T020)

**Checkpoint**: Unidentifiable callers are rejected with the correct status; data stays isolated.

---

## Phase 5: User Story 3 - Resolve identity from a configurable set of attributes (Priority: P2)

**Goal**: Resolve identity from the ordered `settings.user_id_path_list`, trying each candidate
in turn and using the first that yields a non-empty value, with robust skip rules for
missing/empty/type-mismatched segments.

**Independent Test**: With the first candidate (`userClaims.mail.0`) absent but `project`
present, confirm the `project` value is used; with no candidate resolvable, confirm rejection.

### Tests for User Story 3 (write FIRST, confirm FAILING) ⚠️

- [X] T022 [P] [US3] Unit tests for ordered fallback + skip rules in `extract_user_id` (fallback to later path, empty/whitespace skipped, list index vs dict key, index out of range, type mismatch, no-path → `None`) in `tests/unit/lib/utils/test_user_id.py`
- [X] T023 [P] [US3] Unit test asserting the default `user_id_path_list == ["userClaims.mail.0", "project"]` in `tests/unit/lib/test_settings.py`

### Implementation for User Story 3

- [X] T024 [US3] Extend/confirm `extract_user_id` traversal to satisfy ordered fallback, digit-index handling, and all skip rules in `src/lib/utils/user_id.py` (depends on T008)

**Checkpoint**: Identity resolution is configurable and falls back across candidate paths.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Quality gates and final validation across all stories.

- [X] T025 [P] Run `uv run ruff check src tests` and `uv run ruff format src tests`; fix any findings
- [X] T026 [P] Run `uv run mypy src` and fix all type findings
- [X] T027 Run `uv run pytest --cov=src --cov-report=term-missing`; ensure all new code (auth.py, dial_client.py, user_id.py, setting) is covered and all tests pass
- [X] T028 Execute the `quickstart.md` validation scenarios end-to-end

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories.
- **User Stories (Phase 3–5)**: All depend on Foundational.
  - US1 (P1) is the MVP and creates the shared modules (`user_id.py`, `dial_client.py`,
    `auth.py`) that US2 and US3 extend.
  - US2 depends on US1 (extends `DialClient` and `get_user_id` with rejection branches).
  - US3 depends on US1 (extends `extract_user_id`); independent of US2.
- **Polish (Phase 6)**: Depends on all targeted user stories being complete.

### User Story Dependencies

- **US1 (P1)**: Starts after Foundational. Creates the new files end-to-end (happy path).
- **US2 (P1)**: Starts after US1 (same `dial_client.py` / `auth.py` files — sequential, not
  parallel with US1).
- **US3 (P2)**: Starts after US1 (same `user_id.py` file). Independent of US2 — US2 and US3 can
  proceed in parallel after US1 since they touch different files.

### Within Each User Story

- Tests written and FAILING before implementation (Red→Green).
- Pure helper (`extract_user_id`) and client before the dependency; dependency before endpoint
  wiring; wiring before updating existing endpoint tests.

### Parallel Opportunities

- Setup: T001, T002 in parallel.
- Foundational: T003, T004 in parallel.
- US1 tests: T005, T006, T007 in parallel (different files). T013, T014 in parallel after wiring.
- After US1 completes, US2 and US3 can run in parallel (different files): the US2 chain
  (T017→T019, T018→T020, T021) alongside the US3 chain (T022/T023→T024).
- Polish: T025, T026 in parallel.

---

## Parallel Example: User Story 1 (tests first)

```bash
# Write all US1 unit tests together (must fail before implementation):
Task: "Happy-path extract_user_id test in tests/unit/lib/utils/test_user_id.py"
Task: "DialClient.get_user_info success test in tests/unit/lib/clients/test_dial_client.py"
Task: "get_user_id success-path test in tests/unit/api/dependencies/test_auth.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup → Phase 2 Foundational.
2. Phase 3 US1: identity resolved from DIAL and wired into all six endpoints (happy path).
3. **STOP and VALIDATE**: endpoints scope data to the resolved id; `MOCK_USER_ID` gone from
   endpoint modules. This is a demoable MVP.

### Incremental Delivery

1. Foundation → US1 (MVP: real identity on every endpoint).
2. US2 → robust rejection of unidentifiable callers (401/502).
3. US3 → configurable, ordered, fallback identity resolution.
4. Polish → all quality gates green + quickstart validation.

---

## Notes

- [P] = different files, no dependency on an incomplete task.
- TDD is non-negotiable: confirm each test fails before implementing.
- Never log the `Api-Key` value or full DIAL response body (constitution security rule).
- `lib/` must not import from `src.api`; the dependency lives in `src/api/dependencies/auth.py`.
- Commit after each task or logical group.
