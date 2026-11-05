# Contract: `is_deleted` Response Field and Its Immutability

**Endpoints affected**: `GET /v1/schedules`, `GET /v1/schedules/{schedule_id}`,
`POST /v1/schedules/`, `PUT /v1/schedules/{schedule_id}`,
`POST /v1/schedules/{schedule_id}/pause`, `POST /v1/schedules/{schedule_id}/resume`
**Schemas**: `src/lib/schemas/schedule.py` — `ScheduleResponse`, `ScheduleListItem`
**Change type**: Additive response field (non-breaking) + new 409 state on three existing
mutating routes. No major version bump.

## New response field

| Field | Type | Nullable | Present in |
|-------|------|----------|-----------|
| `is_deleted` | `boolean` | no (always present) | `ScheduleResponse` (create, update, details, pause, resume) and `ScheduleListItem` (list rows) |

Values: `false` for a live schedule, `true` for a soft-deleted one. Hard-deleted schedules have no
representation — they are simply absent (404 / not listed).

A newly created schedule always reports `is_deleted: false` (FR-007). Pre-existing schedules report
`false` after the migration backfill.

## Not a request field

`is_deleted` is deliberately **absent** from `ScheduleRequest`, which backs both `POST /` and
`PUT /{schedule_id}`.

| Client sends | Result |
|--------------|--------|
| `{"is_deleted": true, ...}` on POST | Field ignored; schedule created with `is_deleted: false`. **No 422.** |
| `{"is_deleted": true, ...}` on PUT | Field ignored; the schedule's `is_deleted` is unchanged by the update. **No 422.** |

The mechanism is Pydantic v2's default `extra="ignore"` (no `model_config` is declared anywhere in
`schemas/schedule.py`), plus the fact that `ScheduleRepository.update` never assigns the column. This
implements FR-009's "MUST ignore" wording literally — a 422 would be a different, breaking behaviour.
Because the guarantee rests on a default rather than on explicit code, it is pinned by an
explicit test (data-model.md case 4 and 30).

## New behaviour on existing mutating routes

A soft-deleted schedule is frozen. `PUT /{schedule_id}`, `POST /{schedule_id}/pause`, and
`POST /{schedule_id}/resume` now return:

| Status | Body | Condition |
|--------|------|-----------|
| **409 Conflict** | `{"detail": "Schedule already deleted: {id}"}` | Target schedule has `is_deleted: true` |

This is reachable only for schedules in a state that could not exist before this feature, so no
currently working client call changes behaviour. `GET /{schedule_id}` deliberately still returns
**200** for a soft-deleted schedule — that is how an owner sees the flag and reaches the surviving
run history (research.md D7).

There is no request that clears `is_deleted` (FR-014).

## Read paths that intentionally do not filter on `is_deleted`

| Path | Behaviour for a soft-deleted schedule |
|------|--------------------------------------|
| `GET /v1/schedules/{schedule_id}` | 200 with `is_deleted: true`, `next_run_time: null` |
| `GET /v1/schedules/{schedule_id}/runs` | 200, full run history, unchanged |
| `GET /v1/schedules/{schedule_id}/runs/{run_id}` | 200, unchanged |

The runs routes require **no code change**: they resolve ownership through
`ScheduleRepository.get`, which filters on `id` and `created_by` only. Adding an `is_deleted` filter
to `get` would silently break FR-016, so an integration test pins the current behaviour.

## Acceptance checks (maps to spec)

| Check | Spec ref |
|-------|----------|
| `is_deleted` present on every list item and details response | FR-008, US3 |
| New schedule reports `is_deleted: false` | FR-007, US4 |
| `is_deleted` in a POST or PUT body is ignored, not rejected | FR-009, US4 |
| PUT never changes `is_deleted` | FR-009 |
| PUT / pause / resume on a soft-deleted schedule → 409 | FR-013, US4, SC-005 |
| Soft-deleted schedule still returns 200 on details with `next_run_time: null` | FR-019, US2 |
| Run history of a soft-deleted schedule still fully readable | FR-016, SC-002 |
| No request can set `is_deleted` back to `false` | FR-014, SC-005 |
