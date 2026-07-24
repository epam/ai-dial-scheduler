# Phase 0 Research: Run Conversation Naming Convention

**Feature**: `031-run-conversation-naming`
**Date**: 2026-07-24

## Context

Feature `027-create-run-conversation` created DIAL conversations after a successful chat
completion run. The conversation's display name is currently set to the bare `run_id`
(`src/worker/conversation.py:99`, `name=build_input.run_id`). This feature replaces that with
a three-part, human-recognizable convention:

```
{deployment-slug}__{display-name-slug}__{run-id}
```

Example: `chathub-claude4__hello__964ff385-72e7-46f8-8cdc-fa3358b825ec`

All decisions below resolve the two open defaults flagged in the spec Assumptions
(max segment length, empty-segment placeholder) plus the mechanism for obtaining the
schedule display name in the worker.

---

## Decision 1 — Source of the deployment-id segment

**Decision**: Use the raw target deployment id from `properties.payload["model"]` (the value
the user scheduled against), NOT the resolved underlying model reference.

**Rationale**: The spec (US1 scenario 1, Assumptions) names "slugified deployment id".
`chathub-claude4` in the example is a deployment id, not a resolved model reference. For
application deployments, `create_run_conversation` resolves `payload.model` to an underlying
`reference` used as the *model attribution* (`conversation.py:139-142`); that resolved value is
about rendering, not identity, and would be confusing in the name. The name should reflect
what the user scheduled.

**Alternatives considered**:
- Resolved `model_id` (`build_input.model_id`): rejected — for applications this is the
  underlying reference (e.g. `gpt-4o`), which loses the deployment the user chose and makes
  two different application schedules collide on the deployment segment.

---

## Decision 2 — Source of the display-name segment in the worker

**Decision**: Fetch the schedule's `display_name` in the worker job via
`ScheduleRepository.get_by_id(schedule_id)` and thread it into `ConversationBuildInput`.

**Rationale**: `display_name` is not part of the job kwargs today — jobs are registered with
`kwargs = {**properties, "schedule_id": schedule_id}` (`schedule_service.py:34`), and the
worker reconstructs `ScheduleProperties(**kwargs)`. The worker already opens DB sessions
(`jobs.py` uses `Database().sessionmaker()` and `ScheduleRunRepository`), and
`ScheduleRepository.get_by_id` already exists and returns the full `Schedule`. A single
read at conversation-build time is the least invasive change and avoids duplicating
`display_name` into the persisted run snapshot.

**Alternatives considered**:
- Add `display_name` to the job kwargs / run snapshot: rejected — `ScheduleProperties(**kwargs)`
  would need to tolerate/ignore an extra field, and it duplicates a mutable schedule attribute
  into every run snapshot. More surface area, contradicts YAGNI.
- Pass `display_name` through `RunContext`: rejected — `RunContext` is a run-identity object
  (`run_id`, `schedule_id`, `effective_timeout`); overloading it with schedule attributes
  blurs its purpose. `ConversationBuildInput` is the correct home (it already groups
  build-time inputs).

---

## Decision 3 — Slugification algorithm

**Decision**: Implement a small pure helper `slugify(value: str, max_length: int) -> str` in
`src/lib/utils/` with these rules, applied to the deployment-id and display-name segments only:
1. Lowercase.
2. Replace every run of non-alphanumeric (`[^a-z0-9]+`) characters with a single hyphen `-`.
3. Strip leading/trailing hyphens.
4. Truncate to `max_length` characters, then strip any trailing hyphen produced by truncation.
5. If the result is empty, return the placeholder constant (see Decision 4).

**Rationale**: This is the standard ASCII slug approach, deterministic (FR-008), collapses
whitespace/punctuation to single hyphens (FR-003), and guarantees the `__` separator can never
appear inside a slugified segment (an underscore is non-alphanumeric → collapses to `-`),
keeping the three-part split unambiguous (spec Edge Cases). No third-party dependency is
needed; Python `re` + `str` methods suffice (Principle V / YAGNI).

**Non-Latin input**: Non-ASCII alphanumerics (e.g. Cyrillic) are treated as non-alphanumeric
by the `[^a-z0-9]` class and collapse to hyphens, so a fully non-Latin name slugifies to empty
and falls back to the placeholder (FR-005, SC-004). Transliteration is explicitly out of scope
(YAGNI) — the run id segment still guarantees uniqueness and traceability.

**Alternatives considered**:
- `python-slugify` / `unicodedata` transliteration: rejected — adds a dependency and behavior
  (Unicode → ASCII transliteration) beyond the spec. Can be revisited if a concrete need arises.

---

## Decision 4 — Constants: separator, placeholder, max length

**Decision**: Add three constants to `src/lib/constants.py`:
- `CONVERSATION_NAME_SEPARATOR = "__"` (double underscore).
- `CONVERSATION_NAME_SEGMENT_PLACEHOLDER = "untitled"` (empty-after-slugification fallback).
- `CONVERSATION_NAME_SEGMENT_MAX_LENGTH = 64` (per-segment cap for the two slugified segments).

**Rationale**:
- `__` matches the example verbatim and cannot occur inside a slug (Decision 3), so it is a
  safe, unambiguous delimiter.
- `untitled` is short, readable, and clearly signals "no meaningful name" (FR-005).
- `64` keeps each slugified segment readable and the whole name bounded (deployment ≤ 64 +
  `__` + display ≤ 64 + `__` + run id) while being generous enough that realistic
  deployment ids and display names are not truncated (FR-006). The run id is never truncated
  (FR-004/FR-006).

**Alternatives considered**: 32 (too aggressive for real deployment ids), 100 (needlessly long).
64 is a common, balanced slug cap.

---

## Decision 5 — Where the name is assembled

**Decision**: Assemble the full name in a pure helper `build_conversation_name(deployment_id,
display_name, run_id) -> str` (co-located with the conversation builder, e.g. in
`src/worker/conversation.py` or a small `src/lib/utils/naming.py`), and call it from
`build_conversation` to populate `DialConversation.name`. `build_conversation` already has
`properties.payload["model"]` (deployment) and `build_input.run_id`; it gains
`build_input.display_name`.

**Rationale**: Keeping name assembly pure and separate from I/O makes it trivially unit-testable
under TDD (Principle III) and keeps `build_conversation` readable. Only the `name` field changes;
the conversation `id`/`folderId`/messages are untouched (FR-007, spec scope).

**Alternatives considered**: Inline the logic in `build_conversation`: rejected — harder to unit
test the edge cases (empty/long/non-Latin) in isolation.

---

## Decision 6 — Storage path / conversation id unchanged

**Decision**: Do NOT change `conversation_resource_path` / the conversation `id` / `folderId`.
Only `DialConversation.name` changes.

**Rationale**: Spec FR-007 and Edge Cases explicitly scope this to the display name. The storage
path (`{bucket}/.scheduler/{schedule_id}/{run_id}`) is addressing infrastructure from feature
027 and changing it would be out of scope and risk breaking existing stored conversations.

---

## Summary of resolved unknowns

| Unknown | Resolution |
|---------|------------|
| Deployment segment source | Raw `payload["model"]` (not resolved reference) |
| Display name source in worker | `ScheduleRepository.get_by_id(schedule_id).display_name` |
| Slug algorithm | Lowercase → collapse non-alphanumeric to `-` → trim → truncate → placeholder |
| Separator | `__` |
| Empty-segment placeholder | `untitled` |
| Max segment length | 64 (slugified segments only; run id never truncated) |
| Assembly location | Pure `build_conversation_name` helper, called from `build_conversation` |
| Storage path | Unchanged (only `name` changes) |
