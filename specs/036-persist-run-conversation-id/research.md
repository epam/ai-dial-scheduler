# Phase 0 Research: Persist Conversation ID for Schedule Runs

**Spec**: [spec.md](./spec.md) | **Date**: 2026-08-26

## 1. Package versions / new dependencies

**No new dependencies.** Every moving part already exists in the codebase:

- The conversation identifier is already computed inside `src/worker/conversation.py`
  (`conversation.id`, prefixed with `CONVERSATION_ID_PREFIX = "conversations/"`); today it is
  discarded after the `PUT`.
- Persisting a nullable string column reuses SQLAlchemy/SQLModel + Alembic exactly as prior
  migrations (`String`, `nullable=True`).
- Returning it in the runs list reuses the existing `RunListItem` Pydantic v2 schema and the
  `ScheduleRunService.list_runs` → `ScheduleRunRepository.list_for_schedule` path.

Versions in play (unchanged): Python 3.14, FastAPI, SQLAlchemy 2.x/SQLModel, Alembic, Pydantic v2,
asyncpg (API), psycopg2 (APScheduler jobstore), structlog.

## 2. Key technical decisions

### Decision: Store the DIAL conversation resource id (with the `conversations/` prefix) as a nullable `VARCHAR`
**Decision**: Add `conversation_id: Optional[str]` to `ScheduleRun`, mapped to a nullable
`VARCHAR` column with **no** length cap that would truncate a real path, and no server default
(SQL `NULL` is the natural "no conversation" value). Persist the value `conversation.id`
(e.g. `conversations/{bucket}/.scheduler/{schedule_id}/.../{deployment}__{name}__{run-id}`),
i.e. the full addressable id including the `conversations/` prefix, not the bare resource path.
**Rationale**: FR-002 requires the identifier to "uniquely address the created conversation in DIAL
storage, so it can be used to locate and open that specific conversation". `conversation.id` is
exactly that addressable id and is what DIAL Chat uses to reopen a conversation; the value is built
once in `build_conversation` and reused as the `PUT` target (`resource_path =
conversation.id.removeprefix(CONVERSATION_ID_PREFIX)`), so persisting `conversation.id` guarantees
the stored id and the written resource never drift. FR-005 makes "no conversation" a nullable/absent
value; a nullable column with SQL `NULL` is the direct representation and needs no sentinel.
**Alternatives considered**:
- *Store the bare resource path (without `conversations/`)* — rejected: it would force every
  consumer to re-prepend the prefix to open the conversation, and the spec (Assumptions) says the
  persisted value is "the addressable conversation identifier/path". Keeping the full `id` is what a
  consumer can use directly.
- *Store a separately formatted shareable link* — rejected: explicitly out of scope (spec
  Assumptions). The addressable id is sufficient.
- *A bounded `String(N)`* — rejected: the path length is unbounded (bucket + schedule id + optional
  application folder segments + raw, un-slugified display name + run id). A cap risks silently
  truncating a valid id into an un-openable one, violating SC-003. Postgres `VARCHAR` without a
  length is `text`-like with no storage penalty.
- *A dedicated `run_conversations` table* — rejected by YAGNI: it is a strict 0..1 attribute of a
  run with no independent lifecycle, no second consumer, and no cardinality that a column cannot
  express (FR-008 "independent per run" is satisfied by the value living on the run row).

### Decision: Persist the id in the same transaction that marks the run successful, not in a separate write after conversation creation
**Decision**: `create_run_conversation` **returns** the created `conversation.id` (changing its
signature from `-> None` to `-> str`). The worker's `_maybe_create_conversation` captures that
return value and persists it via a new `ScheduleRunRepository.set_conversation_id(run_id,
conversation_id)` UPDATE, guarded by the same best-effort try/except that already wraps conversation
creation.
**Rationale**: FR-001 says persist *after* a conversation is *successfully* created; FR-003 forbids
persisting a placeholder for a conversation that was not created. The `PUT` to DIAL is the
success signal, and it happens inside `create_run_conversation` — so the return-then-persist shape
keeps the write strictly downstream of the confirmed creation. FR-006 requires the persist to be
best-effort and non-fatal: reusing the existing `try/except Exception` in
`_maybe_create_conversation` (which already swallows conversation-creation failures) means a failed
id-persist logs a warning and leaves the column `NULL`, never altering the already-recorded success
(SC-004). The run has already been marked `success` by `_persist_success` *before*
`_maybe_create_conversation` runs (see `jobs.py`), so a distinct UPDATE is required regardless —
there is no in-flight success transaction to piggyback on.
**Alternatives considered**:
- *Fold the id into the `mark_success` write* — rejected: at `mark_success` time the conversation
  has not been created yet (creation is a later, optional, best-effort step), so the id is not
  available and forcing it earlier would break the "only after success" ordering (FR-003).
