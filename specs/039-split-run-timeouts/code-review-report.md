# Code Review Report — 039-split-run-timeouts

**Reviewed**: commit `fdba489` ("Split run execution timeouts into a ceiling and an idle bound")
**Base**: `origin/development` (`b10e7c6`)
**Scope**: `git diff origin/development...HEAD` plus the surrounding unchanged code the change interacts with
(`src/worker/jobs.py`, `src/worker/clients.py`, `src/worker/app.py`, `src/lib/services/run_launcher.py`,
the pinned httpx/openai sources, `tests/integration/`)
**Artifacts consulted**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`,
`contracts/schedule-properties.md`, `documentation/properties.md`, `CLAUDE.md`
**Reviewer**: code-review-agent
**Date**: 2026-10-09
**Mode**: Fresh review

## Verdict

**Requires changes before merge.** The central mechanism is right. Splitting one number into a
non-resetting ceiling and an event-level idle bound is the correct diagnosis, and
`iterate_with_idle_timeout` is a careful implementation of it: the `asyncio.timeout` scope stops at
`anext()` and does not span the `yield`, `StopAsyncIteration` is caught rather than allowed to escape
the async-generator body, and an outer `asyncio.wait_for` cancellation is **not** misreported as an
idle expiry (verified empirically, see Gates). `build_timeout` is correct against the pinned httpx and
resolves the plan's open verification item. The commit's reasoning about heartbeat pings defeating a
transport-level bound is sound and I could not find a hole in it.

Three things block the merge:

1. **One integration test fails** with a `TypeError` — a call site the commit missed. Proven by running
   the suite against a real PostgreSQL; it is invisible locally because integration tests skip without
   `TEST_DB_URL`.
2. **Non-streaming targets silently lost their only stall detector.** The ceiling rose 60×, the read
   timeout rose with it, and nothing replaced the 60 s bound they previously had. The spec reasons
   exclusively about streaming and never notices this.
3. **`API_HTTP_IDLE_TIMEOUT_SECONDS` is dead configuration.** It is added, tested and documented, but
   never read — the API process resolves the idle bound from the *worker's* setting. This contradicts
   FR-005, research R5 and plan decision 5.

Findings are ranked most severe first. Each states whether I verified it by execution/source or inferred it.

### Gates re-run (this reviewer, not inherited)

| Gate | Result |
|------|--------|
| `uv run pytest tests/unit` | **1021 passed** (14.1 s) |
| `uv run pytest tests/integration` with `TEST_DB_URL` → throwaway PostgreSQL 16 container | **1 failed, 206 passed** (see S1) |
| `uv run ruff check src tests` | All checks passed |
| `uv run ruff format --check src tests` | 178 files already formatted |
| `uv run mypy src` | Success, no issues in 85 source files |

Container: `postgres:16` as `pg-review-039` on port 55439, **removed after the run** (verified: no matching
container remains). No source or test file was modified by this review.

Behavioural probes run against the project venv (Python 3.14, httpx 0.28.1, openai 1.109.1), reproducing
`iterate_with_idle_timeout`'s loop verbatim:

| Probe | Result |
|-------|--------|
| Outer `wait_for` cancels while inside `asyncio.timeout` | propagates as the outer `TimeoutError`, **not** misreported as idle — correct |
| Target raises builtin `TimeoutError` | **misreported as an idle expiry** (S4) |
| Target raises `socket.timeout` (an alias of `TimeoutError` since 3.10) | **misreported as an idle expiry** (S4) |
| `aiter(obj)` where `__aiter__` is an `async def` generator function (the openai SDK shape) | works, yields correctly |
| Inner generator's `finally` when the idle bound fires | runs — no leaked generator |

---

## S1 — Blocking: `tests/integration/test_schedule_run_now.py:313` raises `TypeError`; CI will fail

- **Severity**: Blocking
- **Confidence**: Certain — reproduced
- **File**: `tests/integration/test_schedule_run_now.py:313`

`BackgroundRunLauncher.__init__` gained a third **required** positional parameter
(`src/lib/services/run_launcher.py:37`). The commit updated the unit fixture
(`tests/unit/lib/services/test_run_launcher.py:28`) but not the integration call site:

```python
real_launcher = BackgroundRunLauncher(http_client, default_timeout=30.0)
```

Running the integration suite against a real database:

```
E  TypeError: BackgroundRunLauncher.__init__() missing 1 required positional argument: 'default_max_duration'
tests/integration/test_schedule_run_now.py:313: TypeError
FAILED tests/integration/test_schedule_run_now.py::TestBackgroundCompletion::test_run_reaches_a_terminal_status_after_the_response
1 failed, 206 passed
```

**Why it was not caught**: `tests/integration/conftest.py:30-31` skips the whole package when `TEST_DB_URL`
is unset, so a local `uv run pytest` reports 14 skipped and looks clean. Any CI job that provisions a
database fails.

**Fix**:

```python
real_launcher = BackgroundRunLauncher(http_client, default_timeout=30.0, default_max_duration=900.0)
```

---

## S2 — Non-streaming targets silently lost stall detection: 60 s → 3600 s with nothing in its place

- **Severity**: High (design defect, not just a bug)
- **Confidence**: High — read paths verified in source; the operational consequence is reasoned, not measured
- **Files**: `src/worker/retry.py:77-86`, `src/worker/executors/rest.py:28-32`,
  `src/worker/executors/chat_completion.py:39-50`, `src/worker/executors/responses.py:40-51`
- **Spec anchor**: FR-003, FR-007, spec.md *Edge Cases* → "Non-streaming targets have no idle period"

The ceiling default moved from `timeout_seconds` (60 s) to `max_duration_seconds` (3600 s), and
`build_timeout` propagates the ceiling into the httpx `read` phase for **every** executor. For a
non-streaming target this is the *only* bound that exists — there is no idle wrapper on those paths, by
design (FR-007).

Concretely, for a `rest` schedule with no explicit `timeout`:

| Bound | Before `fdba489` | After |
|---|---|---|
| `asyncio.wait_for` ceiling | 60 s | **3600 s** |
| httpx `read` | 60 s | **3600 s** |
| httpx `connect` | 60 s (clobbered) | 5 s (fixed — genuine improvement) |
| httpx `pool` | 60 s (clobbered) | 5 s (fixed) |

The same applies to `chat_completion`/`responses` with `payload.stream: false`, and to the SSE
*response-header* read before the first event arrives.

**Failure scenario**: a `rest` schedule targets a host that completes the TCP handshake, accepts the
request, and then never writes a response byte — a load balancer with a dead backend, a blackholed
route, a deadlocked worker thread. Today that attempt is abandoned at 60 s and the schedule fires
normally next time. After this change the attempt holds a worker slot, a pooled connection and an
`in_progress` row for a full hour. Because `job_max_instances: 1` (`src/lib/settings.py:137`), every
firing during that hour is rejected, and because `job_misfire_grace_time: 60` (`:138`), those firings
are then discarded rather than coalesced — a 5-minute cron schedule loses ~12 runs with no run rows and
no error, only an APScheduler log line. The ceiling the commit message cites as the protection against
exactly this jam is now 60× too loose to provide it for non-streaming targets.

The spec's edge-case note correctly argues the *idle* bound must not be applied to non-streaming calls.
It never notices the converse: raising the ceiling removes the bound those calls were actually relying
on. The rationale in research R3 ("an hour is long enough that nothing legitimate reaches it") is an
argument about *healthy* runs; it is silent on how long a *broken* run should be allowed to occupy a slot.

**Suggested fix** — pick one, but pick one deliberately rather than leaving it implicit:

- *Preferred*: give non-streaming targets their own ceiling default, e.g. resolve the ceiling as
  `max_duration_seconds` only for streaming targets and keep something closer to the old bound (or a new
  `*_HTTP_NON_STREAMING_MAX_DURATION_SECONDS`) otherwise. `stream_enabled` is already computed in both SDK
  executors and is statically known for `rest`/`sse`.
- *Cheaper*: keep the read phase at `read_timeout_seconds` (60 s) for non-streaming requests instead of
  raising it to the ceiling, so a silent server still fails in a minute while the ceiling stays a backstop.
  This is what plan.md's structure tree already describes as "a read-timeout branch in each of the four
  executors" — see S10.
- *Minimum*: if the 60× loosening is intentional for all target types, say so in spec.md and record the
  `job_max_instances`/`misfire_grace_time` consequence, because right now no document acknowledges it.

---

## S3 — `API_HTTP_IDLE_TIMEOUT_SECONDS` is never read; the API process uses the worker's idle setting

- **Severity**: High
- **Confidence**: Certain — verified by exhaustive grep and by reading the single resolution site
- **Files**: `src/worker/retry.py:82-84`, `src/lib/settings.py:95`, `src/lib/schemas/run.py:31-36`
- **Spec anchor**: **FR-005 violated** — "The ceiling and the idle bound MUST *each* be configurable
  independently for the API process and the worker process"; also research R5 ("the two processes must be
  able to diverge") and plan decision 5

The ceiling has a context-level step and resolves per process correctly:

```python
default_max_duration = (
    ctx.default_max_duration if ctx.default_max_duration is not None else worker_http.max_duration_seconds
)
```

The idle bound does not:

```python
effective_idle_timeout = (
    properties.idle_timeout if properties.idle_timeout is not None else worker_http.idle_timeout_seconds
)
```

There is no `default_idle_timeout` on `RunContext`, nothing is threaded through `BackgroundRunLauncher`,
and `grep -rn "idle_timeout_seconds" src/` returns exactly two definitions in `settings.py` and this one
read of `worker_http`. `ApiHttpSettings.idle_timeout_seconds` is defined, covered by two unit tests
(`tests/unit/lib/test_settings.py:174,192`), and documented — and has no effect on anything.

**Failure scenario**: an operator finds an on-demand (`POST /schedules/{id}/run`) execution of a slow
streaming target being cut at 60 s of silence, and sets `API_HTTP_IDLE_TIMEOUT_SECONDS=180` on the API
deployment. Nothing changes. The API process instantiates `WorkerHttpSettings`, which in an API pod has
no `WORKER_HTTP_*` variables set, so it silently uses the 60.0 default. To fix the API process the
operator must set `WORKER_HTTP_IDLE_TIMEOUT_SECONDS` on the **API** deployment — the opposite of what the
documentation tells them (see S10). The tests pass because they assert the setting *parses*, never that it
*reaches* a run.

Note this is self-inflicted at the design level: `data-model.md` §"Resolution order" openly states the idle
bound "reads from settings directly in the worker path" and defers a context field to later — so the
implementation matches `data-model.md` while contradicting `spec.md`, `research.md` and `plan.md`.

**Fix** — mirror the ceiling exactly:

```python
# src/lib/schemas/run.py
default_idle_timeout: float | None = None

