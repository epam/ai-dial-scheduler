# Phase 0 Research: Observability Improvements

## R1: Installing the DIAL SDK telemetry capability

**Decision**: Change the dependency in `pyproject.toml` from `aidial-sdk==0.37.0` to
`aidial-sdk[telemetry]==0.37.0` and run `uv sync` (lockfile updated/committed).

**Rationale**: The `telemetry` extra (confirmed in the installed `aidial_sdk` METADATA) pulls
`opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc`,
`opentelemetry-exporter-prometheus`, `prometheus-client`, and the FastAPI/httpx/requests/urllib/
logging/system-metrics instrumentors. Without it:
- the worker's `init_telemetry(...)` import fails and is swallowed by its `except Exception` →
  `telemetry_init_failed` warning (no tracing/metrics);
- the API's `DIALApp(telemetry_config=...)` cannot stand up exporters;
- `opentelemetry.trace.get_current_span()` (needed by the log processor, R3) is unavailable.

**Alternatives considered**: Depending on raw `opentelemetry-*` packages directly — rejected: the
constitution mandates using DIAL SDK telemetry, and the extra already curates compatible versions.

## R2: OTLP enablement via environment variables

**Decision**: Expose configuration through the **standard OpenTelemetry SDK environment variables**
that `aidial_sdk.telemetry.types.TelemetryConfig` already reads at construction time. No code is
needed to "wire" them; documenting them is sufficient.

Variables that matter (from `aidial_sdk/telemetry/types.py` + OTel SDK spec):

| Variable | Effect | Example |
|----------|--------|---------|
| `OTEL_TRACES_EXPORTER` | Set to `otlp` to enable OTLP trace export (constructs `TracingConfig`). | `otlp` |
| `OTEL_METRICS_EXPORTER` | `otlp` and/or `prometheus` to enable metric export. | `otlp` |
| `OTEL_LOGS_EXPORTER` | Set to `otlp` to enable OTLP log export. | `otlp` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Collector endpoint for all OTLP signals (gRPC default `:4317`). | `http://otel-collector:4317` |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | Wire protocol; the SDK extra ships the gRPC exporter. | `grpc` |
| `OTEL_SERVICE_NAME` | Service name attribute on emitted telemetry. | `aidial-scheduler` |
| `OTEL_EXPORTER_PROMETHEUS_PORT` | Port for the Prometheus scrape server (when `prometheus` exporter is on). | `9464` |
| `OTEL_LOG_LEVEL` | Verbosity of the `opentelemetry` logger (already honored in `logging.py`). | `ERROR` |
| `OTEL_PYTHON_LOG_CORRELATION` | SDK toggle that patches the **stdlib** log format — see R3 caveat. | `true` |

