# Phase 0 Research: Schedule Pause & Resume Endpoints

All Technical Context items were resolvable from the existing codebase and APScheduler 3.11
behaviour; there were no open `NEEDS CLARIFICATION` markers. This document records the
decisions that shape the implementation.

## 1. How to pause/resume a job by id

- **Decision**: Call `AsyncIOScheduler.pause_job(schedule_id)` and
  `AsyncIOScheduler.resume_job(schedule_id)` on the scheduler held in `app.state.scheduler`,
  using the schedule id directly as the job id.
- **Rationale**: `ScheduleService._register_job` already registers jobs with
  `id=schedule_id` (`src/lib/services/schedule_service.py:38`), and the API contract states the
  uuid is "same value used for schedules.id table and as job id". So no mapping/lookup is
  needed. `pause_job` sets the job's `next_run_time` to `None` in the shared
  `SQLAlchemyJobStore`; `resume_job` recomputes it from the trigger. Because the API and worker
  share the job store, the worker observes the change.
- **Alternatives considered**: Adding a `paused` column to `schedules` and filtering in the
  worker — rejected: the spec explicitly forbids DB changes, and APScheduler's native pause is
  the source of truth for firing. Tracking state in a separate store — rejected (YAGNI).

## 2. Running blocking scheduler calls without blocking the event loop

- **Decision**: Wrap `pause_job`/`resume_job` in `await asyncio.to_thread(...)`.
- **Rationale**: These methods perform synchronous job-store I/O (a `UPDATE` against
  PostgreSQL via the job store's own engine). The codebase already offloads the analogous
  blocking `add_job` call this way (`schedule_service.py:34`). Consistency + Constitution I
  (no event-loop blocking).
- **Alternatives considered**: Calling synchronously — rejected (blocks the loop). Using an
  async job store — rejected: APScheduler 3.x `SQLAlchemyJobStore` is synchronous and changing
  it is out of scope.

## 3. Ownership enforcement and 404 semantics

- **Decision**: Resolve the caller via the existing `get_user_id` dependency, then call
  `ScheduleRepository.get(schedule_id, created_by)`. If it returns `None`, raise
  `ScheduleNotFoundError`, which the endpoint maps to **404** with the same message shape as
  the existing get/update endpoints.
- **Rationale**: `ScheduleRepository.get` already scopes by `created_by` and returns `None`
  for both "missing" and "owned-by-someone-else", giving the required no-existence-disclosure
  behaviour for free (FR-003, FR-004). Identical to update/get
  (`src/api/v1/endpoints/schedules.py:33-69`).
- **Alternatives considered**: A new lightweight "exists & owned" query — rejected: `get`
  already returns exactly what's needed (the row plus `next_run_time`) and reusing it is DRY.

## 4. Behaviour when the schedule is owned but its job no longer exists

- **Decision**: If `pause_job`/`resume_job` raises `apscheduler.jobstores.base.JobLookupError`,
  map it to `ScheduleNotFoundError` → **404**.
- **Rationale**: A one-off (`date`) schedule that has already fired leaves the `schedules`
  row in place but removes the job from the store. There is then no active scheduling to act
  upon, so 404 (per spec Edge Cases) is the consistent answer and avoids leaking a 500.
- **Alternatives considered**: 409 Conflict — rejected: the spec's edge case calls for
  "reported as not found"; introducing a new status code adds surface area for no benefit.

## 5. Idempotency

- **Decision**: Rely on APScheduler's native idempotent behaviour and return success.
- **Rationale**: `pause_job` on an already-paused job re-sets `next_run_time=None` (no error);
  `resume_job` on an active job recomputes `next_run_time` (no error). No extra state checks
  are required to satisfy FR-007. `resume_job` on a trigger with no future fire time removes
  the job and the response reports `next_run_time: null` (spec edge case).

## 6. Success response shape and status code

- **Decision**: Return **200 OK** with the existing `ScheduleResponse` body, re-fetched after
  the pause/resume so `next_run_time` reflects the new state. No request body is required.
- **Rationale**: Consistent with the update endpoint (`PUT` → 200 + `ScheduleResponse`),
  reuses the existing Pydantic v2 schema (Constitution IV), and gives the caller immediate
  confirmation of the resulting `next_run_time` (FR-010). The job-store row is committed by the
  `pause_job`/`resume_job` thread before the re-fetch, and under PostgreSQL READ COMMITTED the
  subsequent `SELECT` in the request's session observes the committed value.
- **Alternatives considered**: 204 No Content — rejected: hides the resulting `next_run_time`
  the spec wants observable; less consistent with the other write endpoint.
