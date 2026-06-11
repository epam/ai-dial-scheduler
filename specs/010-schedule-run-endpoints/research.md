# Phase 0 Research: Schedule Run List & Run Details Endpoints

No `NEEDS CLARIFICATION` markers remained from the spec. The research below records the design
decisions that resolve the spec's documented assumptions against the existing codebase.

---

## D1. `start_time` source — there is no `start_time` column

**Decision**: Map the response field `start_time` to `ScheduleRun.created_at`.

**Rationale**: `ScheduleRun` (`src/lib/models/schedule_run.py`) has no explicit `start_time` column.
It inherits `created_at` (server default `now()`) from `BaseSQLModel`, and the run row is inserted by
`ScheduleRunRepository.create_in_progress` at the moment the job fires — so `created_at` *is* the
start time. `end_time` is a real nullable column already present. No schema change needed.

**Alternatives considered**: Add a dedicated `start_time` column + migration — rejected (YAGNI; the
existing `created_at` already carries fire time, and feature 009 is the owner of that table).

---

## D2. Embedded `schedule` in run details — live row vs. snapshot

**Decision**: Populate the run-details `schedule` object (`display_name`, `trigger`, `properties`)
from the **live `Schedule` row** fetched via `ScheduleRepository.get(schedule_id, created_by)`, not
from the run's `schedule_snapshot`.

**Rationale**: The persisted `schedule_snapshot` is the APScheduler job `kwargs`
(`{**properties, "schedule_id": ...}`) — it contains *properties only*, and lacks `display_name` and
`trigger`. The contract (`documentation/api_contract.md`) requires all three. Reusing
`ScheduleRepository.get` also gives us the ownership check for free and keeps the created_by rule in
one place (`schedule.trigger` → `trigger`, `schedule.kwargs` → `properties`, matching
`ScheduleService._to_response`). The trade-off — the embed reflects the schedule's *current*
definition rather than its fire-time definition — is acceptable for this increment and is recorded as
an assumption in the spec. Fire-time fidelity (via richer snapshots) is out of scope.

**Alternatives considered**:
- Build the embed from `schedule_snapshot` — rejected: missing `display_name`/`trigger`, would need a
  snapshot-format change owned by feature 009.
- Store a full schedule snapshot now — rejected (YAGNI; cross-feature change).

---

## D3. Ownership enforcement & "not found" indistinguishability

**Decision**: Authorize by calling `ScheduleRepository.get(schedule_id, created_by)` first in both
service methods. If it returns `None`, raise `ScheduleNotFoundError` → HTTP 404. Only after ownership
is confirmed do we query runs. The run query itself filters by `schedule_id` (no created_by needed,
since ownership is already proven for that schedule).

**Rationale**: The spec requires that "schedule owned but never ran" returns an empty 200, while
"schedule missing or owned by another user" returns 404 — these must be **distinguishable** server-
side but the 404 must be **identical** across the missing vs. cross-owner cases. A single join of
runs→schedules filtered by created_by cannot distinguish "no runs" from "not owned" (both yield empty).
An explicit ownership check first cleanly separates the two and reuses the exact filter the schedule
endpoints already use. The 404 detail string is generic (`Schedule not found: {schedule_id}`),
matching the existing schedule endpoints, so it leaks no existence info.

**Alternatives considered**: Single join query — rejected (cannot satisfy the empty-vs-404 split).

---

## D4. Run-details lookup must verify the run belongs to the path schedule

**Decision**: `get_for_schedule(schedule_id, run_id)` fetches the run by primary key and returns it
only if its `schedule_id` matches the path. A run that exists under a *different* schedule → `None`
→ `ScheduleRunNotFoundError` → HTTP 404.

**Rationale**: Spec edge case — a run is only reachable through its own schedule. Filtering the query
by both `id` and `schedule_id` enforces this in the database.

---

## D5. `run_id` path-parameter type & malformed-input handling

