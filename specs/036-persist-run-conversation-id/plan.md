# Implementation Plan: Persist Conversation ID for Schedule Runs

**Branch**: `036-persist-run-conversation-id` | **Date**: 2026-08-26 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/036-persist-run-conversation-id/spec.md`

## Summary

Persist the DIAL conversation identifier that is already generated during a successful run's
conversation-creation step, and expose it in the runs API. The identifier — the full addressable
`conversation.id` (`conversations/...`) that DIAL Chat uses to reopen a conversation — is currently
built and then discarded after the `PUT`. The change is a single additive, nullable
`schedule_runs.conversation_id` column, populated best-effort inside the existing guarded
conversation step (`create_run_conversation` returns the id; `_maybe_create_conversation` persists it
via a new `ScheduleRunRepository.set_conversation_id` UPDATE in its own session), and surfaced as a
nullable field on `RunListItem` (mandated) and `RunResponse` (consistency). SQL `NULL` is the direct,
backward-compatible representation of "no conversation", so historical and never-created runs need no
backfill.

## Technical Context

**Language/Version**: Python 3.14
**Primary Dependencies**: No new dependencies — reuses SQLAlchemy/SQLModel, Alembic, Pydantic v2,
FastAPI, APScheduler worker, structlog, all already in the project.
**Storage**: One additive column, `schedule_runs.conversation_id VARCHAR NULL` (unbounded, no
server default, no index, no FK). No backfill; pre-existing rows read `NULL`.
**Testing**: pytest + pytest-asyncio (`asyncio_mode = "auto"`), pytest-mock. TDD per Constitution III.
**Target Platform**: Linux container — API service + APScheduler worker sharing `src/lib/`.
**Project Type**: Web service (API + APScheduler worker sharing `lib/`).
**Performance Goals**: SC-001/SC-002 — 100% correct presence/absence of the id per run; SC-004 — 0%
of runs change success/failure outcome; SC-005 — 0% id drift across repeated listings. No latency
budget impact (one extra targeted UPDATE only on the success + conversation-enabled path).
**Constraints**: Persist strictly *after* confirmed conversation creation (FR-001/FR-003); persist is
best-effort and non-fatal, never altering run outcome (FR-006); id must remain stable once written
(FR-007); the persist must run in its own session so it can never roll back `_persist_success`.
**Scale/Scope**: Small. ~6 source files touched (model, migration, repository, worker conversation,
worker jobs, schemas, service) plus their mirrored tests. No endpoint code change (fields flow
through the existing `response_model`s).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Pre-Design | Post-Design | Notes |
|-----------|-----------|-------------|-------|
| I. Reliability First | ✅ | ✅ | Persist is best-effort in the existing `try/except`; runs in its own session so it cannot roll back the success write; DB error is logged and swallowed (FR-006, SC-004). No new blocking I/O — the UPDATE is `async`. |
| II. Observability (NON-NEGOTIABLE) | ✅ | ✅ | New structured events `conversation_id_persisted` (info) and `conversation_id_persist_failed` (warning), both carrying `run_id` + `schedule_id`, via `src.lib.utils.logging.logger`. No `print()`. The id is a storage path in the owner's own bucket, not a secret; no prompt/response/credential logged. Every run outcome remains persisted (existing behaviour unchanged). |
| III. Test-First (NON-NEGOTIABLE) | ✅ | ✅ | data-model.md §6 enumerates 21 test cases (model/migration, schemas, repository, worker `conversation.py` + `jobs.py`, service, endpoint/integration) written RED-first before implementation. |
| IV. Contract-Driven API | ✅ | ✅ | Two contract files under `contracts/`; both changes are additive nullable response fields — non-breaking, no major version bump. Worker↔repository boundary stays typed (`set_conversation_id(run_id, conversation_id) -> ScheduleRun \| None`); no ad-hoc dicts. |
| V. Simplicity & YAGNI | ✅ | ✅ | A single nullable column on the run row — no `run_conversations` table, no sentinel, no backfill, no new state value. `set_conversation_id` copies the existing `mark_success`/`mark_error` shape. Methods stay within the 2–3 parameter limit. |

No violations. Complexity Tracking table is empty (below).

## Patterns.md Issues Flagged

- **Persist runs in a separate session from the success write.** `_persist_success` has already
  committed the run as `success` before `_maybe_create_conversation` runs; `set_conversation_id` must
  open its **own** `Database().sessionmaker()` session so a persist failure can never roll back the
  recorded success (FR-006, SC-004). Do not fold the id write into the success transaction.
- **Persist strictly after the confirmed `PUT`.** `create_run_conversation` must only *return*
  `conversation.id` after a successful `put_conversation`; the id is written to the DB solely in
  `jobs.py`. Never persist a placeholder before creation succeeds (FR-003), and keep
  `conversation.py` free of any `schedule_runs` write (single owner of run persistence is `jobs.py`).
- **Unbounded `VARCHAR`, no length cap.** The conversation id/path is unbounded (bucket + schedule id
  + optional application-folder segments + un-slugified display name + run id). A `String(N)` cap
  risks silently truncating a valid id into an un-openable one (SC-003) — use `sa.String()` with no
  length.
- **No index / no FK.** The runs list filters by the already-indexed `schedule_id` and never queries
  by `conversation_id`; it is not a reference to any scheduler-owned table.

## Project Structure

### Documentation (this feature)

```text
specs/036-persist-run-conversation-id/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── runs-list-conversation-id.md
│   └── run-detail-conversation-id.md
├── checklists/
│   └── requirements.md
└── tasks.md            # generated next by /speckit-tasks
```

### Source Code (files this feature touches)

```text
src/
├── lib/
│   ├── models/
│   │   └── schedule_run.py                     # + conversation_id: Optional[str] (nullable)
│   ├── repositories/
│   │   └── schedule_run_repository.py          # + set_conversation_id(run_id, conversation_id)
│   ├── schemas/
│   │   └── run.py                              # + conversation_id on RunListItem & RunResponse
│   └── services/
│       └── schedule_run_service.py             # map conversation_id in list_runs & get_run
├── worker/
│   ├── conversation.py                         # create_run_conversation -> str (returns id)
│   └── jobs.py                                 # _maybe_create_conversation: capture + persist id
└── api/
    └── v1/endpoints/runs.py                    # NO code change (fields flow via response_model)

migrations/versions/
└── 20260826_1200_<rev>_add_conversation_id_to_schedule_runs.py   # add_column NULL; down_revision f6a4d5e7b8c9

tests/                                          # mirrors src/ — see data-model.md §6 (21 cases)
├── lib/models/test_schedule_run.py
├── lib/repositories/test_schedule_run_repository.py
├── lib/schemas/test_run.py
├── lib/services/test_schedule_run_service.py
├── worker/test_conversation.py
├── worker/test_jobs.py
└── integration/                                # end-to-end list/detail conversation_id flows
```

## Complexity Tracking

*No Constitution Check violations — nothing to justify.*

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
