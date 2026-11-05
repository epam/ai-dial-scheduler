# Tasks: Persist Conversation ID for Schedule Runs

**Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md) | **Data model**: [data-model.md](./data-model.md)
**Contracts**: [runs-list-conversation-id.md](./contracts/runs-list-conversation-id.md), [run-detail-conversation-id.md](./contracts/run-detail-conversation-id.md)

Feature adds a single additive, nullable `schedule_runs.conversation_id` column, populated best-effort
inside the existing guarded conversation step, and surfaced on `RunListItem` and `RunResponse`.

**Legend**: `[P]` = parallelisable (different files, no incomplete dependency). `[US1]`/`[US2]` =
user-story phase label. Every task names its exact file path. TDD is NON-NEGOTIABLE (Constitution
Principle III): within each user-story phase, test tasks are written and confirmed RED before
implementation.

---

## Phase 1: Setup (baseline)

Establish a known-good, green baseline before any change.

- [ ] T001 Run and record the baseline quality gates, all must be green: `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`, `uv run pytest`. Record the pass/fail of each in the terminal before editing any file.
- [ ] T002 Confirm the Alembic head matches the planned migration's `down_revision`: run `uv run alembic heads` and verify the single head is `f6a4d5e7b8c9` (file `migrations/versions/20260814_1200_f6a4d5e7b8c9_set_service_id_to_dial_native.py`), the value the new migration in Phase 2 will set as `down_revision`.

**Checkpoint**: baseline gates green and head revision `f6a4d5e7b8c9` confirmed. Proceed to Phase 2.

---

## Phase 2: Foundational (Blocking Prerequisites)

**CRITICAL — No user story work can begin until this phase is complete.** These are the storage,
model, repository, and worker-return changes that ALL user stories depend on. Both US1 and US2 read
the persisted column and the mapped schema fields established here.

- [ ] T003 Add the nullable model field in `src/lib/models/schedule_run.py`: `conversation_id: Optional[str] = Field(default=None, sa_type=String(), sa_column_kwargs={"nullable": True})`, following the existing `end_time`/`result` nullable-field style (data-model.md §1). Add the `conversation_id` entry to the class docstring `Attributes:` block per data-model.md §1. Import `String` from `sqlalchemy` if not already imported. No length cap on `String()` (plan.md flagged constraint: unbounded VARCHAR).
- [ ] T004 Generate the Alembic migration `migrations/versions/20260826_1200_<rev>_add_conversation_id_to_schedule_runs.py` via `uv run alembic revision --autogenerate -m "add conversation_id to schedule_runs"`. Verify `down_revision = "f6a4d5e7b8c9"`. `upgrade()` must be exactly `op.add_column("schedule_runs", sa.Column("conversation_id", sa.String(), nullable=True))` — nullable, no `server_default`, no index, no FK (data-model.md §2, plan.md flagged constraints). `downgrade()` must be `op.drop_column("schedule_runs", "conversation_id")`. Hand-edit the generated file if autogenerate adds anything beyond this single add/drop.
- [ ] T005 Apply and verify the migration: `uv run alembic upgrade head`, confirm `schedule_runs` now has a nullable `conversation_id VARCHAR` with no default and every pre-existing row is `NULL`; then `uv run alembic downgrade -1` drops the column and `uv run alembic upgrade head` re-applies it cleanly (data-model.md §6 case 2).
- [ ] T006 Add the repository method `set_conversation_id` in `src/lib/repositories/schedule_run_repository.py`: `async def set_conversation_id(self, run_id: str, conversation_id: str) -> ScheduleRun | None` — `session.get(ScheduleRun, uuid.UUID(run_id))`; if `None`, return `None`; else set `row.conversation_id = conversation_id`, `await self.session.commit()`, `await self.session.refresh(row)`, return `row`. Copy the exact fetch-mutate-commit shape of `mark_success`/`mark_error`; do not alter `status`, `end_time`, or `result` (data-model.md §4, §6 case 8). Google-style docstring on the new method.
- [ ] T007 Change `create_run_conversation` in `src/worker/conversation.py` from `-> None` to `-> str`: after the successful `put_conversation`, `return conversation.id` (the full addressable id including the `conversations/` prefix, already assigned to `DialConversation.id`). Retain the `logger.info("conversation_create_succeeded", ...)` line and all existing build/PUT logic unchanged. Do NOT write any `schedule_runs` column here — `conversation.py` stays free of run persistence (plan.md flagged constraint: single owner of run persistence is `jobs.py`; data-model.md §4).

**Checkpoint**: model field, migration (applied + reversible), repository `set_conversation_id`, and
`create_run_conversation -> str` are all in place. Baseline gates still green. User-story phases may
now begin.

---

