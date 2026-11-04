# Feature Specification: Split Run Execution Timeouts

**Branch**: `039-split-run-timeouts` | **Date**: 2026-10-09 | **Status**: Draft

**Origin**: Production incident on a weekly report schedule. Every run fails with `execution timed out after 60.0s` while the response stream is healthy and actively delivering events.

## Problem

A scheduled run is bounded by a single number, `timeout`, applied at two layers that measure different things:

- `asyncio.wait_for` around the whole attempt — an absolute wall-clock cap that never resets.
- The underlying HTTP/SDK read timeout — which resets on every byte received.

For a streaming target these are not interchangeable. The read timeout can never fire while data flows, so the absolute cap is the only control that ever triggers — and it triggers on healthy work. Trace `573e59c09029866fcd77627aa08836e9` shows the agent beginning a new tool iteration at 58.2 s and being cancelled at 60.2 s, leaving `StacklessClosedChannelException` in dial-core and orphaned MCP/LLM calls still burning resources after the cut.

Raising the single number does not fix this. It was tried in production (60 → 120 s) and runs still failed non-deterministically, because the problem is not the size of the bound but that one bound is being asked to express two unrelated limits: *how long may the whole job take* and *how long may it make no progress*.

A further complication rules out the obvious implementation. The DIAL SDK emits SSE heartbeat ping comments **precisely when the response is idle**. A transport-level read timeout is therefore reset by the very silence it is meant to detect: a hung application that still pings would never time out. Idle detection must observe parsed events, not socket reads.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A long but healthy run completes (Priority: P1)

A schedule targets a DIAL quickapp that calls tools (web search, MCP) and streams its answer. The work legitimately takes several minutes, with the longest silence between events being tens of seconds while a model thinks.

**Why this priority**: This is the reported incident. Until it is fixed the affected schedules produce no output at all.

**Acceptance**: Given a streaming target that emits events for longer than the idle bound allows between any two of them but finishes within the ceiling, the run completes successfully and its result is persisted.

### User Story 2 - A genuinely hung run is abandoned (Priority: P1)

A streaming target accepts the connection, emits some events, then stops making progress — while the platform keeps sending heartbeat pings.

**Why this priority**: Raising the ceiling without preserving stall detection would trade one defect for a worse one. With `job_max_instances: 1`, a run that never ends blocks every later firing of that schedule permanently and leaves its row in `in_progress` forever.

**Acceptance**: Given a stream that delivers no parsed event for longer than the idle bound — heartbeat pings notwithstanding — the attempt is abandoned and the run is recorded as failed with a reason that names stream inactivity, distinct from the ceiling being hit.

### User Story 3 - An unreachable host fails fast (Priority: P2)

A schedule points at a host that does not accept connections.

**Why this priority**: Today the configured connect timeout is silently discarded, so such a run occupies a worker slot for the whole ceiling. With the ceiling raised to an hour this becomes materially worse.

**Acceptance**: Given a target host that cannot be connected to, the attempt fails at the configured connect timeout (5 s by default) rather than at the ceiling.

### User Story 4 - An operator tunes a single schedule (Priority: P3)

An operator knows one particular target is slow to start producing output and wants a looser idle bound for it alone, without touching any other schedule.

**Acceptance**: Both bounds are settable per schedule through the existing `properties` object, are echoed back on read, and survive to the worker. A configuration where the idle bound exceeds the ceiling is rejected at create/update time with a clear message.

### Edge Cases