# src/worker/retry.py
default_idle = ctx.default_idle_timeout if ctx.default_idle_timeout is not None else worker_http.idle_timeout_seconds
effective_idle_timeout = properties.idle_timeout if properties.idle_timeout is not None else default_idle
```

plus the `BackgroundRunLauncher` parameter and the `src/api/app.py` wiring. If instead the decision is that
the API should not diverge, then **remove** `ApiHttpSettings.idle_timeout_seconds` and its tests, and amend
FR-005 — a setting that exists and does nothing is worse than no setting.

---

## S4 — `except TimeoutError` converts a genuine target `TimeoutError` into a false "stream produced no event"

- **Severity**: Medium
- **Confidence**: Certain for the conversion (reproduced); Medium on how often the path is reachable in production
- **File**: `src/worker/executors/base.py:69-74`

```python
except TimeoutError as exc:
    raise ScheduleRunError(f"stream produced no event for {idle_timeout}s", ...)
```

This catches *any* builtin `TimeoutError` surfacing from `anext()`, not only the one `asyncio.timeout`
raises on its own expiry. Since Python 3.10, `socket.timeout is TimeoutError` and `TimeoutError` is an
`OSError` subclass, so an `ETIMEDOUT` from the socket layer, or any nested `asyncio.wait_for`/`asyncio.timeout`
inside the stream, lands in this branch. Probe result:

```
T2 target TimeoutError  -> MISREPORTED as idle
T3 socket.timeout       -> MISREPORTED as idle  | socket.timeout is TimeoutError: True
```

This is mitigated in the common case — httpx maps transport timeouts to `httpx.ReadTimeout`
(`TransportError`), which passes through untouched — so I rate the production reachability Medium, not High.
But the consequence is an operator chasing a phantom stall in a target that actually reported a timeout,
which directly undercuts SC-006 ("an operator can tell which bound expired").

**Fix** — only claim an idle expiry when the context manager actually expired:

```python
while True:
    try:
        async with asyncio.timeout(idle_timeout) as scope:
            item = await anext(iterator)
    except StopAsyncIteration:
        return
    except TimeoutError as exc:
        if not scope.expired():
            raise
        raise ScheduleRunError(
            f"stream produced no event for {idle_timeout}s",
            schedule_id=ctx.schedule_id,
            run_id=ctx.run_id,
        ) from exc
    yield item
