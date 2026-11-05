# Review Report — Schedule Management Endpoints

**spec-dir**: `specs/007-schedule-endpoints/`
**Date**: 2026-06-11
**Run mode**: Verification (second pass)
**Artifacts consulted**: `spec.md`, `plan.md`, `data-model.md`, `contracts/schedule-endpoints.md`, `documentation/api_contract.md`; cross-referenced `src/lib/models/schedule.py`, `src/lib/db/engine.py`, `src/lib/services/trigger_factory.py`, and the unit + integration tests under `tests/`.

## Summary

**Approved with minor suggestions.** All five previously-blocking critical issues (C1–C5) and all five improvements (I1–I5) are resolved, each with a dedicated regression test. `uv run mypy src` (39 files, clean), `uv run ruff check` (clean), and `uv run pytest` (112 passed, 13 skipped — the skips are the new Postgres integration tests gated on `TEST_DB_URL`) all pass. The implementation now matches the spec and the contract. Remaining notes are minor (one stale data-model wording, one informational isolation note); none block merge.

---

## Critical Issues

### C1 — `model_dump()` writes raw `datetime` into JSONB — Status: Resolved
**File**: `src/lib/services/schedule_service.py:64-65` (create), `:109-110` (update).
Both create and update now call `model_dump(mode="json")` for trigger and properties, so `datetime` fields are ISO strings before the JSONB write. Verified by `tests/unit/lib/services/test_schedule_service.py:79` (`test_..._mode_json`) which asserts no raw datetime reaches the persisted dict. Resolved correctly.

### C2 — Create/Update response round-trips an in-memory dict, not the persisted JSONB — Status: Resolved
**File**: `src/lib/services/schedule_service.py:89-91`, `:150-152`.
After registration both paths re-fetch via `repo.get(...)` and map the re-fetched ORM instance. With C1 fixed (ISO strings in), the in-memory dict and the JSONB-decoded form are identical strings, so the create/update response is consistent with what the Details endpoint returns. Consistent and resolved.

