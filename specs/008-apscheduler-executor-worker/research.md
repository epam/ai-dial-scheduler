# Phase 0 Research: APScheduler Executor Worker

All open questions from the Technical Context are resolved below.

## D1 — Source of run configuration (kwargs authority)

**Decision** (locked by user, 2026-06-11): The dispatch handler receives the task config as the
kwargs APScheduler passes it and validates those kwargs directly as `ScheduleProperties`:
`async def execute_scheduled_job(schedule_id: str, **kwargs: Any)`. The worker performs **no DB
lookup** for config and there is **no "missing row" failure path**. It never deserializes
`apscheduler_jobs.job_state`.

**Rationale**: The creator (`ScheduleService._register_job`) already passes
`{**properties, "schedule_id": …}` as APScheduler job kwargs, and `documentation/properties.md`
states `properties` equals "the actual kwargs passed to `scheduler.add_job()`". So the kwargs the
handler receives **are** the authoritative `ScheduleProperties`. Validating them in-place is the
simplest design (Constitution V), keeps the worker dispatch path DB-free, and matches the spec's
literal "validate … kwargs against the schema". This deliberately overrides the spec's
"async engine for the `schedules` lookup" / "missing row" wording (see spec update note).

**Alternatives considered**:
- *Re-read `schedules.kwargs` via the async engine, fail on missing row* — matches the spec's
  stack/error-contract wording but adds a DB round-trip and a failure path the user chose to drop;
  rejected per the locked decision.
- *Deserialize `apscheduler_jobs.job_state`* — explicitly forbidden by the spec.

## D2 — No schedule lookup in the dispatch path

**Decision**: The worker dispatch path does **not** read the database. No
`ScheduleRepository.get_by_id` is added and no async session is opened during a run. `schedule_id`
is used only for correlation/logging and to populate `ScheduleRunError`.

**Rationale**: Follows directly from D1 — config comes from kwargs, so the worker needs no
data-access layer for dispatch. Removes the only reason the worker would touch Postgres at
runtime, simplifying the worker and its tests (Constitution V).

**Alternatives considered**: Keeping an owner-agnostic `get_by_id` read for an existence check —
rejected; the user chose no missing-row path.

## D3 — `chat_completion` executor: endpoint & deployment derivation

**Decision**: From `properties.url` (a standard Azure/OpenAI-compatible deployment URL such as
`https://host/openai/deployments/{deployment}/chat/completions`):
- `azure_endpoint` = `scheme://netloc` (path discarded), via `urllib.parse.urlsplit`.
- `azure_deployment` = the path segment following `deployments/`.
- Instantiate `openai.AsyncAzureOpenAI(azure_endpoint=…, api_key=settings.dial_api_key,
  api_version=properties.api_version, azure_deployment=…)`.
- Call `client.chat.completions.create(**properties.payload, extra_headers=properties.extra_headers)`.
- `payload["stream"]` (defaulted to `False` by `ScheduleProperties` validation) decides
  single-response vs. streamed: a single response is logged as one record; a stream is
  `async for`-iterated and each chunk logged as it arrives.
- `properties.method` and `properties.api_version` rules are already enforced by
  `ScheduleProperties` (method must be unset, api_version allowed only here).

**Rationale**: Matches the spec's "deployment/model comes from url as part of a standard OpenAI
deployment URL" and "azure_endpoint = scheme + domain of url (path ignored)". Using
`azure_deployment` lets the SDK build the correct route; the deployment need not be repeated in
the payload.

**Alternatives considered**: Passing `model=` in the payload only — rejected; the spec says the
deployment comes from the URL, and Azure routing keys off the deployment path segment.

**Dependency**: add `openai` (pin a current 1.x release; confirm `AsyncAzureOpenAI` and
`extra_headers` kwarg support at the pinned version) to `pyproject.toml`.

## D4 — `rest` executor

**Decision**: `httpx.AsyncClient.request(method=properties.method, url=properties.url,
json=properties.payload, headers=merged)` where `merged = {API_KEY_HEADER: dial_api_key,
**properties.extra_headers}` so `extra_headers` win on collision. Apply
`settings.http_timeout_seconds`. Treat non-2xx as failure (`response.raise_for_status()` →
wrapped into `ScheduleRunError`). Log the response body + status as a single record.

**Rationale**: Direct mapping of the spec. Merge order (`{injected, **extra}`) implements the
"extra_headers win" collision rule.

## D5 — `sse` executor