- *Have `create_run_conversation` itself write the column* — rejected: it would spread run-record
  persistence across two modules (`conversation.py` and `jobs.py` both writing `schedule_runs`) and
  couple the DIAL-facing builder to the run repository. Keeping `conversation.py` pure (build + PUT,
  return the id) and letting `jobs.py` own all `schedule_runs` writes matches the existing
  separation and keeps the best-effort boundary in one place.

### Decision: `set_conversation_id` is a targeted UPDATE that no-ops when the run row is missing
**Decision**: New repository method
`async def set_conversation_id(self, run_id: str, conversation_id: str) -> ScheduleRun | None`,
mirroring `mark_success`/`mark_error`: `session.get(ScheduleRun, uuid)`, set the field, commit,
return the row (or `None` if not found).
**Rationale**: Consistency with the three existing `mark_*` methods (same fetch-mutate-commit shape,
same `None`-on-missing contract) keeps the repository uniform and testable, and lets
`_maybe_create_conversation` log `run_record_missing` symmetrically with `_persist_success`.
**Alternatives considered**: A single conditional `UPDATE ... WHERE id = :id` bulk statement —
equally correct, but the `session.get` form matches the file's established style and the run is
already known to exist (it was just marked successful), so the extra selectivity buys nothing.

### Decision: Add `conversation_id` to `RunListItem` only; leave `RunResponse` alone (with a documented option)
**Decision**: Add `conversation_id: str | None = None` to `RunListItem` (the mandated surface,
FR-004). Also add it to `RunResponse` (single-run detail) as the backward-compatible extension the
spec explicitly permits, so the detail view and the list view agree.
**Rationale**: FR-004 mandates the runs *list*. The spec's Assumptions call the single-run detail a
"natural, backward-compatible extension [that] may be included during planning". Including it costs
one field and one mapping line in `get_run`, keeps the two run views consistent (a consumer that
opens a run detail sees the same id), and avoids a future breaking follow-up. Both are additive,
nullable fields — existing consumers are unaffected (spec Assumptions, Contract-Driven principle).
**Alternatives considered**: List-only — acceptable per FR-004, but omitting it from the detail view
would be an arbitrary inconsistency the spec already blesses closing.

### Decision: Historical / never-created runs read `NULL` via the migration, no backfill
**Decision**: The `ADD COLUMN ... NULL` (no server default) leaves every pre-existing row `NULL`.
No data migration.
**Rationale**: FR-005 requires historical runs (recorded before this feature) to report the id as
absent, which is precisely SQL `NULL`. There is no source of truth from which to reconstruct old
conversation ids, and none is required. This mirrors the additive-nullable pattern used across the
project.
**Alternatives considered**: A best-effort backfill re-deriving ids from `schedule_snapshot` +
`run_id` — rejected: the conversation may never have existed (creation disabled or failed), so a
derived id could point at nothing, directly violating FR-003/FR-005. Absent is the only correct
historical value.

## 3. Patterns to follow (references to existing code)

- **Nullable additive column + migration**: `20260810_1200_..._add_is_deleted_to_schedules.py`
  (spec 035) is the template for a single-column `op.add_column` / `op.drop_column` migration with a
  revision chain. This feature's migration differs only in being nullable with no server default.
  The `NOT NULL server_default` shape there is *not* copied — a conversation id has no meaningful
  default.