## Phase 3: User Story 1 — See the conversation for a run in the runs list (Priority: P1)

**Goal**: A successful run that created a DIAL conversation retains and returns that conversation's
addressable identifier in the runs list (and detail), stable across repeated listings.

**Independent Test**: On a schedule with `create_conversation: true`, let a run complete successfully
so a conversation is created, then `GET /v1/schedules/{schedule_id}/runs` and confirm the run entry
carries a non-null `conversation_id` beginning with `conversations/`; `GET
/v1/schedules/{schedule_id}/runs/{run_id}` returns the identical value; re-listing returns the same
value unchanged (quickstart.md US1; SC-001, SC-003, SC-005).

### Tests for User Story 1 — TDD, write FIRST, verify RED before implementing

- [ ] T008 [P] [US1] In `tests/lib/schemas/test_run.py`, add tests that `RunListItem` and `RunResponse` serialise `conversation_id` to the given string when set (data-model.md §6 cases 3, 4). Confirm RED.
- [ ] T009 [P] [US1] In `tests/lib/repositories/test_schedule_run_repository.py`, add tests that `set_conversation_id` sets the column on an existing run and returns the row, and that a re-read shows the value; and that `list_for_schedule` returns runs carrying the persisted `conversation_id` in the same page (data-model.md §6 cases 6, 9 — set case). Confirm RED.
- [ ] T010 [P] [US1] In `tests/worker/test_conversation.py`, add a test that `create_run_conversation` returns the same id it PUTs (`conversation.id`), including the `conversations/` prefix (data-model.md §6 case 10). Confirm RED.
- [ ] T011 [P] [US1] In `tests/worker/test_jobs.py`, add a test that on successful conversation creation `_maybe_create_conversation` calls `set_conversation_id` exactly once with the returned id and logs `conversation_id_persisted` (data-model.md §6 case 11). Confirm RED.
- [ ] T012 [P] [US1] In `tests/lib/services/test_schedule_run_service.py`, add tests that `list_runs` maps `run.conversation_id` into each `RunListItem` and `get_run` maps it into `RunResponse` (set case) (data-model.md §6 cases 16, 17 — set case). Confirm RED.
- [ ] T013 [P] [US1] In `tests/integration/` (new file, e.g. `test_runs_conversation_id.py`), add tests that `GET /v1/schedules/{id}/runs` returns `conversation_id` for a run that created a conversation (SC-001), `GET /v1/schedules/{id}/runs/{run_id}` returns the same value as the list entry (SC-005 consistency), and listing the same run twice returns an identical unchanged value (data-model.md §6 cases 18 set-half, 19, 20; FR-007). Confirm RED.

### Implementation for User Story 1

- [ ] T014 [US1] Add `conversation_id: str | None = None` to `RunListItem` and to `RunResponse` in `src/lib/schemas/run.py` (data-model.md §3; contracts runs-list and run-detail). Makes T008 GREEN.
- [ ] T015 [US1] In `src/worker/jobs.py`, update `_maybe_create_conversation`: capture `conversation_id = await create_run_conversation(...)`, then open a NEW `async with Database().sessionmaker() as session:` and call `ScheduleRunRepository(session).set_conversation_id(ctx.run_id, conversation_id)`; on returned row log `conversation_id_persisted` (info, `run_id`+`schedule_id`), on `None` log `conversation_id_persist_failed` (warning, `run_id`+`schedule_id`, `reason="run_record_missing"`). The persist stays INSIDE the existing best-effort `try/except` (data-model.md §4; plan.md flagged constraints: separate session, strictly after confirmed PUT). Makes T011 GREEN.
- [ ] T016 [US1] In `src/lib/services/schedule_run_service.py`, add `conversation_id=run.conversation_id` to the `RunListItem(...)` construction in `list_runs` and to the `RunResponse(...)` construction in `get_run`; no signature/ownership/pagination/ordering change (data-model.md §4). Makes T012 GREEN and, together with T014, T009/T010/T013 GREEN.
- [ ] T017 [US1] Confirm no code change is needed in `src/api/v1/endpoints/runs.py` — `response_model=Page[RunListItem]` and `response_model=RunResponse` pick up the new field automatically (data-model.md §4). Verify the endpoint tests from T013 pass with the endpoint file unchanged.

**Checkpoint**: US1 acceptance scenarios 1.1/1.2/1.3 satisfied — a successful run's conversation id
is persisted, returned in list and detail identically, and stable on re-listing (SC-001, SC-003,
SC-005). All US1 tests GREEN. US1 is independently demonstrable via quickstart.md US1.

---

## Phase 4: User Story 2 — Distinguish runs that produced no conversation (Priority: P1)