**Decision**: Same request shape as `rest` plus `Accept: text/event-stream`, executed with
`httpx_sse.aconnect_sse(client, method, url, json=…, headers=…)`. Iterate
`event_source.aiter_sse()`; log each event as `{event.event: event.data}`; non-2xx before the
stream opens is a failure; stream end = connection close / iterator exhaustion.

**Rationale**: `httpx-sse` provides robust SSE framing over the shared `httpx.AsyncClient`,
avoiding hand-rolled `text/event-stream` parsing. Honors the spec's per-event logging shape
`{event_name: raw_string}`.

**Alternatives considered**: Manual line parsing of `response.aiter_lines()` — rejected
(error-prone framing, multi-line `data:` handling, comment lines).

**Dependency**: add `httpx-sse` (pin a current release compatible with `httpx==0.28.1`).

## D6 — Shared `httpx.AsyncClient` without module globals

**Decision**: Introduce `src/worker/context.py` with a frozen `WorkerContext` dataclass holding
`http_client: httpx.AsyncClient`, plus `set_worker_context`/`get_worker_context`/
`clear_worker_context` functions over a single private holder. `run_worker` creates the client
(with `settings.http_timeout_seconds`) and the context at startup and clears/closes it on
shutdown. The `rest`/`sse` executors fetch the client via `get_worker_context()`;
`chat_completion` builds a per-call `AsyncAzureOpenAI` (endpoint varies per run).

**Rationale**: APScheduler import-path handlers receive only kwargs, so process-level shared
state needs an accessor. A lifecycle-managed accessor (created and torn down with the worker) is
the worker analog of FastAPI `app.state` and satisfies "shared client" + "no uncontrolled
globals". Documented in the plan's Complexity Tracking.

**Alternatives considered**: per-run client (contradicts "shared", loses pooling); attaching the
client to the scheduler instance (handler doesn't receive the scheduler) — rejected.

## D7 — Heartbeat job placement

**Decision**: Register the worker's heartbeat in a dedicated **`MemoryJobStore`** (alias
`"local"`) added alongside the shared `"default"` `SQLAlchemyJobStore`. Add it with
`IntervalTrigger(seconds=settings.noop_interval_seconds)`, a constant id/name, and
`replace_existing=True`, in the `"local"` jobstore. The handler logs only the execution
timestamp and job name.

**Rationale**: Keeps the worker-owned heartbeat out of the shared persistent store (FR-002,
FR-003 — the worker registers exactly one job and never pollutes the creator's store). A memory
store is per-process, so multiple worker replicas each heartbeat independently without coordination.

**Alternatives considered**: Putting the heartbeat in the shared `default` store — rejected
(persists to the creator's DB, visible to the creator, risks replica collisions).

## D8 — Error surfacing via `EVENT_JOB_ERROR`

**Decision**: `ScheduleRunError(SchedulerError)` carries `schedule_id` and `run_id` attributes.
The dispatch handler wraps its body in `try/except`, re-raising any failure as
`ScheduleRunError`. `run_worker` attaches a listener via
`scheduler.add_listener(on_job_error, EVENT_JOB_ERROR)`; the listener reads `event.exception`
and, when it is a `ScheduleRunError`, logs it with both ids. The handler also binds
`run_id`/`schedule_id` into `structlog.contextvars` for the duration of the run so every executor
log line is correlated.

**Rationale**: Matches the spec's error contract (single standardized exception, surfaced by an
`EVENT_JOB_ERROR` listener, logging-only). Contextvars give correlation without threading ids
through every log call. An exception in one job does not stop others (APScheduler isolates job
executions).

## D9 — Telemetry for a non-API service

**Decision**: Call AiDialSdk's `init_telemetry` once in `run_worker` (after `configure_logging`),
guarded so the worker still starts if telemetry config is absent.

**Rationale**: Constitution II requires non-API services to use AiDialSdk's `init_telemetry` for
the same built-in tracing/metrics the API service gets via `DialApp`. Confirm the exact import
path/signature in `aidial_sdk` at implementation time.

**Alternatives considered**: No telemetry — rejected (violates Constitution II).

## D10 — New settings

**Decision**: Add to `Settings` (or a nested `WorkerSettings`): `noop_interval_seconds: int`
(heartbeat interval) and `http_timeout_seconds: float` (REST/SSE timeout). Reuse existing
`dial_api_key: SecretStr` and `dial_url`. Read `dial_api_key.get_secret_value()` only where the
header is built; never log it.

**Rationale**: FR-017/FR-018 require interval and timeout from settings, not hard-coded.
