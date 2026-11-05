# Contract: Delete Schedule

**Endpoint**: `DELETE /v1/schedules/{schedule_id}`
**Handler**: `src/api/v1/endpoints/schedules.py::delete_schedule` (appended at end of file — see
research.md "route registration order is fragile")
**Change type**: Additive (new operation). No existing operation's request or response shape changes.
No major version bump.

## Request

| Part | Value |
|------|-------|
| Path param | `schedule_id: str` |
| Body | none |
| Query | none |
| Auth | Same owner-scoped identity as every other schedules route (`get_user_id`) |

## Responses

| Status | Body | Condition |
|--------|------|-----------|
| **204 No Content** | empty | Deletion completed — the schedule was either removed entirely (no runs) or marked deleted (runs exist). The caller is **not** told which branch ran; both mean "deleted". |
| **404 Not Found** | `{"detail": "Schedule not found: {id}"}` | Unknown id, or the schedule belongs to another user, or it was previously hard-deleted. |
| **409 Conflict** | `{"detail": "Schedule already deleted: {id}"}` | The schedule exists but is already soft-deleted (`is_deleted: true`), including the loser of two concurrent deletes. |
| **502 Bad Gateway** | `{"detail": "<reason>"}` | The scheduling engine could not unregister the job. Nothing was written to the database; the schedule remains live and the request is safe to retry. |

`204` is declared with `status_code=204` and the handler returns `None`; no `response_model` is set.

## Semantics

Branch selection is made by the system from run history alone — there is no caller-supplied option
(FR-005):

| Run records for the schedule | Outcome | Afterwards |
|------------------------------|---------|------------|
| zero (any status) | Job unregistered **and** the schedule row deleted | `GET /{id}` → 404; absent from every list |
| one or more (any status, incl. `in_progress`) | Job unregistered **and** `is_deleted` set to `true` | `GET /{id}` → 200 with `is_deleted: true`, `next_run_time: null`; run history unchanged and still retrievable |

Ordering guarantee: the job is unregistered **before** the database is written. A `204` therefore
implies the schedule can no longer fire. If the database write fails after a successful unregister,
the caller receives a 5xx and the schedule is left live-looking but non-firing; a retried `DELETE`
completes the operation (FR-017).

Idempotency: `DELETE` is **not** idempotent at the status-code level by design — a second call returns
404 (hard-deleted) or 409 (soft-deleted) rather than 204, because FR-015 requires re-deletion to be
rejected. Internally, a missing scheduler job is tolerated silently so retries after a partial failure
still succeed.

Irreversibility: no endpoint, parameter, or body value restores a deleted schedule (FR-014).

## Side effects

- Removes the schedule's entry from the APScheduler job store.
- Either deletes one `schedules` row or sets its `is_deleted` to `true` (and refreshes `updated_at`).
- **Never** inserts, updates, or deletes any `schedule_runs` row. The
  `schedule_runs.schedule_id → schedules.id ON DELETE RESTRICT` foreign key makes history loss
  impossible even under a logic error.
- Emits the structured log events listed in data-model.md §4.

## MCP exposure

The route carries the `schedules` tag, which `src/api/mcp/route_maps.py` maps to `MCPType.TOOL`, so
this operation is **automatically exposed as an MCP tool** alongside create/update/pause/resume. This
is a deliberate, recorded consequence (research.md), not an oversight;
`tests/unit/api/mcp/test_mcp_tools.py` must be updated to include it.

## Acceptance checks (maps to spec)

| Check | Spec ref |
|-------|----------|
| Delete a never-run schedule → 204, then `GET` → 404 and absent from all lists | US1 / FR-003, SC-003 |
| Deleted never-run schedule never fires again | US1 / FR-006 |
| Delete a schedule with runs → 204, `GET` → 200 with `is_deleted: true` | US2 / FR-004 |
| Run history of a soft-deleted schedule is fully retrievable and unchanged | US2 / FR-016, SC-002 |
| Soft-deleted schedule reports `next_run_time: null` | FR-019 |
| Unknown / foreign schedule → 404, nothing changed | US1 / FR-002 |
| Re-delete of a soft-deleted schedule → 409, nothing changed | US4 / FR-015 |
| Missing scheduler job → deletion still succeeds | Edge case |
| Scheduler unregister failure → 502, schedule still live, retry works | Edge case / FR-017 |
| Persist failure after unregister → error returned, retried delete completes | Edge case / FR-017, SC-006 |
| Run inserted between count and delete → soft-deleted, new run preserved | Edge case |
| In-progress run at delete time → run completes, schedule soft-deleted | Edge case |
| Two concurrent deletes → exactly one 204, the other 409 | Edge case |
| Deleting one schedule affects no other schedule or history | Edge case |
| Every attempt is traceable in logs with the branch taken | FR-018, SC-008 |