**Decision**: Declare the path parameter as `run_id: uuid.UUID`. FastAPI validates it and returns
HTTP 422 automatically for a malformed UUID, before any DB call. Pass `str(run_id)` into the service.

**Rationale**: `ScheduleRun.id` is a real `UUID`. Typing the path param as `uuid.UUID` gives free,
consistent validation and avoids the `uuid.UUID(...)` `ValueError` path inside the repository. This
satisfies the "malformed identifier rejected without leaking internal detail" edge case. `schedule_id`
remains `str` (schedules use string UUIDs as their PK, consistent with `schedules.py`).

**Alternatives considered**: `run_id: str` + manual try/except → 404 — rejected (more code, less
consistent than letting FastAPI validate).

---

## D6. New exception vs. reuse

**Decision**: Add `ScheduleRunNotFoundError(SchedulerError)` to `src/lib/exceptions/errors.py`.
Reuse the existing `ScheduleNotFoundError` for the schedule-ownership failure.

**Rationale**: Two distinct 404 causes (schedule not owned/missing vs. run missing/mismatched) read
more clearly as two exception types, both mapping to 404 in the endpoint. Mirrors the existing
pattern of small, specific `SchedulerError` subclasses.

---

## D7. Pagination, ordering, and base path

**Decision**: Reuse the `Pagination` dependency and `Paginator.paginate(count, results)` envelope.
Base path for `next`/`previous` links is `/schedules/{schedule_id}/runs` (the concrete schedule_id
interpolated). Order runs by `created_at DESC, id DESC` for stable, gap-free paging
(FR-013 / SC-002).

**Rationale**: Matches the schedule list endpoint exactly (`DEFAULT_PAGE_LIMIT = 20`, `limit >= 1`,
`offset >= 0`). `created_at` alone is not strictly unique (server `now()` could collide), so `id` is
added as a deterministic tiebreaker to guarantee no skips/duplicates across pages. There is currently
no enforced maximum page size in the codebase — this feature does not introduce one (consistency with
existing behavior; tightening the bound is a separate cross-cutting change).

**Alternatives considered**: Order by `end_time` — rejected (NULL for in-progress runs breaks
ordering). Order by `created_at` only — rejected (non-deterministic tiebreak risks paging anomalies).

---

## D8. Routing — separate router vs. extend `schedules.py`

**Decision**: New `src/api/v1/endpoints/runs.py` with
`router = APIRouter(prefix="/schedules/{schedule_id}/runs", tags=["runs"])`, included in `app.py` via
`app.include_router(runs_router, prefix="/v1")`.

**Rationale**: Keeps run handlers cohesive and separately taggable in OpenAPI, while producing the
exact contract paths. `schedule_id` becomes a shared path parameter for both run handlers. Mirrors
the existing one-router-per-resource convention without bloating `schedules.py`.

---

## D9. User identity

**Decision**: Use `MOCK_USER_ID` from `src/lib/constants.py` as `created_by`, exactly as the existing
schedule endpoints do today.

**Rationale**: DIAL user resolution is not yet wired into the schedule endpoints; introducing it here
would be inconsistent and out of scope. When real DIAL identity is added, all endpoints (schedules +
runs) should adopt it together. Logged and tracked as a shared follow-up, not a blocker for this
feature.

---

## Summary of resulting components

| Component | File | Change |
|-----------|------|--------|
| Response schemas `RunListItem`, `RunResponse`, `RunScheduleSummary` | `src/lib/schemas/run.py` | MODIFY (add) |
| `ScheduleRunNotFoundError` | `src/lib/exceptions/errors.py` | MODIFY (add) |
| `list_for_schedule`, `get_for_schedule` | `src/lib/repositories/schedule_run_repository.py` | MODIFY (add) |
| `ScheduleRunService` | `src/lib/services/schedule_run_service.py` | NEW |
| `get_schedule_run_service` | `src/api/dependencies/runs.py` | NEW |
| `list_runs`, `get_run` handlers | `src/api/v1/endpoints/runs.py` | NEW |
| Router registration | `src/api/app.py` | MODIFY |
