# Contract: Conversation model resolution from payload.model

**Feature**: `029-base-url-model-field` | **Module**: `src/worker/conversation.py`
(`create_run_conversation`)

## Behavior (after this feature)

The deployment identifier used for conversation creation is taken from `properties.payload["model"]`
instead of `extract_deployment_id(properties.url)`.

1. Read `deployment = properties.payload.get("model")`.
   - If missing/empty → raise `ConversationCreationError` (caller treats as non-fatal). In practice,
     `ScheduleProperties` validation guarantees presence for chat-completion schedules.
2. `is_application = deployment.startswith(APPLICATION_DEPLOYMENT_PREFIX)` *(unchanged logic)*.
3. If application → `model_id = (await client.get_application(credentials, deployment)).reference`;
   else → `model_id = deployment` *(unchanged)*.
4. Log `conversation_create_start` with `deployment_kind` ("application"|"model") *(unchanged)*.
5. Resolve bucket, build the conversation, `put_conversation` *(unchanged)*.

## Acceptance

| # | Scenario | Expected |
|---|----------|----------|
| 1 | `payload.model="gpt-4"` (plain model) | `model_id="gpt-4"`; `deployment_kind="model"`; conversation saved with that model. |
| 2 | `payload.model` starts with application prefix | `get_application` called; `model_id=details.reference`; `deployment_kind="application"`. |
| 3 | `payload.model` missing | `ConversationCreationError` raised; run outcome unaffected (non-fatal). |
| 4 | Conversation history/temperature build | Unchanged from current behavior (sourced from `payload`). |
