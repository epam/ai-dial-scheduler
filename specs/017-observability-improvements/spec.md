# Feature Specification: Observability Improvements

**Feature Branch**: `017-observability-improvements`

**Created**: 2026-06-17

**Status**: Draft

**Input**: User description: "Improve application general observability. 1) Add telemetry extra to `aidial-sdk` dependency. 2) Add .env.example env variables that will enable otlp, update readme with that envs. 3) Ensure that all log records (both api and worker) include trace and span id. 4) Add /ready endpoint that checks database connection."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Correlate logs with traces across API and worker (Priority: P1)

An operator investigating a production incident needs to follow a single request or scheduled job as it moves through the system. Every log line emitted by both the API service and the background worker must carry the active trace and span identifiers so the operator can pivot from any log entry to the corresponding distributed trace (and vice versa) without guessing which log lines belong together.

**Why this priority**: Log-to-trace correlation is the single highest-leverage observability capability. Without it, traces and logs are two disconnected data sets and root-cause analysis across the API → worker boundary remains manual and error-prone. This story delivers immediate diagnostic value even before exporting is fully configured.

**Independent Test**: Trigger any API request and any scheduled job, then inspect emitted log records. Each record produced within an active span includes a populated trace identifier and span identifier; records emitted outside any span are still well-formed (identifiers absent or empty, never malformed).

**Acceptance Scenarios**:

1. **Given** the API service is running, **When** a request is handled within an active trace, **Then** every log record emitted during that request includes the matching trace ID and span ID.
2. **Given** the worker is running, **When** a scheduled job executes within an active span, **Then** every log record emitted during that job includes the matching trace ID and span ID.
3. **Given** code emits a log record while no span is active, **When** the record is rendered, **Then** the record is still valid structured output and does not raise an error.
4. **Given** a trace spans an API call that enqueues work later executed by the worker, **When** logs from both services are collected, **Then** they share the same trace ID, enabling end-to-end correlation.

---

### User Story 2 - Enable OTLP export through configuration (Priority: P2)

An operator deploying the application wants to send traces and metrics to their existing observability backend (e.g., an OTLP collector) purely through environment configuration, without code changes. They need a documented, discoverable set of environment variables and a working example to copy from.

**Why this priority**: Correlated telemetry (Story 1) is only externally useful once it can be exported to a backend. This depends on the telemetry capability being installed and on configuration being documented, so it follows P1 but is required for the feature to deliver operational value in a real deployment.

**Independent Test**: Starting from the provided example configuration, set the documented OTLP environment variables to point at a collector; confirm the application starts cleanly and traces/metrics arrive at the collector. With the variables unset, confirm the application still starts and runs normally (export simply inactive).

**Acceptance Scenarios**:

1. **Given** the documented OTLP environment variables are set to a reachable endpoint, **When** the API service and worker run, **Then** traces and metrics are exported to that endpoint.
2. **Given** no OTLP environment variables are set, **When** the services run, **Then** they start and operate normally with telemetry export inactive (no startup failure).
3. **Given** a new contributor reads the example environment file and the README, **When** they look for how to enable OTLP, **Then** the relevant variables and their purpose are documented and copyable.

---

### User Story 3 - Verify service readiness including database (Priority: P2)

An operator (or an orchestration platform such as Kubernetes) needs to know whether the API service is ready to serve traffic, where "ready" specifically includes being able to reach the database. They need a dedicated readiness endpoint distinct from a basic liveness check, so traffic is only routed once dependencies are actually usable.

**Why this priority**: Readiness gating prevents routing traffic to an instance that cannot reach its database, reducing user-visible errors during startup, deploys, and database disruptions. It is independent of the tracing work and delivers value on its own, but is a focused addition rather than the core correlation capability, so it sits at P2.

**Independent Test**: Call the readiness endpoint while the database is reachable and confirm a success response; simulate the database being unreachable and confirm the endpoint reports not-ready with an appropriate failure status, without crashing the service.

**Acceptance Scenarios**:

1. **Given** the API service is running and the database is reachable, **When** the readiness endpoint is called, **Then** it responds with a success status indicating the service is ready.
2. **Given** the database is unreachable or unhealthy, **When** the readiness endpoint is called, **Then** it responds with a not-ready status (distinct from a generic server error) and the service itself remains running.
3. **Given** an orchestration platform polls the readiness endpoint, **When** the database transitions from unreachable to reachable, **Then** the endpoint transitions from not-ready to ready without a restart.

---

### Edge Cases

