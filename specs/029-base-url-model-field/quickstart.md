# Quickstart: Base URL and Payload Model for Chat Completion

**Feature**: `029-base-url-model-field`

Validates that chat-completion schedules use a base URL and select the deployment via
`payload.model`, matching the responses target type.

## Prerequisites

- `uv sync`
- Reachable DIAL/OpenAI-compatible base URL and a valid per-user credential (OBO).
- A configured `service_id` for the schedule owner.

## Configuration shape (after this feature)

Chat-completion `properties`:

```json
{
  "target_type": "chat_completion",
  "url": "https://<dial-host>/openai/v1",
  "api_version": "2024-02-01",
  "payload": {
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hello"}]
  },
  "create_conversation": false
}
```

- `url` is a **base URL** (no `/deployments/{name}/chat/completions` path).
- `payload.model` names the deployment; it is **required**.

## Validation checks

1. **Model required**: submit the config above without `payload.model` → schedule create/update is
   rejected with `payload.model is required for target_type 'chat_completion'`.
2. **Valid config accepted**: submit with `payload.model` set → schedule is created; `stream` defaults
   to `False`.

## Run checks

3. **Deployment routing**: trigger a run → the request calls the deployment named in `payload.model`;
   stored result has the standard `{"result": {...}}` shape.
4. **Streaming parity**: set `payload.stream = true`, trigger a run → assembled result matches the
   non-streaming shape.
5. **Conversation attribution**: set `create_conversation = true`, trigger a successful run → a DIAL
   conversation is saved attributed to `payload.model` (application references resolved when the model
   id uses the application prefix).

## Automated validation

```bash
uv run pytest tests/unit/lib/schemas/test_schedule_schemas.py \
              tests/unit/worker/executors/test_chat_completion.py \
              tests/unit/worker/test_conversation.py \
              tests/unit/lib/utils/test_dial.py
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
```

Expected: chat-completion validation requires `payload.model`; executor builds the client with
`base_url`; conversation model resolves from `payload.model`; removed-helper tests are gone; all gates
pass.

## References

- Validation: [contracts/schedule-properties.md](./contracts/schedule-properties.md)
- Executor: [contracts/chat-completion-executor.md](./contracts/chat-completion-executor.md)
- Conversation: [contracts/conversation-model.md](./contracts/conversation-model.md)
- Data model: [data-model.md](./data-model.md)