**Goal**: Every run that produced no conversation — creation disabled, run not successful, in-progress,
conversation creation or its persistence failed, or a historical run — reports `conversation_id` as
`null`, unambiguously and without altering the run's outcome.

**Independent Test**: List runs for a schedule that includes a run with `create_conversation: false`,
a run that failed, and (simulated) a successful run whose conversation creation did not complete;
confirm each reports `conversation_id: null` while an unchanged run outcome is preserved
(quickstart.md US2; SC-002, SC-004).

### Tests for User Story 2 — TDD, write FIRST, verify RED before implementing

- [ ] T018 [P] [US2] In `tests/lib/models/test_schedule_run.py`, add a test that `conversation_id` is nullable with no server default and a newly created run reads `None` (data-model.md §6 case 1). Confirm RED.
- [ ] T019 [P] [US2] In `tests/lib/schemas/test_run.py`, add tests that `RunListItem` and `RunResponse` serialise `conversation_id` as `null` when `None`, and that constructing either schema WITHOUT `conversation_id` yields `None` with no validation error (backward compatibility) (data-model.md §6 cases 3/4 null-half, 5). Confirm RED.
- [ ] T020 [P] [US2] In `tests/lib/repositories/test_schedule_run_repository.py`, add tests that `set_conversation_id` returns `None` for an unknown `run_id` and writes nothing; that it does not alter `status`, `end_time`, or `result` on an existing run; and that `list_for_schedule` returns `None` for a run without a persisted id in a page also containing a run with one (data-model.md §6 cases 7, 8, 9 null-half). Confirm RED.
- [ ] T021 [P] [US2] In `tests/worker/test_jobs.py`, add tests that: conversation creation raising `ConversationCreationError` → `set_conversation_id` NOT called, `conversation_create_failed` logged, run stays `success`, id stays `NULL`; `set_conversation_id` raising a DB error → swallowed, run outcome unchanged; `set_conversation_id` returning `None` → `conversation_id_persist_failed` logged, no exception; and non-chat-completion or `create_conversation=False` → neither `create_run_conversation` nor `set_conversation_id` called (data-model.md §6 cases 12, 13, 14, 15; FR-005, FR-006, SC-004). Confirm RED.
- [ ] T022 [P] [US2] In `tests/lib/services/test_schedule_run_service.py`, add tests that `list_runs` and `get_run` map `conversation_id=None` through to the schemas (data-model.md §6 cases 16, 17 null-half). Confirm RED.
- [ ] T023 [P] [US2] In `tests/integration/test_runs_conversation_id.py`, add tests that `GET /v1/schedules/{id}/runs` reports `conversation_id: null` for a run of a schedule with creation disabled and for a failed run (SC-002), and an end-to-end assertion covering a mixed page: one successful run with a non-null id addressing the created conversation and one disabled/failed run with `null` (data-model.md §6 cases 18 null-half, 21). Confirm RED.

### Implementation for User Story 2

- [ ] T024 [US2] Verify the Foundational and US1 implementation already satisfy the "no conversation → null" behaviour end to end, and make the US2 tests GREEN without adding new production code: the migration adds the column as `NULL` (T004/T005 → cases 1, 18-historical), the nullable schema fields default to `None` (T014 → cases 3/4/5 null-half), `set_conversation_id` no-ops on missing run and leaves other fields untouched (T006 → cases 7, 8), the early-return guard in `_maybe_create_conversation` skips the persist path for disabled/non-chat runs and the best-effort `try/except` swallows creation/persist failures leaving the column `NULL` (T015 → cases 12, 13, 14, 15), and the service maps `None` through (T016 → cases 16, 17 null-half). If any US2 test is RED, fix the corresponding file identified above rather than adding new logic (defensive: no new column-clearing path may exist — FR-007). File touched only if a fix is required: one of `src/lib/models/schedule_run.py`, `src/lib/repositories/schedule_run_repository.py`, `src/worker/jobs.py`, `src/lib/services/schedule_run_service.py`, `src/lib/schemas/run.py`.

**Checkpoint**: US2 acceptance scenarios 2.1/2.2/2.3 and all edge cases (in-progress, historical,
non-chat-completion, persist failure) satisfied — every no-conversation run reports `null` and no run
outcome changed (SC-002, SC-004). All US2 tests GREEN.

---

## Phase 5: Polish

- [ ] T025 Run `uv run ruff check src tests` and fix any findings introduced by this feature.
- [ ] T026 Run `uv run ruff format src tests` to format all touched files.
- [ ] T027 Run `uv run mypy src` and fix any type findings (notably the new `create_run_conversation -> str` return and `set_conversation_id -> ScheduleRun | None` signatures).
- [ ] T028 Run `uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml`; confirm the full suite is green and the new/changed lines in `schedule_run.py`, `schedule_run_repository.py`, `conversation.py`, `jobs.py`, `run.py`, `schedule_run_service.py` are covered.
- [ ] T029 Execute quickstart.md end-to-end against a running API + worker: US1 (successful run → non-null id in list, identical in detail, stable on re-list) and US2 (disabled → null, failed → null, simulated conversation-creation failure → success + null), plus the Historical / In-progress / Non-chat-completion edge checks.

