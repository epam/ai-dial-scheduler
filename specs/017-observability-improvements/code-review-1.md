# Code Review Report — 017 Observability Improvements

- **spec-dir**: `specs/017-observability-improvements/`
- **Date**: 2026-06-17
- **Run mode**: Fresh Review
- **Artifacts consulted**: `spec.md`, `plan.md`, `data-model.md`, `contracts/readiness.md`
- **Reviewer scope**: changes on branch `017-observability-improvements` (working tree, uncommitted)

## Summary

**Approved with minor suggestions.** The implementation faithfully covers all three user stories and FR-001 through FR-015. Lint (`ruff`), type checking (`mypy`), and all 23 new tests pass. No critical bugs found. The findings below are reliability/coverage nuances and one project-convention deviation in the tests.

---

## Critical Issues

None.

---

## Improvements

### I1 — Heartbeat worker job is not wrapped in a span (FR-003 partial gap)
- **Status**: Open
- **File**: `src/worker/jobs.py:95-97` (`heartbeat`), `src/worker/app.py:65-71` (job registration)
- **Problem**: Only `execute_scheduled_job` is wrapped in `_tracer.start_as_current_span(...)`. The recurring `heartbeat` job runs outside any span, so its log record (`logger.info("heartbeat", ...)`) carries no `trace_id`/`span_id`. FR-003/SC-001 state that *every* log record emitted by the worker *while a span is active* must include the IDs. The heartbeat is technically compliant with FR-004 (no span ⇒ IDs absent), but it is the most frequent worker log line and the one an operator first reaches for when checking worker health, so it is the least useful to leave un-correlated. The plan (plan.md line 99, "MODIFY: wrap each job run in an OTel span") implies *each* job, not only scheduled executions.
- **Suggested fix**: Wrap the heartbeat in a span as well, or factor a small decorator/helper so every APScheduler-dispatched callable runs inside a span:
  ```python
  async def heartbeat() -> None:
      with _tracer.start_as_current_span("job:heartbeat"):
          clear_contextvars()
          logger.info("heartbeat", job_name=HEARTBEAT_JOB_NAME, executed_at=datetime.now(UTC).isoformat())
  ```
- **Note**: If leaving heartbeat un-spanned is an intentional scope decision, record it explicitly in the spec/plan so the gap is traceable.

### I2 — Dead `ctx is not None` guard in the trace processor
- **Status**: Open
- **File**: `src/lib/utils/logging.py:18-19`
- **Problem**: `trace.get_current_span()` never returns `None` (it returns `INVALID_SPAN` when no span is active), and `get_span_context()` always returns a `SpanContext` (the invalid one has `is_valid == False`). The `ctx is not None` check is therefore always true and is dead code. The real and sufficient guard is `ctx.is_valid`. Harmless, but misleading about the API contract.
- **Suggested fix**: Drop the redundant clause:
  ```python
  ctx = trace.get_current_span().get_span_context()
  if ctx.is_valid:
      ...
  ```

### I3 — Tests use `unittest.mock` directly instead of `pytest-mock` (convention deviation)
- **Status**: Open
- **File**: `tests/unit/api/v1/endpoints/test_health.py:4` (`from unittest.mock import AsyncMock, MagicMock`)
- **Problem**: CLAUDE.md and the review checklist require `pytest-mock` for mocking. The other new test files correctly use the `mocker` fixture; `test_health.py` mixes `mocker.patch(...)` with bare `unittest.mock.AsyncMock`/`MagicMock`. Inconsistent and against the stated rule.
- **Suggested fix**: Build the mocks via the `mocker` fixture — `mocker.AsyncMock()` / `mocker.MagicMock()` — and drop the `unittest.mock` import, matching `test_job_spans.py`.

### I4 — Readiness timeout path leaves the cancelled `SELECT 1` to unwind through the session
- **Status**: Open
- **File**: `src/api/v1/endpoints/health.py:20-32`
- **Problem**: On timeout, `asyncio.wait_for` cancels `_query()` mid-`session.execute`. The `async with ...sessionmaker()` block then runs its `__aexit__` (rollback/close) during cancellation against a connection that may still be mid-statement. With asyncpg this is generally safe (the connection is discarded/returned), and the broad `except Exception` correctly yields `503`, so behavior matches the contract (table row 3) and the test passes. Flagging only as a reliability nuance: under sustained DB hangs, repeated cancelled-mid-statement connections can churn the pool. Acceptable for the stated lightweight-probe scope, but worth a follow-up if probe frequency is high.
- **Suggested fix (optional)**: Acquire a raw connection with an explicit per-statement timeout, or document that pool churn under prolonged DB hangs is accepted. No change required for correctness.

