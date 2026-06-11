# Phase 0 Research: Schedule Management Endpoints

All Technical Context unknowns are resolved below. Each decision is grounded in the existing
codebase (feature 006 models, `src/api/app.py`, `src/worker`, `src/lib/scheduler.py`) and the
constitution. There are **no remaining NEEDS CLARIFICATION** items.

## R1 — Where the executed job lives and how the service references it

**Decision**: `execute_scheduled_job` is defined in `src/worker/jobs.py`. The service registers
it by **textual reference** `"src.worker.jobs:execute_scheduled_job"`, stored as a constant
`EXECUTE_SCHEDULED_JOB_REF` in `src/lib/constants.py`. APScheduler accepts a string `func`
and resolves it by import path inside the executing process.

**Rationale**: The worker is the sole executor (`app.py` starts the API scheduler
`paused=True`). The job therefore belongs in `worker/`. The constitution forbids `lib/`
importing from `src.worker`; a string reference lets `lib/services` register the job without
importing it, preserving the inward-only dependency arrow. The existing demo (`worker/jobs.py:dummy_job`)
confirms jobs live in `worker/`.

**Alternatives considered**: Importing the callable in the service (violates layer rule);
placing the job in `lib/` (wrong layer — it is worker execution logic, not shared library code).

## R2 — Does `add_job` on a `paused` scheduler persist and compute `next_run_time`?

**Decision**: Yes. The API scheduler is started with `start(paused=True)`, which puts it in
`STATE_PAUSED` (not `STATE_STOPPED`). In that state `add_job` runs the real add path: it
computes `next_run_time` from the trigger and writes the job (with that time) to the
`SQLAlchemyJobStore`; it simply does not call `wakeup()`. The worker process (running, with a
5s `wakeup()` poller) is what dispatches. So reading `next_run_time` back from
`apscheduler_jobs` immediately after `add_job` returns a real value.

**Rationale**: Verified against APScheduler 3.x `BaseScheduler.add_job` / `_real_add_job`
control flow and the project's `app.py` lifespan. The pause only suppresses dispatch, not
persistence or next-run computation.

**Consequence/Edge**: `next_run_time` is legitimately `None` when the trigger has no future
fire time (past `date` trigger, exhausted cron `end_date`). The response models allow null.

## R3 — Atomicity across the ORM row and the APScheduler jobstore (saga)