- **`schedule_runs` model field style**: `src/lib/models/schedule_run.py` — `Optional[...]` fields
  use `Field(default=None, sa_type=..., sa_column_kwargs={"nullable": True})`. `conversation_id`
  follows `end_time`/`result` exactly (nullable, `default=None`).
- **Repository mutation method**: `ScheduleRunRepository.mark_success` /
  `mark_error` (`src/lib/repositories/schedule_run_repository.py`) — `session.get` → mutate →
  `commit` → return row-or-`None`. `set_conversation_id` copies this shape.
- **Best-effort conversation step**: `jobs.py::_maybe_create_conversation` — the existing
  `try/except Exception` that logs `conversation_create_failed` and swallows. The id-persist is
  added *inside* the same guarded block so a persist failure is equally non-fatal (FR-006).
- **Conversation id source of truth**: `conversation.py::build_conversation` builds
  `conversation.id = f"{CONVERSATION_ID_PREFIX}{conversation_resource_path(build_input)}"`; the same
  value is the `PUT` target. Returning it from `create_run_conversation` is the minimal change (specs
  027 "create run conversation" and 031 "run conversation naming" established this id/path
  construction).
- **List → item mapping**: `ScheduleRunService.list_runs` maps each ORM `run` to a `RunListItem`;
  add `conversation_id=run.conversation_id`. `get_run` maps to `RunResponse` similarly.
- **Structured logging**: `src.lib.utils.logging.logger` with `run_id` / `schedule_id` context, per
  Constitution II. New event `conversation_id_persisted` / `conversation_id_persist_failed`.

## 4. Edge cases and failure modes (from spec) — technical approach

| Edge case (spec) | Technical approach | Backing FR |
|------------------|--------------------|-----------|
| **In-progress run** | Column is `NULL` at `create_in_progress`; it is only ever set inside `_maybe_create_conversation`, which runs only after `_persist_success`. An in-progress run has no id → serialised as `null`. | FR-005 |
| **Conversation creation fails after a successful run** | `create_run_conversation` raises `ConversationCreationError` *before* returning an id; the existing `except` logs `conversation_create_failed` and the persist line is never reached → column stays `NULL`, run stays `success`. | FR-005, FR-006 |
| **Persist step itself fails** (DIAL PUT succeeded, DB UPDATE failed) | The persist UPDATE is inside the same best-effort `try/except`; a DB error logs `conversation_id_persist_failed` (warning) and is swallowed → run outcome unchanged, id absent. Accepted residual: a conversation exists in DIAL but the run shows `null`. Non-fatal by FR-006; the conversation is still openable by browsing DIAL, and no run outcome changed (SC-004). | FR-006, SC-004 |
| **Historical runs** | Migration adds the column as `NULL`; pre-existing rows read `null`. | FR-005 |
| **Non-chat-completion schedules** | `_maybe_create_conversation` returns early unless `target_type == CHAT_COMPLETION and create_conversation`; the persist path is never entered → `null`. | FR-005 |
| **Conversation creation disabled** | Same early-return guard (`not properties.create_conversation`) → `null`. | FR-005 |
| **Re-runs of the same schedule** | The id is written per `run_id` (`set_conversation_id(run_id, ...)`), and the id itself embeds `run_id` (`{deployment}__{name}__{run-id}`). No shared row, no overwrite across runs. | FR-008, SC-005 |
| **Stability across listings** | Once written, the value is never mutated again (no code path updates it a second time; the run is terminal at `success`). Repeated `list_runs` reads the same column. | FR-007, SC-005 |

## 5. Observability (Constitution II)

New structured events via `src.lib.utils.logging.logger`, both carrying `run_id` and `schedule_id`,
no payload/credentials:

| Event | Level | When |
|-------|-------|------|
| `conversation_id_persisted` | info | UPDATE succeeded (row found) |
| `conversation_id_persist_failed` | warning | UPDATE raised, or (optionally) the run row was missing |

The DIAL conversation id is not a secret (it is a storage path in the owner's own bucket), so it may
appear in logs; no message content, prompt, response, or credential is logged. The existing
`conversation_create_start` / `conversation_create_succeeded` / `conversation_create_failed` events
are retained unchanged.
