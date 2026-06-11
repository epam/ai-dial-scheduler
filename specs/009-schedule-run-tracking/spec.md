# Feature Specification: Schedule Run Tracking

**Feature Branch**: `009-schedule-run-tracking`

**Created**: 2026-06-11

**Status**: Draft

**Input**: User description: "For each fired apscheduler job (except no-op), create/update schedule_run records according to current state. Full run lifecycle is covered, for each run exists record with status and current state. If error happens its reason is stored, if run successful its results stored. Remember to log all actions."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Record that a run started (Priority: P1)

When a scheduled task fires and begins executing, an operator (or the surrounding
service surfacing run history) must be able to see that a run is currently in progress,
which schedule it belongs to, and the exact definition the schedule had at the moment it
fired.

**Why this priority**: Without a persisted "in progress" record at the start, there is no
durable evidence a run ever began. If the process crashes mid-run, the run would otherwise
vanish without a trace. This is the foundation the success/failure updates attach to, so it
must exist first.

**Independent Test**: Fire a real scheduled task and, before it completes, confirm a run
record exists with status "in progress", no end time, no result, the correct schedule
identifier, and a snapshot of the schedule's parameters as they were when it fired.

**Acceptance Scenarios**:

1. **Given** a scheduled task is due, **When** it begins executing, **Then** a new run
   record is created with status "in progress", an empty end time, an empty result, the
   originating schedule identifier, and a point-in-time snapshot of the schedule's
   parameters.
2. **Given** a run record was just created, **When** an operator inspects run history,
   **Then** the in-progress run is visible with its current state.
3. **Given** the worker's own internal maintenance/heartbeat (no-op) job fires, **When** it
   runs, **Then** no run record is created for it.

---

### User Story 2 - Record a successful run with its results (Priority: P1)

When a scheduled task finishes successfully, the previously created run record must be
updated to reflect success, capture when it finished, and store the result the task
produced, so the outcome is durably available for later inspection.

**Why this priority**: A run that started but never gets a terminal outcome is
indistinguishable from a crashed run. Capturing success and the result is the core value
of the feature — it answers "did it work, and what did it produce?".

**Independent Test**: Fire a task that completes successfully and confirm its run record
transitions to status "success", gains an end time, and stores the result the task returned.

**Acceptance Scenarios**:

1. **Given** an in-progress run record exists for a task, **When** the task completes
   successfully, **Then** that same record is updated to status "success", its end time is
   set to the completion time, and its result field holds the task's produced result.
2. **Given** a successful task returns its standardized outcome, **When** the success is
   recorded, **Then** the correct run record is located using the run identifier carried in
   that outcome (not by guessing).

---

### User Story 3 - Record a failed run with its reason (Priority: P1)

When a scheduled task fails, the previously created run record must be updated to reflect
the failure, capture when it ended, and store the reason for the failure, so operators can
diagnose what went wrong.

**Why this priority**: Failures are the runs operators most need to investigate. Storing the
failure reason turns an opaque "it broke" into an actionable record. Equal in importance to
recording success — together they complete the lifecycle.

**Independent Test**: Fire a task that raises an error and confirm its run record transitions
to status "error", gains an end time, and stores the failure reason text.

**Acceptance Scenarios**:

1. **Given** an in-progress run record exists for a task, **When** the task fails, **Then**
   that same record is updated to status "error", its end time is set to the failure time,
   and its result field stores the failure reason.
2. **Given** a failed task surfaces its standardized failure with a run identifier, **When**
   the failure is recorded, **Then** the correct run record is located using that run
   identifier.
3. **Given** a task's parameters are invalid and it cannot start its real work, **When** it
   fails validation, **Then** the failure is still recorded against its run record with the
   reason.

---

### Edge Cases

- **No-op / heartbeat jobs**: The worker's own internal maintenance job fires on an interval
  but represents no user-facing run; it MUST NOT produce run records.
- **Crash between start and finish**: If the process dies after the in-progress record is
  created but before a terminal outcome is recorded, the record remains in "in progress".
  This is acceptable and intentionally visible — it signals an interrupted run rather than
  hiding it. (Reaping/timing-out stale in-progress runs is out of scope for this feature.)
- **Terminal update cannot find a matching record**: If a success/failure outcome arrives
  but no in-progress record matches its run identifier (e.g. the start record was never
  created), the system MUST log this discrepancy rather than fail silently.
- **Run-record persistence fails at start**: If the in-progress record cannot be persisted
  when the task begins, the failure MUST be logged; how the run itself proceeds is governed
  by Assumptions below.
- **Missed / misfired jobs**: A job whose execution window lapses before it runs never
  actually executes, so no run record is created for it. Recording missed runs is out of
  scope for this feature.