---

## Dependencies

### Phase dependency table

| Phase | Depends on | Blocks |
|-------|-----------|--------|
| Phase 1 Setup | — | Phase 2 |
| Phase 2 Foundational | Phase 1 | Phase 3, Phase 4 |
| Phase 3 US1 | Phase 2 | Phase 5 |
| Phase 4 US2 | Phase 2 (and US1 implementation T014–T016, which US2 relies on being present) | Phase 5 |
| Phase 5 Polish | Phase 3, Phase 4 | — |

Both stories are P1. US1 delivers the persist + return path; US2 verifies the absence path on top of
the same code, so US2's implementation task (T024) assumes US1's T014–T016 are already merged.

### Within-phase dependency table — Phase 2 (Foundational)

| Task | Depends on |
|------|-----------|
| T003 model field | — |
| T004 migration | T003 (autogenerate compares against the model) |
| T005 apply/verify migration | T004 |
| T006 `set_conversation_id` | T003 |
| T007 `create_run_conversation -> str` | — |

### Within-phase dependency table — Phase 3 (US1)

| Task | Depends on |
|------|-----------|
| T008–T013 tests (RED) | Phase 2 complete |
| T014 schema fields | T008 RED |
| T015 jobs persist | T007, T006, T011 RED |
| T016 service mapping | T014, T012 RED |
| T017 endpoint no-change verify | T014, T016, T013 |

### Within-phase dependency table — Phase 4 (US2)

| Task | Depends on |
|------|-----------|
| T018–T023 tests (RED) | Phase 2 + US1 implementation (T014–T016) |
| T024 verify/fix null path | T018–T023 RED |

### Parallel execution examples

- **Phase 2**: T003 and T007 can start in the same batch (different files, no interdependency). T006 follows T003. T004→T005 are serial.
- **US1 tests**: T008, T009, T010, T011, T012, T013 are all `[P]` — different test files, all runnable in one batch once Phase 2 is done.
- **US2 tests**: T018, T019, T020, T021, T022, T023 are all `[P]` — different test files, one batch.
- US1 implementation (T014–T016) is largely serial by data flow: schema field → service mapping; the jobs change (T015) is independent of the schema/service edits and can run alongside T014/T016 (different file).

### Implementation strategy

**MVP first (US1)**: Phase 1 (T001–T002) → Phase 2 (T003–T007) → Phase 3 (T008–T017). At the end of
Phase 3 the feature's core value is delivered and demonstrable: a successful run persists and returns
its DIAL conversation id in both the list and detail responses, stable across listings. This alone
satisfies FR-001–FR-004, FR-007, FR-008 and SC-001, SC-003, SC-005.

**Incremental delivery**:
- US1 — validate by running quickstart.md US1: a `create_conversation: true` chat-completion schedule's successful run shows a non-null `conversation_id` in `GET .../runs`, identical in `GET .../runs/{run_id}`, unchanged on re-list.
- US2 — validate by running quickstart.md US2: a disabled-creation run, a failed run, and a simulated conversation-creation-failure run each report `conversation_id: null` with their run outcome unchanged.

**Key implementation constraints** (source: plan.md "Patterns.md Issues Flagged"):

| Constraint | Location | Source |
|-----------|----------|--------|
| Persist runs in its OWN `Database().sessionmaker()` session, opened after the confirmed-success write, so a persist failure can never roll back `_persist_success`. | `src/worker/jobs.py` `_maybe_create_conversation` (T015) | plan.md flagged issue 1 (FR-006, SC-004) |
| Persist strictly AFTER the confirmed `PUT`: `create_run_conversation` only *returns* `conversation.id` after a successful `put_conversation`; the DB write lives solely in `jobs.py`. Never persist a placeholder before creation succeeds. | `src/worker/conversation.py` (T007) + `src/worker/jobs.py` (T015) | plan.md flagged issue 2 (FR-003) |
| Unbounded `VARCHAR`, NO length cap — `sa.String()` with no length; a `String(N)` cap risks silently truncating a valid id into an un-openable one. | `src/lib/models/schedule_run.py` (T003) + migration (T004) | plan.md flagged issue 3 (SC-003) |
| NO index / NO FK — the list filters by the already-indexed `schedule_id` and never queries by `conversation_id`; it is not a reference to a scheduler-owned table. | migration (T004) | plan.md flagged issue 4 |
