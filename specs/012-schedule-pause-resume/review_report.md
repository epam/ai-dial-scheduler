# Review Report — Schedule Pause & Resume Endpoints

- **spec-dir**: `specs/012-schedule-pause-resume/`
- **Date**: 2026-06-12
- **Run mode**: Fresh Review
- **Artifacts consulted**: `spec.md`, `plan.md`, `tasks.md`, `contracts/pause-resume-endpoints.md`
- **Files reviewed**:
  - `src/lib/services/schedule_service.py` (added `pause`, `resume`)
  - `src/api/v1/endpoints/schedules.py` (added `pause_schedule`, `resume_schedule`)
  - `tests/unit/lib/services/test_schedule_service.py` (`TestScheduleServicePause`, `TestScheduleServiceResume`)
  - `tests/integration/test_schedules_api.py` (`TestPauseSchedule`, `TestResumeSchedule`)

## Summary

**Approved with minor suggestions.** The implementation faithfully matches the spec, plan, and
contract. Ownership is enforced before any scheduler call, `JobLookupError` is mapped to
`ScheduleNotFoundError` → 404, off-loop APScheduler calls use `asyncio.to_thread`, logging is
structured, and `ScheduleResponse` is reused. `ruff`, `mypy`, and the 9 new unit tests all pass.
There are no critical issues. The notes below are improvements and polish only.

## Critical Issues

None.

## Improvements

### I1 — `JobLookupError` unit test does not prove the *specific* mapping — Status: Open
- **Files**: `tests/unit/lib/services/test_schedule_service.py:352-370` (pause), `:444-462` (resume)
- **Problem**: The test patches `asyncio.to_thread` with `AsyncMock(side_effect=JobLookupError(...))`
  and asserts `ScheduleNotFoundError` is raised. Because the service catches *only* `JobLookupError`,
  the test does confirm the mapping — but it would equally pass if the code caught a broader class
  (e.g. `except Exception`). The contract requirement (`contracts/pause-resume-endpoints.md` lines
  25-27) is specifically that `JobLookupError` maps to 404 while *other* scheduler errors do not
  silently become 404. There is no negative test asserting that an unexpected scheduler error
  (e.g. `RuntimeError`) propagates rather than being swallowed into a 404.
- **Suggested fix**: Add one test per method asserting a non-`JobLookupError` exception propagates:
  ```python
  async def test_pause_unexpected_scheduler_error_propagates(self):
      repo = AsyncMock()
      scheduler = MagicMock()
      now = datetime.now(UTC)
      schedule = make_schedule()
      schedule.created_at = now
      schedule.updated_at = now
      repo.get.return_value = (schedule, None)
      service = ScheduleService(repo, scheduler)
      with patch(
          "src.lib.services.schedule_service.asyncio.to_thread",
          new=AsyncMock(side_effect=RuntimeError("job store down")),
      ):
          with pytest.raises(RuntimeError):
              await service.pause("test-uuid", "mock_user_id")
  ```
  This locks in the narrow catch and prevents a future regression to `except Exception`.

### I2 — `pause`/`resume` duplicate the body of `get` plus an off-loop call — Status: Open
- **File**: `src/lib/services/schedule_service.py:184-210`
- **Problem (DRY)**: `pause` and `resume` are byte-for-byte identical except for the scheduler method
  (`pause_job` vs `resume_job`) and the log event string. They also duplicate the
  ownership-load / not-found / re-fetch / `_to_response` flow already present in `get`. This is minor
  given there are only two methods, but the duplication is exact.
- **Suggested fix (optional)**: Extract a private helper, keeping the two public methods as thin
  wrappers (still ≤2 params, single responsibility preserved):
  ```python
  async def _apply_job_state(self, schedule_id: str, created_by: str, action, log_event: str) -> ScheduleResponse:
      if await self._repo.get(schedule_id, created_by) is None:
          raise ScheduleNotFoundError(schedule_id)
      try:
          await asyncio.to_thread(action, schedule_id)
      except JobLookupError as exc:
          raise ScheduleNotFoundError(schedule_id) from exc
      logger.info(log_event, schedule_id=schedule_id)
      schedule, next_run_time = await self._repo.get(schedule_id, created_by)  # type: ignore[misc]
      return self._to_response(schedule, next_run_time)
  ```
  Weigh this against YAGNI — with only two call sites the current explicit form is also defensible.
  Not blocking.

### I3 — `created_by` is logged neither here nor implicitly — Status: Open
- **File**: `src/lib/services/schedule_service.py:193, 207`
- **Problem (intent alignment, minor)**: FR-009 requires logging "sufficiently to trace **which
  caller** acted on which schedule." The log lines emit only `schedule_id=...`. The caller identity
  (`created_by`) is not in the structured event. The task description (T005/T009) only specified
  `schedule_id`, so this is a spec-vs-task gap rather than a code defect, but FR-009 explicitly names
  the caller as part of the trace requirement.
