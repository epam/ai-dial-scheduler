# Feature Specification: APScheduler Executor Worker

**Feature Branch**: `008-apscheduler-executor-worker`

**Created**: 2026-06-11

**Status**: Draft

**Input**: User description: "APScheduler Executor Worker — A standalone worker process that executes scheduled tasks fired by APScheduler. It does not create, schedule, or persist results of jobs. It reads the task config, runs it, and logs. No result persistence."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Execute a fired scheduled task (Priority: P1)

When APScheduler fires a job in the shared jobstore, the worker receives the job's
configuration, determines what kind of target the task addresses (a chat completion, a
plain REST call, or a streaming SSE call), performs the call against the configured target,
and records the outcome through structured logs. The task creator service owns scheduling;
this worker is solely responsible for carrying out the work when a job becomes due.

**Why this priority**: This is the entire reason the worker exists. Without reliable
execution and branching by target type, no scheduled task produces any effect. It is the
minimum viable product on its own.

**Independent Test**: Add a job to the shared jobstore (as the creator service would) that
references the worker's dispatch handler with a valid configuration for each target type,
let it fire, and confirm the corresponding call is made and its result is logged with the
run and schedule identifiers.

**Acceptance Scenarios**:

1. **Given** a fired job whose configuration declares a `chat_completion` target with a
   non-streaming payload, **When** the dispatch handler runs, **Then** the worker calls the
   chat target once and logs a single result record carrying the run and schedule
   identifiers.
2. **Given** a fired job whose configuration declares a `chat_completion` target with a
   streaming payload, **When** the dispatch handler runs, **Then** the worker logs one
   record per streamed chunk as each chunk arrives.
3. **Given** a fired job whose configuration declares a `rest` target with a valid method
   and a success response, **When** the dispatch handler runs, **Then** the worker performs
   the request and logs the response as a single record.
4. **Given** a fired job whose configuration declares an `sse` target, **When** the dispatch
   handler runs, **Then** the worker performs the request with a stream-accepting
   preference and logs one record per received event until the stream closes.
5. **Given** any fired job, **When** the dispatch handler begins, **Then** a unique run
   identifier is generated and attached to every log record for that run.

---

### User Story 2 - Fail a run safely with full diagnostic context (Priority: P1)

When anything goes wrong during a run — the received configuration is invalid, the network
call fails, or the target returns a non-success response — the worker must not crash silently
or corrupt other runs. It records a single, well-formed failure that always identifies which
schedule and which run failed, so the failure is fully traceable.

**Why this priority**: Scheduled tasks address external systems that fail unpredictably.
Without a guaranteed, identifiable failure path, operators cannot diagnose why a scheduled
task did not take effect, and the constitution's observability requirement is violated.

**Independent Test**: Fire jobs with each failure trigger (malformed configuration kwargs,
unreachable target, non-success response) and confirm each produces exactly one logged failure
that contains both the schedule identifier and the run identifier.

**Acceptance Scenarios**:

1. **Given** a fired job whose configuration does not match the required structure, **When**
   the dispatch handler validates it, **Then** the run fails and a failure is logged with
   the schedule and run identifiers.
2. **Given** a `rest` or `sse` target that returns a non-success response, **When** the
   executor processes it, **Then** the run fails and the failure is logged with the schedule
   and run identifiers.
3. **Given** any transport-level error (connection refused, timeout), **When** it occurs
   during execution, **Then** the run fails and the failure is logged with the schedule and
   run identifiers.
4. **Given** a failed run, **When** the failure is surfaced, **Then** it is captured by the
   worker's job-error handling path and does not interrupt unrelated jobs.

---

### User Story 3 - Confirm the worker is alive (Priority: P2)

The worker periodically performs a no-op heartbeat that proves the process is running and
its scheduling loop is healthy, independent of whether any real tasks are currently
scheduled. Operators can observe these heartbeats to distinguish "the worker is idle" from
"the worker is down."

