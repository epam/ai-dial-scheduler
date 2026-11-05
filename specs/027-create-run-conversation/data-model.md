# Phase 1 Data Model: Create DIAL Conversation on Run Complete

No database schema changes. The `create_conversation` flag rides the existing `Schedule.kwargs` JSONB (serialized `ScheduleProperties`) and the `ScheduleRun.schedule_snapshot` JSONB, so **no Alembic migration is required**. All new entities below are Pydantic v2 models (in-memory / wire schemas), consistent with Constitution Principle IV (typed interfaces across boundaries).

## 1. ScheduleProperties (MODIFIED — `src/lib/schemas/schedule.py`)

New field on the existing model:

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `create_conversation` | `bool` | `False` | When `True`, a DIAL conversation is created after a successful run. |

**Validation rule** (extend `validate_target_rules` model validator):

- If `create_conversation is True` and `target_type != TargetType.CHAT_COMPLETION` → raise `ValueError("create_conversation is only allowed for target_type 'chat_completion'")`.

**Persistence flow (unchanged)**: `ScheduleService.create` → `model_dump(mode="json")` into `Schedule.kwargs` → APScheduler job kwargs → `ScheduleRun.schedule_snapshot` → worker `ScheduleProperties(**kwargs)`.

## 2. Application details (NEW — `src/lib/schemas/conversation.py`)

Parsed subset of `GET /openai/applications/{application_name}`.

| Field | Type | Notes |
|-------|------|-------|
| `reference` | `str` | Underlying model reference; becomes the conversation `model.id` for application deployments. |

Extra response fields are ignored (`model_config` permits/ignores extras). Missing/empty `reference` is a resolution failure → non-fatal (logged, conversation skipped).

## 3. DialMessage (NEW — `src/lib/schemas/conversation.py`)

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `role` | `str` | — | `"user"` or `"assistant"`. |
| `content` | `str` | `""` | Text content. |
| `model` | `DialModel` | — | `{ "id": <model_id> }`. |
| `custom_content` | `dict[str, Any] \| None` | `None` | Assistant only; copied from `result.choices[0].message.custom_content` when present (tool history, stages). |
| `settings` | `dict[str, Any]` | see below | `{ "prompt": "", "temperature": <t>, "selectedAddons": [] }`. |

## 4. DialModel (NEW — `src/lib/schemas/conversation.py`)

| Field | Type | Notes |
|-------|------|-------|
| `id` | `str` | Deployment id (models) or resolved `reference` (applications). |

## 5. DialConversation (NEW — `src/lib/schemas/conversation.py`)

The document written via `PUT /v1/conversations/{bucket}/{conversation_path}`.

| Field | Type | Notes |
|-------|------|-------|
| `id` | `str` | `conversations/{bucket}/.scheduler/{schedule_id}/{run_id}`. |
| `name` | `str` | Display name derived from the user prompt (truncated). |
| `model` | `DialModel` | Resolved model id. |
| `prompt` | `str` | System prompt (default `""`). |
| `temperature` | `float` | From payload or default `1`. |
| `folderId` | `str` | `conversations/{bucket}/.scheduler/{schedule_id}`. |
| `messages` | `list[DialMessage]` | Exactly two: user then assistant. |
| `selectedAddons` | `list[str]` | `[]`. |
| `updatedAt` | `int` | Epoch milliseconds at creation. |
| `responseFormat` | `str` | `"Markdown"`. |

**Construction sources**:
- User message `content` ← last `role == "user"` message in `ScheduleProperties.payload["messages"]` (from run snapshot).
- Assistant message `content` + `custom_content` ← `result["choices"][0]["message"]` (from `ScheduleRun.result["result"]`, the assembled completion).
- `model.id` ← R4 resolution (deployment id or application `reference`).

## 5a. ConversationBuildInput (NEW — `src/lib/schemas/conversation.py`)

Groups the resolved inputs passed to `build_conversation` so the builder keeps a small
parameter list (Constitution Principle V / CLAUDE.md ≤3 params).

| Field | Type | Notes |
|-------|------|-------|
| `model_id` | `str` | Resolved model id (deployment id or application `reference`). |
| `bucket` | `str` | Owner's DIAL storage bucket. |
| `schedule_id` | `str` | Schedule id (from `RunContext`). |
| `run_id` | `str` | Run id (from `RunContext`). |

## 6. ConversationCreationError (NEW — `src/lib/exceptions/errors.py`)

Worker-internal exception for typed signaling of a conversation-creation failure (bucket/application/write). Always caught at the job boundary; never propagates past run success. Carries a message and optional cause; never carries secrets or streamed content.

## Relationships

```
Schedule.kwargs (ScheduleProperties incl. create_conversation)
   └─(snapshot at run)→ ScheduleRun.schedule_snapshot ──┐
ScheduleRun.result (assembled ChatCompletion) ──────────┤
                                                         ▼
                              build DialConversation (2 messages)
                                                         │ resolve model.id
                                                         │  ├─ model: deployment id
                                                         │  └─ application: GET /openai/applications → reference
                                                         ▼
                              PUT /v1/conversations/{bucket}/.scheduler/{schedule_id}/{run_id}
```

## State / lifecycle notes

- Conversation creation occurs only in the run `success` terminal state and only when `create_conversation is True`.
- It is a post-persistence, best-effort step; its own success/failure does not alter `ScheduleRunStatus`.
- Each run writes a distinct `{run_id}` leaf → no cross-run overwrite.
