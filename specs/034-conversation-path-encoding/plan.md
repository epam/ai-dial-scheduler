# Implementation Plan: Conversation Path Encoding for Schedule Executions

**Branch**: `034-conversation-path-encoding` | **Date**: 2026-08-06 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/034-conversation-path-encoding/spec.md`

## Summary

Conversations created after a schedule run are currently filed under the *resolved model reference*
for application-backed schedules, and under a *slugified* schedule name. Both are lossy: two
applications sharing a backing model become indistinguishable, and names like `Привет` are
transliterated to `privet` while `Weekly Sync` and `weekly-sync` collapse together.

This feature replaces both with URL-encoded values in the storage path: the deployment id exactly as
configured on the schedule (one rule for models and applications alike), and the schedule name
verbatim. The conversation *document* is deliberately left alone — it still records the resolved
reference as `model.id`, so reopening an application-backed conversation behaves exactly as today.

The change is confined to the naming/encoding logic in `src/worker/conversation.py` plus one new field
on `ConversationBuildInput`. No API change, no database change, no migration. The `slugify` dependency
is dropped.

Three findings from Phase 0 shape the implementation and are not deducible from the spec:

1. Plain `quote(v, safe="")` leaves `_` untouched, so an application id such as
   `Untitled app 1101__0.0.1` — which appears in this repo's own fixtures — injects a false separator
   into a `__`-joined name. Underscore must be encoded too (research R-002).
2. The 64-character segment cap cannot survive: truncating encoded text can sever a `%XX` escape, and
   truncating raw text contradicts the lossless-decode requirement. The cap is removed (R-003).
3. httpx preserves existing percent-escapes on the wire, so `DialClient` needs no change (R-001).

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: httpx (transport, verified encoding-preserving), Pydantic v2 (`ConversationBuildInput`), `urllib.parse.quote`/`unquote` (stdlib — the whole encoding rule). `python-slugify` is **removed**.

**Storage**: PostgreSQL via SQLAlchemy/SQLModel — **not touched by this feature**. The affected store is external DIAL conversation storage, written over HTTP.

**Testing**: pytest + pytest-asyncio (`asyncio_mode = "auto"`), pytest-mock. Primary suite: `tests/unit/worker/test_conversation.py`.

**Target Platform**: Linux container — the APScheduler background worker service.

**Project Type**: Backend web service (FastAPI API + background worker), shared `src/lib/`.

**Performance Goals**: Not a factor. The change is pure string manipulation on an already-existing best-effort post-run path; it removes a dependency rather than adding work.

**Constraints**: Encoding must be exactly invertible (FR-007) and must keep the three `__`-joined parts separable (FR-008) — these two together are what force the underscore-encoding decision. Conversation creation stays best-effort: no failure may alter a run outcome (FR-011). No migration of existing conversations (FR-012).

**Scale/Scope**: One module changed (`src/worker/conversation.py`), one schema field added, one constant and one dependency removed. Roughly 40 lines of production change; the bulk of the work is test coverage.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design.*

| Principle | Assessment | Verdict |
|---|---|---|
| **I. Reliability First** | Encoding is total — defined for every input string, no exception path of its own. The existing best-effort wrapper at `src/worker/jobs.py:81-89` continues to absorb failures without touching the run outcome. Removing truncation removes a silent-corruption class (severed `%XX` escapes) rather than adding one. Transport behaviour verified empirically, not assumed. | PASS |
| **II. Observability** | No new decision points, so no new log entries are required. The existing `conversation_create_start` / `conversation_create_succeeded` / `conversation_create_failed` entries already carry `run_id` and `schedule_id`. Deployment ids and schedule names are user-supplied but non-secret; no credentials or prompt content enter any log. | PASS |
| **III. Test-First** | Tests are written and confirmed failing before `src/` changes. The behaviour is pure and deterministic, so nearly all of it is unit-testable; round-trip properties are asserted directly. Existing tests that encode the old convention are **updated**, never deleted — listed explicitly in [quickstart.md](quickstart.md). | PASS |
| **IV. Contract-Driven API** | The scheduler's REST surface is unchanged (no OpenAPI delta). The outbound DIAL naming convention *is* a contract and is versioned explicitly at [contracts/conversation-path.md](contracts/conversation-path.md) v2.0.0 — a breaking change from the 031 convention, with the non-migration of old conversations stated for consumers. The internal API/worker boundary stays a typed Pydantic model. | PASS |
| **V. Simplicity & YAGNI** | Two stdlib calls and one `.replace`. No new abstraction, no new module, no encoding-strategy indirection. The helper stays private to its only consumer rather than being promoted to `src/lib/utils/` speculatively. A dependency is deleted. `ConversationBuildInput` gains one field, keeping the builder's parameter count at one model. | PASS |

**Result**: no violations. Complexity Tracking table is not required and is omitted.

**Post-Phase-1 re-evaluation**: the design added no modules, no abstractions, and no dependencies; it
removed one constant and one dependency. All five verdicts stand unchanged.

One judgement call worth surfacing rather than burying: encoding `_` as `%5F` makes ordinary
underscores in schedule names render as escapes, which is mildly ugly. It is accepted because FR-009
already commits to showing users percent-escapes, and because the alternative — a name that splits
into the wrong number of parts for a real application id already present in the fixtures — is a
correctness defect, not a cosmetic one.

## Project Structure

### Documentation (this feature)

```text
specs/034-conversation-path-encoding/
├── plan.md                         # This file
├── spec.md                         # Feature specification
├── research.md                     # Phase 0 — R-001..R-006
├── data-model.md                   # Phase 1 — ConversationBuildInput delta
├── quickstart.md                   # Phase 1 — validation guide
├── contracts/
│   └── conversation-path.md        # Phase 1 — outbound naming contract v2.0.0
├── checklists/
│   └── requirements.md             # Spec quality checklist
└── tasks.md                        # Phase 2 — created by /speckit-tasks
```

### Source Code (repository root)

```text
src/
├── worker/
│   └── conversation.py             # CHANGED — encoding helper; name/path construction;
│                                   #   pass deployment_id through; drop slugify import
└── lib/
    ├── schemas/
    │   └── conversation.py         # CHANGED — ConversationBuildInput gains deployment_id
    ├── constants.py                # CHANGED — remove CONVERSATION_NAME_SEGMENT_MAX_LENGTH
    └── clients/
        └── dial_client.py          # UNCHANGED — httpx preserves escapes (R-001)

