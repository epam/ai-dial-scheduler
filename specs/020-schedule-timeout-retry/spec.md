# Feature Specification: Schedule Timeout & Retry

**Feature Branch**: `020-schedule-timeout-retry`

**Created**: 2026-06-17

**Status**: Draft

**Input**: User description: "For each schedule add two additional nullable properties: `timeout` and `retry`. If values are not provided use `default` application timeout and *no retry*. If `timeout` or `retry` provided use that schedule specific timeout and retry for each job. Timeout should be integer value in seconds. Retry: `{ \"backoff_interval\": [2,5,8], \"retryable_errors\": [500, 502] }` — backoff_interval is a list of intervals in seconds waited before the next try (it defines both the intervals and the number of attempts); retryable_errors are error codes that should be retried. Logging: log records should include information about retry/timeout. Each retry should state in logs that it's a retry; the last retry, if failed, should say it was the last retry."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Configure a per-schedule timeout (Priority: P1)

A person creating or editing a schedule can specify how long a single execution of that schedule's target is allowed to run before it is abandoned. If they do not specify a timeout, the schedule uses the system's default timeout. This lets owners of slow targets extend the limit and owners of latency-sensitive targets shorten it, without changing a global setting that affects everyone.

**Why this priority**: Timeout control is the foundational, independently valuable capability. It can ship on its own and immediately prevents a single slow or hung target from holding resources longer than intended.

**Independent Test**: Create a schedule with `timeout` set to a small value, point it at a target that responds slower than that value, and confirm the execution is abandoned at the configured limit and recorded as failed. Create another schedule without a `timeout` and confirm it uses the default timeout.

**Acceptance Scenarios**:

1. **Given** a schedule created with `timeout` = 10, **When** its target takes longer than 10 seconds to respond, **Then** the execution is stopped at the 10-second limit and the run is recorded as failed with a timeout reason.
2. **Given** a schedule created without a `timeout`, **When** it executes, **Then** the system applies the default application timeout.
3. **Given** an existing schedule created before this feature, **When** it executes, **Then** it behaves exactly as before, using the default application timeout.
4. **Given** a request to create a schedule with `timeout` set to a non-positive integer, **When** the request is submitted, **Then** it is rejected with a validation error.

---

### User Story 2 - Configure automatic retry with backoff (Priority: P1)

A person creating or editing a schedule can specify that failed executions should be automatically retried, defining both the wait time before each retry and which error codes are worth retrying. The list of backoff intervals determines both how long to wait between attempts and how many retries to make. If they do not specify a retry configuration, the schedule does not retry — it makes a single attempt, as today.

**Why this priority**: Automatic retry of transient failures is the other half of the resilience story and the primary motivation for the feature. It is independently valuable: even without custom timeouts, retrying transient errors meaningfully improves successful completion rates.

**Independent Test**: Create a schedule with `retry` = `{ "backoff_interval": [2, 5], "retryable_errors": [500] }`, point it at a target that fails with code 500 twice then succeeds, and confirm the system waits 2 seconds, retries, waits 5 seconds, retries, and ultimately records success. Create another schedule without `retry` and confirm a failing target is attempted only once.

**Acceptance Scenarios**:

1. **Given** a schedule with `retry.backoff_interval` = `[2, 5, 8]` and `retry.retryable_errors` = `[500, 502]`, **When** the target fails every time with code 500, **Then** the system makes the initial attempt plus 3 retries (waiting 2, 5, and 8 seconds respectively) and then records the run as finally failed.
2. **Given** the same schedule, **When** the target fails with code 500 on the first attempt and then succeeds on the first retry, **Then** the system stops retrying and records the run as successful.
3. **Given** a schedule with `retry.retryable_errors` = `[500]`, **When** the target fails with a code that is not in the list (e.g., 403), **Then** the system does not retry and records the run as failed immediately.
4. **Given** a schedule created without a `retry` configuration, **When** its target fails, **Then** the system makes a single attempt and records the failure with no retries.
5. **Given** a request to create a schedule whose `retry.backoff_interval` is empty or contains non-positive values, **When** the request is submitted, **Then** it is rejected with a validation error.

---

### User Story 3 - Observe timeout and retry behavior in logs (Priority: P2)

An operator investigating a schedule's behavior can read the logs and clearly see the timeout that applied, whether retries happened, which attempt is a retry, and which attempt was the final one. This makes diagnosing transient-failure patterns and timeout tuning practical.

**Why this priority**: Observability depends on stories 1 and 2 existing but is essential for operating the feature safely. It is separately testable by inspecting emitted log records for a known run.

**Independent Test**: Run a schedule configured with a timeout and a retry policy against a consistently failing target, then inspect the log records and confirm each attempt is identified, retries are labeled as retries, the configured timeout and backoff are present, and the final failed attempt is labeled as the last retry.

**Acceptance Scenarios**:

1. **Given** a schedule executes with a configured timeout, **When** log records are produced for the run, **Then** the records include the timeout value that was applied.
2. **Given** a schedule retries after a failure, **When** the retry attempt is logged, **Then** the log record clearly states that the attempt is a retry and includes which attempt number it is.
3. **Given** a schedule exhausts all configured retries and still fails, **When** the final attempt is logged, **Then** the log record states that this was the last retry and that it failed.
4. **Given** every execution (with or without retry/timeout configured), **When** logs are produced, **Then** they identify the schedule and indicate whether a retry policy and custom timeout were in effect.

---

### Edge Cases

