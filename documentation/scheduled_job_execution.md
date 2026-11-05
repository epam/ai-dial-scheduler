# Common rule
`run_id`,`schedule_id` and `result`/`exception` are used in schedule_run lifecycle management.

# Who executes a run
Two callers produce runs, and both go through the same execution core
(`src.worker.jobs.execute_run`), so their run records are indistinguishable:

- **The worker**, when a trigger fires. Uses the worker's shared HTTP client and
  `WORKER_HTTP_*` timeouts.
- **The API**, for `POST /schedules/{schedule_id}/run` (see `api_contract.md`). The run executes
  in the background inside the API process — never handed to the worker — using the API's
  app-state HTTP client and `API_HTTP_*` timeouts. The scheduler is not touched, so the
  schedule's `next_run_time` does not move.

Which client and default timeout a run uses is carried on its `RunContext`; everything below
applies identically to both.

# Credential resolution (runs before dispatch)
Before the target action is dispatched, the worker resolves per-user on-behalf-of (OBO)
credentials for the schedule's owner (`created_by`) and `service_id` via DIAL. If this fails
(owner not signed in to the external service, DIAL rejects the lookup, or the schedule/owner
cannot be resolved), the run is recorded as failed with `result.stage == "credentials"` and the
target action is never dispatched — this failure is terminal and does not consume the schedule's
retry policy. Only once credentials are resolved does execution proceed to dispatch (below).

# Successful jobs
If jub successful it should return standardized object with next fields:

- `run_id`: uuid4
- `schedule_id`: str
- `result`: json # accumulated list of values in case of streamed chat completion or sse, or direct response json

# Failed jobs
Scheduled job execution wrapped in `try/except` block and reraises standardized exception
that includes additional fields:

- `run_id`: uuid4
- `schedule_id`: str

A failed job's persisted `result` carries `stage == "credentials"` when the failure happened
during credential resolution (see above); it has no `stage` key when the failure happened during
dispatch of the target action itself (the pre-existing behavior, subject to the schedule's retry
policy).
