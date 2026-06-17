"""Tests verifying TelemetryConfig responds to standard OTel environment variables.

These tests document the env-var contract relied on by .env.example and README.
They verify the DIAL SDK reads OTEL_*_EXPORTER vars to determine whether to enable
tracing, metrics, and log export — so operators can configure export without code changes.
"""

import sys


def _fresh_telemetry_config(monkeypatch, env_vars: dict[str, str]):
    """Return a fresh TelemetryConfig with the given env vars applied.

    aidial_sdk.telemetry.types reads env vars at module import time, so we reload
    the module after patching the env to get a fresh config object.
    """
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    for mod_name in list(sys.modules):
        if "aidial_sdk.telemetry" in mod_name:
            del sys.modules[mod_name]

    from aidial_sdk.telemetry.types import TelemetryConfig

    return TelemetryConfig()


class TestTelemetryConfigIsNoop:
    def test_is_noop_when_no_exporter_vars_set(self, monkeypatch):
        monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
        monkeypatch.delenv("OTEL_METRICS_EXPORTER", raising=False)
        monkeypatch.delenv("OTEL_LOGS_EXPORTER", raising=False)

        for mod_name in list(sys.modules):
            if "aidial_sdk.telemetry" in mod_name:
                del sys.modules[mod_name]

        from aidial_sdk.telemetry.types import TelemetryConfig

        config = TelemetryConfig()
        assert config.is_noop()


class TestTelemetryConfigOtlpTracesExporter:
    def test_tracing_enabled_when_otlp_traces_exporter_set(self, monkeypatch):
        config = _fresh_telemetry_config(monkeypatch, {"OTEL_TRACES_EXPORTER": "otlp"})

        assert config.tracing is not None
        assert config.tracing.otlp_export is True

    def test_tracing_none_when_traces_exporter_unset(self, monkeypatch):
        monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
        config = _fresh_telemetry_config(monkeypatch, {})

        assert config.tracing is None


class TestTelemetryConfigOtlpMetricsExporter:
    def test_metrics_enabled_when_otlp_metrics_exporter_set(self, monkeypatch):
        config = _fresh_telemetry_config(monkeypatch, {"OTEL_METRICS_EXPORTER": "otlp"})

        assert config.metrics is not None
        assert config.metrics.otlp_export is True

    def test_prometheus_enabled_when_prometheus_metrics_exporter_set(self, monkeypatch):
        config = _fresh_telemetry_config(monkeypatch, {"OTEL_METRICS_EXPORTER": "prometheus"})

        assert config.metrics is not None
        assert config.metrics.prometheus_export is True

    def test_metrics_none_when_exporter_unset(self, monkeypatch):
        monkeypatch.delenv("OTEL_METRICS_EXPORTER", raising=False)
        config = _fresh_telemetry_config(monkeypatch, {})

        assert config.metrics is None


class TestTelemetryConfigOtlpLogsExporter:
    def test_logs_enabled_when_otlp_logs_exporter_set(self, monkeypatch):
        config = _fresh_telemetry_config(monkeypatch, {"OTEL_LOGS_EXPORTER": "otlp"})

        assert config.logs is not None
        assert config.logs.otlp_export is True

    def test_logs_none_when_exporter_unset(self, monkeypatch):
        monkeypatch.delenv("OTEL_LOGS_EXPORTER", raising=False)
        config = _fresh_telemetry_config(monkeypatch, {})

        assert config.logs is None
