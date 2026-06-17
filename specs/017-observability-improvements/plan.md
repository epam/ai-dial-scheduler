# Implementation Plan: Observability Improvements

**Branch**: `017-observability-improvements` | **Date**: 2026-06-17 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/017-observability-improvements/spec.md`

## Summary

Strengthen end-to-end observability across the API and worker services by: (1) installing the
`aidial-sdk[telemetry]` extra so OpenTelemetry tracing/metrics/log exporters are available in both
services; (2) documenting the standard OTel environment variables that toggle OTLP export in
`.env.example` and the README; (3) injecting the active `trace_id` and `span_id` into every
structured (structlog/JSON) log record in both services via a custom structlog processor — because
the SDK's built-in `LoggingInstrumentor` correlation only patches the *stdlib* log format and never
reaches our structlog JSON output; and (4) adding an unauthenticated `/ready` readiness endpoint to
the API that performs a time-bounded `SELECT 1` against the database, returning `200` when reachable
and `503` when not, distinct from the SDK's existing liveness `/health`.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python >=3.14,<3.15`)

**Primary Dependencies**: `aidial-sdk[telemetry]==0.37.0` (DIALApp + `init_telemetry`, brings
`opentelemetry-api/sdk`, OTLP gRPC + Prometheus exporters, FastAPI/httpx/logging instrumentors),
`structlog==26.1.0`, `apscheduler~=3.11`, `sqlalchemy[asyncio]==2.0.50` + `asyncpg`, FastAPI (via
DIALApp), Pydantic v2 / pydantic-settings.

**Storage**: PostgreSQL (async via asyncpg); readiness check reuses the existing `Database`
singleton sessionmaker. No new tables or migrations.

**Testing**: `pytest` + `pytest-asyncio` (`asyncio_mode=auto`), `pytest-mock`.

**Target Platform**: Linux server, two Docker containers (API service + APScheduler worker).

**Project Type**: Web service (API) + background worker, sharing `src/lib/`.

**Performance Goals**: Trace/span processor adds negligible per-record overhead (a single
in-process `get_current_span()` lookup, no I/O). Readiness probe answers within a bounded timeout
(default 2s) and is cheap enough for frequent orchestrator polling.

**Constraints**: Async I/O only — the readiness DB check MUST NOT block the event loop and MUST be
wrapped in `asyncio.wait_for`. No `print()`. No secrets in logs. OTLP export failures MUST NOT crash
or block either service. `assert` forbidden in production code.

**Scale/Scope**: Small, cross-cutting change. ~1 dependency edit, 1 structlog processor, 1 worker
span wrapper, 1 new endpoint + schema, 1 settings field, docs (`.env.example`, README).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|-----------|------------|
| **I. Reliability First** | PASS — readiness check is time-bounded (`asyncio.wait_for`) and degrades to `503` rather than hanging; OTLP export failures are non-fatal (worker already wraps `init_telemetry` in try/except; export batching is async and best-effort). No event-loop blocking. |
| **II. Observability (NON-NEGOTIABLE)** | PASS — this *is* the observability feature. Tracing/metrics come from DIAL SDK telemetry: `DIALApp(telemetry_config=...)` for the API and `init_telemetry(app=None, config=TelemetryConfig())` for the worker (both already wired; the extra makes them functional). All logging continues through `src.lib.utils.logging.logger`; trace/span IDs are added to that pipeline. |
| **III. Test-First (NON-NEGOTIABLE)** | PASS — unit tests for the structlog processor (span active / no span), readiness endpoint (DB up / down / timeout), and worker span wrapper are written before implementation in the `/speckit-implement` phase. |
| **IV. Contract-Driven API** | PASS — `/ready` is a new external interface; its request/response contract is captured in `contracts/` and surfaces in the auto-generated OpenAPI. Response is a Pydantic v2 model, not an ad-hoc dict. |
| **V. Simplicity & YAGNI** | PASS — no new abstractions; reuses the existing `Database` singleton, existing logging config, and standard OTel env-var conventions. Readiness checks the DB only (no speculative dependency registry). |

**Result**: PASS. No violations; Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/017-observability-improvements/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── readiness.md     # GET /ready contract
│   └── otlp-config.md   # OTLP environment-variable contract
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── api/
│   ├── app.py                      # MODIFY: include health/readiness router
│   └── v1/
│       └── endpoints/
│           └── health.py           # NEW: GET /ready readiness endpoint (root path, no /v1 prefix)
├── worker/
│   └── app.py                      # MODIFY: wrap each job run in an OTel span so worker logs carry IDs
└── lib/
    ├── settings.py                 # MODIFY: add readiness_db_timeout_seconds
    ├── schemas/
    │   └── health.py               # NEW: ReadinessResponse Pydantic schema
    └── utils/
        └── logging.py              # MODIFY: add trace/span structlog processor to both config + configure_logging

tests/
├── api/v1/endpoints/test_health.py # NEW: readiness endpoint (ready / not-ready / timeout)
├── lib/utils/test_logging.py       # NEW or MODIFY: trace/span processor (span active / absent)
└── worker/test_app.py              # MODIFY: job runs within a span

pyproject.toml                      # MODIFY: aidial-sdk[telemetry]==0.37.0
.env.example                        # MODIFY: OTLP env vars
README.md                           # MODIFY: OTLP env-var documentation
```

**Structure Decision**: Single repo, dual-entry-point layout (`src/api`, `src/worker`, shared
`src/lib`) per the constitution. The trace/span processor and the new `ReadinessResponse` schema live
in `src/lib` (shared, no inward dependency violation); the endpoint lives in `src/api`; the per-job
span wrapper lives in `src/worker`. The `/ready` route is registered without the `/v1` prefix so the
path is exactly `/ready`, matching the liveness `/health` that DIALApp already exposes at root.

## Complexity Tracking

> No constitution violations — section intentionally empty.
