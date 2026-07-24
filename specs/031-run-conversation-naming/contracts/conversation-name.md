# Internal Contract: Run Conversation Name

**Feature**: `031-run-conversation-naming`
**Type**: Internal typed interface (worker ↔ conversation builder). No REST/OpenAPI change.

This feature exposes no new external API. Per Constitution Principle IV, the internal boundary
is expressed through typed Python interfaces (Pydantic model + pure functions), documented here.

---

## Function contract: `slugify`

```
slugify(value: str, max_length: int) -> str
```

**Location**: `src/lib/utils/naming.py`

**Behavior**: lowercase → collapse `[^a-z0-9]+` to single `-` → strip leading/trailing `-` →
truncate to `max_length` then strip trailing `-` → if empty return
`CONVERSATION_NAME_SEGMENT_PLACEHOLDER`.

**Guarantees**:
- Output contains only `[a-z0-9-]`.
- Output never contains `__` (the separator), so it is safe as a name segment.
- Output is non-empty (placeholder fallback).
- Output length ≤ `max_length`.
- Deterministic.

**Examples**:

| Input `value` | `max_length` | Output |
|---------------|--------------|--------|
| `chathub-claude4` | 64 | `chathub-claude4` |
| `hello` | 64 | `hello` |
| `Daily GPT-4o Summary!` | 64 | `daily-gpt-4o-summary` |
| `  Weird__Name  ` | 64 | `weird-name` |
| `!!!` (only punctuation) | 64 | `untitled` |
| `Привет` (non-Latin) | 64 | `untitled` |
| `a`×80 | 64 | `a`×64 |
| `aaaa-…` truncated at a hyphen | 8 | trailing `-` stripped |

---

## Function contract: `build_conversation_name`

```
build_conversation_name(deployment_id: str, display_name: str, run_id: str) -> str
```

**Location**: `src/lib/utils/naming.py`

**Behavior**: returns
`slugify(deployment_id, MAX) + SEP + slugify(display_name, MAX) + SEP + run_id`
where `SEP = CONVERSATION_NAME_SEPARATOR`, `MAX = CONVERSATION_NAME_SEGMENT_MAX_LENGTH`.

**Guarantees**:
- Exactly two `__` separators at segment boundaries (run id carries no `__`), so
  `name.split("__", 2)` recovers the three segments unambiguously.
- Deterministic (FR-008).

**Canonical example**:
```
build_conversation_name("chathub-claude4", "hello", "964ff385-72e7-46f8-8cdc-fa3358b825ec")
  == "chathub-claude4__hello__964ff385-72e7-46f8-8cdc-fa3358b825ec"
```

---

## Model contract: `ConversationBuildInput` (updated)

```
ConversationBuildInput(
    model_id: str,        # existing — resolved model attribution
    bucket: str,          # existing
    schedule_id: str,     # existing
    run_id: str,          # existing
    deployment_id: str,   # NEW — raw scheduled deployment id (name segment source)
    display_name: str,    # NEW — schedule display name (name segment source)
)
```

**Consumer**: `build_conversation(properties, result, build_input)` sets
`DialConversation.name = build_conversation_name(build_input.deployment_id,
build_input.display_name, build_input.run_id)`. All other conversation fields (`id`,
`folderId`, `model`, `messages`, …) are unchanged (FR-007).

**Producer**: `create_run_conversation` (worker) populates `deployment_id` from
`properties.payload["model"]` and `display_name` from the schedule fetched via
`ScheduleRepository.get_by_id(schedule_id)`.

---

## Backward compatibility

- No stored data is migrated. Conversations created before this change keep their old
  (`run_id`) names; only newly created conversations use the new name.
- Conversation storage `id`/path is unchanged, so addressing/retrieval is unaffected.