### C3 — Update rollback did not restore `updated_at` — Status: Resolved
**File**: `src/lib/services/schedule_service.py:106`, `:133-141`; `repo.update(..., updated_at=...)` at `src/lib/repositories/schedule_repository.py:65,77-78`.
`prior_updated_at` is now captured before the update and passed to the compensating `repo.update(..., updated_at=prior_updated_at)`, which sets the column explicitly (bypassing `onupdate`). Verified by `test_update_restores_updated_at_on_job_failure` (asserts the restore call's `updated_at` equals the prior value). Fully satisfies FR-012 / SC-003. Resolved.

### C4 — `ScheduleProperties` did not reject inapplicable fields — Status: Resolved
**File**: `src/lib/schemas/schedule.py:70-82`.
The validator now raises `ValueError` when `method` is supplied for `chat_completion` (`:73-74`) and when `api_version` is supplied for `rest`/`sse` (`:80-81`), and still requires `method` for non-chat types. Rejection (not coercion) was the chosen contract, matching the spec edge case at `spec.md:83`. Covered by `test_chat_completion_rejects_method`, `test_rest_rejects_api_version`, `test_sse_rejects_api_version`. Resolved. (See M1 for a stale data-model wording.)

### C5 — `validate_url` did not enforce a valid http(s) URL — Status: Resolved
**File**: `src/lib/schemas/schedule.py:9,64-68`.
A module-level `_HTTP_URL = TypeAdapter(AnyHttpUrl)` is now used via `_HTTP_URL.validate_python(v)`, which raises `ValidationError` on malformed input. Covered by `test_invalid_url_raises` (`"not-a-url"`) and `test_non_http_url_raises` (`"ftp://..."`). Resolved.

---

## Improvements

### I1 — `Paginator.paginate` lost per-call generic binding — Status: Resolved
**File**: `src/lib/schemas/pagination.py:8,30`.
A method-level `TypeVar _S` now binds `paginate(self, count, results: list[_S]) -> Page[_S]`, so the element type flows to the returned `Page`. `uv run mypy src` is clean and the service's `list` returns `Page[ScheduleListItem]` correctly. Resolved.

### I2 — `next_run_time` LEFT JOIN needed an integration test — Status: Resolved
**File**: `tests/integration/test_schedule_run_join.py`.
A Postgres integration suite now covers the LEFT JOIN: matched job populates `next_run_time`, unmatched job yields `None`, plus list ordering/pagination. Gated on `TEST_DB_URL` (skipped in this run, which has no DB). The query in `schedule_repository.py:31-35,101-108` is structurally sound. Resolved (CI must set `TEST_DB_URL` to actually exercise it).

### I3 — Update saga: APScheduler job left replaced if the row-restore commit fails — Status: Resolved
**File**: `src/lib/services/schedule_service.py:133-148`.
The compensating restore is wrapped in try/except; a restore failure is logged at error level with `schedule_id`, and the original `JobRegistrationError` is still raised. Verified by `test_update_raises_job_error_even_when_restore_fails`. Resolved.

### I4 — `replace_existing` silently becomes "create" when the job is gone — Status: Resolved (acknowledged)
No code change required; behavior satisfies FR-011. Acknowledged as acceptable in the prior pass; no regression observed.

### I5 — `list` trigger_type derivation duplicated schema logic — Status: Resolved
**File**: `src/lib/services/schedule_service.py:160`.
`list` now uses `ScheduleTrigger.model_validate(schedule.trigger).trigger_type`, removing the duplicated truthiness rule and centralizing classification on the schema. Resolved.

---

## Minor Suggestions

- **M1 (new, doc) — Status: Open.** `data-model.md:76` still describes `method` for `chat_completion` as "ignored (forced/left None; not required)", but the implemented and spec-mandated behavior is now *reject*. Update that table cell to "MUST be absent (rejected if provided)" so the data-model matches `spec.md:83`, the validator, and the tests. Documentation-only; no code impact.
- **M2 (informational) — Status: Open.** `src/lib/services/schedule_service.py:89` reads `next_run_time` back through the async session after `add_job` committed via APScheduler's separate sync engine. This is correct under PostgreSQL's default READ COMMITTED isolation (each `execute` takes a fresh snapshot and sees the cross-engine commit). If the session isolation is ever raised to REPEATABLE READ/SERIALIZABLE, this cross-engine read could return a stale `None`. No action needed now; worth a one-line comment if isolation policy changes.
- **M3 — Status: Resolved.** Prior minor on `JobRegistrationError` → 502 vs 500 is settled: `contracts/schedule-endpoints.md:79,99,111,120` explicitly mandates **502** for `add_job` failure, which the endpoint (`schedules.py:29,44`) matches exactly.
- **M4 — Status: Resolved.** `_to_next_run_time` is now `@staticmethod`; `trigger`/`kwargs` repository params are `dict[str, Any]`; exceptions share a `SchedulerError` base — all prior minor items addressed.
- **M5 — Status: Open (pre-existing, out of scope).** `src/worker/jobs.py:7` `dummy_job` remains but is still referenced by `src/api/v1/endpoints/demo.py` (prior-feature scaffolding), so it is not dead code in this feature. Leave as-is.

---

## Intent Alignment

- **FR-022 / US1.2 (date & cron-with-bounds triggers)** — now satisfied (C1 fixed); date/start_date/end_date serialize as ISO strings into JSONB.
- **FR-025 / FR-026 / spec.md:83 (target-type property rules)** — now satisfied (C4); inapplicable `method`/`api_version` are rejected, not coerced. Only the data-model wording (M1) lags.
- **FR-012 / SC-003 (full prior-state restore on update failure)** — now satisfied (C3); `updated_at` restored explicitly.
- **FR-025 (valid http(s) URL)** — now enforced (C5).
- **FR-007 / FR-012 error mapping** — endpoint returns 502 per the contract (M3).
- All other requirements (FR-002 uuid id reuse, FR-003/FR-010 persist-then-register ordering, FR-004/FR-011 kwargs + injected `schedule_id`, FR-006 string func ref preserving the `lib/`→no-`api`/`worker` rule, FR-008/FR-013 next_run_time from the jobstore, FR-014–FR-018 pagination + ownership scoping, FR-019/FR-020 mock owner, FR-028/FR-029 ORM-only + service-layer logic) remain implemented in line with the plan.

## Checklist Pass

- **Correctness / saga / JOIN / Paginator / properties validation**: PASS — all C1–C5, I1–I5 verified fixed with tests.
- **Type safety**: PASS — `uv run mypy src` clean across 39 files; generic binding restored (I1).
- **Style / lint**: PASS — `uv run ruff check src tests` clean.
- **Tests**: PASS — 112 passed, 13 skipped (Postgres integration, `TEST_DB_URL`-gated). Every fixed issue has a dedicated regression test.
- **Architecture & layering**: PASS — service/repository/endpoint separation intact; `lib/` references the worker job only by string constant; DI via `Depends`; scheduler from `app.state`.
- **Async correctness**: PASS — sync `add_job` offloaded via `asyncio.to_thread`.
- **No raw SQL**: PASS — ORM/Core only.
- **Logging / no print / no secrets**: PASS.
- **Contract alignment**: PASS — 502 status and pagination link rules match `contracts/schedule-endpoints.md`.