- **Non-streaming targets have no idle period.** A `rest` call, or a chat/responses call with `stream: false`, spends its entire wait in a single read. Applying the idle bound to it would cap it at the idle value and deny it the ceiling entirely. The consequence is that a non-streaming target has **no stall detector at all** — which is why its ceiling must not be raised as far as the streaming one. A host that accepts the connection and then goes silent holds a worker slot for the whole ceiling.
- **Heartbeat-only streams.** A stream delivering nothing but ping comments is, by definition, idle. The SSE comment is discarded by the parser and never becomes an event, so event-level idle detection sees the silence correctly.
- **On-demand runs are additionally bounded by shutdown drain.** They execute in the API process under `DRAIN_TIMEOUT_SECONDS = 30.0`, so a ceiling above 30 s does not survive a rollout for that path regardless of configuration.
- **Timeouts are not retryable.** A timeout carries no HTTP status code, and the retry policy matches on status codes only, so neither bound can trigger a retry. This is existing behaviour and is preserved.
- **Both bounds unset** is the common case and must keep working: the process defaults apply.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `ScheduleProperties` MUST expose two independent optional bounds: the existing `timeout` (absolute ceiling on one attempt) and a new `idle_timeout` (maximum gap between consecutive stream events).
- **FR-002**: `timeout` MUST retain its current meaning and validation. No deployed schedule that sets it explicitly may have its ceiling reinterpreted.
- **FR-003**: When `timeout` is unset, the ceiling MUST fall back to a per-process configured default acting as an emergency backstop against hangs, not as a routine bound.
- **FR-003a**: That fallback MUST be larger for streaming targets than for non-streaming ones. Only a streaming target has an idle bound watching it; a non-streaming target has no progress signal, so total duration is its sole instrument and must stay tighter.
- **FR-004**: When `idle_timeout` is unset, the idle bound MUST fall back to a per-process configured default.
- **FR-005**: Every bound in FR-003, FR-003a and FR-004 MUST be configurable independently for the API process and the worker process, and the value configured for a process MUST actually be the one its runs use.
- **FR-006**: Idle detection MUST operate on parsed stream events, not on socket reads, so that heartbeat ping comments do not mask inactivity.
- **FR-007**: The idle bound MUST apply only to streaming requests. Non-streaming requests MUST be bounded by the ceiling alone.
- **FR-008**: Per-request HTTP timeouts MUST preserve the client's configured connect, write and pool phases, overriding only the read phase.
- **FR-009**: A configuration where `idle_timeout` exceeds `timeout` (both set) MUST be rejected at create and update time with a validation error.
- **FR-010**: Both resolved values MUST appear in the attempt-start log entry.
- **FR-011**: A ceiling expiry and an idle expiry MUST be distinguishable from each other in logs by event name alone, not only by free-text message, and in the persisted run failure reason.
- **FR-012**: Both fields MUST round-trip through `schedules.kwargs`, the run `schedule_snapshot`, and the schedule read responses without a database migration.
- **FR-013**: Executors MUST read both resolved values from the run context only, never from `properties` directly.
- **FR-014**: The `*_HTTP_TIMEOUT_SECONDS` setting MUST be removed from both processes. DIAL metadata calls MUST be bounded by the shared HTTP client's configured connect, read, write and pool phases rather than a single flat value. A deployment that still sets the removed variable MUST start normally.

### Key Entities

- **ScheduleProperties** — the per-execution call configuration persisted in `schedules.kwargs` (JSONB). Gains `idle_timeout`.
- **RunContext** — the per-run object carrying resolved configuration from the orchestrator to the executor. Gains the resolved idle bound and the process timeout defaults; loses `default_timeout`.
- **HTTP settings (per process)** — gains two ceiling defaults (streaming and non-streaming) and an idle default; loses `timeout_seconds`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The affected production schedule completes successfully and persists a result, where it previously failed on every run.
- **SC-002**: A streaming target that emits events continuously for longer than the idle bound, with no single gap exceeding it, completes rather than being cut off.
- **SC-003**: A streaming target that stops producing events while still sending heartbeat pings is abandoned within the idle bound plus scheduling slack, not at the ceiling.
- **SC-004**: A non-streaming target is permitted to run beyond the idle bound and is not capped at it, while still being held to a ceiling materially tighter than the streaming one.
- **SC-004a**: A non-streaming target that accepts a connection and then goes permanently silent is abandoned well inside the streaming ceiling, bounding how long it can occupy a worker slot and a pooled connection.
- **SC-005**: An unreachable host fails within the configured connect timeout rather than occupying a worker slot until the ceiling.
- **SC-006**: Given a run log, an operator can tell which bound expired without reading source code.
- **SC-007**: Existing schedules that set neither field continue to execute, with the ceiling now supplied by the process default.
- **SC-008**: No Alembic migration is produced by this feature.

## Assumptions

- The observed worst-case gap between events is ~22 s, from trace `573e59c09029866fcd77627aa08836e9` (longest silence 21.80 s during model thinking; second longest 7.49 s; remainder 2–3 s). A 60 s idle default carries roughly a ×2.7 margin over the worst observed gap.
- No legitimate run is expected to approach an hour. The ceiling is sized so that it fires only when something is genuinely broken, which is what makes it acceptable as a non-resetting bound.
- Heartbeat is enabled in the targeted quickapp, confirmed by the operator from quickapp logs. The design does not depend on it being enabled, only on it not defeating idle detection.
- Per-attempt detail remains logs-only; the run row keeps the final outcome only, per the deviation recorded in feature 020.
- The production mitigation `fix/change-api-and-worker-timeout` (raising `timeout_seconds` to 600 in both HTTP settings classes) was a stopgap and is superseded by this feature. It merged to `development` first. This feature keeps its 600 s run budget as the non-streaming ceiling default and removes `timeout_seconds` itself (FR-014). The resolution is recorded in the plan.

## Out of Scope

- Persisting per-attempt history to the database.
- Surfacing `error_text` on run list items.
- `job_misfire_grace_time` behaviour, where a missed firing leaves no trace in run history.
- Any change to the retry policy or to which failures are retryable.
