---

description: "Task list for Workload-Identity Database Authentication"
---

# Tasks: Workload-Identity Database Authentication

**Input**: Design documents from `/specs/022-db-workload-identity-auth/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/db-auth-config.md, quickstart.md

**Tests**: INCLUDED — the project constitution mandates Test-First (TDD, NON-NEGOTIABLE). Every test
task MUST be written and confirmed FAILING before its implementation tasks begin.

**Organization**: Tasks grouped by user story. Story labels map to spec.md:
US1 = GCP IAM (P1), US2 = Azure Entra (P2), US3 = Backward compatibility (P1).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 / US2 / US3 (user-story phases only)

## Path Conventions

Single repo, web-service + worker: source in `src/` (`src/api`, `src/worker`, shared `src/lib`),
tests in `tests/`, migrations in `migrations/`. Paths below are repo-root relative.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add the cloud SDK dependencies the new modes require.

- [X] T001 Add `cloud-sql-python-connector[pg8000]` and `azure-identity` to `[project].dependencies` in pyproject.toml
- [X] T002 Run `uv sync` to resolve and commit the updated uv.lock
- [ ] T003 [P] Verify the container still builds with the new deps (`docker build` against Dockerfile); adjust only if a system lib is needed

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Enum, settings, URL helpers, and the centralized mode-aware factories — with the
existing `password`/`connection-string` paths fully preserved. BLOCKS all user stories.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T004 Add `GCP_IAM = "gcp_iam"` and `AZURE_ENTRA = "azure_entra"` to `DbAuthMode` in src/lib/db/enums.py
- [X] T005 [P] Add GCP fields (`db_gcp_instance_connection_name`, `db_gcp_iam_user`, `db_gcp_private_ip`) and Azure fields (`db_azure_entra_user`, `db_azure_managed_identity_client_id`) to `DatabaseSettings` in src/lib/settings.py
- [X] T006 Extend `validate_auth_requirements` in src/lib/settings.py with required-field tuples for `gcp_iam` and `azure_entra` (depends on T004, T005; same file as T005)
- [X] T007 [P] Add a password-less sync-URL helper variant in src/lib/db/url.py (host/port/db/user only, for token/IAM injection)
- [X] T008 [P] Create credential/connector lifecycle module src/lib/db/credentials.py with typed stubs: GCP `Connector` holder + Azure sync/async token-provider interfaces (no logic yet)
- [X] T009 Create mode-aware sync engine factory src/lib/db/sync_engine.py with `build_sync_engine(settings)` implementing the `password`/`connection-string` (psycopg2) branch and a clear dispatch on `db_auth_mode` (depends on T004, T007)
- [X] T010 Create src/lib/db/jobstore.py with `build_jobstore(settings)` returning `SQLAlchemyJobStore(url=...)` for `password`/`connection-string` (depends on T009)
- [X] T011 Rewire src/lib/scheduler.py to use `build_jobstore(settings)` (remove duplicated URL wiring) (depends on T010)
- [X] T012 Rewire src/worker/app.py to use `build_jobstore(settings)` (remove duplicated URL wiring) (depends on T010)
- [X] T013 Rewire migrations/env.py to obtain its connection via `build_sync_engine(settings.db)` for non-URL modes while keeping URL-based modes unchanged (depends on T009)
- [X] T014 Branch async `init_engine` in src/lib/db/engine.py by `db_auth_mode` (dispatch skeleton; `password`/`connection-string` → existing `_init_engine_url_based`; `gcp_iam`/`azure_entra` raise `NotImplementedError` placeholder) (depends on T004)

**Checkpoint**: Foundation ready — existing modes work through the new factories; user stories can begin.

---

## Phase 3: User Story 1 - GCP IAM workload identity (Priority: P1) 🎯 MVP

**Goal**: Deploy on GCP with `gcp_iam` mode — async API engine, sync pg8000 job-store/Alembic
engine, no password — using the Cloud SQL Connector.

**Independent Test**: With `gcp_iam` configured against a Cloud SQL instance and a granted workload
identity, the service connects, serves a DB-backed request, runs the worker heartbeat, and survives
past one credential lifetime (quickstart Scenario C).

### Tests for User Story 1 (write first, confirm FAILING) ⚠️

- [X] T015 [P] [US1] Unit test: async engine selects the GCP Connector `async_creator` path for `gcp_iam` (mock `Connector`) in tests/unit/lib/test_db_engine_selection.py
- [X] T016 [P] [US1] Unit test: `build_sync_engine` builds a `postgresql+pg8000://` engine via Connector `creator` for `gcp_iam` (mock `Connector`) in tests/unit/lib/test_sync_engine.py
- [X] T017 [P] [US1] Unit test: `build_jobstore` passes `engine=` (not `url=`) for `gcp_iam` in tests/unit/lib/test_jobstore.py
- [X] T018 [P] [US1] Unit test: settings validation fails fast naming `db_gcp_instance_connection_name, db_gcp_iam_user` when missing in tests/unit/lib/test_settings_db_auth.py

