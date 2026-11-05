# Tasks: Observability Improvements

**Input**: Design documents from `/specs/017-observability-improvements/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: INCLUDED — the project constitution mandates Test-First (TDD, NON-NEGOTIABLE). Test tasks
are written and confirmed failing before their implementation tasks.

**Organization**: Tasks are grouped by user story (US1=P1, US2=P2, US3=P2) for independent
implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 / US2 / US3 (Setup, Foundational, Polish have no story label)
- File paths are repo-root relative.

## Path Conventions

Single repo, dual entry point per the constitution: `src/api/` (API service), `src/worker/`
(APScheduler worker), shared `src/lib/`; tests mirror `src/` under `tests/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Install the telemetry capability both services and the log processor depend on.

- [x] T001 Change the dependency in `pyproject.toml` from `aidial-sdk==0.37.0` to `aidial-sdk[telemetry]==0.37.0`, run `uv sync`, and commit the updated `uv.lock`. Verify with `uv run python -c "import opentelemetry.trace; from aidial_sdk.telemetry.init import init_telemetry; print('telemetry OK')"`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Cross-story prerequisites that must exist before user-story work.

**⚠️ CRITICAL**: No user story work can begin until Phase 1 (the telemetry extra) is complete — the
structlog processor (US1) and OTLP export (US2) both require `opentelemetry.*` to import.

- No additional foundational tasks. The telemetry dependency in **T001** is the only shared
  prerequisite; all remaining work is partitioned cleanly across the three user stories.

**Checkpoint**: With T001 done, all user stories can proceed (in parallel if staffed).

---

## Phase 3: User Story 1 - Correlate logs with traces across API and worker (Priority: P1) 🎯 MVP

**Goal**: Every log record emitted within an active span — in both the API and the worker — carries
the active `trace_id` (32 hex) and `span_id` (16 hex); records emitted with no active span stay valid
with those fields absent.

**Independent Test**: Run the API and trigger a request; run the worker and let a job fire. Inspect
JSON log output — request/job logs include matching `trace_id`/`span_id`; startup logs (no span) are
valid JSON without them. Covered by the unit tests below.

### Tests for User Story 1 (write FIRST, confirm FAILING) ⚠️

- [x] T002 [P] [US1] Unit tests for the trace/span structlog processor in `tests/lib/utils/test_logging.py`: (a) with a valid active span, the rendered record includes `trace_id` as 32-char hex and `span_id` as 16-char hex matching the span context; (b) with no active span, neither field is present and the record renders without error; (c) a pre-existing `trace_id`/`span_id` key in the event dict is not overwritten.
- [x] T003 [P] [US1] Unit test for the worker per-job span wrapper in `tests/worker/test_app.py`: a job executed through the worker's dispatch runs within an active span, so a log emitted inside the job carries populated `trace_id`/`span_id`.

### Implementation for User Story 1

- [x] T004 [US1] Implement the trace/span processor in `src/lib/utils/logging.py`: add a processor that reads `opentelemetry.trace.get_current_span().get_span_context()` and, when valid (`trace_id != 0`), adds zero-padded hex `trace_id`/`span_id` to the event dict without overwriting existing keys. Register it in `configure_logging()`'s `structlog.configure(processors=[...])` chain AND in `LoggingConfig.get_config()`'s `ProcessorFormatter` `processors` and `foreign_pre_chain`, placed after `add_logger_name` and before `_order_keys`/`JSONRenderer`. (depends on T001)
- [x] T005 [US1] Wrap each job run in an OTel span in `src/worker/app.py`: obtain a module-level `tracer = opentelemetry.trace.get_tracer(__name__)` and execute the job callable inside `tracer.start_as_current_span("job:<job_id>")` so worker logs inherit the span context. (depends on T001)

**Checkpoint**: API and worker logs carry correlated trace/span IDs — MVP complete and independently testable.

---

## Phase 4: User Story 2 - Enable OTLP export through configuration (Priority: P2)

**Goal**: Operators enable/point OTLP export entirely via standard OTel environment variables, with
the variables documented in `.env.example` and the README; unset/unreachable config never crashes a service.

**Independent Test**: From the example config, set the OTLP vars at a reachable collector and confirm
telemetry arrives; unset them and confirm both services start normally; set an unreachable endpoint and
confirm services keep serving.

### Tests for User Story 2 (write FIRST, confirm FAILING) ⚠️

- [x] T006 [P] [US2] Unit test in `tests/lib/test_telemetry_config.py` asserting `aidial_sdk.telemetry.types.TelemetryConfig().is_noop()` is `True` when no `OTEL_*_EXPORTER` env vars are set, and that setting `OTEL_TRACES_EXPORTER=otlp` (via monkeypatched env + fresh import) produces a non-noop config — documenting the env-driven contract relied on by `.env.example`/README. (depends on T001)

### Implementation for User Story 2

