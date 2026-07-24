# Quickstart & Validation: Run Conversation Naming Convention

**Feature**: `031-run-conversation-naming`

This guide validates that auto-created run conversations are named
`{deployment-slug}__{display-name-slug}__{run-id}`. See [contracts/conversation-name.md](./contracts/conversation-name.md)
and [data-model.md](./data-model.md) for the precise rules.

## Prerequisites

- `uv sync`
- PostgreSQL reachable (worker DB) — only needed for the integration scenario.
- A DIAL environment with a chat completion deployment — only for the end-to-end scenario.

## Quality gates (run all before marking complete)

```bash
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
```

## Unit validation (primary, no external services)

Drives the pure naming logic — the core of this feature.

```bash
uv run pytest tests/unit/lib/utils/test_naming.py -v
uv run pytest tests/unit/worker/test_conversation.py -v
```

**Expected — `test_naming.py`** (write these failing first, TDD):
- `slugify("Daily GPT-4o Summary!", 64) == "daily-gpt-4o-summary"`
- `slugify("!!!", 64) == "untitled"` and `slugify("Привет", 64) == "untitled"`
- `slugify("a"*80, 64)` has length 64 and no trailing `-`
- `slugify` output never contains `__`
- `build_conversation_name("chathub-claude4", "hello", "964ff385-72e7-46f8-8cdc-fa3358b825ec")`
  `== "chathub-claude4__hello__964ff385-72e7-46f8-8cdc-fa3358b825ec"`
- `build_conversation_name(...).split("__", 2)` recovers three segments; last equals the run id verbatim

**Expected — `test_conversation.py`** (updated):
- `build_conversation(...)` sets `DialConversation.name` to the three-part name (not the bare run id).
- `ConversationBuildInput` now requires `deployment_id` and `display_name`; existing test
  fixtures updated accordingly.
- Conversation `id`/`folderId`/`messages` are unchanged by the new name (FR-007).

## Integration validation (worker path)

Confirms the worker sources `display_name` (via `ScheduleRepository.get_by_id`) and
`deployment_id` (from `payload["model"]`) into the build input.

```bash
uv run pytest tests/unit/worker/ -k conversation -v
```

**Expected**:
- With a schedule display name "hello" and payload model "chathub-claude4", a completed run's
  created conversation is named `chathub-claude4__hello__{run_id}`.
- A schedule whose display name is only punctuation still yields a valid name
  (`chathub-claude4__untitled__{run_id}`) and conversation creation succeeds (SC-004).

## End-to-end (manual, optional)

1. Create a chat completion schedule with a recognizable `display_name` and
   `properties.create_conversation = true`, targeting a known deployment id.
2. Let a run complete (or trigger one).
3. Open DIAL Chat and locate the created conversation.
4. **Verify**: its title reads `{deployment-slug}__{display-name-slug}__{run-id}`, matching the
   scheduled deployment and display name, with the run id as the final segment.

## Success criteria mapping

| Criterion | Validated by |
|-----------|--------------|
| SC-001 (pattern match) | unit + integration name assertions |
| SC-002 (identify deployment/schedule from name) | canonical example + E2E step 4 |
| SC-003 (uniqueness by run id) | `build_conversation_name` split test across two run ids |
| SC-004 (never fails on odd inputs) | punctuation / non-Latin / long-input slug tests |