### Implementation for User Story 1

- [X] T019 [US1] Implement the GCP `Connector` holder (lazy, event-loop-bound for async; sync `connect`; `dispose`) in src/lib/db/credentials.py
- [X] T020 [US1] Implement `_init_engine_gcp_iam` (async `connect_async` + `enable_iam_auth`, IP-type from `db_gcp_private_ip`) and connector disposal in src/lib/db/engine.py (depends on T019)
- [X] T021 [US1] Implement the `gcp_iam` pg8000 branch in src/lib/db/sync_engine.py using the Connector `creator` (depends on T019)
- [X] T022 [US1] Wire the `gcp_iam` `engine=` branch in src/lib/db/jobstore.py (depends on T021)
- [X] T023 [US1] Add structured log entries for GCP engine init / credential acquisition outcome via `src.lib.utils.logging` (mode only, NO secrets) in src/lib/db/engine.py and src/lib/db/sync_engine.py

**Checkpoint**: GCP IAM fully functional for API, worker, and migrations.

---

## Phase 4: User Story 2 - Azure Entra workload identity (Priority: P2)

**Goal**: Deploy on Azure with `azure_entra` mode — async asyncpg engine and sync psycopg2
job-store/Alembic engine both authenticate with a fresh Entra token per connection.

**Independent Test**: With `azure_entra` configured against an Azure PostgreSQL instance and a
granted managed identity, the service connects, serves a DB-backed request, runs the worker
heartbeat, and survives past the token lifetime (quickstart Scenario D).

### Tests for User Story 2 (write first, confirm FAILING) ⚠️

- [X] T024 [P] [US2] Unit test: async engine uses `async_creator` fetching a fresh token as password for `azure_entra` (mock async credential) in tests/unit/lib/test_db_engine_selection.py
- [X] T025 [P] [US2] Unit test: sync engine registers a `do_connect` listener injecting a fresh token into `cparams["password"]` for `azure_entra` (mock credential) in tests/unit/lib/test_sync_engine.py
- [X] T026 [P] [US2] Unit test: Azure token providers request scope `https://ossrdbms-aad.database.windows.net/.default` and honor optional managed-identity client id in tests/unit/lib/test_db_credentials.py
- [X] T027 [P] [US2] Unit test: settings validation fails fast naming `db_azure_entra_user` when missing in tests/unit/lib/test_settings_db_auth.py

### Implementation for User Story 2

- [X] T028 [US2] Implement Azure sync + async (`azure.identity.aio`) token providers (`DefaultAzureCredential`, configured scope, optional user-assigned client id, `get_token_password()`) in src/lib/db/credentials.py
- [X] T029 [US2] Implement `_init_engine_azure_entra` (async `async_creator` with fresh token password, `ssl="require"`) and async-credential disposal in src/lib/db/engine.py (depends on T028)
- [X] T030 [US2] Implement the `azure_entra` psycopg2 branch in src/lib/db/sync_engine.py: password-less URL + `do_connect` event injecting a fresh token (depends on T028)
- [X] T031 [US2] Wire the `azure_entra` `engine=` branch in src/lib/db/jobstore.py (depends on T030)
- [X] T032 [US2] Add structured log entries for Azure engine init / token acquisition outcome (mode only, NO secrets) in src/lib/db/engine.py and src/lib/db/sync_engine.py

**Checkpoint**: Azure Entra fully functional for API, worker, and migrations.

---

## Phase 5: User Story 3 - Existing deployments unaffected (Priority: P1)

**Goal**: Guarantee `password` and `connection-string` modes are byte-for-byte unchanged after the
factory refactor, with `password` remaining the default.

