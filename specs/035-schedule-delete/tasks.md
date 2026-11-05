---

description: "Task list for Support Schedules Delete"
---

# Tasks: Support Schedules Delete

**Input**: Design documents from `specs/035-schedule-delete/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts), [quickstart.md](./quickstart.md)

**Tests**: REQUIRED. Constitution Principle III (Test-First) is NON-NEGOTIABLE — every test task
must be written and confirmed **failing** before its implementation task begins. Test case numbers in
brackets (e.g. *[case 7]*) refer to the inventory in [data-model.md §5](./data-model.md).

**Organization**: Tasks are grouped by user story. Note the deviation recorded under
[Story Independence](#story-independence): US1 and US2 are the two branches of a single endpoint and
together form the MVP; US1 is not deployable on its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US4)
- Exact file paths are given in every task

## Path Conventions

Existing single web-service layout: `src/api/`, `src/lib/`, `src/worker/`, `tests/unit/`,
`tests/integration/`, `migrations/versions/`, `documentation/` — all at repository root.

---

## Phase 1: Setup

**Purpose**: Establish a known-good baseline so later failures are attributable to this feature.

- [X] T001 Run and record the baseline quality gates (`uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`, `uv run pytest`) from the repository root; all four must be green before any edit
- [X] T002 [P] Confirm the Alembic head is `d4e2b3c5f6a7` via `uv run alembic heads`, matching the `down_revision` assumed by the new migration in `migrations/versions/`

**Checkpoint**: Baseline green and migration head confirmed.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The `is_deleted` column, the new exceptions, the run count, and the widened service
constructor. Every user story depends on these.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Tests (write first, confirm failing)

- [X] T003 [P] Add a unit test asserting `Schedule.is_deleted` is `NOT NULL` with server default `false` and that a newly constructed schedule reads `False`, in `tests/unit/lib/models/test_schedule.py` *[case 1]*
- [X] T004 [P] Add an integration test asserting `alembic upgrade head` backfills pre-existing schedule rows to `is_deleted = false` and that `downgrade -1` drops the column, in `tests/integration/test_schedule_delete.py` *[case 2]*
- [X] T005 [P] Add unit tests for `ScheduleRunRepository.count_for_schedule` covering runs of every status (`success`, `error`, `in_progress`, `missed`) and `0` for an unknown schedule, in `tests/unit/lib/repositories/test_schedule_run_repository.py` *[case 14]*
- [X] T006 [P] Add a unit test asserting `list_for_schedule` returns unchanged totals after the count refactor, in `tests/unit/lib/repositories/test_schedule_run_repository.py` *[case 15]*

### Implementation

- [X] T007 Add the `is_deleted: bool` field (default `False`, `nullable=False`, `server_default=text("false")`) with a docstring entry stating it is never cleared, in `src/lib/models/schedule.py`
- [X] T008 Create the Alembic revision `migrations/versions/20260810_1200_e5f3c4d6a7b8_add_is_deleted_to_schedules.py` with `down_revision = "d4e2b3c5f6a7"`, adding the column as `sa.Boolean(), nullable=False, server_default=sa.text("false")` (retained default, no index) and dropping it in `downgrade()`
- [X] T009 [P] Add `ScheduleAlreadyDeletedError` and `JobRemovalError`, both subclassing `SchedulerError` with Google-style docstrings, in `src/lib/exceptions/errors.py`
- [X] T010 [P] Add `count_for_schedule(schedule_id: str) -> int` (no status predicate) and refactor `list_for_schedule` to call it instead of its inlined count statement, in `src/lib/repositories/schedule_run_repository.py`
- [X] T011 Widen `ScheduleService.__init__` to `(repository, run_repository, scheduler)` in `src/lib/services/schedule_service.py`
- [X] T012 Construct and inject `ScheduleRunRepository(session)` into `ScheduleService` in `src/api/dependencies/scheduler.py::get_schedule_service`
- [X] T013 Update every existing `ScheduleService(...)` construction in `tests/unit/lib/services/test_schedule_service.py` (and any other test that builds the service) for the new signature, keeping all pre-existing assertions passing

**Checkpoint**: `uv run pytest` green again; the column exists, migrations apply and roll back, and the service accepts the run repository.

---

## Phase 3: User Story 1 - Delete a schedule that has never run (Priority: P1) 🎯 MVP (part 1 of 2)

**Goal**: `DELETE /v1/schedules/{schedule_id}` unregisters the job and removes the row entirely when
the schedule has no run history.

**Independent Test**: Create a schedule, do not let it execute, delete it, then confirm 204, that
`GET /{id}` returns 404, that it is absent from the list under both filter values, and that it never
fires again.

**⚠️ Not deployable alone**: until Phase 4 lands, deleting a schedule that *has* run history is
rejected by the `ON DELETE RESTRICT` foreign key and surfaces as a 5xx. Ship Phase 3 + Phase 4
together.

### Tests for User Story 1 (write first, confirm failing)

- [X] T014 [P] [US1] Add unit tests for `ScheduleRepository.delete_owned`: removes an owned live row and returns `True`; returns `False` for wrong owner, unknown id, and an already-soft-deleted row, in `tests/unit/lib/repositories/test_schedule_repository.py` *[cases 5, 6]*
- [X] T015 [P] [US1] Add unit tests for `ScheduleService.delete` on the zero-run branch: `remove_job` called exactly once, then `delete_owned`, and no soft mark, in `tests/unit/lib/services/test_schedule_service.py` *[case 16]*
- [X] T016 [P] [US1] Add unit tests for the store-ordering guarantees: `remove_job` raising `JobLookupError` still completes the delete; `remove_job` raising any other exception raises `JobRemovalError` with **no** database write; a persist failure after a successful unregister propagates and a second `delete` call succeeds, in `tests/unit/lib/services/test_schedule_service.py` *[cases 18, 19, 20]*
- [X] T017 [P] [US1] Add a unit test asserting an unknown or foreign `schedule_id` raises `ScheduleNotFoundError` and the scheduler is never touched, in `tests/unit/lib/services/test_schedule_service.py` *[case 23]*
- [X] T018 [P] [US1] Add endpoint unit tests for `DELETE /v1/schedules/{id}`: 204 with an empty body, 404 for unknown/foreign, 502 on `JobRemovalError`, in `tests/unit/api/v1/endpoints/test_schedules.py` *[cases 26, 27 partial]*
- [X] T019 [P] [US1] Add an integration test for the full never-run journey (create → delete → 204 → `GET` 404 → absent from `?is_deleted=false` and `?is_deleted=true`), in `tests/integration/test_schedule_delete.py`

### Implementation for User Story 1

- [X] T020 [US1] Add `delete_owned(schedule_id: str, created_by: str) -> bool` as a single conditional `DELETE ... WHERE id AND created_by AND is_deleted = false` returning `rowcount == 1`, leaving the existing unscoped `delete` untouched, in `src/lib/repositories/schedule_repository.py`
- [X] T021 [US1] Implement `async def delete(self, schedule_id, created_by) -> None` in `src/lib/services/schedule_service.py` per [data-model.md §3](./data-model.md): ownership read → run count → `asyncio.to_thread(scheduler.remove_job, ...)` (swallow `JobLookupError`, wrap other exceptions in `JobRemovalError`) → `delete_owned` on the zero-run branch. The unregister step MUST precede any database write
- [X] T022 [US1] Add the structured log events `schedule_delete_start`, `schedule_delete_job_removed`, `schedule_delete_job_absent`, `schedule_delete_job_removal_failed`, `schedule_deleted` (with `outcome="removed"`, `run_count`), and `schedule_delete_persist_failed` via `src.lib.utils.logging.logger` in `src/lib/services/schedule_service.py`
- [X] T023 [US1] Add the `delete_schedule` handler with `@router.delete("/{schedule_id}", status_code=204)` returning `None`, mapping `ScheduleNotFoundError` → 404 and `JobRemovalError` → 502, **appended at the end of** `src/api/v1/endpoints/schedules.py` (never above either `router.routes[-1].description +=` line — see research.md)

**Checkpoint**: A never-run schedule can be deleted end to end; scheduler and database failure paths behave per contract.

---

## Phase 4: User Story 2 - Delete a schedule that has run history (Priority: P1) 🎯 MVP (part 2 of 2)

**Goal**: The same endpoint unregisters the job and sets `is_deleted = true` when run history exists,
preserving every run record.

**Independent Test**: Create a schedule, let it produce at least one run, delete it, then confirm
204, that the schedule is marked deleted and reports no next run time, that its run history is
returned unchanged, and that no new runs appear.

### Tests for User Story 2 (write first, confirm failing)

- [X] T024 [P] [US2] Add unit tests for `ScheduleRepository.mark_deleted`: flips the flag once returning `True`, a second call returns `False`, wrong owner returns `False`, and `updated_at` advances on success, in `tests/unit/lib/repositories/test_schedule_repository.py` *[cases 8, 9]*
- [X] T025 [P] [US2] Add an integration test asserting a hard delete of a schedule that has runs raises `IntegrityError` from the `ON DELETE RESTRICT` foreign key, in `tests/integration/test_schedule_delete.py` *[case 7]*
- [X] T026 [P] [US2] Add unit tests for `ScheduleService.delete` on the runs-exist branch: `remove_job` called once, `mark_deleted` used, run records untouched; and `mark_deleted`/`delete_owned` returning `False` raises `ScheduleAlreadyDeletedError`, in `tests/unit/lib/services/test_schedule_service.py` *[cases 17, 22]*
- [X] T027 [P] [US2] Add a unit test asserting `delete_owned` raising `IntegrityError` falls back to `mark_deleted`, logs the race event, and the request succeeds, in `tests/unit/lib/services/test_schedule_service.py` *[case 21]*
- [X] T028 [P] [US2] Add unit tests asserting the worker creates **no** run record and logs `run_skipped_schedule_deleted` when the schedule is soft-deleted or missing, in `tests/unit/worker/test_jobs.py` (create the module if absent) *[cases 32, 33]*
- [X] T029 [P] [US2] Add integration tests for the soft-delete journey: run history is returned unchanged after deletion, `GET /{id}` returns 200 with `is_deleted: true` and `next_run_time: null`, and `ScheduleRepository.get` still returns the soft-deleted row, in `tests/integration/test_schedule_delete.py` *[cases 13, 31]*

### Implementation for User Story 2

- [X] T030 [US2] Add `mark_deleted(schedule_id: str, created_by: str) -> bool` as a single conditional `UPDATE ... SET is_deleted = true WHERE id AND created_by AND is_deleted = false` returning `rowcount == 1`, in `src/lib/repositories/schedule_repository.py`
- [X] T031 [US2] Extend `ScheduleService.delete` with the runs-exist branch (`mark_deleted`) and the `IntegrityError` fallback from the zero-run branch to the soft branch, mapping a `False` result to `ScheduleAlreadyDeletedError`, in `src/lib/services/schedule_service.py`
- [X] T032 [US2] Add the `schedule_marked_deleted` (with `outcome="marked_deleted"`, `run_count`) and `schedule_delete_race_fallback` log events in `src/lib/services/schedule_service.py`
- [X] T033 [US2] Add the pre-flight guard at the top of `_execute_scheduled_job_inner` — **before** `create_in_progress` — loading the schedule via `ScheduleRepository.get_by_id` and returning early with a `run_skipped_schedule_deleted` log when it is missing or `is_deleted`, while an unexpected lookup error logs and proceeds, in `src/worker/jobs.py`

**Checkpoint**: 🎯 **MVP complete.** Both delete branches work; run history is provably preserved; deleted schedules cannot execute from either process.

---

## Phase 5: User Story 3 - See and filter by deletion state (Priority: P2)

**Goal**: `is_deleted` appears on every list item and details response, and the list is filtered by
it, defaulting to live schedules only.

**Independent Test**: With one live and one soft-deleted schedule, request the list with no filter,
then with `?is_deleted=true`, and confirm each response contains exactly the expected schedules and
that every item exposes its deletion state.

### Tests for User Story 3 (write first, confirm failing)

- [X] T034 [P] [US3] Add unit tests asserting `ScheduleResponse` and `ScheduleListItem` serialise `is_deleted`, in `tests/unit/lib/schemas/test_schedule_schemas.py` *[case 3]*
- [X] T035 [P] [US3] Add unit tests for `ScheduleRepository.list`: `is_deleted=False` excludes soft-deleted rows, `is_deleted=True` returns only them, the returned `count` reflects the filter, and the filter composes with name filter, metadata filter, and ordering, in `tests/unit/lib/repositories/test_schedule_repository.py` *[cases 10, 11, 12]*
- [X] T036 [P] [US3] Add a unit test asserting `ScheduleService.list` echoes `is_deleted=true` into the `next`/`previous` links and omits it when `false`, in `tests/unit/lib/services/test_schedule_service.py` *[case 25]*
- [X] T037 [P] [US3] Add endpoint tests: default list excludes soft-deleted schedules, `?is_deleted=true` returns them, `?is_deleted=maybe` → 422, and `GET /{id}` of a soft-deleted schedule → 200 with `is_deleted: true` and `next_run_time: null`, in `tests/unit/api/v1/endpoints/test_schedules.py` *[cases 28, 29]*

### Implementation for User Story 3

- [X] T038 [P] [US3] Add the required `is_deleted: bool` field to `ScheduleResponse` and `ScheduleListItem`, leaving `ScheduleRequest` unchanged, in `src/lib/schemas/schedule.py`
- [X] T039 [US3] Add `is_deleted: bool = False` to `ScheduleRepository.list` and append `is_deleted_col == is_deleted` to the shared `conditions` list so it applies to **both** the count statement and the page statement, in `src/lib/repositories/schedule_repository.py`
- [X] T040 [US3] Map `is_deleted` in `_to_response`, set it on each `ScheduleListItem`, add the `is_deleted: bool = False` parameter to `ScheduleService.list`, forward it to the repository, and **append** rather than overwrite the pagination fragment (`paginator.extra_query = ordering.to_query() + ("&is_deleted=true" if is_deleted else "")`), in `src/lib/services/schedule_service.py`
- [X] T041 [US3] Add the `is_deleted: Annotated[bool, Query()] = False` query parameter to `list_schedules`, pass it to the service, and document it in the handler docstring, in `src/api/v1/endpoints/schedules.py`

**Checkpoint**: Deletion state is visible and filterable, and pagination cannot flip between live and deleted results.

---

## Phase 6: User Story 4 - Deleted schedules cannot be changed or restored (Priority: P2)

**Goal**: A soft-deleted schedule is frozen — update, pause, resume, and re-delete are all rejected —
and `is_deleted` can never be set through a request body.

**Independent Test**: Soft-delete a schedule, then attempt update, pause, resume, and a second
delete, confirming each is rejected with 409 and leaves the schedule deleted and non-executing.

### Tests for User Story 4 (write first, confirm failing)

- [X] T042 [P] [US4] Add a unit test asserting `ScheduleRequest` ignores an `is_deleted` key in the body (no validation error, no attribute), in `tests/unit/lib/schemas/test_schedule_schemas.py` *[case 4]*
- [X] T043 [P] [US4] Add unit tests asserting `update`, `pause`, `resume`, and `delete` on a soft-deleted schedule each raise `ScheduleAlreadyDeletedError` and never touch the scheduler, in `tests/unit/lib/services/test_schedule_service.py` *[case 24]*
- [X] T044 [P] [US4] Add endpoint tests asserting 409 from `PUT`, `POST /pause`, `POST /resume`, and a second `DELETE` on a soft-deleted schedule, and that `PUT` with `"is_deleted": true` on a live schedule returns 200 with `is_deleted: false`, in `tests/unit/api/v1/endpoints/test_schedules.py` *[cases 27, 30]*
- [X] T045 [P] [US4] Add an integration test asserting no request sequence returns a soft-deleted schedule to a live state, in `tests/integration/test_schedule_delete.py`

### Implementation for User Story 4

- [X] T046 [US4] Add the `if <schedule>.is_deleted: raise ScheduleAlreadyDeletedError` guard immediately after the existing `if row is None` check in `update`, `pause`, and `resume`, in `src/lib/services/schedule_service.py`
- [X] T047 [US4] Add `except ScheduleAlreadyDeletedError` → `HTTPException(status_code=409, detail=f"Schedule already deleted: {schedule_id}")` to the `update_schedule`, `pause_schedule`, `resume_schedule`, and `delete_schedule` handlers, and add the `schedule_delete_conflict` log event, in `src/api/v1/endpoints/schedules.py` and `src/lib/services/schedule_service.py`

**Checkpoint**: All four user stories are functional; deletion is final and unwritable through the API.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T048 [P] Update the asserted MCP tool set to include the auto-exposed schedules delete tool, in `tests/unit/api/mcp/test_mcp_tools.py` *[case 34]*
- [X] T049 [P] Verify `documentation/api_contract.md` and `documentation/db_tables.md` (both already updated during planning) match the shipped implementation — DELETE status codes, the `is_deleted` field on all four response shapes, the list query parameter, and the `schedules.is_deleted` column description — and correct any divergence
- [X] T050 [P] Add or update Google-style docstrings on every new public method (`delete`, `delete_owned`, `mark_deleted`, `count_for_schedule`, `delete_schedule`) and confirm no `print()` and no `assert` were introduced in `src/`
- [X] T051 Run `uv run ruff check src tests`, `uv run ruff format src tests`, and `uv run mypy src` and fix all findings
- [X] T052 Run the full suite with coverage: `uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml`; all new code must be covered
- [~] T053 Execute the [quickstart.md](./quickstart.md) validation guide end to end. **Partially done**: the migration up/down/up cycle and the pre-existing-row backfill were verified against a real PostgreSQL 16 container, and the quality gates all pass. The manual `curl` walkthrough (§4) was **not** performed by hand — it needs a running API plus DIAL; every assertion in it is instead covered by the automated end-to-end tests in `tests/integration/test_schedules_api.py` (`TestDeleteScheduleWithoutRuns`, `TestDeleteScheduleWithRuns`, `TestIsDeletedFieldExposure`), which drive the real HTTP → service → repository → PostgreSQL stack.
- [X] T054 Spawn the `code-review-agent` per the constitution's SDLC (agent must not see the implementing agent's context), read its report from `specs/035-schedule-delete/`, address the findings, and repeat until clean

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies.
- **Phase 2 (Foundational)**: Depends on Phase 1. **BLOCKS all user stories** — the column, the
  exceptions, the run count, and the widened service constructor are prerequisites for every story.
- **Phase 3 (US1)**: Depends on Phase 2.
- **Phase 4 (US2)**: Depends on Phase 3 — it extends the `delete` method and the endpoint created
  there. Phases 3+4 together are the MVP.
- **Phase 5 (US3)**: Depends on Phase 2 only. Can run in parallel with Phases 3–4.
- **Phase 6 (US4)**: Depends on Phase 2 for the exception, and on Phase 3 for the `delete_schedule`
  handler that T047 edits. Its update/pause/resume guards depend only on Phase 2.
- **Phase 7 (Polish)**: Depends on all shipped stories.

### Story Independence

This feature deviates from strict per-story independence, and the deviation is intentional:

- **US1 and US2 are both P1 and share one endpoint.** They are the two branches of a single
  operation selected by run history, so US1 alone leaves the runs-exist path failing at the foreign
  key. Treat Phase 3 + Phase 4 as one deployable unit.
- **US3 is genuinely independent** of the delete operation (it only reads and filters a column that
  Phase 2 creates) and can be built in parallel by a second developer.
- **US4 partially depends on US1** only because T047 edits the handler US1 creates; its service-layer
  guards (T046) are independent.
- **Observability note**: without US3, the soft-delete outcome of US2 is not visible over the API and
  must be verified in the database or in logs. This makes US3 a practical companion to US2 even
  though it is formally P2.

### Within Each Story

- Test tasks precede implementation tasks and must be confirmed failing first (Constitution III).
- Repository → service → endpoint, in that order.
- The unregister-before-persist ordering inside `ScheduleService.delete` (T021) is a correctness
  requirement, not a style choice — see [research.md D1](./research.md).

### Parallel Opportunities

- T003–T006 (Phase 2 tests) all touch different files and can run together.
- T009 and T010 are independent of each other and of T007/T008.
- T014–T019 (US1 tests) are all in different files or different test classes and can be written in
  parallel; likewise T024–T029, T034–T037, and T042–T045.
- Phase 5 (US3) can proceed in parallel with Phases 3–4 once Phase 2 is done.
- T048–T050 in Polish are independent of each other.

**Serialization warning**: T015, T016, T017, T026, T027, T036, and T043 all edit
`tests/unit/lib/services/test_schedule_service.py`, and T018, T037, T044 all edit
`tests/unit/api/v1/endpoints/test_schedules.py`. They are marked `[P]` because they are logically
independent, but if worked concurrently they will conflict in those two files — assign each file to
one worker, or apply them sequentially.

---

## Parallel Example: Phase 2 Foundational Tests

```bash
# Four independent test files — write all four failing tests together:
Task: "Model column test in tests/unit/lib/models/test_schedule.py"                       # T003
Task: "Migration backfill test in tests/integration/test_schedule_delete.py"              # T004
Task: "count_for_schedule tests in tests/unit/lib/repositories/test_schedule_run_repository.py"  # T005
Task: "list_for_schedule regression test in tests/unit/lib/repositories/test_schedule_run_repository.py"  # T006
```

## Parallel Example: US1 vs US3

```bash
# After Phase 2, two developers can work independently:
Developer A: T014-T023  # US1 — delete_owned, service.delete, DELETE endpoint
Developer B: T034-T041  # US3 — is_deleted response field, list filter, query param
```

---

## Implementation Strategy

### MVP (US1 + US2)

1. Phase 1: Setup — baseline green.
2. Phase 2: Foundational — column, migration, exceptions, run count, service wiring. **Blocking.**
3. Phase 3: US1 — hard-delete branch and the endpoint.
4. Phase 4: US2 — soft-delete branch, race fallback, worker guard.
5. **STOP and VALIDATE**: both delete branches, run-history preservation, and the failure paths in
   [quickstart.md §5](./quickstart.md).
6. Deployable. Note that without Phase 5 the soft-delete outcome is only observable in the database
   and logs, so shipping Phase 5 alongside is strongly recommended.

### Incremental Delivery

1. Setup + Foundational → foundation ready.
2. US1 + US2 → delete works for every schedule → validate → MVP.
3. US3 → deletion state visible and filterable → validate → deploy.
4. US4 → deleted schedules provably frozen → validate → deploy.
5. Polish → docs verified, MCP surface updated, gates and code review clean.

### Parallel Team Strategy

1. Everyone on Setup + Foundational.
2. Then: Developer A takes US1 → US2 (sequential, same files); Developer B takes US3; Developer B
   picks up US4's T046 once Phase 2 is done, holding T047 until US1's handler exists.
3. Reconcile in Polish.

---

## Notes

- `[P]` = different files, no dependencies — but see the serialization warning above for the two
  shared test files.
- Do **not** change the `schedule_runs → schedules` foreign key to `CASCADE`; the `RESTRICT`
  behaviour is what makes run-history loss impossible and drives the race fallback.
- Do **not** insert the new `DELETE` route above either `router.routes[-1].description +=` line in
  `src/api/v1/endpoints/schedules.py`.
- Do **not** add `is_deleted` to `ScheduleRequest`, and do not add `extra="forbid"`; FR-009 requires
  the field to be *ignored*, not rejected.
- Commit after each task or logical group; verify tests fail before implementing.
