# Phase 1 Data Model: Conversation Path Encoding

**Feature**: 034-conversation-path-encoding | **Date**: 2026-08-06

No database schema changes. No Alembic migration. This feature alters one in-memory transfer object
and the derived string values written into an external (DIAL) document.

---

## Changed: `ConversationBuildInput`

**Location**: `src/lib/schemas/conversation.py`

Pydantic v2 model grouping the values resolved during orchestration. One field is added.

| Field | Type | Change | Meaning |
|---|---|---|---|
| `deployment_id` | `str` | **ADDED** | The deployment id exactly as configured on the schedule (`properties.payload["model"]`). Used — URL-encoded — in the conversation name and storage path. Never the application's underlying reference. |
| `model_id` | `str` | unchanged | The resolved model reference: an application's `reference`, or the deployment id itself for models. Written into `conversation.model.id` and each message's `model.id`. Never used in the path. |
| `bucket` | `str` | unchanged | Owner's DIAL storage bucket id. |
| `schedule_id` | `str` | unchanged | Owning schedule's id; a path segment. |
| `run_id` | `str` | unchanged | The run's id (UUID); the final part of the name. |
| `display_name` | `str` | unchanged | The schedule's name, verbatim. Now URL-encoded rather than slugified. |

**Invariant**: for a model-backed schedule `deployment_id == model_id`. For an application-backed
schedule they differ, and that difference is the entire point of FR-003 vs FR-010.

**Validation**: no new validators. `deployment_id` is non-empty by construction — `create_run_conversation`
already raises `ConversationCreationError` when `payload.model` is missing
(`src/worker/conversation.py:154-155`).

---

## Changed: derived string values

These are computed, not stored. Shown for a schedule named `Daily Report Sync` targeting the
application `applications/bkt/Untitled app 1101__0.0.1`.

| Value | Before | After |
|---|---|---|
| name deployment part | `c9e5decf-1284-...` (the reference) | `applications%2Fbkt%2FUntitled%20app%201101%5F%5F0.0.1` |
| name schedule part | `daily-report-sync` (slug) | `Daily%20Report%20Sync` |
| name run part | `<run-id>` | `<run-id>` (unchanged) |
| `conversation.name` | `c9e5decf-...__daily-report-sync__<run-id>` | `applications%2Fbkt%2FUntitled%20app%201101%5F%5F0.0.1__Daily%20Report%20Sync__<run-id>` |
| `conversation.id` | `conversations/{bucket}/.scheduler/{sid}/{name}` | same shape, new `{name}` |
| `conversation.folderId` | `conversations/{bucket}/.scheduler/{sid}` | **unchanged** |
| `conversation.model.id` | `c9e5decf-1284-...` | **unchanged** — still the reference (FR-010) |

Note the `%5F%5F` in the encoded deployment part: the literal `__` inside the application id is
encoded, so the three `__`-joined parts stay unambiguously separable (see research R-002).

---

## Unchanged

- `DialConversation`, `DialMessage`, `DialModel`, `ApplicationDetails` — no field changes.
- `Schedule` ORM model and all repositories — untouched.
- `ScheduleRequest.display_name` — deliberately **not** given a `max_length` here; see research R-003,
  logged as an out-of-scope follow-up.

---

## Removed

- `CONVERSATION_NAME_SEGMENT_MAX_LENGTH` (`src/lib/constants.py:35`) — becomes unreachable once
  slugification and truncation are gone (research R-003).
- `python-slugify` dependency (`pyproject.toml:29`) — sole consumer removed (research R-006).