- **Large or streamed results**: A successful result may be an accumulated list of values
  (e.g. from streamed responses) rather than a single value; the full accumulated result is
  what gets stored.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST create a run record at the moment a scheduled task begins
  executing, capturing: status "in progress", no end time, no result, the originating
  schedule identifier, and a snapshot of the schedule's parameters as they were when it fired.
- **FR-002**: The system MUST NOT create run records for the worker's own internal no-op /
  heartbeat job.
- **FR-003**: A successfully completed task MUST surface a standardized outcome carrying its
  run identifier, its schedule identifier, and its produced result.
- **FR-004**: A failed task MUST surface a standardized failure carrying its run identifier
  and its schedule identifier.
- **FR-005**: When a task completes successfully, the system MUST update the matching
  in-progress run record to status "success", set its end time to the completion time, and
  store the produced result.
- **FR-006**: When a task fails, the system MUST update the matching in-progress run record
  to status "error", set its end time to the failure time, and store the failure reason.
- **FR-007**: Terminal updates (success or failure) MUST locate the run record to update by
  the run identifier carried in the task's standardized outcome/failure, not by inference.
- **FR-008**: The system MUST record failures uniformly regardless of cause, including
  invalid-parameter (validation) failures that prevent a task from starting its real work.
- **FR-009**: The system MUST log every lifecycle action it takes — run-record creation,
  successful-outcome recording, failure recording, no-op skips, and any discrepancy such as a
  terminal outcome with no matching in-progress record.
- **FR-010**: The system MUST handle a terminal outcome that has no matching in-progress
  record by logging the discrepancy rather than crashing the worker or silently discarding it.
- **FR-011**: Each run record MUST be uniquely identifiable, and the in-progress record and
  its terminal update MUST refer to the same record for a given execution.
- **FR-012**: Stored failure reasons and results MUST NOT include secrets or credentials.

### Key Entities *(include if feature involves data)*

- **Schedule Run**: One recorded execution attempt of a schedule. Carries a unique run
  identifier, a status (in progress → success or error), the time it ended (empty while in
  progress), the result it produced (empty until it finishes successfully) or the failure
  reason, the originating schedule identifier, and a point-in-time snapshot of the schedule's
  parameters at fire time.
- **Run Outcome (success)**: The standardized object a successful task surfaces — run
  identifier, schedule identifier, and produced result — used to update the run record.
- **Run Failure (error)**: The standardized failure a failed task surfaces — run identifier
  and schedule identifier — used to locate the run record and record the failure reason.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of fired non-heartbeat scheduled tasks produce a run record with a status
  reflecting their current or final state.
- **SC-002**: Every run record reaches exactly one terminal status (success or error) when
  its task finishes, or remains "in progress" only while genuinely executing or after an
  abrupt process termination.
- **SC-003**: 100% of successful runs have their produced result stored and retrievable.
- **SC-004**: 100% of failed runs have a non-empty failure reason stored and retrievable.
- **SC-005**: 0 run records are created for the worker's internal no-op / heartbeat job.
- **SC-006**: Every lifecycle action (create, success update, failure update, no-op skip,
  discrepancy) emits a corresponding log entry, verifiable in logs.
- **SC-007**: Given a run identifier, an operator can locate the single corresponding run
  record and read its status, timing, and result/reason.

## Assumptions

- **Run-record store already exists**: The persistent store for run records (the
  `schedule_runs` structure and its status values, including "in progress", "success", and
  "error") was delivered by an earlier feature and is reused as-is; no new storage structure
  is introduced here.
- **Worker executes the jobs**: The executor worker that fires and runs scheduled tasks
  already exists; this feature adds run-lifecycle persistence around that existing execution,
  which previously only logged outcomes without persisting them.
- **"Schedule snapshot" content**: The snapshot stored at fire time is the schedule's
  execution parameters (the keyword arguments the task was invoked with), capturing the
  schedule definition as it was when the run started.
- **Result shape**: A successful result is stored as produced — a single response payload, or
  an accumulated list of values for streamed responses — without reshaping.
- **Best-effort start persistence**: If the in-progress record cannot be created when a task
  begins, the inability is logged; the task's own execution is not the subject of this
  feature and proceeds per the existing worker behavior. The absence of a start record is
  later surfaced as the "no matching record" discrepancy if a terminal outcome arrives.
- **Missed/misfired runs out of scope**: Only tasks that actually execute produce run
  records; jobs skipped due to a lapsed execution window are not recorded by this feature.
- **No new external interface**: This feature persists run state internally; it does not add
  or change any externally exposed API in this increment.
