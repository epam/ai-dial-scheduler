# Feature Specification: Kubernetes Probes for Worker Service

**Feature Branch**: `025-k8s-worker-probes`

**Created**: 2026-07-13

**Status**: Draft

**Input**: User description: "Implement k8s probes for worker service"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Detect and recover a hung or crashed worker (Priority: P1)

As the platform operations team running the scheduler in Kubernetes, I need the worker workload to expose its health so that Kubernetes can automatically detect a worker instance that has stopped processing scheduled jobs (hung, deadlocked, or crashed internally) and restart it, without a human having to notice missed schedule runs first.

**Why this priority**: The worker is the component that actually executes scheduled jobs. Today it has no externally observable health signal, so a stuck worker can silently stop running schedules for an unbounded amount of time. This is the core value of the feature.

**Independent Test**: Simulate a worker whose internal scheduling loop has stopped making progress (e.g., its periodic internal signal goes stale) and verify that Kubernetes' liveness check fails and the pod is restarted, while a healthy worker's liveness check continues to succeed.

**Acceptance Scenarios**:

1. **Given** a worker instance is running and actively processing its internal schedule, **When** Kubernetes performs a liveness check, **Then** the check reports healthy.
2. **Given** a worker instance's internal scheduling loop has stopped updating its health signal for longer than the configured staleness threshold, **When** Kubernetes performs a liveness check, **Then** the check reports unhealthy so Kubernetes restarts the instance.
3. **Given** a worker instance has just crashed and Kubernetes has started a replacement pod, **When** the replacement is still initializing, **Then** it is not prematurely killed for failing liveness checks before it has had a reasonable chance to start.

---

### User Story 2 - Avoid routing/scaling actions to a worker that isn't ready (Priority: P2)

As the platform operations team, I need Kubernetes to know when a worker instance has finished initializing (connected to its dependencies and loaded its scheduled jobs) versus when it is still starting up, so that orchestration decisions (e.g., rolling updates, not counting the pod as available) reflect the worker's actual ability to do work.

**Why this priority**: Without a readiness signal, Kubernetes treats the worker as available the instant the container process starts, even if it hasn't yet connected to the database or loaded jobs — this can cause rolling deployments to proceed before a new worker is actually able to pick up work.

**Independent Test**: Start a worker instance while its required dependency (database) is unavailable and verify readiness reports not-ready; then restore the dependency and verify readiness becomes ready once initialization completes.

**Acceptance Scenarios**:

1. **Given** a worker instance is still connecting to its database or loading scheduled jobs, **When** Kubernetes performs a readiness check, **Then** the check reports not-ready.
2. **Given** a worker instance has completed initialization and its database connection is healthy, **When** Kubernetes performs a readiness check, **Then** the check reports ready.
3. **Given** a running, previously-ready worker instance loses connectivity to its database, **When** Kubernetes performs a readiness check, **Then** the check reports not-ready until connectivity is restored.

---

### User Story 3 - Give a slow-starting worker enough time to initialize (Priority: P3)

As the platform operations team, I need Kubernetes to distinguish "still starting up" from "started but unhealthy" for the worker, so that normal startup time (connecting to the database, loading potentially many scheduled jobs) is not mistaken for a liveness failure and does not cause restart loops.

**Why this priority**: This refines the reliability of the other two probes under slow-start conditions (e.g., a large number of persisted schedules to load, or a temporarily slow database) but is not required for the core detect-and-recover value.

**Independent Test**: Start a worker with an artificially slow initialization step and verify it is not killed by the liveness probe during the allowed startup grace period, but is killed if it fails to become live within that period.

**Acceptance Scenarios**:

1. **Given** a worker instance is within its allowed startup grace period and still initializing, **When** Kubernetes checks startup status, **Then** the instance is not restarted for failing liveness/readiness checks.
2. **Given** a worker instance fails to become live before the startup grace period elapses, **When** the grace period ends, **Then** Kubernetes restarts the instance.

---

### Edge Cases

