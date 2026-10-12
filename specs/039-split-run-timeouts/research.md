# Phase 0 Research: Split Run Execution Timeouts

All technical-context items are known from the existing codebase. The work below resolves the design questions and records why the first design for this feature was abandoned. No `NEEDS CLARIFICATION` markers remain.

## R1. Why one number cannot express both bounds

- **Decision**: Two independent fields. The existing `timeout` stays the absolute ceiling; a new `idle_timeout` bounds the gap between stream events.
- **Rationale**: Feature 020 R3 deliberately applied one value at two layers — `asyncio.wait_for` in the orchestrator (`src/worker/retry.py:95`) and the per-request HTTP/SDK timeout in each executor. Those layers measure different things: `wait_for` is absolute wall-clock and never resets, while a read timeout resets on every chunk. For a streaming target the read timeout can therefore never fire, leaving the ceiling as the only live control — and it fires on healthy work. The two semantics are irreducible to one number.
- **Alternatives considered**:
  - *Reinterpret `timeout` as the idle bound and add `max_duration` for the ceiling*: reads more naturally, but every deployed schedule with an explicit `timeout` would silently have its real ceiling raised to the new default. Rejected by the operator.
  - *Both fields new, `timeout` deprecated*: three timeout fields plus compatibility code. Rejected.
  - *Raise the single number*: tried in production (60 → 120 s); runs still failed non-deterministically. Rejected on evidence.

## R2. Why idle detection cannot live at the transport layer

- **Decision**: Idle is measured on **parsed stream events**, by wrapping the stream iterator's `anext()` in `asyncio.timeout(idle)`. It is **not** the httpx `read` timeout.
- **Rationale**: This reverses the first design for this feature. The DIAL SDK emits SSE heartbeat ping comments exactly when the response is idle. A ping is a socket read, so it resets a transport-level read timeout — meaning a hung application that still pings would never trip it. Using the read timeout as the idle bound would replace one ineffective mechanism with none at all. An SSE comment, by contrast, is discarded by the parser and never surfaces as an event, so an event-level bound measures real progress.
- **Shape**:

  ```python
  iterator = stream.__aiter__()
  while True:
      try:
          async with asyncio.timeout(idle_timeout):
              event = await anext(iterator)
      except StopAsyncIteration:
          break
  ```

- **Applies to**: `sse.py` (`aiter_sse()`), and `chat_completion.py` / `responses.py` on their streaming branch. Non-streaming paths have no iterator and are unaffected.
- **Alternatives considered**:
  - *httpx `read` timeout as the idle bound* (the original design): defeated by heartbeat. Rejected.
  - *Disable heartbeat platform-side*: not ours to control, and the fix must not depend on a remote configuration staying a particular way. Rejected.
  - *Set `stream: false` to avoid the problem*: the streaming loop looks pointless because nothing is forwarded, but it is the only source of a progress signal. Without it, "working for four minutes" and "hung" are indistinguishable. Verified against the SDK source — `get_final_completion()` merely drains an already-exhausted iterator, so the whole wait happens in the loop. Rejected.

## R3. The ceiling is an emergency backstop, and only streaming earns the long one

- **Decision**: The ceiling remains enforced by `asyncio.wait_for`. Its default is **3600 s for streaming targets** and **600 s for everything else**.
- **Revision (2026-10-12)**: The non-streaming default was first set to 300 s. It was raised to 600 s after the production stopgap (`fix/change-api-and-worker-timeout`) merged to `development`: that stopgap had raised `timeout_seconds`, then the run ceiling, to 600 s, so 300 s would have halved the budget non-streaming runs already had in production. See R11.
- **Rationale**: The operator rejected a non-resetting ceiling as the primary control — "this code kills the connection" — and a fixed small number on an open-ended agentic loop is an arbitrary value that will eventually cut live work. But removing the ceiling entirely is unsafe: with `job_max_instances: 1` (`src/lib/settings.py:129`), a run that never finishes blocks every subsequent firing of that schedule permanently and leaves its row in `in_progress` forever.
- **Why the split**: the hour has to be *earned*. A streaming target has `idle_timeout` watching it, so a stall is caught in a minute and the ceiling is a formality. A non-streaming target has no progress signal at all — a server computing for ten minutes and a server that has hung are identical at the transport layer — so total duration is its only instrument. Giving a blunt instrument an hour-long setting means a silently hung REST host would hold a worker slot, a pooled connection and an `in_progress` row for that hour; with `job_max_instances: 1` a five-minute schedule would lose roughly twelve firings. 600 s keeps that blast radius at a sixth of the streaming ceiling while preserving the budget production already runs with.
- **Alternatives considered**:
  - *One ceiling for everything at 3600 s*: this was the first version of this feature, and it silently removed the only stall detector non-streaming targets had, since their read phase also follows the ceiling. Rejected once the consequence was spelled out.
  - *No ceiling at all (`null` default)*: closest to the operator's objection, but exposes the `job_max_instances: 1` jam. Rejected.
  - *300 s for everything*: the original proposal. Unmeasured, and for streaming it would re-create the incident at a different scale. Rejected for streaming; adopted as the general bound at first, then raised to 600 s (see the revision note above).

