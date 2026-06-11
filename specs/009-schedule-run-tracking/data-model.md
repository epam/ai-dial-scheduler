# Phase 1 Data Model: Schedule Run Tracking

No schema changes. This feature **reads from and writes to the existing `schedule_runs` table**
(model `src/lib/models/schedule_run.py`, migration `20260610_1947_092b5f51aa98`). It also adds
one internal in-memory schema (`RunOutcome`).

## Persisted entity: ScheduleRun (existing — reused)

| Field | Type | Notes for this feature |
|-------|------|------------------------|
| `id` | `uuid.UUID` (PK) | Set to the job-generated `run_id`. Primary-key lookup key for terminal updates. |
| `status` | `str` (CHECK in {`success`,`error`,`in_progress`,`missed`}) | `in_progress` at start → `success` or `error` at terminal. `missed` is not written by this feature. |
| `end_time` | `datetime \| None` (tz-aware) | `None` while in progress; set to `datetime.now(UTC)` at terminal update. |
| `result` | `dict[str, Any] \| None` (JSONB) | `None` at start. On success: the executor's returned result object. On error: `{"error_text": "<exception message>"}`. |
| `schedule_id` | `str` (FK → `schedules.id`, ON DELETE RESTRICT) | The schedule that fired. |
| `schedule_snapshot` | `dict[str, Any]` (JSONB, NOT NULL) | JSON-safe copy of the job `kwargs` at fire time. |
| `created_at` / `updated_at` | `datetime` (server defaults) | Managed by `BaseSQLModel`. `updated_at` advances on the terminal update. |

### State transitions

```
            create_in_progress                 mark_success
(no row) ─────────────────────▶ in_progress ───────────────▶ success   (terminal)
                                     │
                                     │ mark_error
                                     └──────────────────────▶ error     (terminal)

interrupted process: row stays in_progress (intentionally visible; not reaped by this feature)
```

**Invariants**
- Exactly one row per fired non-heartbeat execution, keyed by `run_id`.
- `end_time` is `NULL` **iff** `status = in_progress` (under normal completion).
- `result` is non-null after `success`; `result.error_text` is non-empty after `error`.
- The heartbeat job creates **no** row.

## Internal schema: RunOutcome (new) — `src/lib/schemas/run.py`

Standardized success object returned by `execute_scheduled_job` and carried as
`JobExecutionEvent.retval` to the `EVENT_JOB_EXECUTED` listener.

| Field | Type | Notes |
|-------|------|-------|
| `run_id` | `str` | Same value as the in-progress row's `id`. |
| `schedule_id` | `str` | Originating schedule. |
| `result` | `dict[str, Any]` | Executor-produced result object (see contracts/run-outcome.md). |

`RunContext` (existing) is unchanged and continues to carry `run_id` + `schedule_id` through the
job and into the executors.

## Failure carrier: ScheduleRunError (existing — reused)

`ScheduleRunError(message, schedule_id, run_id)` is raised on any failure (including validation)
and arrives as `JobExecutionEvent.exception` at the `EVENT_JOB_ERROR` listener. `run_id` is the
lookup key; `str(exception)` becomes `result.error_text`.

## Validation rules applied

- `status` values constrained by the DB CHECK constraint and the `ScheduleRunStatus` enum.
- `schedule_snapshot` must be JSON-serializable: the job builds it as a JSON-safe dict from
  `kwargs` (enum values coerced to their string form) before insert.
- Secrets/credentials must never appear in `result` or `error_text` (FR-012); executor result
  shapes deliberately exclude request auth headers.