**Why this priority**: Liveness signalling is essential for operating the worker but is not
required to execute tasks. It is a strong P2 that makes the P1 capabilities operable.

**Independent Test**: Start the worker with no scheduled tasks and confirm a heartbeat is
logged on the configured interval, recording the execution timestamp and the heartbeat job
name and nothing else.

**Acceptance Scenarios**:

1. **Given** a running worker, **When** the heartbeat interval elapses, **Then** a heartbeat
   record is logged containing the execution timestamp and the heartbeat job name.
2. **Given** a running worker, **When** the heartbeat fires, **Then** it performs no task
   work and produces no other side effects.
3. **Given** a running worker, **When** it starts up, **Then** the heartbeat is the only job
   the worker itself registers; all task jobs originate from the creator service.

---

### Edge Cases

- **Method omitted for a target that requires it**: A `rest` or `sse` configuration with no
  method is invalid and fails the run. A method present on a `chat_completion` configuration
  is ignored, not an error.
- **Header collision**: When the configuration's extra headers contain a header the worker
  would otherwise inject (e.g. the API key header), the configuration's value takes
  precedence.
- **API version absent**: `api_version` is used only for `chat_completion`. For other target
  types it is irrelevant; for `chat_completion` an absent value follows the chat client's
  default behaviour.
- **Streaming flag inside the payload**: For `chat_completion`, whether the response is a
  single result or a stream is determined by the payload itself (the stream indicator
  defaults to non-streaming when absent).
- **Empty or immediately closed stream**: A streaming response that yields zero chunks/events
  before closing completes the run successfully with no per-chunk records.
- **Concurrent firings**: Multiple jobs firing close together each run with their own run
  identifier and must not share or overwrite one another's logging context.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The worker MUST run as a standalone long-lived process that observes a job
  store shared with the separate creator service and executes jobs as they fire, without
  ever creating, scheduling, modifying, or deleting task jobs itself.
- **FR-002**: The worker MUST register exactly one job of its own: a periodic heartbeat that
  fires on a configurable interval and logs only the execution timestamp and the heartbeat
  job name, performing no other work.
- **FR-003**: All task jobs MUST be executed through a single shared dispatch entry point
  that the creator service references; the worker MUST NOT enumerate, poll, or pre-register
  task jobs.
- **FR-004**: On each task firing, the worker MUST generate a unique run identifier and
  include it, together with the schedule identifier, on every log record produced for that
  run.
- **FR-005**: On each task firing, the worker MUST treat the task configuration it receives
  as the authoritative source of run configuration and MUST NOT rely on the scheduler's
  internal serialized job state for configuration.
- **FR-006**: The worker MUST validate the received task configuration against the defined
  structure before executing, and MUST fail the run if validation does not pass.
- **FR-007**: The worker MUST take the run configuration from the kwargs delivered with the
  fired job and MUST NOT perform any database read to obtain or confirm it.
- **FR-008**: The worker MUST branch execution on the configured target type into one of
  three behaviours — chat completion, REST, or streaming SSE — that share a common execution
  interface.
- **FR-009**: For a chat-completion target, the worker MUST derive the chat endpoint and
  deployment/model identity from the configured URL, authenticate using the configured
  platform API key, apply the configured API version, send the configured payload together
  with the configured extra headers, and ignore the configured method.
- **FR-010**: For a chat-completion target, the worker MUST log a single result record when
  the response is a single result, and MUST log one record per chunk when the response is a
  stream.
- **FR-011**: For a REST target, the worker MUST require a non-null method, perform the
  request to the configured URL using the configured method and payload, and send the
  configured extra headers merged with an injected platform API-key header where the
  configured extra headers win on collision.
- **FR-012**: For a REST target, the worker MUST treat a non-success response as a run
  failure and MUST log a successful, non-streamed response as a single record.
- **FR-013**: For an SSE target, the worker MUST behave like the REST target for method,
  URL, payload, and header handling, but additionally signal a stream-accepting preference,
  and MUST log each received event as a named-event-to-raw-content record until the stream
  ends.