**Decision**: Use a **compensating transaction**. Create flow:
1. Insert the `schedules` row and **commit** (row now durable).
2. `add_job(...)` (writes to `apscheduler_jobs` via APScheduler's own sync engine).
3. On `add_job` failure → **delete** the row and commit the deletion, then raise
   `JobRegistrationError`.

Update flow:
1. Load the existing owned row (capture prior `display_name`/`trigger`/`kwargs` for restore).
2. Apply changes and **commit**.
3. `add_job(..., replace_existing=True)`.
4. On failure → restore the captured prior values and commit, then raise.

**Rationale**: The two stores commit independently — the async asyncpg session and
APScheduler's internal synchronous psycopg2 engine are different connections/transactions, so
a single DB transaction cannot wrap both. The compensating action keeps `schedules` and the
jobstore from diverging, satisfying spec FR-007/FR-012 and Principle I (data-loss / consistency).

**Alternatives considered**: (a) Single `session.begin()` around both — impossible, `add_job`
commits outside our session. (b) `add_job` first, then insert row — contradicts the spec's
explicit "create record first, then `add_job`, rollback record on failure" ordering. (c)
Two-phase commit — unavailable across these drivers; over-engineered (YAGNI).

## R4 — Avoiding event-loop blocking on the synchronous jobstore write

**Decision**: Call `add_job` via `await asyncio.to_thread(...)`. The synchronous psycopg2
jobstore write then runs off the event loop.

**Rationale**: Principle I forbids blocking the event loop on I/O. `add_job` performs
synchronous DB I/O. Offloading to a thread is safe here: the scheduler is paused (no
`wakeup()` / loop interaction during add), and `add_job` only mutates the jobstore under an
internal threading lock. Returns a `Job`; we ignore it and read `next_run_time` from the table.

**Alternatives considered**: Direct synchronous call (as in the demo) — acceptable for a demo
but violates the no-blocking principle for production endpoints.

## R5 — Reading `next_run_time` from `apscheduler_jobs`

**Decision**: Query the existing read-only Core `Table` `apscheduler_jobs` (declared in
`src/lib/models/apscheduler_job.py` on unmanaged metadata) for the `next_run_time` column,
keyed by job id (= schedule id). The stored column is `Double` (Unix epoch seconds, UTC).
Convert with `datetime.fromtimestamp(value, tz=timezone.utc)`; `None` stays `None`.

For **List**, perform a single `SELECT ... FROM schedules LEFT JOIN apscheduler_jobs ON
schedules.id = apscheduler_jobs.id` (LEFT JOIN so a schedule with no live job still appears
with `next_run_time = NULL`). This lives in `ScheduleRepository`, which already owns the
`schedules` query.

**Rationale**: `next_run_time` is authoritative only in the engine store (R2). Using the Core
table reference keeps us ORM/Core-only (no raw SQL) and matches the intent of the 006
`apscheduler_job` model ("only columns needed for the JOIN"). Co-locating the join in the
schedule repository avoids a second repository touching the same query (Simplicity).

**Alternatives considered**: Separate `ApSchedulerJobRepository` — rejected as redundant since
the list path needs the join inside the schedule query anyway.

## R6 — Trigger and properties validation (Pydantic v2)

**Decision**: Two request-layer Pydantic models in `src/lib/schemas/schedule.py`:

- `ScheduleTrigger`: optional `cron: CronTriggerSpec | None` and `date: datetime | None`, with
  a `model_validator(mode="after")` enforcing **exactly one** present (zero or both → error).
  `CronTriggerSpec` holds `fields: CronFields` (all of `year, month, day, week, day_of_week,
  hour, minute, second` as `str | int | None`, each optional) plus optional `start_date`,
  `end_date`.
- `ScheduleProperties`: `target_type: TargetType`, `method: HttpMethod | None`, `url: str`,
  `payload: dict`, `extra_headers: dict[str, str] = {}`, `api_version: str | None`, with a
  `model_validator(mode="after")` applying target-type rules (see data-model.md for the exact
  matrix). For `chat_completion`: `method` ignored/forced absent, `url` must be a valid
  OpenAI-compatible deployment URL, `payload.stream` read with default `False`, `api_version`
  applicable. For `rest`/`sse`: `method` required, `api_version` not used.

Validation failures surface as HTTP 422 (FastAPI/Pydantic) so **nothing is persisted**
(FR-026), since validation precedes the service call.

**Rationale**: Centralizes validation at the API boundary (properties.md: structure "restricted
by the API … not enforced by the database"). The same `ScheduleProperties` model is the shared
API↔worker contract (Principle IV); the worker reconstructs/validates kwargs from it before
each run (full run-time validation is a future feature; this iteration's job only logs).

**Alternatives considered**: Validating in the service (loses automatic 422 + OpenAPI schema);
DB-level constraints (contradicts properties.md and uses JSONB columns).

## R7 — Building APScheduler triggers from the request

**Decision**: Map `ScheduleTrigger` → `CronTrigger(**fields, start_date=..., end_date=...,
timezone="UTC")` when `cron` is set, or `DateTrigger(run_date=date, timezone="UTC")` when
`date` is set. A small pure helper builds the trigger; `timezone="UTC"` matches the scheduler
configuration in `src/lib/scheduler.py`.

**Rationale**: Direct, explicit mapping; UTC everywhere is consistent with the existing
scheduler/jobstore configuration.

## R8 — Ownership scoping with the mock identity

**Decision**: `MOCK_USER_ID = "mock_user_id"` constant in `src/lib/constants.py`. Create stores
it as `created_by`; List/Details/Update filter `WHERE created_by = MOCK_USER_ID`. A missing or
non-owned id yields `ScheduleNotFoundError` → HTTP 404.

**Rationale**: The spec fixes the owner to a mock value this iteration (real DIAL `/v1/info`
email derivation is deferred). Treating non-owned as 404 (not 403) avoids leaking existence,
matching the contract note "only creator can read/update."

## R9 — Routing, trailing slashes, and DI

**Decision**: Router `prefix="/schedules"`. Collection routes use path `"/"` → `/schedules/`
(matching the contract's `POST /schedules/` and `GET /schedules/`); item routes use
`"/{schedule_id}"` → `/schedules/{schedule_id}`. The app sets `redirect_slashes=False`, so
paths are declared to match the contract exactly. Mount under the `/v1` API surface consistent
with the project's `api/v1` layout. DI providers in `src/api/dependencies/scheduler.py`:
`get_scheduler(request)` returns `request.app.state.scheduler`; `get_schedule_service` builds
`ScheduleService(repository=ScheduleRepository(session), scheduler=...)` from `get_db_session`
+ `get_scheduler`. Endpoints contain no logic (FR-029).

**Rationale**: `redirect_slashes=False` means exact-match paths; declaring the trailing slash
on collection routes reproduces the contract URLs. App state holds the scheduler (no globals,
Principle: reusable components in app state).

## R10 — Migrations

**Decision**: **None.** `schedules` and `apscheduler_jobs` already exist (feature 006). The new
enums (`TargetType`, `HttpMethod`, `TriggerType`) are API-level Python `StrEnum`s used in
Pydantic validation and JSONB serialization — they are **not** PostgreSQL enum types and need
no DDL. `trigger` and `kwargs` remain JSONB.

**Rationale**: This feature adds behavior over existing tables only.

## Summary of decisions

| # | Decision |
|---|----------|
| R1 | Job in `worker/jobs.py`; registered by string ref constant (no `lib`→`worker` import) |
| R2 | Paused scheduler still persists + computes `next_run_time`; null when no future fire |
| R3 | Compensating transaction (saga) for ORM row ↔ jobstore consistency |
| R4 | `add_job` offloaded via `asyncio.to_thread` to avoid blocking the loop |
| R5 | Read `next_run_time` from `apscheduler_jobs` (epoch→UTC); LEFT JOIN for list |
| R6 | Pydantic trigger (cron xor date) + properties (per-target-type) validation at API layer |
| R7 | Map request → `CronTrigger`/`DateTrigger` (UTC) |
| R8 | `MOCK_USER_ID` scoping; non-owned/missing → 404 |
| R9 | Router prefix `/schedules`, trailing-slash collection paths, DI providers |
| R10 | No migrations; new enums are API-level StrEnums over JSONB |
