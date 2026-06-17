# Code Review Report — Schedule Metadata & Filtering

- **Spec dir**: `specs/019-schedule-metadata-filtering/`
- **Branch**: `019-schedule-metadata-filtering`
- **Date**: 2026-06-17
- **Mode**: Fresh Review
- **Artifacts consulted**: `spec.md`, `plan.md`, `data-model.md`, `research.md`, `tasks.md`, `contracts/schedules-metadata.md`, `.specify/memory/constitution.md`

## Summary

**Approved with minor suggestions.** The implementation is clean, faithful to the spec/plan, and well-tested. JSONB containment usage is correct and type-aware (satisfying the string-only matching requirement FR-009 without per-key guards), the migration is correct and reversible, and the rollback handling in `update` correctly restores prior metadata. `ruff check`, `mypy src`, and the 90 affected unit tests all pass. No Critical or Major issues. Findings are limited to one unit-test coverage gap and a few nitpicks.

Verification performed during review:
- `uv run pytest` (affected unit tests) → 90 passed.
- `uv run ruff check src tests` → All checks passed.
- `uv run mypy src` → Success, no issues.
- Compiled the containment expression: `schedules.metadata @> CAST(:param::JSONB AS JSONB)` — correct operator and bind.
- Inspected the ORM column: name `metadata`, type `JSONB`, `nullable=False`, `server_default '{}'::jsonb` — matches data-model.md.
- Migration chain: `a1b2c3d4e5f6` → down_revision `092b5f51aa98` (the prior head). Correct.

---

## Critical Issues

None.

---

## Major Issues

None.

---

## Minor Issues

### M1 — Repository unit tests do not exercise the `metadata_filter` containment path — Status: Open
**File**: `tests/unit/lib/repositories/test_schedule_repository.py:206-242`

`TestScheduleRepositoryList` only calls `repo.list(...)` without `metadata_filter`. The branch that builds the `@>` containment predicate (`schedule_repository.py:118-119`) has no dedicated **unit** test. It is covered by `tests/integration/test_schedule_metadata.py`, but those tests are skipped when `TEST_DB_URL` is unset (the default CI/local fast path), so the SQL-construction branch can silently rot in unit runs.

`tasks.md` T016 explicitly calls for a repository test covering "AND across keys, string-only matching, owner scoping, unmatched key → empty, and pagination over filtered results." That intent is currently satisfied only at the integration layer.

**Recommendation**: Add a unit test asserting that, when `metadata_filter` is non-empty, both the count statement and page statement carry the containment predicate. A lightweight approach is to capture the statements passed to `session.execute` (via the `AsyncMock` `side_effect`/`call_args_list`) and assert the compiled SQL contains `@>` and the `metadata` column, e.g.:

```python
async def test_list_applies_metadata_containment_filter(self):
    session = AsyncMock()
    count_result = MagicMock(); count_result.scalar_one.return_value = 0
    page_result = MagicMock(); page_result.__iter__ = MagicMock(return_value=iter([]))
    session.execute = AsyncMock(side_effect=[count_result, page_result])
    repo = ScheduleRepository(session)

    await repo.list("u", limit=10, offset=0, metadata_filter={"tenant": "hcscd"})

    count_sql = str(session.execute.await_args_list[0].args[0])
    assert "@>" in count_sql and "metadata" in count_sql
```

This keeps fast-path coverage on the load-bearing branch independent of a live database.

---

## Nitpicks

### N1 — Model test does not assert the new `metadata` column — Status: Open
**File**: `tests/unit/lib/models/test_schedule.py`

`TestScheduleModel` asserts column type/nullability for `id`, `trigger`, `kwargs`, etc., but adds nothing for the new `metadata` column. Consider adding assertions mirroring the other columns to lock the contract in (`schedule_metadata` maps to DB column `metadata`, type `JSONB`, `nullable is False`, and a server default is present):

```python
def test_metadata_column_name_and_type(self) -> None:
    col = Schedule.__table__.c["metadata"]
    assert isinstance(col.type, JSONB)
    assert col.nullable is False
    assert col.server_default is not None
```

This is the only structural attribute of the feature with no unit assertion.

