# Code Review Report — 017 Observability Improvements (Pass 2)

- **spec-dir**: `specs/017-observability-improvements/`
- **Date**: 2026-06-17
- **Run mode**: Verification (follow-up to `code-review-1.md`)
- **Artifacts consulted**: `spec.md`, `plan.md`, `code-review-1.md`
- **Reviewer scope**: changes on branch `017-observability-improvements`

## Summary

**Approved.** All four improvements (I1–I4) and all four minor suggestions (M1–M4) from the first
pass have been correctly addressed or explicitly accepted. `ruff check`, `mypy src` (64 files), and
the feature's 24 unit tests all pass. No regressions and no new issues introduced by the fixes.

---

## Verified Issues (carried from Pass 1)

### I1 — Heartbeat worker job not wrapped in a span — **RESOLVED**
- **Status**: Resolved
- **File**: `src/worker/jobs.py:95-98`
- **Resolution**: `heartbeat()` now opens `_tracer.start_as_current_span("job:heartbeat")` and emits
  the `logger.info("heartbeat", ...)` inside that span, exactly as suggested. Worker heartbeat log
  records now carry `trace_id`/`span_id`, closing the FR-003/SC-001 gap. New regression test
  `TestHeartbeatSpan.test_heartbeat_starts_span` (`tests/unit/worker/test_job_spans.py:27-42`)
  asserts the span name. Verified passing.

### I2 — Dead `ctx is not None` guard — **RESOLVED**
- **Status**: Resolved
- **File**: `src/lib/utils/logging.py:17-18`
- **Resolution**: The redundant clause is gone; the processor now reads
  `ctx = trace.get_current_span().get_span_context()` followed by `if ctx.is_valid:`. The
  no-span path is still covered by `test_no_fields_added_when_no_active_span`.

### I3 — Tests used `unittest.mock` directly — **RESOLVED**
- **Status**: Resolved
- **File**: `tests/unit/api/v1/endpoints/test_health.py`
- **Resolution**: The `from unittest.mock import ...` line is removed (grep for `unittest` returns no
  matches). All mocks are now built via the `mocker` fixture (`mocker.AsyncMock()`,
  `mocker.MagicMock()`), consistent with `test_job_spans.py` and the CLAUDE.md mocking rule.

### I4 — Cancelled `SELECT 1` unwinds through the session on timeout — **ACCEPTED (Open, by design)**
- **Status**: Open (accepted)
- **File**: `src/api/v1/endpoints/health.py:20-32`
- **Note**: Accepted as-is per the maintainer's decision. The behavior is safe with asyncpg (the
  connection is discarded/returned on cancellation) and the broad `except Exception` still yields a
  `503`, matching `contracts/readiness.md` and FR-013. `test_returns_503_on_timeout` confirms the
  timeout path. No change required for correctness; only a potential pool-churn nuance under
  sustained DB hangs remains, which is acceptable for the lightweight-probe scope.

### M1 — Return `JSONResponse(...model_dump())` vs. model — **ACCEPTED (stylistic)**
- **Status**: Open (accepted)
- The current `JSONResponse(status_code=..., content=_READY.model_dump())` approach is correct and
  surfaces the schema in OpenAPI via `responses=`. Purely stylistic; left as-is.

### M2 — `_order_keys` lacked type hints — **RESOLVED**
- **Status**: Resolved
- **File**: `src/lib/utils/logging.py:26`
- **Resolution**: Signature is now
  `def _order_keys(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> OrderedDict:`,
  matching `_add_trace_span_ids`.

### M3 — `_add_trace_span_ids` docstring overstated injection — **RESOLVED**
- **Status**: Resolved
- **File**: `src/lib/utils/logging.py:16`
- **Resolution**: Docstring now reads "Inject trace_id and span_id when a valid OTel span is active.",
  accurately reflecting the `is_valid` guard.

### M4 — `opentelemetry` import was function-local — **RESOLVED**
- **Status**: Resolved
- **File**: `src/lib/utils/logging.py:8`
- **Resolution**: `from opentelemetry import trace` is now a module-level import; no per-record import
  lookup. No circular-import issue surfaced (mypy/ruff clean, tests pass).

---

## New Findings (regressions or fix-induced issues)

None. The fixes were surgical and introduced no new problems.

---

## Intent Alignment

- **FR-002 / FR-003 / SC-001**: Now fully met — both worker job paths (`execute_scheduled_job` and
  `heartbeat`) run inside spans, so 100% of worker log records within an active span carry IDs. The
  partial gap flagged in Pass 1 is closed.
- **FR-004**: Still met — `is_valid` guard with regression coverage.
- **All remaining FRs (FR-001, FR-005–FR-015)**: Unchanged from Pass 1 and remain met. No divergence
  from `spec.md` / `plan.md`.

---

## Checklist Pass

- **Correctness & Logic**: Pass — both job entry points spanned; readiness timeout path correct; no
  bare `except:`, no `assert`.
- **Type Safety**: Pass — `mypy src` clean (64 files); processor helpers now fully annotated.
- **Code Style & Conventions**: Pass — `ruff check src tests` clean; module-level imports;
  `pytest-mock` used exclusively in the touched test files; logger used, no `print()`.
- **Architecture & Design**: Pass — unchanged, correct layering.
- **Database & ORM**: Pass — SQLAlchemy Core `text("SELECT 1")` probe only; no new models/migrations.
- **Project Structure**: Pass — files in correct directories; tests mirror `src/`.
- **Testing**: Pass — 24 feature tests pass; heartbeat-span regression test added; no `unittest.mock`.
- **Security**: Pass — no secrets logged; `readiness_check_failed` logs only `str(exc)`.

## Verification Commands Run

- `uv run ruff check src tests` → All checks passed
- `uv run mypy src` → no issues found in 64 source files
- `uv run pytest tests/unit/api/v1/endpoints/test_health.py tests/unit/worker/test_job_spans.py tests/unit/lib/utils/test_logging.py tests/unit/lib/test_telemetry_config.py -q` → 24 passed