- A log record is emitted before telemetry is initialized during startup, or after shutdown begins — the record must remain valid with identifiers simply absent.
- OTLP environment variables are set to an unreachable or misconfigured endpoint — the application must continue serving requests and running jobs; export failures must not crash the service or block the event loop.
- The readiness check is called concurrently and repeatedly by an orchestrator — it must remain cheap and must not exhaust the database connection pool or itself become a load source.
- The readiness database check hangs because the database is slow — the check must bound its wait and report not-ready rather than hanging indefinitely.
- Trace and span identifiers must never overwrite or collide with existing structured log fields of other meaning.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST package the telemetry capability of the DIAL SDK so that distributed tracing and metrics support is available to both the API service and the background worker.
- **FR-002**: Every log record emitted by the API service while a tracing span is active MUST include the active trace identifier and span identifier.
- **FR-003**: Every log record emitted by the background worker while a tracing span is active MUST include the active trace identifier and span identifier.
- **FR-004**: Log records emitted when no span is active MUST remain valid, structured, and free of errors (trace/span identifiers absent or empty).
- **FR-005**: Trace and span identifiers MUST appear in the same structured log output used by the rest of the application's logging, so they are present in both human inspection and downstream log aggregation.
- **FR-006**: The system MUST allow operators to enable and configure OTLP export of traces and metrics entirely through environment variables, without code changes.
- **FR-007**: The example environment file MUST document the OTLP-related environment variables, including which ones enable export and where data is sent.
- **FR-008**: The README MUST describe the OTLP environment variables and how to use them to enable export.
- **FR-009**: When OTLP environment variables are not set, the application MUST start and operate normally with telemetry export inactive.
- **FR-010**: When OTLP environment variables point to an unreachable endpoint, the application MUST continue to serve requests and run jobs without crashing or blocking.
- **FR-011**: The API service MUST expose a readiness endpoint, distinct from any existing liveness/health endpoint, that verifies the database connection.
- **FR-012**: The readiness endpoint MUST return a success response when the database is reachable and a not-ready response (distinguishable from a generic internal error) when it is not.
- **FR-013**: The readiness database check MUST be bounded in time so a slow or hanging database results in a not-ready response rather than an indefinite wait.
- **FR-014**: The readiness endpoint MUST NOT cause the service to crash when the database is unavailable, and MUST recover to ready once the database becomes reachable again.
- **FR-015**: Existing liveness/health checking behavior MUST be preserved (the new readiness endpoint adds to, rather than replaces, current health checking).

### Key Entities *(include if feature involves data)*

- **Trace context**: The identifiers (trace ID, span ID) describing the currently active distributed-tracing span, used to correlate logs, traces, and metrics across the API and worker.
- **Readiness status**: The computed assessment of whether the API service can serve traffic, primarily determined by database reachability, exposed via the readiness endpoint.
- **OTLP configuration**: The set of environment variables that control whether and where telemetry (traces, metrics) is exported.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of log records emitted within an active span by both the API and worker include populated trace and span identifiers.
- **SC-002**: An operator can correlate a log record to its trace, and a trace to its logs, using a shared trace identifier in 100% of cases where a span was active.
- **SC-003**: An operator can enable OTLP export and confirm telemetry arrival at a collector using only the documented environment variables, with no code changes, in under 10 minutes from a clean checkout.
- **SC-004**: With OTLP variables unset or pointing at an unreachable endpoint, the application starts successfully and serves requests in 100% of trials (zero export-related crashes).
- **SC-005**: The readiness endpoint returns ready when the database is reachable and not-ready within a bounded time (no indefinite hang) when it is not, in 100% of trials.
- **SC-006**: A new contributor can locate and understand the OTLP configuration from the example environment file and README without consulting source code.

## Assumptions

- The "users" of this feature are operators, SREs, and developers diagnosing and deploying the system, not end users of the scheduled tasks.
- The DIAL SDK's telemetry capability is the intended mechanism for tracing/metrics in both services, consistent with the project constitution (DIAL SDK telemetry for API and worker).
- OTLP export configuration follows the standard environment-variable conventions recognized by the underlying telemetry tooling (e.g., endpoint, protocol, enable toggles); exact variable names will be finalized during planning against the SDK/OpenTelemetry conventions.
- The readiness endpoint is unauthenticated and lightweight, suitable for orchestration probes, and checks the database only (not every downstream dependency) for the initial scope.
- The existing structured (JSON) logging pipeline is retained; trace/span identifiers are added to it rather than introducing a new logging format.
- A liveness/health endpoint already exists via the API framework; this feature adds a separate readiness endpoint.
- Bounded timeout values for the readiness database check use reasonable defaults and may be configurable, to be decided during planning.
