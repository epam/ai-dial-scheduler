# Quickstart & Validation: Observability Improvements

Validates the four feature outcomes: telemetry packaging, OTLP env config, trace/span IDs in logs
(API + worker), and the `/ready` endpoint. References `contracts/` and `data-model.md` for details.

## Prerequisites

- `uv sync` succeeds with `aidial-sdk[telemetry]==0.37.0` in `pyproject.toml`.
- A local PostgreSQL reachable per `.env` (copy from `.env.example`).
- Optional: an OTLP collector (e.g. `otel/opentelemetry-collector`) listening on `:4317` to validate export.

## 1. Telemetry capability installed (FR-001)

```bash
uv sync
uv run python -c "import opentelemetry.trace; from aidial_sdk.telemetry.init import init_telemetry; print('telemetry OK')"
```

Expected: prints `telemetry OK` (the extra and its OpenTelemetry deps resolve).

## 2. Trace/span IDs in API logs (FR-002, FR-005, SC-001/002)

```bash
uv run python -m src.api  # or the project's documented API run command
curl -s http://localhost:5000/v1/schedules -H "<auth headers>"
```

Expected: API log lines emitted while handling the request are JSON objects that include populated
`trace_id` (32 hex chars) and `span_id` (16 hex chars). Pick a `trace_id` from a log line — it matches
the span the SDK's FastAPI instrumentation created for that request.

## 3. Trace/span IDs in worker logs (FR-003, SC-001/002)

```bash
uv run python -m src.worker  # or the project's documented worker run command
```

Expected: when the heartbeat (or any) job runs, the job's log lines include `trace_id`/`span_id` from
the per-run span. Logs emitted at worker startup (before any job span) are valid JSON **without** those
fields (FR-004).

## 4. No-span records stay valid (FR-004)

```bash
uv run pytest tests/lib/utils/test_logging.py
```

Expected: a record logged with no active span renders as valid JSON and omits `trace_id`/`span_id`
(no error, no empty-string garbage).

## 5. OTLP export via env vars (FR-006..FR-010, SC-003/004)

With a collector running, set the documented variables (see `contracts/otlp-config.md`):

```bash
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
# start API and worker, generate a request / let a job run
```

Expected: traces and metrics appear in the collector. Then **unset** the variables and restart:
both services start and run normally with export inactive (FR-009). Set the endpoint to an unreachable
host: services still serve requests, no crash (FR-010).

## 6. Readiness endpoint (FR-011..FR-015, SC-005)

```bash
# DB up:
curl -i http://localhost:5000/ready
# -> HTTP/1.1 200 OK   {"status":"ready","database":"ok"}

# Stop/again with DB stopped:
curl -i http://localhost:5000/ready
# -> HTTP/1.1 503 Service Unavailable   {"status":"not_ready","database":"unavailable"}

# Liveness unchanged:
curl -i http://localhost:5000/health
# -> 200 (does not check the DB)
```

Expected: `200/ready` when the DB is reachable, `503/not_ready` (returned within
~`READINESS_DB_TIMEOUT_SECONDS`) when it is not, and automatic recovery to `200` once the DB returns —
all without restarting the service.

## Quality gates

```bash
uv run pytest --cov=src
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
```

All gates must pass before the feature is considered complete (constitution Development Workflow).
