# Quickstart: Validating Conversation Path Encoding

**Feature**: 034-conversation-path-encoding | **Date**: 2026-08-06

How to prove this feature works. Details of the encoding rule live in
[contracts/conversation-path.md](contracts/conversation-path.md); field-level changes in
[data-model.md](data-model.md). This document is the run/validation guide only.

## Prerequisites

```powershell
uv sync
```

A running DIAL instance is required only for the end-to-end check in step 4. Steps 1–3 are offline.

---

## 1. Quality gates

```powershell
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest --cov=src
```

All four must pass. Per the constitution's TDD requirement, the unit tests below are written and
confirmed **failing** before any `src/` change is made.

---

## 2. Unit validation

```powershell
uv run pytest tests/unit/worker/test_conversation.py -v
```

Scenarios that must be covered, mapped to requirements:

| Scenario | Expectation | Req |
|---|---|---|
| Application-backed schedule | name's deployment part decodes to the `applications/...` id; the resolved reference appears nowhere in the path | FR-001, FR-003, SC-001 |
| Application-backed schedule | `conversation.model.id` is still the resolved reference | FR-010, SC-007 |
| Two apps sharing one backing model | produce different paths | US1 scenario 2 |
| Model-backed schedule | same rule, no branching | FR-002, US1 scenario 3 |
| Deployment id with `/` | encoded as `%2F`; final segment contains no bare `/` | FR-004, US2 scenario 1 |
| App id containing `__` (e.g. `Untitled app 1101__0.0.1`) | encoded `%5F%5F`; `name.split("__")` still yields exactly 3 parts | FR-008, research R-002 |
| Name `Daily Report Sync` | decodes to exactly that — case and spaces intact, no hyphens | FR-005, FR-006, SC-003 |
| Names `Weekly Sync` vs `weekly-sync` | distinct paths | SC-004 |
| Name `Привет` | round-trips; **not** transliterated to `privet` | FR-007, SC-003 |
| Name containing `__` or `/` | round-trips; split still yields 3 parts | FR-008 |
| 80-character name | no truncation; full name round-trips | research R-003 |
| `conversation.name` vs final path segment | byte-identical | FR-009 |
| `folderId` | unchanged shape, still `conversations/{bucket}/.scheduler/{sid}` | R-005 |

The single most important assertion, in one line:

```python
assert unquote(conv.name.split("__")[0]) == "applications/bkt/Untitled app 1101__0.0.1"
```

Existing tests that **must be updated, not deleted** — they encode the old behaviour:

- `test_name_follows_convention`, `test_id_and_folder_follow_convention`
- `test_build_conversation_handles_edge_display_names` — the `("Привет", "privet")` and
  `("a"*80, "a"*64)` cases now assert round-trip, not transliteration/truncation
- `TestCreateRunConversationApplication::test_application_deployment_resolves_reference` — the
  reference must still reach `model.id`, but must no longer appear in the name

---

## 3. Transport check (no double-encoding)

Assert on the encoded wire bytes, never on `httpx.URL.path` (which shows a decoded view and will
mislead you — see research R-001):

```python
assert b"%2F" in httpx.URL(called_url).raw_path
```

---

## 4. End-to-end against a live DIAL — **required before merge**

> This step is not optional polish. It closes **research R-007 / review C1**: nothing offline proves
> DIAL Core preserves percent-escapes in a stored path segment. If it decodes them, application ids
> split back into nested paths — the exact defect US2 exists to prevent — and because FR-011
> deliberately swallows conversation-write failures, **the scheduler still reports success**. All
> three failure modes are silent, so "nobody complained" is not evidence. Record the outcome in
> `research.md` R-007.


Use the existing recipe:

```text
tests/e2e/1.3-Create-chat-completion-schedule-oauth-with-conversation-creation.http
```

Adapt it to cover both deployment kinds:

1. Create a schedule with `create_conversation: true`, `display_name: "Daily Report Sync"`, targeting a
   plain model. Trigger it; wait for the run to succeed.
2. Repeat targeting an `applications/...` deployment whose id contains a space or `__`.

Then confirm, for each:

- `GET /v1/conversations/{bucket}/.scheduler/{schedule_id}/` lists the new conversation.
- The listed name percent-decodes to the exact deployment id and schedule name.
- The stored document opens in DIAL Chat, and the application-backed one opens against its resolved
  model exactly as before this change.

---

## 5. Non-regression on failure handling

Conversation creation is best-effort. Force a failure (e.g. an unreachable DIAL) and confirm the run
still completes successfully with a single `conversation_create_failed` log entry carrying `run_id`
and `schedule_id`, and no credentials or prompt content. This path already exists at
`src/worker/jobs.py:81-89` and must remain untouched (FR-011).
