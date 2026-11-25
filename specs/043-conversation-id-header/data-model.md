# Data Model: Conversation ID Header and Scheduler Request Identity

No database schema change and no migration. `schedule_runs.conversation_id` keeps its meaning and is written exactly as today
(only when the conversation was saved).

## 1. `ConversationLocation` (new, `src/lib/schemas/conversation.py`)

Everything that determines where a run's conversation lives and what its id is. Resolved once per run before the target
request; reused when the conversation is saved.

| Field          | Type  | Source                                                                    | Rules                                                     |
|----------------|-------|---------------------------------------------------------------------------|-----------------------------------------------------------|
| `deployment_id`| `str` | `payload.model`, `unquote`-d to its raw form                               | non-empty string; otherwise resolution fails              |
| `bucket`       | `str` | `GET /v1/bucket` with the owner's OBO credentials                          | non-empty; missing/err → resolution fails                 |
| `schedule_id`  | `str` | `RunContext.schedule_id`                                                  | —                                                         |
| `run_id`       | `str` | `RunContext.run_id`                                                       | —                                                         |
| `display_name` | `str` | schedule row loaded from DB                                               | schedule missing → resolution fails                       |

Derived (functions in `src/worker/conversation.py`, not stored):
- `conversation_resource_path(location)` → `{bucket}/.scheduler/{schedule_id}/[{deployment folder}/]{leaf}` (existing function,
  now typed on `ConversationLocation`).
- `conversation_id_for(location)` → `conversations/` + resource path. Single source for the header value, the document `id`, and
  the PUT target.

Validation at resolution: `len(encode_header_value(conversation_id_for(location))) <= 4096`
(`MAX_CONVERSATION_ID_HEADER_BYTES`); otherwise resolution fails with `ConversationCreationError` (no header, no conversation).

Stored vs. sent form: `schedule_runs.conversation_id` holds the raw id; the header — and therefore DIAL Core `chat.id` and the
DIAL Admin session id — holds `encode_header_value(raw id)`. They are equal for ASCII display names.

## 2. `ConversationBuildInput` (changed)

Becomes `ConversationLocation` + `model_id: str` (subclass). Field set and constructor keywords are unchanged, so existing
callers/tests keep working; `build_conversation` and `conversation_resource_path` accept it as before.

`model_id` is resolved post-run: the application reference for `applications/...` deployments, otherwise `deployment_id`.

## 3. `RunContext` (changed, `src/lib/schemas/run.py`)

| New field               | Type                          | Default | Set by                                   | Read by                                         |
|-------------------------|-------------------------------|---------|------------------------------------------|-------------------------------------------------|
| `conversation_location` | `ConversationLocation \| None` | `None`  | `execute_run` (once, before attempts)    | `ChatCompletionExecutor`, `create_run_conversation`, `_maybe_create_conversation` |

State per run:

```
            chat_completion && create_conversation?
                    │no                         │yes
                    ▼                           ▼
             location = None          resolve_conversation_location
             (no header)                │ok                    │error
                                        ▼                      ▼
                         location set, conversation_id   location = None
                         bound to log context            warning conversation_id_resolve_failed
                                        │                      │
                       attempts send X-CONVERSATION-ID   attempts send no header
                                        │                      │
                          success → PUT at same path     success → info conversation_create_skipped
                          → runs.conversation_id set       runs.conversation_id stays NULL
                          failure → no PUT (as today)     failure → as today
```

## 4. Constants (`src/lib/constants.py`)

- `CONVERSATION_ID_HEADER = "X-CONVERSATION-ID"` (new).
- `MAX_CONVERSATION_ID_HEADER_BYTES = 4096` (new) — upper bound for the encoded header value.
- `USER_AGENT_HEADER`, `USER_AGENT_PRODUCT` (exist since `f94816b`).

## 5. Header helpers (new, `src/lib/utils/conversation_header.py`)

- `encode_header_value(value: str) -> str` — percent-encodes every character outside printable ASCII as UTF-8 bytes; printable
  ASCII unchanged. Idempotent for already-ASCII input.
- `with_conversation_id(headers: dict[str, str], conversation_id: str | None) -> dict[str, str]` — `None` → copy of `headers`
  unchanged; otherwise a copy without any key matching `X-CONVERSATION-ID` case-insensitively, plus
  `X-CONVERSATION-ID: encode_header_value(conversation_id)`.