## R4. The idle default is 60 s, and this one is measured

- **Decision**: `idle_timeout` defaults to **60 s**.
- **Rationale**: From operator-supplied trace `573e59c09029866fcd77627aa08836e9`: the longest silence between events across the whole trace was **21.80 s** (a thinking model), the second longest 7.49 s, and the remainder 2–3 s. 60 s carries a ×2.7 margin over the worst observed gap. This is the only number in the feature backed by data.
- **Note**: The platform's own per-tool bound is `DEFAULT_TOOL_TIMEOUT_SECONDS = 300.0`, i.e. the quickapp considers a single tool call silent for up to 300 s to be acceptable. The 21.8 s observation is what that looks like in practice for this workload; if a future target legitimately exceeds 60 s of silence, the per-schedule `idle_timeout` is the escape hatch, and the default can be revisited with new data.

## R5. Where the defaults live

- **Decision**: Three new fields on **both** `ApiHttpSettings` and `WorkerHttpSettings` (`src/lib/settings.py:83`, `:100`): `max_duration_seconds: float = 600.0`, `streaming_max_duration_seconds: float = 3600.0` and `idle_timeout_seconds: float = 60.0`. Six new environment variables, three per process. All three are carried to the executing process as a single `RunTimeouts` model on `RunContext`, not as loose floats.
- **Rationale**: The two processes must be able to diverge. On-demand runs execute inside the API process under `DRAIN_TIMEOUT_SECONDS = 30.0` (`src/lib/services/run_launcher.py:19`), so a ceiling above 30 s does not survive a rollout for that path; the worker has no such constraint. A single global setting could not express that. Grouping them into `RunTimeouts` keeps `BackgroundRunLauncher.__init__` within the three-parameter limit and removes the hazard of three adjacent positional floats.
- **Correctness note**: every one of the three must be threaded per-process, not just the ceiling. An earlier revision added `API_HTTP_IDLE_TIMEOUT_SECONDS` as a setting but resolved the idle bound from `worker_http` unconditionally, making that variable a silent no-op — defined, tested and documented, yet inert, which is worse than not existing. The `RunTimeouts` grouping exists partly so this cannot recur: the three travel together or not at all.
- **Alternatives considered**:
  - *One global `max_run_duration_seconds` on root `Settings`*: simpler, but the two processes could not diverge. Rejected by the operator.
  - *Reuse the existing `timeout_seconds` as the idle default* (the original design): no longer viable. `timeout_seconds` backs the shared httpx client defaults and every `DialClient` call (`src/worker/jobs.py:147-150`, `src/worker/conversation.py:206-209`), and the production mitigation has already moved it to 600. Overloading it a third time would couple the idle bound to DIAL metadata calls. Rejected; idle gets its own setting. `timeout_seconds` was later removed altogether — see R11.
  - *Separate floats on the launcher*: five positional parameters, breaking Principle V and inviting transposition. Rejected in favour of `RunTimeouts`.

## R6. Streaming versus non-streaming

- **Decision**: The idle bound applies only to streaming requests. The HTTP `read` timeout is the ceiling for non-streaming requests; for streaming requests the event-level bound is the real control.
- **Rationale**: A non-streaming call has no idle period — the entire wait for the response is one read — so applying a 60 s idle bound would cap `rest` and `stream: false` targets at 60 s and deny them any benefit from the larger ceiling. That silent regression is the reason for the branch.
- **Mechanics**: `sse` always streams; `rest` never does; `chat_completion` and `responses` already compute `stream_enabled = bool(create_kwargs.pop("stream", None))` (`chat_completion.py:36`, `responses.py:37`). The flag is always well-defined because `ScheduleProperties.validate_target_rules` injects `payload["stream"] = False` when absent (`src/lib/schemas/schedule.py:129-130`).

## R7. The discarded connect phase

- **Decision**: Build an explicit `httpx.Timeout` through one shared helper that preserves the client's connect, write and pool phases and sets only `read`.
- **Rationale**: `rest.py:28` and `sse.py:25` construct `httpx.Timeout(ctx.effective_timeout)` with a single positional argument, which expands to **all four** phases and discards the `connect_timeout_seconds: float = 5.0` configured on both clients. `chat_completion.py` and `responses.py` pass a bare float to the SDK with the same effect. Today that means an unreachable host hangs for the full bound instead of failing in 5 s; with a ceiling of an hour it would hang for an hour. Fixing it restores `*_HTTP_CONNECT_TIMEOUT_SECONDS` as meaningful configuration.
- **Verified**: httpx 0.28.1 exposes `AsyncClient.timeout` as a property, and `httpx.Timeout.__init__` accepts all four phases as keywords with `None` permitted. The helper is sound on the pinned version.