**Independent Test**: Start with `password` (and `connection-string`) config locally — behavior
identical to the prior release; omitting `DB_AUTH_MODE` defaults to `password` (quickstart
Scenario A). NOTE: per TDD these regression tests should be authored alongside Phase 2 to guard the
refactor; they are grouped here for story traceability.

### Tests for User Story 3 (write before/with Phase 2 refactor, keep GREEN) ⚠️

- [X] T033 [P] [US3] Regression unit test: `password` and `connection-string` async engines build via `_init_engine_url_based` unchanged in tests/unit/lib/test_db_engine_selection.py
- [X] T034 [P] [US3] Regression unit test: `build_jobstore` uses `url=` for `password`/`connection-string` in tests/unit/lib/test_jobstore.py
- [X] T035 [P] [US3] Unit test: unset `DB_AUTH_MODE` resolves to `password` default in tests/unit/lib/test_settings_db_auth.py

### Implementation for User Story 3

- [X] T036 [US3] Confirm and, if needed, correct that `password`/`connection-string` paths in src/lib/db/engine.py, src/lib/db/sync_engine.py, and src/lib/db/jobstore.py preserve prior behavior (no signature/URL changes for these modes)
- [X] T037 [US3] Execute quickstart Scenario A (local password) and Scenario B (fail-fast) and record results

**Checkpoint**: All modes coexist; no regression to existing deployments.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T038 [P] Document the new auth modes (env vars per mode) in README.md, referencing contracts/db-auth-config.md
- [X] T039 [P] Audit all `logger` calls in src/lib/db/* to confirm no token/password/URL secret is logged at any level
- [X] T040 Run quality gates: `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`, `uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml`
- [ ] T041 Run quickstart cloud Scenarios C (GCP) and D (Azure) in their respective environments and record results

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories.
- **US1 / US2 / US3 (Phases 3–5)**: All depend on Foundational. Once Foundational is done they can
  proceed in parallel (different concerns), though they touch shared files (see below).
- **Polish (Phase 6)**: Depends on the targeted user stories being complete.

### User Story Dependencies

- **US1 (GCP, P1)** and **US2 (Azure, P2)** are independent of each other.
- **US3 (backward compat, P1)** depends only on the Phase 2 refactor; its tests guard that refactor
  and should be written first.

### Shared-file coordination (NOT parallel within a story across these files)

- `src/lib/db/engine.py`: T014 → T020 → T029 (sequential edits to the same file).
- `src/lib/db/sync_engine.py`: T009 → T021 → T030.
- `src/lib/db/jobstore.py`: T010 → T022 → T031.
- `src/lib/db/credentials.py`: T008 → T019 → T028.

### Parallel Opportunities

- Phase 2: T005, T007, T008 [P] (different files).
- Each story's test tasks marked [P] are in different test files (or independent cases) and run
  together; write them first and confirm FAILING.
- US1 and US2 implementation can be staffed in parallel provided same-file edits are serialized per
  the coordination list above.

---

## Parallel Example: User Story 1 tests

```bash
# Author these together, confirm they FAIL before implementing T019–T023:
Task: "Async GCP engine selection test in tests/unit/lib/test_db_engine_selection.py"
Task: "pg8000 sync engine test in tests/unit/lib/test_sync_engine.py"
Task: "build_jobstore engine= test in tests/unit/lib/test_jobstore.py"
Task: "GCP settings validation test in tests/unit/lib/test_settings_db_auth.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 — GCP IAM)

1. Phase 1: Setup → 2. Phase 2: Foundational (also land US3 regression tests here) →
3. Phase 3: US1 → **STOP & VALIDATE** quickstart Scenario C → deploy/demo.

### Incremental Delivery

1. Setup + Foundational → existing modes verified green (US3 safety net).
2. US1 (GCP) → validate → deploy.
3. US2 (Azure) → validate → deploy.
4. Polish + cloud validation.

---

## Notes

- [P] = different files, no incomplete dependencies. Same-file tasks are serialized per the
  coordination list.
- TDD: confirm each test FAILS before implementing; no task is complete until its tests pass.
- NEVER log tokens, passwords, or connection strings (constitution Security + FR-008).
- `__init__.py` files stay empty (project convention).
- Commit after each task or logical group.
