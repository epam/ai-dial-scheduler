# Implementation Plan: Run Conversation Naming Convention

**Branch**: `031-run-conversation-naming` | **Date**: 2026-07-24 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/031-run-conversation-naming/spec.md`

## Summary

Replace the auto-created run conversation's display name (currently the bare `run_id`,
`src/worker/conversation.py:99`) with a three-part, human-recognizable convention:
`{deployment-slug}__{display-name-slug}__{run-id}`. The deployment segment comes from the
scheduled deployment id (`properties.payload["model"]`), the display-name segment from the
schedule's `display_name` (fetched in the worker via `ScheduleRepository.get_by_id`), and the
run id is embedded verbatim. Both slugified segments are lowercased, hyphen-collapsed,
trimmed, capped at 64 chars, and fall back to `untitled` when empty. Only `DialConversation.name`
changes; the conversation storage path/id, messages, and all other attributes are untouched.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: FastAPI, APScheduler, SQLAlchemy/SQLModel, Pydantic v2 (no new deps —
slugification uses stdlib `re`)

**Storage**: PostgreSQL (no schema change — display name is read from the existing
`schedules` table; no migration required)

**Testing**: pytest + pytest-asyncio (`asyncio_mode = "auto"`), pytest-mock

**Target Platform**: Linux server (background worker container)

**Project Type**: Web service (API + background worker sharing `lib/`)

**Performance Goals**: Negligible — one extra indexed primary-key read per conversation-creating
run; naming is pure in-memory string work.

**Constraints**: Deterministic naming (FR-008); no change to conversation storage path (FR-007);
conversation creation must never fail due to name inputs (SC-004); no secrets/content logged.

**Scale/Scope**: Small, localized change — one new pure helper, three constants, one new field
on `ConversationBuildInput`, and wiring in `build_conversation` + the worker job's conversation
path. Affects only chat completion schedules with `create_conversation=True`.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Reliability First | ✅ PASS | Naming is pure/deterministic; empty/long/non-Latin inputs handled via placeholder + truncation so conversation creation never fails on name inputs (SC-004). The extra schedule read is inside the existing best-effort conversation path (`_maybe_create_conversation` already swallows failures, `jobs.py:83`). |
| II. Observability | ✅ PASS | Existing `conversation_create_start/succeeded/failed` logs retained. No new decision points; no prompt/response/credentials logged. |
| III. Test-First | ✅ PASS | Pure `slugify` + `build_conversation_name` helpers and the updated `build_conversation` are unit-testable; TDD applies (tests written failing first). |
| IV. Contract-Driven API | ✅ PASS | No REST API change. The internal `ConversationBuildInput` (Pydantic model) is the typed contract between the worker job and the builder; it gains one field. No ad-hoc dicts. |
| V. Simplicity & YAGNI | ✅ PASS | Stdlib-only slug; no transliteration; no new abstraction beyond a small pure helper (justified by testability). `build_conversation`/helper keep ≤3 params via `ConversationBuildInput`. |

**Result**: PASS — no violations. Complexity Tracking not required.

**Post-Design Re-check**: PASS — Phase 1 artifacts introduce no new violations (see bottom).

## Project Structure

### Documentation (this feature)

```text
specs/031-run-conversation-naming/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── conversation-name.md
├── checklists/
│   └── requirements.md  # from /speckit-specify
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── lib/
│   ├── constants.py                 # + CONVERSATION_NAME_SEPARATOR,
│   │                                #   CONVERSATION_NAME_SEGMENT_PLACEHOLDER,
│   │                                #   CONVERSATION_NAME_SEGMENT_MAX_LENGTH
│   ├── schemas/
│   │   └── conversation.py          # ConversationBuildInput += display_name: str
│   └── utils/
│       └── naming.py                # NEW: slugify() + build_conversation_name()
└── worker/
    ├── conversation.py              # build_conversation uses build_conversation_name;
    │                                #   create_run_conversation threads display_name in
    └── jobs.py                      # fetch schedule.display_name for the conversation build

tests/
└── unit/
    ├── lib/
    │   └── utils/
    │       └── test_naming.py       # NEW: slugify + build_conversation_name edge cases
    └── worker/
        └── test_conversation.py     # updated: assert new name; ConversationBuildInput.display_name
```

**Structure Decision**: Single web-service layout (existing). The naming logic lives in
`src/lib/utils/naming.py` (pure, shared, no I/O) per the `lib/` layering rule; the worker
(`src/worker/`) consumes it. `lib/` does not import from `worker/` or `api/` (dependency arrow
flows inward). No migration and no API contract change.

## Complexity Tracking

> No Constitution Check violations — table intentionally empty.

## Post-Design Constitution Re-check

- **IV. Contract-Driven**: `ConversationBuildInput` gains `display_name: str` — a typed Pydantic
  field, documented in `contracts/conversation-name.md`. No dict passing. ✅
- **V. YAGNI**: Data model adds no entities/tables; one field + three constants + one helper
  module. ✅
- **III. Test-First**: `data-model.md` + `contracts/` enumerate the exact edge cases to drive
  failing tests first. ✅

**Result**: PASS.
