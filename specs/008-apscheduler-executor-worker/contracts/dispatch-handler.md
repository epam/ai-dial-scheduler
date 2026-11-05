# Contract: Dispatch Handler (worker ↔ creator)

This is the internal contract between the creator service (which registers jobs) and this
worker (which executes them). Per Constitution IV, it is expressed as a typed callable + Pydantic
models — no ad-hoc dict passing across the service boundary.

## Import path (stable reference)

`src.worker.jobs:execute_scheduled_job`

Exposed via `src/lib/constants.py::EXECUTE_SCHEDULED_JOB_REF`. The creator's
`scheduler.add_job(EXECUTE_SCHEDULED_JOB_REF, …)` references this exact path. **Changing this
path is a breaking change** for the creator and requires coordinated migration of existing jobs.

## Signature

```text
async def execute_scheduled_job(schedule_id: str, **kwargs: Any) -> None
```

- `schedule_id` (str, required): used for correlation/logging and to populate `ScheduleRunError`.
- `**kwargs`: the task config the creator passes via `add_job(..., kwargs={**properties,
  "schedule_id": ...})`. These kwargs **are** the authoritative `ScheduleProperties` and are
  validated directly. The worker performs no DB read and never deserializes
  `apscheduler_jobs.job_state`.

## Behaviour contract

On invocation the handler MUST:

1. Generate `run_id = uuid4()` and bind `{run_id, schedule_id}` into logging context.
2. Validate the received `kwargs` as `ScheduleProperties`. **Invalid ⇒
   `ScheduleRunError(schedule_id, run_id)`.**
3. Resolve `target_type → Executor` and call `executor.execute(run_id, properties)`.
4. Log results as they arrive (per-chunk/per-event for streaming; one record otherwise).
5. On any failure (validation, transport, non-2xx) raise `ScheduleRunError` carrying
   `schedule_id` and `run_id`.

The handler MUST NOT persist any result. The handler MUST NOT read the database. The handler MUST
NOT deserialize `apscheduler_jobs.job_state`.

## Failure surfacing

`ScheduleRunError` raised by the handler propagates to APScheduler, which emits an
`EVENT_JOB_ERROR` event. The worker's listener logs `event.exception` with `schedule_id` and
`run_id`. A failure in one job MUST NOT prevent other jobs from executing.

## `ScheduleRunError` contract

```text
class ScheduleRunError(SchedulerError):
    schedule_id: str
    run_id: str
```

Both attributes MUST be populated for every raised instance (SC-002).