tests/
├── unit/
│   └── worker/
│       └── test_conversation.py    # CHANGED — round-trip, separator, and edge-input coverage
└── e2e/
    └── 1.3-Create-chat-completion-schedule-oauth-with-conversation-creation.http
                                    # CHANGED — extend to cover an application deployment

pyproject.toml                      # CHANGED — drop python-slugify
```

**Structure Decision**: The existing `api/` + `worker/` + `lib/` layout is kept as-is. The encoding
helper stays private to `src/worker/conversation.py`, its only consumer — promoting it to
`src/lib/utils/` would be speculative generality (Principle V). `src/lib/` acquires no dependency on
`src/worker/`, so the inward-only dependency rule holds. No new directories.

## Implementation Approach

Ordered so each step is independently verifiable. TDD applies throughout: the test for a step is
written and confirmed failing first.

1. **Add `deployment_id` to `ConversationBuildInput`** (`src/lib/schemas/conversation.py`). A required
   `str` alongside the existing `model_id`. Update the docstring to spell out the split — path-facing
   vs document-facing — because the distinction is exactly what a future reader will get wrong.

2. **Add the encoding helper** (`src/worker/conversation.py`), private, one line of logic:
   `quote(value, safe="").replace("_", "%5F")`. Its docstring must state *why* the underscore step
   exists — otherwise it reads as a pointless flourish and will be "cleaned up" by someone later.

3. **Rewrite `_conversation_name`** to join `encode(deployment_id)`, `encode(display_name)`, and the
   raw `run_id` on `CONVERSATION_NAME_SEPARATOR`. Delete the `slugify` import and the `max_length`
   argument. `conversation_resource_path` and `build_conversation` need no structural change — they
   consume the name and derive `id`/`folderId` as before (R-005).

4. **Populate `deployment_id` in `create_run_conversation`** from the raw `payload["model"]`, while
   `model_id` keeps its current resolution (application `reference`, else the deployment id). **Retain
   the `get_application` call** — FR-010 depends on it. This is the step most at risk of a
   well-intentioned wrong simplification.

5. **Remove `CONVERSATION_NAME_SEGMENT_MAX_LENGTH`** from `src/lib/constants.py` once no references
   remain.

6. **Drop `python-slugify`** from `pyproject.toml`, re-grep to confirm no remaining import, `uv sync`,
   and re-run the suite.

7. **Update and extend tests** per the table in [quickstart.md](quickstart.md), then run the four
   quality gates.

## Risks

| Risk | Mitigation |
|---|---|
| Someone removes the `get_application` lookup as dead code, silently regressing FR-010 | Called out in R-004, the contract, and step 4; covered by a test asserting `model.id` still equals the reference |
| Someone "simplifies" `.replace("_", "%5F")` away | Rationale in the helper's docstring, in R-002, and pinned by a test using the real `Untitled app 1101__0.0.1` fixture |
| A test asserts on `httpx.URL.path` (decoded) and appears to prove double-decoding | R-001 and quickstart step 3 both direct assertions at `raw_path` |
| Unbounded `display_name` yields an over-long path that DIAL rejects | Accepted and non-fatal by design (FR-011, existing handler). Adding `max_length` to `display_name` is recorded as an out-of-scope follow-up in R-003 |
| Consumers of the old naming convention break | Contract versioned 2.0.0 with both forms documented; old conversations are explicitly not migrated (FR-012) |

## Out of Scope

- Migrating, relocating, or renaming conversations created before this change (FR-012).
- Adding a `max_length` validator to `ScheduleRequest.display_name` — a schedule-API contract change;
  see research R-003.
- Any change to `DialClient`, the scheduler's REST API, or the database schema.