- **FR-014**: Every failure — validation failure, transport error, or non-success response —
  MUST raise a single standardized failure that carries both the schedule identifier and the
  run identifier.
- **FR-015**: The worker MUST surface standardized failures through a dedicated job-error
  handling path that logs them, and a failure in one run MUST NOT prevent other runs from
  executing.
- **FR-016**: The worker MUST NOT persist any task results or failures; recording is limited
  to structured logging. (See Assumptions regarding run-lifecycle persistence ownership.)
- **FR-017**: All connection, credential, interval, and timeout configuration MUST be read
  from application settings rather than hard-coded.
- **FR-018**: The worker MUST apply a configured request timeout to outbound REST and SSE
  calls.
- **FR-019**: All worker activity — heartbeats, run starts, per-chunk/per-event results,
  single results, and failures — MUST be emitted as structured log entries through the
  shared logging facility; plain printing is forbidden.

### Key Entities *(include if feature involves data)*

- **Task job (shared jobstore record)**: The authoritative record of *when* and *what* fires,
  owned and written by the creator service. Each task job references the worker's shared
  dispatch entry point and carries the schedule identifier. The worker reads these as they
  fire and never deserializes the scheduler's internal job state for configuration.
- **Schedule (queryable mirror)**: A denormalized, queryable mirror of each job's
  configuration, joined to a task job by schedule identifier, written by the creator service.
  Read-only from the worker's perspective.
- **Run configuration (received task configuration)**: The authoritative per-run
  configuration the dispatch handler receives, comprising the schedule identifier, target
  type (chat completion / REST / SSE), method (required for REST and SSE, ignored for chat
  completion), target URL, payload, a flat map of extra headers that win on collision, and an
  optional API version used only for chat completion.
- **Run**: A single execution occurrence identified by a unique run identifier paired with
  the schedule identifier; it produces either logged results or a single standardized,
  fully-identified failure.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of fired task jobs result in either logged result records or a single
  logged, identified failure — no fired job ends without an observable outcome.
- **SC-002**: 100% of logged failures contain both the schedule identifier and the run
  identifier.
- **SC-003**: Each of the three target types is dispatched to its correct behaviour in 100%
  of fired jobs, verified across the full validation suite.
- **SC-004**: For streaming responses, the count of logged chunk/event records equals the
  number of chunks/events received, and each is logged as it arrives rather than only after
  the stream completes.
- **SC-005**: The heartbeat is logged on every interval with no missed intervals while the
  worker is running and idle, demonstrating uninterrupted liveness signalling.
- **SC-006**: A failure in one run never prevents a concurrently or subsequently fired run
  from executing, verified under concurrent firing conditions.
- **SC-007**: No task result or failure is written to any persistent store by the worker;
  every outcome is observable only through logs.

## Assumptions

- The creator service is responsible for creating, scheduling, and removing all task jobs,
  for writing the queryable schedule mirror, and for wiring each task job to the worker's
  shared dispatch entry point. This worker depends on that contract and does not enforce it.
- The structure of the received task configuration is enforced by the creator's API, not by
  the database; therefore the worker re-validates the configuration on every run.
- Run-lifecycle persistence (the recording of run outcomes in a runs store) is owned outside
  this worker. The worker produces a standardized success result object and a standardized
  failure carrying the run and schedule identifiers; persisting those belongs to the
  surrounding lifecycle management, not to this worker. This reconciles the "no result
  persistence" scope of this feature with the project's broader observability requirement
  that all task runs be persisted — persistence is simply another service's responsibility.
- For a chat-completion target, the target URL is a valid platform-compatible deployment URL
  from which the endpoint and deployment/model can be derived; the endpoint is taken from the
  URL's scheme and domain only and the path is otherwise not used for routing.
- The platform API key used for authentication and header injection is supplied through
  settings and is never logged.
- "Success" for REST/SSE responses means a 2xx status; any other status fails the run.
- The worker and the creator service point at the same database and job store; schema
  ownership and migrations for those shared structures belong to the creator service.