## R8. Cross-field validation

- **Decision**: Reject `idle_timeout > timeout` when both are set.
- **Rationale**: The idle bound could never fire before the ceiling, so the configuration is always a mistake. Failing at create/update is far cheaper than discovering it from a run log. Only validated when both are explicitly set — comparing an explicit field against a process default would make validity depend on deployment configuration.

## R9. Observability

- **Decision**: Both resolved values go into the attempt-start payload. The two expiries each get their own log event: the ceiling continues to surface as `attempt_timed_out` from the `asyncio.TimeoutError` branch in `retry.py`; an idle expiry is raised inside the executor as `StreamIdleTimeoutError` (a `ScheduleRunError` subclass) and logged by the orchestrator as `attempt_idle_timed_out`.
- **Rationale**: Principle II, and SC-006 — an operator must be able to tell which bound expired without reading source. A dedicated subclass rather than message-text matching is what makes the distinction mechanical: an idle expiry would otherwise be indistinguishable from any other transport failure, all of which log as `attempt_failed` with `reason=no_status_code`. Per-attempt detail stays logs-only, consistent with the deviation recorded in feature 020 R5.
- **Retry interaction**: neither expiry is retryable. Retry matches on a non-`None` `status_code` (`retry.py`) and a timeout carries none; the idle branch returns terminally before the retry logic is reached. Unchanged, and worth stating because it is a common misreading.

## R10. Persistence

- **Decision**: No migration.
- **Rationale**: Both fields live on `ScheduleProperties`, which is serialized into the existing `schedules.kwargs` JSONB, copied into each run's `schedule_snapshot`, and echoed through `ScheduleResponse.properties` as `dict[str, Any]`. Per feature 020 R1/R2.

## R11. Removing `timeout_seconds`

- **Decision**: Remove `timeout_seconds` from both `ApiHttpSettings` and `WorkerHttpSettings`, together with `RunContext.default_timeout` and the `default_timeout` parameter of `BackgroundRunLauncher`. `DialClient` no longer takes a timeout; its requests inherit the per-phase timeouts configured on the shared client.
- **Rationale**: Once the ceiling moved to `max_duration_seconds` / `streaming_max_duration_seconds`, `timeout_seconds` no longer bounded any run. What remained:
  - It was the positional default of `httpx.Timeout(...)` in `src/api/app.py`, `src/worker/clients.py` and `src/api/mcp/backing_client.py`. All four phases are passed explicitly at each site, so httpx ignored it (verified against httpx 0.28.1).
  - Its only live use was `DialClient`, which passed it as `timeout=` on every request. A bare float expands to all four phases, so it **replaced** the client's `connect=5 / read=60 / write=60 / pool=5` with 600 s each. That covered the user-info lookup in the API auth dependency, which runs on every API request. An unreachable DIAL could therefore hold each API request for up to ten minutes waiting to connect, instead of failing in 5 s. This is the R7 defect, one layer over.
  - DIAL metadata calls (user info, application status, OBO credentials, bucket, application details, conversation write) are short JSON requests. The client's per-phase timeouts are the right bound for them, so no dedicated setting replaces `timeout_seconds`. `request_external_service_signin` keeps its own `asyncio.timeout`, driven by `CLIENT_CHANNEL_SIGNIN_TIMEOUT_SECONDS`.
- **Compatibility**: both settings classes use `extra="ignore"`, so a deployment that still sets `API_HTTP_TIMEOUT_SECONDS` or `WORKER_HTTP_TIMEOUT_SECONDS` starts normally and the value is ignored. A unit test pins this.
- **Alternatives considered**:
  - *Keep it as a dedicated DIAL-call bound*: one more timeout-shaped setting with no consumer that the client's phases do not already serve. Rejected.
  - *Keep it at 600 s as merged by the stopgap*: preserves the ten-minute connect wait on every API request. Rejected.

## Resolved unknowns summary

| Question | Resolution |
|----------|------------|
| One field or two? | Two: `timeout` (ceiling, unchanged) + `idle_timeout` (new) |
| Where is idle measured? | On parsed events, `asyncio.timeout` around `anext()` — not the httpx read timeout |
| Ceiling default | 3600 s for streaming, 600 s otherwise — the long one is earned by having an idle detector |
| Idle default | 60 s, measured (×2.7 over worst observed gap of 21.8 s) |
| Where do defaults live? | Both `ApiHttpSettings` and `WorkerHttpSettings`, three new fields each, carried as one `RunTimeouts` |
| Non-streaming targets | Ceiling only, and a tighter one; no idle bound |
| Connect phase | Restored via a shared `httpx.Timeout` helper (verified against httpx 0.28.1) |
| Telling the two expiries apart | `StreamIdleTimeoutError` → `attempt_idle_timed_out`, vs `attempt_timed_out` |
| Migration needed? | No |
| What happens to `timeout_seconds`? | Removed; `DialClient` inherits the shared client's per-phase timeouts |
