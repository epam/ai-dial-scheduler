# Contract: OTLP Telemetry Configuration (Environment Variables)

These standard OpenTelemetry SDK environment variables control telemetry export for **both** the API
service and the worker. They are read by the DIAL SDK's `TelemetryConfig` at startup. No application
code consumes them directly — this contract is documentation + `.env.example` + README.

## Variables

| Variable | Required to enable | Default (unset) | Description |
|----------|--------------------|-----------------|-------------|
| `OTEL_TRACES_EXPORTER` | for trace OTLP export | unset → traces disabled | Set to `otlp` to export traces. |
| `OTEL_METRICS_EXPORTER` | for metrics export | unset → metrics disabled | `otlp` and/or `prometheus`. |
| `OTEL_LOGS_EXPORTER` | for log OTLP export | unset → log export disabled | Set to `otlp` to export logs via OTLP. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | when any OTLP exporter on | none | Collector endpoint (gRPC, default port `4317`), e.g. `http://otel-collector:4317`. |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | optional | `grpc` | Wire protocol; the SDK extra ships the gRPC exporter. |
| `OTEL_SERVICE_NAME` | optional | app name | Service name attribute on telemetry. |
| `OTEL_EXPORTER_PROMETHEUS_PORT` | when `prometheus` metrics on | `9464` | Port for the Prometheus scrape server. |
| `OTEL_LOG_LEVEL` | optional | `ERROR` | Verbosity of the internal `opentelemetry` logger. |
| `OTEL_PYTHON_LOG_CORRELATION` | optional | `false` | SDK stdlib-log correlation toggle. Note: trace/span IDs in this app's JSON logs come from a dedicated structlog processor, independent of this flag. |

## Behavior contract

| # | Given | When | Then |
|---|-------|------|------|
| 1 | OTLP exporters set + reachable endpoint | services run | traces/metrics/logs arrive at the collector |
| 2 | No OTEL exporter vars set | services run | start normally; telemetry export is a no-op (`TelemetryConfig.is_noop()`) |
| 3 | OTLP endpoint set but unreachable | services run | services keep serving; export fails silently in background batch processors, no crash/block |

## Example (`.env`)

```dotenv
# Enable OTLP export of traces and metrics to a collector
OTEL_TRACES_EXPORTER=otlp
OTEL_METRICS_EXPORTER=otlp
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_EXPORTER_OTLP_PROTOCOL=grpc
OTEL_SERVICE_NAME=aidial-scheduler
# OTEL_LOGS_EXPORTER=otlp
# OTEL_LOG_LEVEL=ERROR
```