- **Empty backoff list**: A `retry` object with an empty `backoff_interval` is rejected at validation time (a retry policy must define at least one retry).
- **Empty retryable_errors**: A `retry` object with an empty `retryable_errors` list means no error code qualifies for retry, so executions effectively behave as a single attempt; this is allowed but documented as having no retry effect.
- **Non-positive timeout or backoff values**: Rejected at validation time.
- **Timeout during an attempt**: A timed-out attempt is treated as a terminal failure for that run by default and is not retried, because a timeout produces no error code to match against `retryable_errors` (see Assumptions). Only failures whose error code matches `retryable_errors` are retried.
- **Error code not in retryable_errors**: The run fails immediately with no retry.
- **Success on a retry**: Retrying stops as soon as an attempt succeeds; remaining backoff intervals are not used.
- **Both timeout and retry set**: The timeout applies to each individual attempt; retries are governed independently by the backoff list.
- **Backward compatibility**: Schedules and stored data created before this feature have neither field and must continue to execute using the default timeout and no retry.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST allow an optional `timeout` to be specified per schedule when creating or updating a schedule. The value MUST be an integer number of seconds.
- **FR-002**: System MUST allow an optional `retry` configuration to be specified per schedule when creating or updating a schedule, consisting of `backoff_interval` (an ordered list of integer seconds to wait before each successive retry) and `retryable_errors` (a list of error codes that qualify a failed attempt for retry).
- **FR-003**: When `timeout` is not provided for a schedule, the system MUST apply the default application timeout to each execution.
- **FR-004**: When `retry` is not provided for a schedule, the system MUST NOT retry — each execution makes a single attempt.
- **FR-005**: System MUST apply the schedule's `timeout` to each individual execution attempt of that schedule.
- **FR-006**: On a retryable failure, the system MUST wait the next interval from `backoff_interval` before the next attempt, performing exactly as many retries as there are entries in `backoff_interval`, in order.
- **FR-007**: System MUST retry only when the failed attempt's error code is present in `retryable_errors`; any other failure MUST cause the run to fail immediately without retry.
- **FR-008**: After all configured retries are exhausted without success, the system MUST record the run as finally failed.
- **FR-009**: System MUST emit a structured log record for every attempt (the initial attempt and each retry), including its outcome, so the full attempt history of a run is recoverable from logs. Per-attempt detail is recorded in logs only and MUST NOT be written to the database; the run row persists the final outcome (success result or error) as today.
- **FR-010**: System MUST emit structured log records for each execution that include the applied timeout and, when configured, the retry policy in effect.
- **FR-011**: System MUST log each retry attempt in a way that clearly identifies it as a retry, including the attempt number.
- **FR-012**: System MUST log the final attempt, when all retries are exhausted and it failed, in a way that clearly states it was the last retry and that it failed.
- **FR-013**: System MUST validate `timeout` (positive integer), `backoff_interval` (non-empty list of positive integers), and `retryable_errors` (list of valid error codes) on create and update, rejecting invalid configurations with a clear validation error.
- **FR-014**: System MUST return the `timeout` and `retry` values (or their absence) when a schedule is retrieved, so callers can see the effective configuration.
- **FR-015**: System MUST continue to execute schedules created before this feature (which have neither field) using the default timeout and no retry, with no manual migration required by users.

### Key Entities *(include if feature involves data)*

- **Schedule**: An existing definition of what to run and when. Extended with two optional attributes: `timeout` (integer seconds; absent means use default) and `retry` (a retry policy; absent means no retry).
- **Retry Policy**: A configuration belonging to a schedule, composed of `backoff_interval` (ordered list of wait times in seconds that also determines the number of retries) and `retryable_errors` (the set of error codes that make a failed attempt eligible for retry).
- **Schedule Run**: An existing record of an execution and its final outcome (success result or error), persisted in the database as today. The per-attempt breakdown (initial attempt and every retry) is emitted to logs only and is not stored on the run record.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a schedule with a configured timeout, 100% of executions whose target exceeds that limit are stopped at the configured limit and recorded as failed.
- **SC-002**: For a schedule with a retry policy, a target that fails with a retryable error code is retried exactly the number of times defined by `backoff_interval`, honoring each configured wait, in 100% of runs.
- **SC-003**: A target that fails with a non-retryable error code is attempted exactly once (no retries) in 100% of runs.
- **SC-004**: 100% of schedules created before this feature continue to run after it ships, using the default timeout and making a single attempt, with no user action.
- **SC-005**: For every run, an operator can determine from the logs alone the timeout that applied, whether and how many retries occurred, and which attempt was the final one.
- **SC-006**: 100% of attempts (initial and retries) appear in the logs for a given run, each identifiable by attempt number and outcome.

## Assumptions

- **"Error code" means the response/status code returned by the schedule's target** (e.g., HTTP status codes such as 500 and 502 in the example). `retryable_errors` is matched against these codes.
- **Timeouts and transport-level failures are not retried by default**, because they do not produce an error code that can be matched against `retryable_errors`. They are recorded as terminal failures for the run. (If retry-on-timeout is desired, it can be addressed as a follow-up clarification.)
- **The default application timeout** referenced when `timeout` is absent is the system's existing default execution timeout already configured at the application level.
- **The timeout applies per attempt**, not cumulatively across all retries, so each retry gets a fresh timeout budget.
- **`backoff_interval` entries are absolute waits in seconds before each successive attempt**, applied in the listed order (first interval before the first retry, and so on); the feature does not compute multipliers or jitter.
- **Validation is applied at create and update time**; configurations are stored as provided and used as-is at execution time.
- **Both fields are independently optional**: a schedule may set only `timeout`, only `retry`, both, or neither.