---

## Minor Suggestions

- **M1** — `src/api/v1/endpoints/health.py:42`: the endpoint returns `JSONResponse(...model_dump())` and declares the schema only via `responses=`. This is correct and surfaces in OpenAPI, but returning the `ReadinessResponse` model directly with an explicit `status_code` via a small branch (or `Response`) would let FastAPI handle serialization and keep the contract single-sourced. Current approach is fine; purely stylistic.
- **M2** — `src/lib/utils/logging.py:27` (`_order_keys`): this pre-existing helper lacks type hints and a docstring (params typed as `_, __`). Not part of this change set, but since the file was touched, consider adding `event_dict: MutableMapping[str, Any]` typing for consistency with the new `_add_trace_span_ids`.
- **M3** — `src/lib/utils/logging.py:14`: the `_add_trace_span_ids` docstring says "into every log record" — it actually injects only when a span is active. Tightening the wording ("when a valid span is active") matches `data-model.md` rules and avoids implying unconditional injection.
- **M4** — The local `from opentelemetry import trace` import inside `_add_trace_span_ids` (line 16) runs on every log record. The lookup is cheap (module already imported) and avoids an import-time dependency, so this is acceptable; a module-level import would be marginally cleaner if no circular-import concern exists.

---

## Intent Alignment

- **FR-001 (telemetry extra)**: Met — `pyproject.toml` pins `aidial-sdk[telemetry]==0.37.0`.
- **FR-002 (API logs carry IDs)**: Met — processor added to both the `dictConfig` formatter chains and `structlog.configure`; API spans come from `DIALApp` FastAPI instrumentation.
- **FR-003 (worker logs carry IDs)**: Met for scheduled jobs; **partial** for the heartbeat job (see I1).
- **FR-004 (valid records with no span)**: Met — `is_valid` guard; test `test_no_fields_added_when_no_active_span` confirms.
- **FR-005 (same JSON pipeline)**: Met — IDs flow through the existing structlog JSON renderer; non-overwrite rule enforced and tested (I-fields not clobbered).
- **FR-006–FR-009 (OTLP via env, docs, no-op default)**: Met — `.env.example` + README document the standard `OTEL_*` vars; `test_telemetry_config.py` proves no-op when unset and enablement when set.
- **FR-010 (unreachable endpoint non-fatal)**: Met by design — worker wraps `init_telemetry` in try/except (`worker/app.py:78`); SDK batch exporters are best-effort. Not independently unit-tested (acceptable — it is SDK behavior).
- **FR-011/FR-012/FR-014/FR-015 (`/ready`, 200/503, no crash, liveness preserved)**: Met — root-mounted `/ready` distinct from DIALApp `/health`; returns `ReadinessResponse`; broad `except` guarantees no crash. Matches `contracts/readiness.md`.
- **FR-013 (bounded check)**: Met — `asyncio.wait_for(..., timeout=settings.readiness_db_timeout_seconds)`; timeout→503 covered by `test_returns_503_on_timeout`.
- **No raw SQL**: `text("SELECT 1")` is SQLAlchemy Core, the standard idiom for a liveness probe — compliant with the ORM-only rule.
- **Settings**: `readiness_db_timeout_seconds: float = 2.0` matches `data-model.md`.

No divergence from the spec/plan beyond the heartbeat-span nuance (I1).

---

## Checklist Pass

- **Correctness & Logic**: Pass — no off-by-one or conditional errors; async I/O correct; no `assert` in production; exceptions handled without bare `except:`.
- **Type Safety**: Pass — `mypy src` clean on all four changed source files; Pydantic v2 `Literal` fields correct.
- **Code Style & Conventions**: Pass — `ruff check` clean; line length, naming, import order all conform; no `print()`; logger used throughout.
- **Architecture & Design**: Pass — schema in `src/lib/schemas`, endpoint in `src/api`, span wrapper in `src/worker`; reuses the `Database` singleton; no speculative abstraction.
- **Database & ORM**: Pass — SQLAlchemy only, no new models/migrations (none needed).
- **Project Structure**: Pass — files in correct directories; tests mirror `src/`; `__init__.py` files remain empty.
- **Testing**: Pass with one deviation (I3) — happy and error/timeout paths covered for `/ready`; span-active/absent and non-overwrite cases covered for the processor; env-var contract covered.
- **Security**: Pass — no secrets logged; `readiness_check_failed` logs only `str(exc)` with no credentials; readiness endpoint exposes no sensitive data; no `.env`/secrets files read.