- **Suggested fix**: Add the caller to the structured event (it is a `user_id`, not a secret, so this
  is consistent with the "no secrets" rule):
  ```python
  logger.info("schedule_paused", schedule_id=schedule_id, created_by=created_by)
  ```
  Confirm with the team whether `created_by` is considered PII under their logging policy; if so,
  hash or omit. Otherwise FR-009 is only partially satisfied.

## Minor Suggestions

- **M1** — `src/lib/services/schedule_service.py:195, 209`: the `row` reuse + tuple unpack with
  `# type: ignore[misc]` mirrors the existing `create`/`update` pattern, so it is consistent. No change
  needed; flagged only for awareness that the `type: ignore` exists because the re-fetch is known to be
  non-`None` (the row existed moments earlier). A micro-clarity alternative is to unpack directly:
  `schedule, next_run_time = await self._repo.get(...)  # type: ignore[misc]`.
- **M2** — `tests/integration/test_schedules_api.py:406, 480`: `from fastapi import HTTPException`
  is imported inside the test body. `HTTPException` is already a top-level import candidate in this
  module's third-party block (`fastapi` is imported at line 14). Moving it to the module header would
  be marginally cleaner, though local import keeps the auth-override helper self-contained. Optional.
- **M3** — Integration tests for pause/resume assert state and shape but, like the rest of the file,
  depend on `TEST_DB_URL`/`engine` and are skipped without a DB. That matches the existing pattern in
  the module; no action needed.

## Intent Alignment

- **FR-001 / FR-002 (endpoints exist)**: Met — `POST /{schedule_id}/pause`, `POST /{schedule_id}/resume`,
  both `response_model=ScheduleResponse`.
- **FR-003 / FR-004 / US3 (owner-scoping, no disclosure)**: Met — ownership is checked via
  `self._repo.get(schedule_id, created_by)` *before* any scheduler call
  (`schedule_service.py:186-188, 200-202`); the repository scopes on `created_by`
  (`schedule_repository.py:35`). Non-owner and unknown id both yield `None` → identical 404. Verified by
  `test_pause/resume_cross_owner_returns_404_identical_to_unknown` and the unit tests asserting
  `pause_job`/`resume_job` is never called when `repo.get` returns `None`.
- **FR-005 (no DB mutation)**: Met — neither method writes to the `schedules` table; only the off-loop
  scheduler call runs. No migration added.
- **FR-006 / FR-010 (resume restores firing / next_run_time)**: Met by design — `next_run_time` is
  re-read from the live APScheduler job store join after the scheduler call, so it reflects the post-
  action state. Verified by the exhausted-trigger unit test (`next_run_time is None`).
- **FR-007 (idempotent)**: Met — APScheduler's `pause_job`/`resume_job` are idempotent and the service
  does not branch on current state; covered by `test_pause_idempotent_already_paused` and
  `test_resume_idempotent_already_active`.
- **FR-008 (auth, 401)**: Met — `get_user_id` dependency; covered by `test_*_unauthenticated_returns_401`.
- **FR-009 (traceable logging, no secrets)**: **Partially met** — structured `schedule_paused`/
  `schedule_resumed` events are emitted with `schedule_id`, but the *caller* is not included; see I3.
- **Off-loop requirement (plan Constitution I)**: Met — `asyncio.to_thread(self._scheduler.pause_job, ...)`,
  asserted in unit tests via `mock_thread.await_args[0]`.
- **JobLookupError → 404 (contract)**: Met — caught and re-raised as `ScheduleNotFoundError`; see I1 for
  a hardening suggestion.

## Checklist Pass

- **Correctness & Logic**: Pass. Ownership precedes scheduler call; idempotent; `JobLookupError` handled;
  no bare `except`; no `assert` in production code.
- **Type Safety**: Pass. Full type hints; `mypy` clean. `# type: ignore[misc]` on re-fetch unpack matches
  existing pattern.
- **Code Style & Conventions**: Pass. `ruff check` clean; line length ≤120; `snake_case`/`PascalCase`;
  Google-style docstrings on both new methods and both handlers; no `print()`; imports ordered.
- **Architecture & Design**: Pass. Business logic in service, thin handlers; ≤2 params on service methods;
  scheduler via `app.state`/`Depends`. Minor DRY note in I2.
- **Database & ORM**: Pass. No raw SQL; no schema change; no migration needed (per spec).
- **Project Structure**: Pass. Only the four intended files changed; tests mirror `src/`.
- **Testing**: Pass with note. Happy path, idempotency, `JobLookupError`, cross-owner, unknown id, and 401
  all covered in unit and/or integration; pytest-mock-style mocking via `unittest.mock` is consistent with
  the existing file. Gap: no negative test that a non-`JobLookupError` scheduler error propagates (I1).
- **Security**: Pass. No existence disclosure (identical 404s, verified); no secrets logged; auth enforced
  at the boundary. Confirm `created_by` logging policy if I3 is adopted.
