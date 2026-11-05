# Phase 1 Data Model: Observability Improvements

This feature introduces **no database entities and no migrations**. The "data" here is in-process
telemetry context, a new response schema, and a configuration field.

## 1. Log record enrichment fields (structlog event dict)

Added by the trace/span processor to each log record **only when a valid span is active**.

| Field | Type | Format | Source |
|-------|------|--------|--------|
| `trace_id` | string | 32-char lowercase hex (zero-padded) | `get_current_span().get_span_context().trace_id` |
| `span_id` | string | 16-char lowercase hex (zero-padded) | `get_current_span().get_span_context().span_id` |

**Rules**:
- Omitted entirely when no span is active or the span context is invalid (`trace_id == 0`).
- Must not overwrite an existing key of the same name already present in the event dict.
- Rendered inside the existing JSON output, after the ordered header keys.

## 2. ReadinessResponse (Pydantic v2 schema — `src/lib/schemas/health.py`)

Returned by `GET /ready`.

| Field | Type | Allowed values | Description |
|-------|------|----------------|-------------|
| `status` | `Literal["ready", "not_ready"]` | `ready` \| `not_ready` | Overall readiness verdict. |
| `database` | `Literal["ok", "unavailable"]` | `ok` \| `unavailable` | Result of the database connectivity check. |

**State mapping**:

| Database check outcome | HTTP status | `status` | `database` |
|------------------------|-------------|----------|------------|
| `SELECT 1` succeeds within timeout | `200` | `ready` | `ok` |
| Error, connection failure, or timeout | `503` | `not_ready` | `unavailable` |

## 3. Settings addition (`src/lib/settings.py` → `Settings`)

| Field | Type | Default | Env var | Description |
|-------|------|---------|---------|-------------|
| `readiness_db_timeout_seconds` | `float` | `2.0` | `READINESS_DB_TIMEOUT_SECONDS` | Upper bound for the readiness `SELECT 1`; on expiry the endpoint returns `503`. |

## 4. OTLP configuration (environment only — no code model)

Documented in `contracts/otlp-config.md`, `.env.example`, and README. These are consumed by the DIAL
SDK's `TelemetryConfig` at construction; the application defines no Pydantic model for them and stores
no values — they flow straight to the OpenTelemetry SDK.

## Validation rules (from requirements)

- `trace_id`/`span_id` present for 100% of records emitted within an active span (FR-002, FR-003, SC-001).
- Records valid with the fields absent when no span is active (FR-004).
- `ReadinessResponse` always returns a defined `status`/`database` pair; the endpoint never raises
  uncaught exceptions (FR-012, FR-014).
- `readiness_db_timeout_seconds` bounds the check duration (FR-013).