### N2 — Redundant double cast in the containment expression — Status: Open
**File**: `src/lib/repositories/schedule_repository.py:119`

`cast(dict(metadata_filter), JSONB)` compiles to `CAST(:param::JSONB AS JSONB)` — the bind already renders as `::JSONB`, so the outer `CAST(... AS JSONB)` is redundant (harmless, but noise). `metadata_col.op("@>")(bindparam(..., type_=JSONB))` or `func`-free `column.contains()` would be tidier. Also, `dict(metadata_filter)` is a defensive copy of an already-`dict` value; it is harmless but unnecessary. Low priority — leave as-is if you prefer the explicit cast for readability.

### N3 — `_to_response` reads `schedule.kwargs` into `properties` (pre-existing) — Status: Open
**File**: `src/lib/services/schedule_service.py:51-53`

Not part of this change, but adjacent: `properties=schedule.kwargs` maps the ORM `kwargs` column to the response `properties` field. This naming mismatch predates the feature and is out of scope; flagging only so it is not mistaken for a metadata-related defect.

---

## Intent Alignment

The implementation matches the spec and plan with no divergences found:

- **FR-001/002/003/004/005** (store/replace/default metadata): `ScheduleRequest.metadata` defaults to `{}` (`schemas/schedule.py:93`); create persists it (`schedule_service.py:75`); update replaces it via the repo `metadata` kwarg (`schedule_service.py:122`). ✔
- **FR-006** (details returns metadata): `_to_response` maps `schedule.schedule_metadata → ScheduleResponse.metadata` (`schedule_service.py:53`); `ScheduleResponse.metadata` added (`schemas/schedule.py:111`). ✔
- **FR-007** (`metadata__<key>` query params): `parse_metadata_filter` strips the first prefix, supports `__` in keys, skips bare `metadata__`, last-value-wins — all matching research R5 and the spec's edge cases. ✔ Unit tests cover every documented edge case (`test_metadata_filter.py`).
- **FR-008** (AND semantics): single containment object built from all pairs. ✔
- **FR-009** (string-only matching): `@>` containment is type-aware; verified the integration test `test_string_only_matching_excludes_numeric` asserts a numeric `{"count": 5}` does not match `metadata__count=5`. ✔ This is the spec's most subtle requirement and it is correctly handled with zero custom type-checking, exactly as plan/research R2 prescribe.
- **FR-010** (no-filter parity): when the filter map is empty/`None`, no predicate is appended; the query is byte-for-byte the prior behavior (`schedule_repository.py:117-119`). ✔
- **FR-011** (owner-scoped): `created_by == created_by` is always in `conditions`; integration test `test_filter_is_owner_scoped` confirms. ✔
- **FR-012** (pagination composes): the containment predicate is applied to *both* the count and the page statements (`schedule_repository.py:121-132`), so `count` reflects the filtered total — exactly as data-model.md requires. ✔
- **FR-013** (absent key → empty, no error): covered by `test_unmatched_key_returns_empty`. ✔
- **FR-014** (pre-existing rows = empty metadata): NOT NULL + `server_default '{}'::jsonb` backfills existing rows. ✔
- **Reserved attribute (R1)**: column mapped under `schedule_metadata`, DB column pinned to `metadata` via `sa_column=Column("metadata", ...)`. ✔
- **Indexing (R3)**: GIN index `IX_schedules_metadata` with `postgresql_using="gin"` created in the same migration; downgrade drops index then column. ✔
- **R6**: metadata added to `ScheduleResponse` only, not `ScheduleListItem`. ✔

## Constitution Compliance

- **I. Reliability**: NOT NULL + server default for safe backfill; `update` rollback restores `display_name`, `trigger`, `kwargs`, `metadata`, and `updated_at`, with a nested restore-failure path that logs and still raises `JobRegistrationError`. The metadata rollback is correctly wired (verified by `test_update_restores_prior_metadata_on_job_failure`). PASS.
- **II. Observability**: No metadata *values* are logged anywhere; log events carry only `schedule_id` and exception reasons. No `print()`. PASS.
- **III. Test-First / Coverage**: Schema, service, parser, model, and integration tests present and passing. One unit-coverage gap on the repository filter branch (see M1). Substantially PASS.
- **IV. Contract-Driven API**: `metadata` added to request/response schemas; filter documented in `contracts/schedules-metadata.md`. Typed signatures throughout. PASS.
- **V. Simplicity & YAGNI**: Native `@>` instead of custom per-key SQL; single dependency for parsing; keyword-only `metadata_filter`/`metadata` params keep signatures within limits. PASS.
- **No raw SQL**: containment built via SQLAlchemy expression API (`.op("@>")` + `cast`). PASS.
- **Async I/O / type hints / ruff (line-length 120) / empty `__init__.py`**: `mypy` and `ruff` both clean; no blocking calls introduced. PASS.