```

(`asyncio.Timeout.expired()` is available on 3.11+; the project targets 3.14.)

---

## S5 — Operational blast radius of a 60× ceiling: pool exhaustion and the 30 s drain

- **Severity**: Medium
- **Confidence**: Medium — settings and call sites verified; the capacity arithmetic is reasoned, not load-tested
- **Files**: `src/lib/settings.py:97-98,118-119`, `src/worker/clients.py:24-36`,
  `src/lib/services/run_launcher.py:19,77`, `src/api/app.py:59`

Three separate things get worse together:

**(a) Connection pool.** `max_connections: 200`, `max_keepalive_connections: 20`, `pool_timeout_seconds: 5.0`.
With a 60 s ceiling, a hung attempt returned its connection within a minute; the pool self-healed. With a
3600 s ceiling the steady-state count of connections held by stuck attempts is bounded only by how many
schedules can be concurrently broken. Once 200 are held, every *new* request fails
`PoolTimeout` after 5 s → `httpx.TransportError` → `ScheduleRunError` with `status_code=None` → **terminal,
never retried** (`src/worker/retry.py:122-125`). So one misbehaving upstream can convert into blanket,
non-retryable failures across unrelated schedules. I did not load-test this; the mechanism is read from
source and I rate it plausible rather than certain.

**(b) `DRAIN_TIMEOUT_SECONDS = 30.0`.** On-demand runs execute in the API process and are drained for 30 s on
shutdown, then marked errored and cancelled. The ceiling for that path is now `API_HTTP_MAX_DURATION_SECONDS`
= 3600 s — a 120× mismatch. Any on-demand streaming run that outlives 30 s dies on every rollout, HPA
scale-down or pod eviction. `spec.md` *Edge Cases* and `quickstart.md` both acknowledge this honestly, so it
is a known limitation rather than an oversight — but shipping an hour-long ceiling on a path with a 30 s
drain means the feature's headline capability is effectively unavailable to `POST /schedules/{id}/run`.
Either raise the drain for that path or state plainly in the docs that long runs must use the cron path.

**(c) `job_max_instances: 1` + `job_misfire_grace_time: 60`.** Covered in S2; the interaction is the same for
streaming targets, just less likely to trigger because the idle bound usually fires first.

**Suggestion**: nothing here is a correctness bug, but the hour-long ceiling deserves one of (i) a lower
default, (ii) an explicit capacity note in `plan.md` tying `max_connections` to the worst-case number of
concurrently stuck attempts, or (iii) raising `DRAIN_TIMEOUT_SECONDS` toward the API ceiling.

---

## S6 — On `chat_completion`/`responses` the idle bound counts *SDK-filtered* events, which may not equal progress

- **Severity**: Medium
- **Confidence**: Medium — the SDK filter is verified in source; whether DIAL emits enough filtered-only
  traffic to trip it is **inferred, not measured**
- **Files**: `src/worker/executors/chat_completion.py:45`, `src/worker/executors/responses.py:46`;
  `openai/lib/streaming/chat/_completions.py:237-243,764-770` (openai 1.109.1)

The design argument is "count parsed events, not socket reads." For `sse.py` that is exactly what happens —
`aiter_sse()` yields one item per SSE event and discards comments. For the two SDK executors the wrapped
iterator is one layer higher, and that layer drops chunks:

```python
async for sse_event in self._raw_stream:
    if not _is_valid_chat_completion_chunk_weak(sse_event):
        continue              # <-- chunk arrived, no event reaches our wrapper
    events_to_fire = self._state.handle_chunk(sse_event)