- [x] T007 [P] [US2] Add the OTLP environment variables (commented, with example values) to `.env.example` per `contracts/otlp-config.md`: `OTEL_TRACES_EXPORTER`, `OTEL_METRICS_EXPORTER`, `OTEL_LOGS_EXPORTER`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_PROTOCOL`, `OTEL_SERVICE_NAME`, `OTEL_EXPORTER_PROMETHEUS_PORT`, `OTEL_LOG_LEVEL`.
- [x] T008 [P] [US2] Add an "Observability / OTLP" section to `README.md` documenting each OTLP env var, what it enables, an enable example, and the no-op-when-unset behavior (reference `contracts/otlp-config.md`).

**Checkpoint**: OTLP export is configurable and documented; US1 still works (independent).

---

## Phase 5: User Story 3 - Verify service readiness including database (Priority: P2)

**Goal**: The API exposes `GET /ready` that returns `200 {status: ready, database: ok}` when the
database is reachable and `503 {status: not_ready, database: unavailable}` when not, within a bounded
timeout, distinct from the existing liveness `/health`.

**Independent Test**: Call `/ready` with the DB up (expect 200), with the DB down (expect 503, service
stays up), and confirm it returns within ~`READINESS_DB_TIMEOUT_SECONDS` on a hanging DB. Covered by tests below.

### Tests for User Story 3 (write FIRST, confirm FAILING) ⚠️

- [x] T009 [P] [US3] Tests for the readiness endpoint in `tests/api/v1/endpoints/test_health.py`: (a) DB reachable → `200` with body `{"status":"ready","database":"ok"}`; (b) DB session raises a DB error → `503` with `{"status":"not_ready","database":"unavailable"}` and no exception escapes; (c) DB check exceeds the configured timeout → `503` (mock so `asyncio.wait_for` times out); (d) the existing `/health` liveness endpoint still returns `200`.

### Implementation for User Story 3

- [x] T010 [P] [US3] Create `ReadinessResponse` Pydantic v2 schema in `src/lib/schemas/health.py` with `status: Literal["ready","not_ready"]` and `database: Literal["ok","unavailable"]` (per `data-model.md`).
- [x] T011 [P] [US3] Add `readiness_db_timeout_seconds: float = 2.0` to the `Settings` model in `src/lib/settings.py`.
- [x] T012 [US3] Implement the `GET /ready` handler in `src/api/v1/endpoints/health.py`: acquire a session from the `Database` singleton sessionmaker, run `SELECT 1` wrapped in `asyncio.wait_for(..., timeout=settings.readiness_db_timeout_seconds)`, return `200` `ReadinessResponse(status="ready", database="ok")` on success and `503` `ReadinessResponse(status="not_ready", database="unavailable")` on any error/timeout (log via `logger`, never re-raise). (depends on T010, T011)
- [x] T013 [US3] Register the health router in `src/api/app.py` via `app.include_router(health_router)` with NO `/v1` prefix so the path is exactly `/ready`; leave the SDK liveness `/health` untouched. (depends on T012)

**Checkpoint**: `/ready` gates traffic on DB reachability; all three stories work independently.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Validation and quality gates across all stories.

- [ ] T014 Run the `specs/017-observability-improvements/quickstart.md` validation scenarios (sections 1–6) end to end.
- [x] T015 Run all quality gates and fix issues: `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`, `uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately. **T001 blocks everything else.**
- **Foundational (Phase 2)**: None beyond T001.
- **User Stories (Phase 3–5)**: All depend only on T001; otherwise independent of each other and may proceed in parallel.
- **Polish (Phase 6)**: Depends on all targeted stories being complete.

### User Story Dependencies

- **US1 (P1)**: Needs T001. No dependency on US2/US3.
- **US2 (P2)**: Needs T001. No dependency on US1/US3.
- **US3 (P2)**: Needs T001. No dependency on US1/US2.

### Within Each Story

- Tests first, confirmed failing, before implementation (TDD).
- US1: T002, T003 → T004, T005.
- US2: T006 → T007, T008.
- US3: T009 → (T010, T011) → T012 → T013.

### Parallel Opportunities

- After T001: US1, US2, US3 can be developed in parallel by different developers.
- `[P]` tasks touch different files: T002/T003; T007/T008; T010/T011; and the test-writing tasks across stories (T002, T003, T006, T009) can all be written together.

---

## Parallel Example: User Story 3

```bash
# Write the failing tests first:
Task: "Readiness endpoint tests in tests/api/v1/endpoints/test_health.py"   # T009

# Then the two independent building blocks in parallel:
Task: "ReadinessResponse schema in src/lib/schemas/health.py"               # T010
Task: "readiness_db_timeout_seconds setting in src/lib/settings.py"         # T011
# Then T012 (handler) → T013 (router registration)
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1: T001 (telemetry extra).
2. Phase 3: T002–T005 (log correlation, API + worker).
3. **STOP and VALIDATE**: confirm trace/span IDs in both services' logs.
4. Demo — log↔trace correlation is the highest-value capability.

### Incremental Delivery

1. T001 → foundation ready.
2. US1 → correlated logs (MVP).
3. US2 → OTLP export configurable + documented.
4. US3 → `/ready` readiness probe.
5. Polish → quickstart + quality gates.

---

## Notes

- `[P]` = different files, no incomplete dependencies.
- Verify each test fails before implementing it (constitution: Test-First).
- `__init__.py` files must stay empty (no re-exports) when creating new `tests/` packages.
- Commit after each task or logical group.