## Checklist Pass

The following areas passed without issues: Correctness & JSONB semantics; Type safety (mypy clean); Style (ruff clean); Architecture/layering (parser in `api/dependencies`, logic in service, access in repository); Database/ORM (no raw SQL, correct migration + GIN index, async sessions); Project structure; Security (no secrets/metadata values logged); Intent alignment with all 14 functional requirements.

The only actionable item before considering the test suite complete is **M1** (repository unit test for the containment branch); **N1/N2/N3** are optional polish.

---

## Re-review (round 2) — Verification

- **Date**: 2026-06-17
- **Mode**: Verification
- **Scope**: Confirm M1/N1/N2 are resolved, the `.contains()` containment remains correct, and no regressions were introduced. Source files were not modified.

**Verdict: Approved.** All three addressed findings are resolved. The containment semantics are unchanged and still correct (type-aware string-only matching applied to both the count and page statements). No regressions found.

### M1 — Status: Resolved
`tests/unit/lib/repositories/test_schedule_repository.py` now contains `test_list_applies_metadata_containment_filter` (lines 233-250), which compiles both statements via a `_compiled_statements` helper (PostgreSQL dialect) and asserts each carries `@>` and `metadata`, plus `test_list_without_filter_has_no_containment` (252-265) and `test_list_empty_filter_has_no_containment` (267-280) which assert no `@>` predicate when the filter is `None`/empty. This closes the fast-path coverage gap on the load-bearing branch without requiring a live DB; it also pins the FR-012 requirement (predicate on both count and page) at the unit level.

### N1 — Status: Resolved
`tests/unit/lib/models/test_schedule.py` adds five assertions for the new column (lines 40-60): the `metadata` column exists, is `JSONB`, is `not nullable`, has a `server_default`, and `schedule_metadata` defaults to `{}`. The structural contract is now locked at the unit level.

### N2 — Status: Resolved
`src/lib/repositories/schedule_repository.py:120` now uses `metadata_col.contains(dict(metadata_filter))`. Compiling the expression yields `schedules.metadata @> :param::JSONB` — a single cast; the redundant outer `CAST(... AS JSONB)` is gone. The now-unused `cast` and `JSONB` imports were removed (imports are `Column, func, outerjoin, select`). The `dict(metadata_filter)` defensive copy remains (harmless).

### N3 — Status: Open (out of scope, unchanged)
Pre-existing `kwargs`→`properties` naming in `_to_response`; intentionally left out of scope as previously noted.

### Containment correctness (re-confirmed)
- Operator: `.contains()` on a JSONB column compiles to `@>`, which is type-aware — a string filter value matches only string-valued JSON entries, satisfying FR-009 with no per-key guards. Behavior is byte-for-byte equivalent to the prior `cast(..., JSONB)` form (same operator, same bind type), so the fix is purely cosmetic at the SQL level.
- Both `count_stmt` (line 122) and `page_stmt` (line 129) share the same `conditions` list, so the predicate is applied to both — FR-012 preserved.
- No-filter / empty-filter parity (FR-010) preserved: the `if metadata_filter:` guard skips the predicate, now asserted by the two new negative unit tests.

### Regression check
- No new issues introduced. The repository module's public signatures, imports, and `update`/`get`/`create`/`delete` paths are unchanged.
- Affected unit suite: `pytest tests/unit/lib/repositories/test_schedule_repository.py tests/unit/lib/models/test_schedule.py` → 33 passed.
- Full suite reported by author: 348 passed / 92 skipped; `ruff check`/`format` clean; `mypy src` clean.
