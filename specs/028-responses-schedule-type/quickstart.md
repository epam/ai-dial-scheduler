# Quickstart: Responses Schedule Type

Validation guide proving a `responses` schedule can be created, runs via the Responses API, and behaves like a chat completion schedule for triggering/timeout/retry/tracking.

## Prerequisites

- Local stack running (API + worker) with `uv sync` completed.
- A valid DIAL deployment reachable at a `/openai/deployments/{deployment}/responses` URL and a `service_id` configured for OBO credentials (same setup used for chat completion schedules).
- See `res.py` at repo root for the Responses API SDK usage this feature mirrors.

## Quality gates (run all)

```bash
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
```

Expected: all pass. New unit tests: `tests/unit/worker/executors/test_responses.py`, updated `tests/unit/worker/test_dispatcher.py` and `tests/unit/lib/schemas/test_schedule_schemas.py`.

## Scenario 1 — Create a `responses` schedule (FR-001, FR-006, FR-008)

Create a schedule with `target_type: "responses"` (see [contracts/schedule-properties.md](./contracts/schedule-properties.md) for the request body). An `.http` example lives under `tests/e2e/` mirroring the chat completion examples.

- **Expected**: 201/200 with the schedule stored; `properties.target_type == "responses"` in the response.
- **Negative**: setting `method` → 422; omitting `api_version` → 422; `create_conversation: true` → 422.

## Scenario 2 — Run invokes the Responses API and stores the result (FR-002, FR-003)

Trigger the schedule (or wait for its cron/date fire).

- **Expected**: A `schedule_runs` row transitions to `success`; `result` contains `{"result": {...}}` where the inner object is the assembled `Response` (`Response.model_dump()`).
- **Verify** via the runs endpoint that `result` is populated and shaped like an assembled response.

## Scenario 3 — Streaming and non-streaming parity (SC-003)

Create two `responses` schedules, one with `payload.stream: true`, one with `payload.stream: false`.

- **Expected**: Both store a `result` of the same assembled shape (single final Response), confirming transport-independent result storage.

## Scenario 4 — Behavior parity with chat completion (FR-004, FR-005, FR-007, SC-004, SC-005)

- **Timeout**: set `properties.timeout` low against a slow deployment → run recorded as failed on timeout, identical to chat completion.
- **Retry**: set a `retry` policy with a retryable status; force a retryable error → retries applied per `interval_sequence`.
- **Pause/resume**: pause the schedule → no new runs; resume → runs continue.
- **Auth**: runs execute under the owner's OBO credentials (same `service_id` resolution as chat completion).
- **Failure**: point at an invalid deployment → run recorded as `error` with the status code surfaced.

## Done

- All quality gates green.
- Scenarios 1–4 produce the expected outcomes, demonstrating a new `responses` schedule type that uses the Responses API while reusing all non-interaction-specific schedule behavior.
