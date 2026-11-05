# Phase 1 Data Model: Run Conversation Naming Convention

**Feature**: `031-run-conversation-naming`
**Date**: 2026-07-24

No database schema change and no migration. This feature adds one field to an existing internal
Pydantic model and derives a computed value (the conversation name). Persistent entities
(`schedules`, `schedule_runs`) are unchanged.

---

## Modified model: `ConversationBuildInput`

`src/lib/schemas/conversation.py` — groups the resolved inputs for assembling a conversation.

| Field | Type | Existing? | Notes |
|-------|------|-----------|-------|
| `model_id` | `str` | existing | Resolved model attribution (used for `DialModel`, unchanged). |
| `bucket` | `str` | existing | Owner storage bucket. |
| `schedule_id` | `str` | existing | Schedule identifier (part of storage path). |
| `run_id` | `str` | existing | Run identifier (storage path + verbatim name segment). |
| `deployment_id` | `str` | **NEW** | Raw scheduled deployment id (`properties.payload["model"]`); source of the deployment name segment. |
| `display_name` | `str` | **NEW** | Schedule display name; source of the display-name segment. |

**Validation**: both new fields are plain required `str`. No length/content validation here —
robustness lives in the slug helper (empty/long inputs are handled, not rejected) so conversation
creation never fails on name inputs (SC-004).

> Note: `deployment_id` is added distinctly from `model_id` because for application deployments
> `model_id` is the resolved underlying reference, whereas the name segment must reflect the
> deployment the user scheduled (research Decision 1).

---

## Computed value: Run Conversation Name

Not persisted as a distinct entity; it is the value assigned to `DialConversation.name`.

**Shape**: `{deployment-slug}{SEP}{display-name-slug}{SEP}{run-id}`
where `SEP = "__"`.

**Segment rules**:

| Segment | Source | Transform |
|---------|--------|-----------|
| deployment-slug | `ConversationBuildInput.deployment_id` | `slugify(value, max_length=64)` |
| display-name-slug | `ConversationBuildInput.display_name` | `slugify(value, max_length=64)` |
| run-id | `ConversationBuildInput.run_id` | verbatim (no slugify, no truncation) |

**`slugify(value, max_length)` contract** (pure function):
1. Lowercase.
2. Replace each run of `[^a-z0-9]+` with a single `-`.
3. Strip leading/trailing `-`.
4. Truncate to `max_length`, then strip a trailing `-` left by truncation.
5. If empty after the above, return `CONVERSATION_NAME_SEGMENT_PLACEHOLDER` (`"untitled"`).

Determinism: same inputs → same output (FR-008).

---

## New constants

`src/lib/constants.py`:

| Constant | Value | Purpose |
|----------|-------|---------|
| `CONVERSATION_NAME_SEPARATOR` | `"__"` | Joins the three segments (FR-002). |
| `CONVERSATION_NAME_SEGMENT_PLACEHOLDER` | `"untitled"` | Substituted for an empty-after-slugification segment (FR-005). |
| `CONVERSATION_NAME_SEGMENT_MAX_LENGTH` | `64` | Per-segment cap for the two slugified segments (FR-006). |

---

## Requirement → model/behavior mapping

| FR | Where satisfied |
|----|-----------------|
| FR-001 (three ordered segments) | `build_conversation_name` |
| FR-002 (`__` separator) | `CONVERSATION_NAME_SEPARATOR` |
| FR-003 (slug rules) | `slugify` steps 1–3 |
| FR-004 (run id verbatim) | name assembly passes `run_id` unslugified |
| FR-005 (empty → placeholder) | `slugify` step 5 |
| FR-006 (truncate slugs, not run id) | `slugify` step 4; run id untouched |
| FR-007 (only name changes) | `build_conversation` sets `name=...`; `id`/`folderId`/messages unchanged |
| FR-008 (deterministic) | pure functions, no randomness/time in name |