```

with

```python
def _is_valid_chat_completion_chunk_weak(sse_event) -> bool:
    # ... broken by the Azure OpenAI in case of Asynchronous Filter enabled.
    return sse_event.object == "chat.completion.chunk"
```

So a chunk whose `object` is anything else — the SDK's own comment names Azure's Asynchronous Filter events,
and DIAL is Azure-shaped — is swallowed. If a target emits a run of such frames for longer than
`idle_timeout`, the wrapper sees silence while the stream is genuinely working, and the attempt is killed with
a message asserting the opposite.

This is the inverse of the heartbeat problem the feature exists to solve, one layer up, and it is the one
place where SSE and the SDK paths are **not** equivalent despite the code and the docs treating them as such.
I could not confirm whether DIAL emits such frames in practice — the operator trace in research R4 is
summarised as gaps, not frame types — so I am flagging it as a risk to check, not a confirmed defect.

**Suggestion**: either wrap `stream._raw_stream` (chunk level) rather than `aiter(stream)` (filtered-event
level) for the SDK executors, or add a note to research R2 recording that the SDK's `object` filter sits
between the socket and the idle bound and why that is acceptable. The first is more faithful to "parsed
events, not socket reads" while still being above the SSE-comment layer.

---

## S7 — The cross-field rule is defensible, but the configurations it lets through are undocumented at the point of use

- **Severity**: Medium-Low
- **Confidence**: High — verified in source and tests
- **Files**: `src/lib/schemas/schedule.py:139-145`; `contracts/schedule-properties.md`
- **Spec anchor**: FR-002, FR-009

The "only when both are explicit" scoping (R8) is **correct** and I would not change it — comparing a stored
value against a process default would make a schedule's validity depend on which pod validated it. Two
consequences follow that nothing warns about at runtime:

**(a) An explicit `timeout` below the idle default makes the idle bound inert.** `timeout: 30` with
`idle_timeout` unset resolves to ceiling 30 s / idle 60 s. The idle bound can never fire; the ceiling fires
first — precisely the pre-feature behaviour the incident describes. Benign (nothing breaks that was not
already broken), but a schedule in exactly the failing configuration passes validation with no signal.

**(b) Existing schedules with an explicit `timeout` get no benefit at all.** FR-002 mandates this, and
`contracts/schedule-properties.md` §"Backward compatibility" states it clearly. Worth restating here because
it means **the fix is not automatic for the affected population**: any schedule already carrying
`"timeout": 60` (including anything created after the production 60 → 120 mitigation) keeps dying at its own
ceiling until someone edits it. The contract also correctly flags the converse hazard — a streaming schedule
with `timeout: 600` newly acquires a 60 s idle bound it never had, so a target with legitimately long gaps
*starts* failing after this deploy.

**Suggestion**: no code change required for correctness, but (i) log a one-line warning at resolution time
when `effective_idle_timeout >= effective_timeout`, so case (a) is visible in the attempt-start line where
both values already appear, and (ii) add a migration note to `quickstart.md` listing the SQL to find
schedules with an explicit `timeout` so the affected population can be identified before the deploy.

---

## S8 — Timing-based tests are flaky under load: margins as small as 20 ms

- **Severity**: Medium-Low
- **Confidence**: High — margins read directly from the tests; flakiness is inherent, not observed in this run
- **Files**: `tests/unit/worker/executors/test_base.py:55,67`, `tests/unit/worker/executors/test_sse.py:287,307,320`

| Test | Gap | Bound | Margin |
|---|---|---|---|
| `test_bound_resets_between_items` | 0.03 s ×3 | 0.05 s | **20 ms** |
| `test_total_duration_may_exceed_the_bound` | 0.04 s ×3 | 0.10 s | 60 ms |
| `test_healthy_long_stream_is_not_cut_off` | 0.03 s ×3 | 0.05 s | **20 ms** |
| `test_no_idle_bound_leaves_the_stream_unbounded` | 0.08 s | — | n/a |

A 20 ms margin is within normal event-loop scheduling jitter on a loaded CI runner or a cold-started
container; `asyncio.sleep(0.03)` routinely wakes at 0.035–0.05 s under contention. These will go red
intermittently, and an intermittently-red idle-timeout test is the kind that gets quarantined, which would
remove the feature's only real coverage.

The negative tests (0.5 s gap vs 0.05 s bound, 10× margin) are fine. It is only the *positive* ones — the
"must not be cut off" assertions — that are tight, and those are the ones that matter most (SC-002).

**Fix**: widen the ratio rather than the absolute values, e.g. gaps of 0.01 s against a 0.5 s bound. The
tests assert ordering of two durations, not real-time behaviour, so a 50× ratio costs nothing in runtime and
removes the jitter sensitivity entirely.

---

## S9 — The riskiest code path has no test: the idle wrapper is never exercised against a real OpenAI SDK stream

- **Severity**: Medium-Low
- **Confidence**: Certain — verified by grep across the whole test tree
- **Files**: `tests/unit/worker/executors/test_chat_completion.py`, `tests/unit/worker/executors/test_responses.py`,
  `tests/integration/test_echo_endpoints.py:77`

`grep -rn "idle" tests/unit/worker/executors/test_chat_completion.py tests/unit/worker/executors/test_responses.py tests/integration/` returns **nothing**. Coverage is:

- `test_base.py` — the wrapper against plain `async def` generators. Good unit coverage of the wrapper.
- `test_sse.py` — the wrapper inside `SseExecutor`, with `aiter_sse` replaced by a plain async generator.
- `test_chat_completion.py` / `test_responses.py` — **no idle tests at all**.
- `test_echo_endpoints.py` — the one test that drives a **real** `AsyncChatCompletionStream` against a live
  uvicorn endpoint builds its context as `RunContext(run_id=..., schedule_id=...)` (line 77), so
  `effective_idle_timeout` is `None` and the wrapper takes the **pass-through branch**.

The net effect: the branch production *always* takes (`asyncio.timeout` around `anext(aiter(real_openai_stream))`)
is only ever run against hand-written generators, while the only test touching a real SDK stream object runs
the branch production *never* takes. The specific risks that go unverified are exactly the ones this review
had to settle by reading SDK source and running probes — that `aiter()` accepts an object whose `__aiter__` is
an `async def` generator function, and that `get_final_completion()` still works after a wrapped iteration.

Both of those turn out to be **fine** (verified in `openai/lib/streaming/chat/_completions.py:188-243`:
`__aiter__` is an async generator delegating to a shared `self._iterator`, and `get_final_completion()` →
`until_done()` → `consume_async_iterator(self)` drains an already-exhausted iterator and returns immediately).
But nothing in the suite would catch an openai minor-version bump that changes either, and the dependency is
pinned only as `openai>=1.0.0,<2`.

This also contradicts the plan's own test matrix, which promised
"`test_*.py` — EXTEND: ... heartbeat-only stream abandoned at `idle_timeout`" for the executors generally.

**Fix**: set `effective_idle_timeout` on the `RunContext` in `test_echo_endpoints.py:77` so the real-SDK path
exercises the bounded branch, and add one stalling-stream test each to `test_chat_completion.py` and
`test_responses.py`.

Related, lower-priority: the `idle_timeout is None` branch (`base.py:58-61`) is **dead in production** —
`retry.py:82-84` always resolves a non-`None` value. It exists only for tests. Either drop it and have the
tests pass a bound, or keep it and note why.

---

## S10 — Documents claim things the implementation does not do

- **Severity**: Low (documentation), but S10.1 is operator-facing and actively misleading
- **Confidence**: Certain — each claim checked against code

**S10.1 — `documentation/properties.md:60`** (operator-facing) says of `idle_timeout`:
"When omitted, `*_HTTP_IDLE_TIMEOUT_SECONDS` applies (default 60)." The `*_` wildcard tells an operator that
`API_HTTP_IDLE_TIMEOUT_SECONDS` governs on-demand runs. It does not — see S3. Until S3 is fixed this line
must read `WORKER_HTTP_IDLE_TIMEOUT_SECONDS` only, with a note that it applies to the API process too.
(`contracts/schedule-properties.md` gets this right; the operator-facing doc does not.)

**S10.2 — `plan.md` §"Scale/Scope"** promises "a read-timeout branch in each of the four executors", and the
structure tree says `chat_completion.py` / `responses.py` — "CHANGE: branch on `stream_enabled`". There is
**no read-timeout branch anywhere**: `build_timeout(http_client, ctx.effective_timeout)` is computed
unconditionally before the `if stream_enabled:` in both SDK executors, and `stream_enabled` only selects
`.stream()` vs `.create()`. The read phase is the ceiling for streaming and non-streaming alike. The simpler
implementation is defensible on its own terms, but it is the direct cause of S2 — plan decision 6 was the
mechanism that would have kept non-streaming calls bounded, and it was dropped without the plan being updated.

**S10.3 — `plan.md` §"Open Items"** still lists "`httpx.AsyncClient.timeout` phase access — verify against the
pinned httpx version during implementation (R7)" as unresolved. I verified it: httpx 0.28.1 exposes
`timeout` as a property on `BaseClient` (`httpx/_client.py:254`) returning a `Timeout`, and
`Timeout.__init__` has an explicit branch accepting all four phases as keywords with `None` permitted
(`httpx/_config.py`, the `elif not (isinstance(connect, UnsetType) or ...)` arm). `build_timeout` is correct,
including when a phase is `None`. The item should be closed.

**S10.4 — `plan.md` §"Open Items"** still lists the disposition of `fix/change-api-and-worker-timeout` as
undecided. On this branch `timeout_seconds` is `60.0` in both settings classes, i.e. the branch landed
*under* this feature rather than on top of it. Worth recording the decision explicitly, because if that
stopgap is later merged it raises `DialClient`'s bound to 600 s with no relationship to either new bound.

> **Resolution (2026-10-12):** the stopgap merged to `development` first, and this branch was merged on top of
> it. Its run budget is kept by raising `max_duration_seconds` to 600, and `timeout_seconds` is removed
> entirely, so `DialClient` now inherits the shared client's per-phase timeouts. See research.md R11.

**S10.5 — `spec.md` FR-005** is not met (S3). **FR-011/SC-006** are met only weakly: a ceiling expiry gets its
own log event (`attempt_timed_out`), but an idle expiry is logged as the generic
`attempt_failed`/`attempt_failed_non_retryable` with `reason=no_status_code` — identical to any transport
error. Only the free-text `error` field distinguishes it. A dedicated event, or a `reason=idle_timeout`, would
satisfy SC-006 properly for the cost of one line.

---

## Minor suggestions (non-blocking polish)

- **`RunContext` now carries four timeout-shaped fields** (`default_timeout`, `default_max_duration`,
  `effective_timeout`, `effective_idle_timeout`) with no type-level distinction between them, all
  `float | None`. I checked the "is `default_timeout` a trap?" question specifically: it is **not** — it is
  read only at `src/worker/jobs.py:150` and `src/worker/conversation.py:209` to build a `DialClient` for DIAL
  metadata calls, correctly falls back to `worker_http.timeout_seconds`, and is never confused with the
  ceiling. The docstring at `run.py:19-22` explains it well. The risk is naming, not behaviour: `default_timeout`
  and `default_max_duration` differ by one word and are adjacent positional floats in
  `BackgroundRunLauncher.__init__` — exactly the transposition hazard the plan's own Complexity Tracking
  entry anticipates. Consider renaming `default_timeout` to `default_dial_timeout`.
  *Resolution (2026-10-12): moot — `default_timeout` is removed together with `timeout_seconds` (research.md R11).*
- **`src/api/app.py:49-53`** already uses keyword arguments at the call site, as the Complexity Tracking entry
  promised. Good. Making the two floats keyword-**only** in the signature (`*, default_timeout, default_max_duration`)
  would enforce it and would have made S1 a clearer failure.
- **Credential resolution and conversation creation run outside the ceiling** (`jobs.py:239,248` sit outside
  `execute_with_policy`), bounded only by the `DialClient` timeout. Pre-existing and correct — noting it only
  because "the ceiling bounds the attempt" could be read as bounding the whole run.
- **`base.py:35`** reads `client.timeout` on every request. Negligible, but the value is per-client and could
  be computed once if these ever show up in a profile.

---

## What I verified and found correct

Stated plainly, because several of these were the specific risks the review was asked to probe and none of
them turned out to be defects:

- **`iterate_with_idle_timeout` async-generator semantics** (`base.py:63-75`). The `asyncio.timeout` scope
  closes before `yield item`, so the timer never runs while the consumer is working — it measures source
  idleness, which is the intended semantic. `StopAsyncIteration` is caught and converted to `return`, so it
  never escapes the async-generator body and there is no PEP 479 `RuntimeError` hazard.
- **Outer/inner cancellation is not confused.** An outer `asyncio.wait_for` expiry leaves
  `asyncio.Timeout` in state `ENTERED`, so `__aexit__` re-raises `CancelledError` rather than converting it —
  the ceiling is reported as the ceiling. Verified by probe, not just by reading CPython.
- **No stream or connection leak when the bound fires.** The `CancelledError` unwinds the inner generator's
  frame (its `finally` runs — verified by probe), the `ScheduleRunError` then propagates out of the
  `async with aconnect_sse(...)` / `async with client.chat.completions.stream(...)`, and those context
  managers close the response. The run is not left wedged.
- **`build_timeout` is correct on the pinned httpx** (0.28.1), including `None` phases — see S10.3.
- **`httpx.Timeout` is a valid value for the OpenAI SDK's `timeout=` kwarg**: the signature is
  `float | Timeout | None | NotGiven` (`openai/_base_client.py:1376`) and it is passed straight through.
- **`aiter(stream)` is correct for openai 1.109.1** stream managers, and `get_final_completion()` /
  `get_final_response()` after a fully-consumed wrapped iteration is a no-op drain — see S9.
- **Ceiling resolution order** (`retry.py:77-81`) is right for both processes: schedule → context → settings,
  with `BackgroundRunLauncher` supplying the API value.
- **Timeouts remain non-retryable** in both flavours (`status_code=None`, `retry.py:122-125`), as FR and R9 require.
- **No migration**, and the round-trip through `kwargs` is covered by an explicit test.
- **`__init__.py` files, `print()`, `assert`, bare `except`, type hints, docstrings, import order, 120-char
  lines** — all clean; ruff and mypy pass.

---

## Checklist pass

| Area | Result |
|---|---|
| Correctness & logic | **Issues** — S2 (design), S4; wrapper mechanics themselves verified correct |
| Type safety | Pass — mypy clean, all new signatures fully annotated, `TypeVar` used correctly on the wrapper |
| Code style & conventions | Pass — ruff check + format clean, Google-style docstrings, no `print`/`assert`/bare except |
| Architecture & design | **Issues** — S2 (non-streaming bound), S3 (resolution asymmetry); the shared-helper extraction and `RunContext` threading are otherwise clean and honour 020's single-source-of-truth rule |
| Database & ORM | Pass — no model change, no migration, confirmed `alembic` untouched |
| Project structure | Pass — helpers in `src/worker/executors/base.py`, schema in `src/lib/schemas/`, settings in `src/lib/settings.py`; `lib/` does not import `api/` or `worker/` |
| Testing | **Issues** — S1 (failing), S8 (flaky), S9 (real-SDK path untested) |
| Security | Pass — no secrets logged; `stream_event` logs only the type discriminator, `sse_event` only event/data as before; no `.env` access |
| Intent alignment | **FR-005 not met** (S3); FR-011/SC-006 met weakly (S10.5); plan/doc divergences S10.1–S10.4 |