- What happens when the database becomes temporarily unavailable for a short blip shorter than the check interval? The worker should not flap between ready/not-ready on transient, sub-interval outages if avoidable, but must reliably report not-ready for sustained outages.
- How does the system handle a worker that is alive (process running, internal loop healthy) but its database connection is down? Liveness should still report healthy (the process itself is not stuck); readiness should report not-ready (it cannot do useful work).
- What happens during graceful shutdown (e.g., pod termination)? The worker should stop reporting ready as soon as it begins shutting down, so no new work is expected of it, while it finishes any in-flight job execution.
- What happens if the worker's internal scheduler thread throws an unhandled exception and stops entirely? Liveness must detect this and report unhealthy so the instance is restarted.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The worker service MUST expose a liveness signal that Kubernetes can check to determine whether the worker process and its internal job-scheduling loop are still making progress.
- **FR-002**: The liveness signal MUST report unhealthy when the internal scheduling loop has stopped updating for longer than a configured staleness threshold, indicating the process is hung even though it may still be running.
- **FR-003**: The worker service MUST expose a readiness signal that Kubernetes can check to determine whether the worker has completed initialization and can currently reach its required dependencies (at minimum, the database).
- **FR-004**: The readiness signal MUST report not-ready while the worker is still starting up (before its scheduling loop and database connection are established).
- **FR-005**: The readiness signal MUST report not-ready if a previously-ready worker loses connectivity to the database, and MUST return to ready once connectivity is restored.
- **FR-006**: The worker service MUST expose a startup signal (or equivalent grace-period behavior) so that normal initialization time is not mistaken for a liveness failure, while a worker that never successfully starts is still eventually restarted.
- **FR-007**: Liveness and readiness MUST be independently evaluable — a database outage MUST NOT, by itself, cause the liveness check to fail (the process is not stuck), even though it causes the readiness check to fail.
- **FR-008**: The worker's health signals MUST be checkable using standard Kubernetes probe mechanisms (e.g., HTTP endpoint, TCP socket, or exec command) without requiring the worker to accept unrelated inbound traffic.
- **FR-009**: The deployment configuration for the worker workload MUST define liveness, readiness, and startup probes with initial delay, period, timeout, and failure-threshold values appropriate to the worker's expected startup and processing behavior.
- **FR-010**: Health signal checks MUST NOT meaningfully degrade the worker's ability to process scheduled jobs (e.g., checks must be lightweight and non-blocking relative to job execution).

### Key Entities

- **Worker Liveness Signal**: Represents whether the worker's own process and internal job-scheduling loop are actively making progress; independent of external dependency availability.
- **Worker Readiness Signal**: Represents whether the worker has finished initializing and can currently reach the dependencies it needs (database) to do useful work.
- **Staleness Threshold**: The configured maximum time the internal scheduling loop's progress signal may go without updating before the worker is considered hung.
- **Startup Grace Period**: The configured maximum time a worker instance is allowed to take to become live before Kubernetes considers startup failed.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A worker instance whose internal processing loop hangs is detected and replaced by Kubernetes within a small, bounded number of minutes, without any manual intervention.
- **SC-002**: A worker instance that cannot reach the database is excluded from being counted as available/ready within one check interval of the outage starting, and is automatically included again within one check interval of connectivity being restored.
- **SC-003**: Worker instances starting up under normal conditions are never killed by the liveness probe for still-initializing behavior (zero false-positive restarts during normal startup, measured across rolling deployments).
- **SC-004**: During a rolling update of the worker workload, no scheduled job runs are missed due to traffic/work being routed to a worker instance that was not actually ready.

## Assumptions

- The worker is a single, stateless-per-instance process (no inbound API traffic) whose only hard dependency to check for readiness is the database, consistent with the current implementation (`src/worker/app.py`, `src/worker/jobs.py`) which has no other external dependency (e.g., no Redis).
- The worker's existing periodic internal heartbeat/no-op job (fired every `noop_interval_seconds`, default 60s) is a reasonable basis for the liveness "progress" signal; the staleness threshold is assumed to be a small multiple of this interval (e.g., 2-3x) to avoid false positives, unless otherwise specified in the implementation plan.
- Per the Kubernetes documentation's recommended pattern for containers with no HTTP/TCP/gRPC listener, health signals are exposed via filesystem marker files checked by Kubernetes `exec` probes, rather than by adding a new network listener to the worker (see `plan.md`/`research.md` for the resolved design).
- Kubernetes deployment manifests/Helm charts for this service are maintained outside this repository (per existing `Dockerfile` comments and CI structure); this feature covers making the worker's health observable and documenting the intended probe configuration, with actual manifest changes applied wherever those definitions live.
- The readiness check reuses the same database-connectivity pattern already used by the API's existing `/ready` endpoint (`src/api/v1/endpoints/health.py`), for consistency.
- Graceful shutdown behavior (stop accepting new work but finish in-flight jobs) follows standard Kubernetes pod termination handling and does not require new functional scope beyond flipping readiness to not-ready.