**Rationale**: `TelemetryConfig`'s field defaults are computed from these env vars (`"otlp" in
OTEL_TRACES_EXPORTER`, etc.). When none are set, `tracing/metrics/logs` are `None` and telemetry is a
no-op — the service starts and runs normally (FR-009). Pointing the endpoint at an unreachable
collector does not crash the service: span/metric/log export is performed by background batch
processors that fail silently and retry (FR-010).

**Alternatives considered**: Inventing app-specific settings (e.g. `OTLP_ENABLED`) and mapping them to
OTel internally — rejected (YAGNI; duplicates a well-known standard and risks drift from SDK behavior).

## R3: Including trace_id and span_id in every log record (API + worker)

**Decision**: Add a small custom **structlog processor** to `src/lib/utils/logging.py` that calls
`opentelemetry.trace.get_current_span()`, reads the `SpanContext`, and — when the context is valid —
adds zero-padded hex `trace_id` (32 chars) and `span_id` (16 chars) to the event dict. Register it:
1. in `configure_logging()`'s `structlog.configure(processors=[...])` chain (native structlog calls), and
2. in the `ProcessorFormatter` `processors` **and** `foreign_pre_chain` in `LoggingConfig.get_config()`
   (so stdlib/uvicorn/APScheduler records routed through the formatter are also enriched).

Placement: after `merge_contextvars`/`add_logger_name`, before `_order_keys`/`JSONRenderer`.

**Rationale**: The SDK's correlation path (`TracingConfig.logging` ← `OTEL_PYTHON_LOG_CORRELATION`)
calls `LoggingInstrumentor().instrument(set_logging_format=True)`, which only rewrites the **stdlib**
root logger's *format string*. This project renders logs through **structlog + JSONRenderer**, so that
format string is never used and the IDs would never appear in our JSON output. A structlog processor
reading the live span context is the correct, framework-aligned injection point and works identically
in the API and worker because both call `configure_logging()`. It is also export-independent: IDs
appear whenever a span is active, even before/without OTLP export.

When no span is active (`INVALID_SPAN` / `trace_id == 0`), the processor adds nothing and returns the
event dict unchanged — records stay valid (FR-004).

**Alternatives considered**:
- Rely solely on `OTEL_PYTHON_LOG_CORRELATION` — rejected: does not affect structlog JSON output.
- Use `LoggingHandler`/OTLP log export to carry correlation — rejected: that exports to a backend but
  does not put IDs into the console JSON logs operators read (FR-005), and is gated on `OTEL_LOGS_EXPORTER`.

## R4: Ensuring worker job logs carry trace/span IDs

**Decision**: In `src/worker/app.py`, wrap each job execution in a tracing span using a module-level
tracer (`opentelemetry.trace.get_tracer(__name__)`), e.g. start a `start_as_current_span("job:<id>")`
around the job callable, so all logs emitted during a run inherit that span's `trace_id`/`span_id`.

**Rationale**: APScheduler does not create spans on its own; without an active span the worker's logs
would always fall into the FR-004 "no span" path and SC-001/SC-002 (worker correlation) could not be
met. A per-run span is the minimal addition that satisfies User Story 1, Acceptance Scenario 2, and
also produces useful worker traces once OTLP export is enabled. The API already gets spans from the
SDK's `FastAPIInstrumentor`, so no API-side span work is needed.

**Alternatives considered**: Leaving the worker un-spanned and only enriching when a span happens to
exist — rejected: worker logs would essentially never carry IDs, failing the spec's worker scenarios.

## R5: `/ready` readiness endpoint with a bounded database check

**Decision**: Add `GET /ready` (registered at root, no `/v1` prefix) that acquires a session from the
existing `Database` singleton sessionmaker and executes `SELECT 1`, wrapped in
`asyncio.wait_for(..., timeout=settings.readiness_db_timeout_seconds)` (new setting, default `2.0`s).
On success return `200` with `ReadinessResponse(status="ready", database="ok")`; on any failure
(`OperationalError`, `TimeoutError`, connection error) return `503` with
`ReadinessResponse(status="not_ready", database="unavailable")`. Errors are caught and logged via
`logger`; the handler never propagates an exception that would 500 or crash the service.

**Rationale**: Reuses the established async DB access path (no event-loop blocking, FR/Principle I).
`503` (not `500`) gives orchestrators a clear, distinct "not ready" signal (FR-012). The bounded
`wait_for` prevents an indefinite hang on a slow/dead database (FR-013). Statelessness means it
naturally recovers to ready when the DB returns (FR-014). The SDK's existing liveness `/health`
(`add_healthcheck=True`) is untouched (FR-015).

**Alternatives considered**:
- Reuse `pool_pre_ping` only — rejected: that guards checkout, it is not an externally pollable signal.
- Put readiness under `/v1/ready` — rejected: probes conventionally live at root alongside `/health`.
- Check every dependency (DIAL, Redis, MCP) — rejected (YAGNI; spec scopes readiness to the database).

## Summary of resolved unknowns

| Topic | Resolution |
|-------|-----------|
| Telemetry packaging | `aidial-sdk[telemetry]==0.37.0` |
| OTLP enablement | Standard OTel env vars, documented only (no new code) |
| Log correlation mechanism | Custom structlog processor reading `get_current_span()` |
| Worker correlation | Per-job-run `start_as_current_span` wrapper |
| Readiness | `GET /ready`, async `SELECT 1` under `asyncio.wait_for` (default 2s), 200/503 |
| Readiness timeout default | `readiness_db_timeout_seconds = 2.0` (configurable) |
