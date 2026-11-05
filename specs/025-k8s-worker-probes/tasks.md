---

description: "Task list for feature implementation"
---

# Tasks: Kubernetes Probes for Worker Service

**Input**: Design documents from `/specs/025-k8s-worker-probes/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/worker-health-probes.md, quickstart.md

**Tests**: Included — the project constitution mandates Test-First (TDD, NON-NEGOTIABLE): tests
MUST be written and confirmed failing before implementation for each task group below.

**Organization**: Tasks are grouped by user story (spec.md priorities P1/P2/P3) to enable
independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)

## Path Conventions

Single project — `src/`, `tests/` at repository root, per plan.md's Project Structure
(`src/api/`, `src/worker/`, `src/lib/`, mirrored under `tests/`).

---

## Phase 1: Setup

**Purpose**: Introduce the shared building blocks every user story's tests/implementation import.

- [X] T001 Add `worker_health_dir: str = "/tmp"`, `worker_liveness_staleness_seconds: float = 180.0`, and `worker_readiness_check_interval_seconds: float = 15.0` fields to `Settings` in `src/lib/settings.py`
- [X] T002 [P] Add corresponding unit tests for the new settings' defaults and env-var overrides in `tests/unit/lib/test_settings.py` (follow the existing `test_noop_interval_seconds_default`/`_override` pattern)

**Checkpoint**: Settings available for all subsequent phases.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared DB-check helper used by both the API's existing readiness endpoint and the
worker's new readiness poller. MUST complete before any user story phase below.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Tests for Foundational phase ⚠️

> Write these tests FIRST; confirm they FAIL before implementing `health_check.py`.

- [X] T003 [P] Unit tests for `check_database(timeout)` in `tests/unit/lib/services/test_health_check.py` — covers: returns `True` on successful `SELECT 1`; returns `False` on `OperationalError`; returns `False` on timeout; returns `False` and logs a warning on any other exception (mirror the scenarios already covered in `tests/unit/api/v1/endpoints/test_health.py`)

### Implementation for Foundational phase

- [X] T004 Extract `_check_db` from `src/api/v1/endpoints/health.py` into `src/lib/services/health_check.py` as public `async def check_database(timeout: float) -> bool`, using `src.lib.db.engine.Database` and `src.lib.utils.logging.logger` (unchanged behavior/log message)
- [X] T005 Update `src/api/v1/endpoints/health.py` to import and call `check_database` from `src.lib.services.health_check` instead of the local `_check_db`; remove the now-dead local helper
- [X] T006 Update `tests/unit/api/v1/endpoints/test_health.py` mock patch targets from `src.api.v1.endpoints.health.Database` to `src.lib.services.health_check.Database` (and `get_settings` target if it moves) so existing tests keep passing against the new call path

**Checkpoint**: `check_database` is available from `src/lib/`; existing API readiness tests still pass. User story work can now begin.

---

## Phase 3: User Story 1 - Detect and recover a hung or crashed worker (Priority: P1) 🎯 MVP

**Goal**: Kubernetes can detect a worker whose scheduling loop has stopped making progress (hung,
deadlocked, or crashed) and restart it automatically, via a liveness marker file.

**Independent Test**: Start the worker, confirm `/tmp/heartbeat` exists and its mtime advances
each `noop_interval_seconds` tick; freeze the process (or stop the heartbeat job in a test) and
confirm the liveness check (mtime age vs. `worker_liveness_staleness_seconds`) reports unhealthy
once stale, and healthy again once ticking resumes.

### Tests for User Story 1 ⚠️

> Write these tests FIRST; confirm they FAIL before implementing `src/worker/health.py` changes.

- [X] T007 [P] [US1] Unit tests for `touch_heartbeat()` and `is_heartbeat_fresh(staleness_seconds)` in `tests/unit/worker/test_health.py` — covers: `touch_heartbeat()` creates the file if absent and updates mtime if present; `is_heartbeat_fresh` returns `True` when mtime age is below the threshold and `False` when at/above it; `is_heartbeat_fresh` returns `False` when the file does not exist yet (use `tmp_path`/`mocker.patch` on `worker_health_dir` — do not touch the real `/tmp`)
- [X] T008 [P] [US1] Unit test in `tests/unit/worker/test_jobs.py` verifying `heartbeat()` calls `src.worker.health.touch_heartbeat()` on each successful run (extend existing heartbeat test coverage in that file)
- [X] T009 [P] [US1] Unit test in `tests/unit/worker/test_worker_app.py` verifying `_run()` calls `touch_heartbeat()` once immediately after the scheduler starts, before the first job tick (liveness must be healthy from process start per data-model.md)

### Implementation for User Story 1

- [X] T010 [US1] Create `src/worker/health.py` with `touch_heartbeat() -> None` and `is_heartbeat_fresh(staleness_seconds: float) -> bool`, resolving the file path as `Path(get_settings().worker_health_dir) / "heartbeat"` (depends on T001)
- [X] T011 [US1] Update `src/worker/jobs.py::heartbeat()` to call `src.worker.health.touch_heartbeat()` after logging the existing `heartbeat` log line (depends on T010)
- [X] T012 [US1] Update `src/worker/app.py::_run()` to call `touch_heartbeat()` immediately after `scheduler.start()` succeeds (depends on T010)
- [X] T013 [US1] Add a `docs`/log line: on worker startup, remove any stale `heartbeat` marker file left over from a previous crash inside the same container, before creating the scheduler (data-model.md: avoid false positives from stale files) in `src/worker/app.py::_run()`

**Checkpoint**: Liveness marker file is fully functional and independently testable/deployable — this alone is the MVP.

---

## Phase 4: User Story 2 - Avoid routing/scaling actions to a worker that isn't ready (Priority: P2)

**Goal**: Kubernetes knows when the worker has finished initializing and can currently reach the
database, via a readiness marker file, so rolling updates don't count a not-yet-capable worker as
available.

**Independent Test**: Start the worker with the database unavailable and confirm `/tmp/ready`
never appears; restore the database and confirm `/tmp/ready` appears within one
`worker_readiness_check_interval_seconds` cycle; stop the database again and confirm `/tmp/ready`
is removed within one cycle.

### Tests for User Story 2 ⚠️

> Write these tests FIRST; confirm they FAIL before implementing the readiness poller.

- [X] T014 [P] [US2] Unit tests for `mark_started()`, `mark_ready()`, `mark_not_ready()`, `is_started()` in `tests/unit/worker/test_health.py` — covers: `mark_started()` creates the `started` file; `mark_ready()` creates the `ready` file; `mark_not_ready()` removes the `ready` file if present and is a no-op if absent; `is_started()` reflects file presence
- [X] T015 [P] [US2] Unit tests for `_readiness_poller(stop_event, interval_seconds, db_timeout_seconds)` in `tests/unit/worker/test_health.py` — covers: calls `check_database` each cycle; touches `ready` when it returns `True`; removes `ready` when it returns `False`; stops looping once `stop_event` is set; does not start a cycle if `is_started()` is `False` (mock `src.worker.health.check_database` and the sleep/interval wait)
- [X] T016 [US2] Unit test in `tests/unit/worker/test_worker_app.py` verifying `_run()` calls `mark_started()` after the scheduler starts and the heartbeat job is registered, launches the readiness poller task, and calls `mark_not_ready()` immediately in the shutdown path before `scheduler.shutdown()`

### Implementation for User Story 2

- [X] T017 [US2] Add `mark_started()`, `is_started() -> bool`, `mark_ready()`, `mark_not_ready()` to `src/worker/health.py` (paths: `started`, `ready` files under `worker_health_dir`) (depends on T010)
- [X] T018 [US2] Add `async def _readiness_poller(stop_event: asyncio.Event, interval_seconds: float, db_timeout_seconds: float) -> None` to `src/worker/health.py`: loop until `stop_event` is set, each cycle call `check_database(db_timeout_seconds)` from `src.lib.services.health_check`, call `mark_ready()`/`mark_not_ready()` accordingly, log transitions via `logger` (mirror `_jobstore_poller`'s wait-with-timeout pattern in `src/worker/app.py`) (depends on T004, T017)
- [X] T019 [US2] Update `src/worker/app.py::_run()`: remove any stale `started`/`ready` files at the very start (before scheduler creation); call `mark_started()` right after the heartbeat job is registered; create the `_readiness_poller` task alongside the existing `_jobstore_poller` task using `worker_readiness_check_interval_seconds` and `readiness_db_timeout_seconds` from settings (depends on T017, T018)
- [X] T020 [US2] Update `src/worker/app.py::_run()`'s shutdown (`finally`) block to call `mark_not_ready()` first, then cancel the readiness poller task (mirroring the existing `poller` cancellation), before `scheduler.shutdown(wait=False)` (depends on T017)

**Checkpoint**: Readiness marker file is fully functional; User Stories 1 and 2 both work independently.

---

## Phase 5: User Story 3 - Give a slow-starting worker enough time to initialize (Priority: P3)

**Goal**: A worker's normal startup time (connecting to the database, loading potentially many
persisted schedules) is not mistaken for a liveness failure, via a startup marker file consumed
by Kubernetes' `startupProbe`.

**Independent Test**: Start a worker with an artificially slow initialization step (e.g., a
mocked delay before `mark_started()` is reached) and confirm `/tmp/started` does not exist until
initialization completes, while `/tmp/heartbeat` remains fresh throughout (since it is touched
at the same point liveness begins, immediately after scheduler start — see US1) so a
`startupProbe` against `/tmp/started` correctly gates the liveness/readiness probes per
Kubernetes semantics.

### Tests for User Story 3 ⚠️

> Write these tests FIRST; confirm they FAIL before finalizing the startup ordering.

- [X] T021 [P] [US3] Unit test in `tests/unit/worker/test_worker_app.py` verifying the exact ordering in `_run()`: stale-file cleanup → scheduler start → `touch_heartbeat()` → heartbeat job registration → `mark_started()` → readiness poller task creation (asserts call order via `mocker` call sequence, not just individual calls)
- [X] T022 [P] [US3] Unit test in `tests/unit/worker/test_health.py` verifying `is_started()` returns `False` before `mark_started()` is called and `True` after, independent of `is_heartbeat_fresh`/readiness state

### Implementation for User Story 3

- [X] T023 [US3] Review and, if needed, reorder `src/worker/app.py::_run()` so `mark_started()` is called only after both the scheduler has started and the heartbeat job is registered (not before) — no new file/module needed, this phase is a correctness/ordering pass on top of T012/T017/T019 (depends on T012, T017, T019)

**Checkpoint**: All three user stories are independently functional. The worker's full probe surface (`started`, `heartbeat`, `ready`) is complete.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Documentation and operational completeness beyond the worker's own code.

- [X] T024 [P] Update `documentation/deployment/deployment.md`'s "Worker" section to document the three marker files (`started`, `heartbeat`, `ready`), their default paths, and link to the recommended `exec` probe YAML in `specs/025-k8s-worker-probes/contracts/worker-health-probes.md`
- [X] T025 [P] Add `WORKER_HEALTH_DIR`, `WORKER_LIVENESS_STALENESS_SECONDS`, `WORKER_READINESS_CHECK_INTERVAL_SECONDS` to the `README.md` "Deployment Env Setup — Worker" section
- [X] T026 Run `uv run ruff check src tests`, `uv run ruff format src tests`, and `uv run mypy src` and fix any findings across all files touched by this feature
- [X] T027 Run `uv run pytest --cov=src` and confirm full suite passes with coverage for all new/modified files
- [X] T028 Execute `specs/025-k8s-worker-probes/quickstart.md` end-to-end against a local Postgres (steps 1–7) and confirm every expected outcome

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories (US2 needs `check_database`; US1 does not use it but still waits behind Foundational per template convention since it shares `src/worker/health.py` with US2/US3)
- **User Story 1 (Phase 3)**: Depends on Foundational completion only — independently deployable as the MVP
- **User Story 2 (Phase 4)**: Depends on Foundational completion; builds on `src/worker/health.py` and `src/worker/app.py` touched by US1, but is independently testable once US1's `touch_heartbeat`/`is_heartbeat_fresh` exist alongside it
- **User Story 3 (Phase 5)**: Depends on US1 (T012) and US2 (T017, T019) — it is an ordering/correctness pass over code both prior stories introduce, not new functionality
- **Polish (Phase 6)**: Depends on all three user stories being complete

### Within Each User Story

- Tests written and confirmed failing before implementation tasks (Test-First, constitution Principle III)
- `src/worker/health.py` helpers before the code that calls them (`jobs.py`, `app.py`)
- Story complete and checkpoint validated before moving to the next priority

### Parallel Opportunities

- T001/T002 (Setup) can run in parallel with nothing preceding them
- T003 (Foundational test) is a single task, no parallel siblings
- Within US1: T007, T008, T009 (all distinct test files) can run in parallel
- Within US2: T014, T015 (same file, different functions — sequential within the file but can be drafted together) ; T016 is a distinct file and can run in parallel with T014/T015
- Within US3: T021, T022 (distinct files) can run in parallel
- T024, T025 (Polish docs) can run in parallel; T026–T028 are sequential (lint → typecheck implied by T026, then tests, then manual quickstart)

---

## Parallel Example: User Story 1

```bash
# Launch all three US1 tests together (distinct files):
Task: "Unit tests for touch_heartbeat/is_heartbeat_fresh in tests/unit/worker/test_health.py"
Task: "Unit test for heartbeat() calling touch_heartbeat() in tests/unit/worker/test_jobs.py"
Task: "Unit test for _run() touching heartbeat at startup in tests/unit/worker/test_worker_app.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational
3. Complete Phase 3: User Story 1 (liveness marker file)
4. **STOP and VALIDATE**: run `quickstart.md` steps 1, 2, 4 independently
5. Deploy/demo if ready — a worker with only liveness wired up is already a meaningful
   improvement over today's zero-signal state

### Incremental Delivery

1. Setup + Foundational → shared building blocks ready
2. User Story 1 → validate independently → this is the MVP (hung-worker detection)
3. User Story 2 → validate independently → readiness now gates rollouts correctly
4. User Story 3 → validate independently → slow starts no longer risk false-positive restarts
5. Polish → docs, lint/type/test gates, full quickstart pass

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- This feature adds no database migration and no new external dependency
- Per constitution: write failing tests first for every task group above, then implement until
  green; run `ruff`, `mypy`, and the full suite before considering the feature done; spawn the
  code-review-agent per the project SDLC once implementation and tests are green
- Actual Kubernetes/Helm manifest changes are out of this repository's scope — Polish phase only
  documents the recommended probe YAML for the external deploy repo to adopt
