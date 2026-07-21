# Quickstart: Create DIAL Conversation on Run Complete

Validation guide proving the feature works end-to-end. Details live in [data-model.md](./data-model.md), [contracts/](./contracts/), and [research.md](./research.md).

## Prerequisites

- `uv sync` (deps installed), PostgreSQL reachable, Alembic at head (`uv run alembic upgrade head`) — **no new migration** for this feature.
- API and worker services runnable per project README.
- A DIAL Core instance and a valid owner token. Dev instance verified: `https://core.aks.dev.dial.parts` (Bearer JWT).
- `settings.dial_url` / `settings.dial_api_key` configured; the schedule's `service_id` resolves OBO credentials for the owner.

## Automated tests (primary validation)

```bash
uv run pytest tests/lib/schemas/test_schedule.py            # create_conversation validation rules
uv run pytest tests/worker/executors/test_chat_completion.py # assembled result for stream + non-stream
uv run pytest tests/worker/test_conversation.py             # builder + model/app resolution + failure isolation
uv run pytest tests/lib/clients/test_dial_client.py         # get_bucket / get_application / put_conversation
uv run pytest tests/worker/test_jobs.py                     # conversation invoked on success only; non-fatal on failure
uv run pytest                                               # full suite
```

Quality gates (Constitution / CI):

```bash
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src
uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml
```

## Scenario 1 — Flag validation (US1, US2 opt-in; FR-001, FR-002, SC-004)

1. `POST /schedules/` with `properties.target_type = "chat_completion"` and `properties.create_conversation = true` → **201**, response `properties.create_conversation == true`.
2. Same but `target_type = "rest"` (with `method`) and `create_conversation = true` → **422** (`create_conversation is only allowed for target_type 'chat_completion'`).
3. Omit `create_conversation` on a chat_completion schedule → **201**, `properties.create_conversation == false`.

## Scenario 2 — Model deployment run creates a conversation (US1, US4; FR-003/004/005/006/012)

1. Create a chat_completion schedule targeting a **model** deployment (e.g. `.../openai/deployments/gpt-5.6-sol-2026-07-09-reasoning/chat/completions`), `create_conversation = true`, immediate `date` trigger.
2. Let the run complete. Confirm `GET /schedules/{id}/runs/{run_id}` shows `status = success` and `result.result` is an assembled `ChatCompletion` (`choices[0].message.content` present) — for **both** a streaming (`payload.stream = true`) and non-streaming schedule the result shape is identical (SC-006).
3. Confirm the conversation exists in DIAL storage at `conversations/{bucket}/.scheduler/{schedule_id}/{run_id}` with two messages (user prompt + assistant response) and `model.id == "gpt-5.6-sol-2026-07-09-reasoning"`.

Read-only verification (safe GET) via the dial-api skill:

```bash
cd .claude/skills/dial-api
MSYS_NO_PATHCONV=1 python dial.py GET /v1/bucket "$TOKEN" --base https://core.aks.dev.dial.parts --auth bearer
MSYS_NO_PATHCONV=1 python dial.py GET "/v1/conversations/{bucket}/.scheduler/{schedule_id}/{run_id}" "$TOKEN" --base https://core.aks.dev.dial.parts --auth bearer
```

## Scenario 3 — Application deployment resolves reference (US2; FR-007, FR-008, SC-003)

1. Create a chat_completion schedule targeting an **application** deployment (id begins with `applications/...`), `create_conversation = true`.
2. After the run, confirm the created conversation's `model.id` equals the application's `reference`, not the raw application id.

Read-only verification (the resolution the worker performs):

```bash
MSYS_NO_PATHCONV=1 python dial.py GET "/openai/applications/{application_id}" "$TOKEN" --base https://core.aks.dev.dial.parts --auth bearer
# -> "reference": "c9e5decf-..."  ==  conversation model.id
```

## Scenario 4 — Custom content preserved (US3; FR-005)

1. Use an application/deployment that returns `custom_content` (tool_execution_history, stages).
2. Confirm the assistant message in the stored conversation includes both `content` and `custom_content` (compare against `conversations/app/dial_conversation.json`).

## Scenario 5 — Failure isolation (FR-013, SC-005)

1. With `create_conversation = true`, simulate a conversation-write failure (e.g. unresolvable bucket or DIAL 5xx — in tests, mock `DialClient.put_conversation`/`get_bucket` to raise).
2. Confirm the run is still recorded `success` with its result intact, and a structured error log (`conversation_create_failed` with `run_id`, `schedule_id`, reason) is emitted — no streamed content or token in logs (FR-015).

## Scenario 6 — Disabled flag creates nothing (SC-002)

1. Run a chat_completion schedule with `create_conversation = false` (default).
2. Confirm no conversation is written under `.scheduler/{schedule_id}/{run_id}`.

## Expected outcomes summary

- Opted-in successful chat_completion runs → one conversation at the run-specific path (SC-001).
- Disabled/omitted flag → zero conversations (SC-002).
- Application runs → correct `reference` as model id (SC-003).
- Non-chat-completion + flag → 422 (SC-004).
- Conversation failure → run still success (SC-005).
- Streaming and non-streaming → identical result shape and equivalent conversations (SC-006).
