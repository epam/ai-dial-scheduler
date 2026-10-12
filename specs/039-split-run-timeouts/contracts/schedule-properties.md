# Contract: ScheduleProperties with Split Timeouts

This feature makes **additive, non-breaking** changes to the schedule create/update request contract and the schedule response contract. The affected object is `properties` (the `ScheduleProperties` model) within `ScheduleRequest` and `ScheduleResponse`. No new endpoints; no changed routes.

Affected endpoints (request `properties` / response `properties`):

- `POST /v1/schedules` (create)
- `PUT /v1/schedules/{schedule_id}` (update)
- `GET /v1/schedules/{schedule_id}` (detail)
- `GET /v1/schedules` (list)

Contract version impact: **MINOR** (one additive optional field). No major bump.

## Updated: ScheduleProperties

```jsonc
{
  "target_type": "chat_completion",      // unchanged
  "method": "POST",                      // unchanged
  "url": "https://api.example.com/hook", // unchanged
  "payload": { "stream": true },         // unchanged
  "extra_headers": { },                  // unchanged
  "api_version": null,                   // unchanged
  "create_conversation": false,          // unchanged

  "timeout": 1800,                       // float | null, > 0 (seconds). UNCHANGED MEANING:
                                         //   absolute ceiling on one attempt.
                                         //   null/absent -> process default (3600s).

  // NEW — optional:
  "idle_timeout": 90,                    // float | null, > 0 (seconds).
                                         //   Maximum gap between consecutive stream events.
                                         //   Streaming targets only. null/absent -> 60s.

  "retry": {                             // unchanged
    "backoff_interval": [2, 5, 8],
    "retryable_errors": [500, 502]
  }
}
```

### Field rules

| Field | Type | Optional | Constraint | Default behaviour when omitted |
|-------|------|----------|------------|--------------------------------|
| `timeout` | number | yes | `> 0` | `*_HTTP_STREAMING_MAX_DURATION_SECONDS` (default 3600) for a streaming target, `*_HTTP_MAX_DURATION_SECONDS` (default 600) otherwise — taken from the process that executes the run |
| `idle_timeout` | number | yes | `> 0`; and `<= timeout` when both are set | `*_HTTP_IDLE_TIMEOUT_SECONDS` (default 60) |

The ceiling default is deliberately asymmetric. A streaming target has `idle_timeout` watching it, so a stall is caught in a minute and the ceiling is a formality. A non-streaming target has no progress signal at all, so total duration is its only instrument — and an hour-long setting on a blunt instrument would let a silently hung host occupy a worker slot for an hour.

Both are `number` in JSON Schema terms — the Python type is `float | None`. Feature 020's contract document describes `timeout` as an integer; the implementation has always been `float`, and that document is stale.

### Semantics

The two bounds answer different questions and are enforced at different layers.

| | `timeout` | `idle_timeout` |
|---|---|---|
| Bounds | Total wall-clock duration of one attempt | The gap between two consecutive stream events |
| Resets | Never | On every parsed event |
| Applies to | All target types | Streaming requests only |
| Enforced by | `asyncio.wait_for` around the attempt | `asyncio.timeout` around each `anext()` on the stream |
| Intent | Emergency backstop against a run that never ends | Detecting a target that has stopped making progress |

A target is streaming when `target_type` is `sse`, or when `target_type` is `chat_completion` / `responses` and `payload.stream` is `true`. For a non-streaming target `idle_timeout` is accepted and stored but has no effect, because such a call has no idle period — the entire wait for the response is a single read.

`idle_timeout` is **not** an HTTP read timeout. It counts parsed events, so SSE heartbeat comments — which the platform emits precisely when the response is idle, and which the parser discards — do not reset it.

### Validation error responses

Invalid input is rejected at create/update with a 422 validation error (FastAPI/Pydantic standard). Examples that MUST be rejected:

- `idle_timeout: 0` or `idle_timeout: -5`
- `timeout: 60, idle_timeout: 120` — the idle bound could never fire before the ceiling

Examples that MUST be accepted:

- `idle_timeout` set with `timeout` absent — the cross-field rule applies only when both are explicitly set, so that a stored schedule's validity never depends on deployment configuration
- `idle_timeout` equal to `timeout`
- `idle_timeout` on a non-streaming target — accepted and inert, so that flipping `payload.stream` later does not require also editing the bounds

### Response contract

`ScheduleResponse.properties` echoes the stored properties verbatim, so `timeout` and `idle_timeout` (or their absence) appear exactly as supplied. No change to the `ScheduleResponse` field set is required — `properties` is already a free-form object sourced from `schedules.kwargs`.

The run-detail response is unchanged: `result` still holds only the final outcome. Which bound expired is visible in `error_text` and, by event name, in logs — `attempt_timed_out` for the ceiling, `attempt_idle_timed_out` for the idle bound — but not as a structured response field.

## Backward compatibility

- Clients that omit both fields are unaffected by the contract, but **their effective ceiling changes**. Previously it came from `timeout_seconds`: 60 s, raised to 600 s by the `fix/change-api-and-worker-timeout` stopgap. Now it is 600 s for a non-streaming target and 3600 s for a streaming one. This is the intended fix. Stall detection moves to the new 60 s idle bound for streaming targets, which is a strictly better detector than the mechanism it replaces. Non-streaming targets gain no detector, which is why their ceiling stays at the stopgap's 600 s instead of rising to the hour.
- `*_HTTP_TIMEOUT_SECONDS` is removed. It no longer affects any run; a deployment that still sets it starts normally and the value is ignored.
- Clients that set `timeout` explicitly keep exactly the ceiling they asked for.
- One behaviour change does reach schedules with an explicit `timeout`: a streaming schedule with, say, `timeout: 600` previously had no working idle bound at all, and now acquires the 60 s default. A target whose legitimate inter-event gap exceeds 60 s would newly fail. The escape hatch is to set `idle_timeout` explicitly on that schedule. Measured evidence puts the worst real gap at 21.8 s, so the margin is ×2.7 — but the change is real and is called out here rather than claimed away.
- Existing stored schedules (no `idle_timeout` in `kwargs`) deserialize with the field `null`.

## MCP surface

`_SCHEDULE_EXAMPLES` (`src/api/v1/endpoints/schedules.py:67-116`) currently shows **neither** `timeout` nor `retry` in any of its three examples. It feeds both the OpenAPI examples and, through `_examples_docstring()`, the endpoint docstring that MCP clients read as tool guidance. At least the streaming example should carry both bounds so an agent composing a schedule sees the distinction.

## Internal worker contract (API ↔ worker)

The worker continues to receive the full `properties` object as APScheduler job `kwargs` and reconstruct it via `ScheduleProperties(**kwargs)`. Because `idle_timeout` is part of `ScheduleProperties`, the typed internal contract between API and worker (Constitution IV) is preserved — no ad-hoc dict keys are introduced outside the Pydantic model.

Resolved values travel from the orchestrator to the executor on `RunContext` (`effective_timeout`, `effective_idle_timeout`), never by re-reading `properties` in the executor.
