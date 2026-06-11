# Code Review 3 — APScheduler Executor Worker

**Spec dir**: `specs/008-apscheduler-executor-worker/`
**Date**: 2026-06-11
**Mode**: Verification (round 3 — verifying round-2 minor fixes A–E)
**Artifacts consulted**: `spec.md`, `plan.md`, `data-model.md`, `contracts/executor-interface.md`, `code-review-2.md`

## Summary

**Approved — no remaining code issues.** All 5 minor findings from review 2 (A–E) are resolved correctly in the codebase: `RunResult` is deleted from `run.py`, `SSE_ACCEPT_HEADER` is gone from `constants.py`, the executor interface signature in both `contracts/executor-interface.md` and `data-model.md` now reads `execute(self, ctx: RunContext, properties)`, the chat client construction (`_parse_deployment` + `AsyncAzureOpenAI`) sits inside the executor's `try`, and the SSE guard is the exact non-2xx predicate `not (200 <= status_code < 300)`. One documentation residue from the Finding A fix remains (the deleted `RunResult` is still described in `data-model.md`/`plan.md`) — a doc-only cleanup, non-blocking.

---

## Verification of Review-2 Findings

| # | Title | Sev (R2) | Status |
|---|-------|----------|--------|
| A | `RunResult` defined-but-unused | minor | **Resolved (code)** — `src/lib/schemas/run.py` now defines only `RunContext` (`run_id`, `schedule_id`); `RunResult` class deleted. Grep confirms no `RunResult` reference remains in `src/`. Doc residue noted in M1. |
| B | `SSE_ACCEPT_HEADER` orphaned constant | minor | **Resolved** — removed from `src/lib/constants.py` (file ends at `API_KEY_HEADER`, line 11). Grep confirms zero references to `SSE_ACCEPT_HEADER` anywhere in `src/`. |
| C | Contract / data-model signature drift | minor | **Resolved** — `contracts/executor-interface.md:10` reads `async def execute(self, ctx: RunContext, properties: ScheduleProperties) -> None`, and `:13` adds the prose "`ctx: RunContext` carries `run_id` and `schedule_id`". `data-model.md:71` reads `async def execute(ctx: RunContext, properties: ScheduleProperties) -> None`. Failure prose at `:16` now reads `ScheduleRunError(message, schedule_id=ctx.schedule_id, run_id=ctx.run_id)`. Matches `base.py` and all three executors. |
| D | Chat client construction outside `try` | minor | **Resolved** — `chat_completion.py:31-38`: `_parse_deployment(properties.url)` and `AsyncAzureOpenAI(...)` are now inside the `try` (line 31). A `ValueError` from a malformed deployment URL or an `AsyncAzureOpenAI` construction error is caught by `except Exception` (`:48`) and re-wrapped as `ScheduleRunError(schedule_id=ctx.schedule_id, run_id=ctx.run_id)`. Executor now honors its "any failure ⇒ `ScheduleRunError`" contract independently of the dispatch safety net. |
| E | SSE `>= 400` not exact non-2xx | minor | **Resolved** — `sse.py:28` is `if not (200 <= event_source.response.status_code < 300):` followed by `raise_for_status()` (`:29`). Predicate now matches the contract's "non-2xx before stream open ⇒ `ScheduleRunError`" exactly. `except` still catches `httpx.HTTPStatusError, httpx.TransportError, httpx_sse.SSEError` (`:32`). |

---

## New / Remaining Findings

## Finding M1 (minor, doc-only): `data-model.md` / `plan.md` still describe the deleted `RunResult`
**Files**: `specs/008-apscheduler-executor-worker/data-model.md:47,53`; `specs/008-apscheduler-executor-worker/plan.md:105` (and `tasks.md:43`)
**Severity**: minor (documentation; no code impact)
**Status**: Open
**Description**: The Finding A fix correctly deleted the `RunResult` class from `src/lib/schemas/run.py`, but the design artifacts still document it as a delivered entity:
- `data-model.md:47` heading reads "`RunContext` / `RunResult` — `src/lib/schemas/run.py` (new, Pydantic v2)" and `:53` describes `RunResult: run_id, schedule_id, result: <json> — the standardized success object logged for a completed run.`
- `plan.md:105` comments the file as `schemas/run.py  # NEW: RunContext / RunResult internal models (logging shape)`.

This is the mirror of the Finding C class of issue (code/doc drift, Constitution IV): the code no longer matches the typed data-model. The success record is emitted as ad-hoc kwargs (`logger.info("run_result", run_id=..., result=...)` in `chat_completion.py:47` and the REST path), which is fine — but the data-model still claims a "standardized success object" type that no longer exists.
**Suggestion**: Drop `RunResult` from `data-model.md` (rename the heading to `RunContext`, delete the `RunResult` bullet at `:53`) and from the `plan.md:105` comment so the artifacts reflect the shipped shape. Optionally note in `data-model.md` that single-result/stream records are logged as flat kwargs (`run_result` / `stream_chunk` / `sse_event`) rather than a typed object. `tasks.md:43` is a historical task log and may be left as-is.

---

## Intent Alignment

- **FR-004 / SC-002** (every record carries both ids): met — `RunContext` carries both ids and is passed explicitly to all three executors; failure paths raise `ScheduleRunError(schedule_id=ctx.schedule_id, run_id=ctx.run_id)`.
- **FR-010 / SC-004** (one record per chunk for chat streaming): met — `create(stream=True)` iterated raw, one `stream_chunk` per chunk (`chat_completion.py:43-44`).
- **FR-013 / FR-011** (SSE non-2xx before stream open ⇒ failure): now met with exact 2xx semantics after the E fix (`sse.py:28-29`).
- **Executor failure contract** ("any failure ⇒ `ScheduleRunError`"): now met at the executor boundary for the chat path after the D fix, not just at the dispatch handler.
- **Constitution IV** (contract-driven): the executor-interface contract and the data-model interface block now match the code (C resolved). The only residual drift is the `RunResult` entity description in `data-model.md`/`plan.md` (M1) — doc-only.

## Checklist Pass (areas with no issues found)

- `src/lib/schemas/run.py`: clean — single `RunContext` model with full type annotations, no dead code.
- `src/lib/constants.py`: clean — no orphaned constants; `API_KEY_HEADER` is referenced by `rest.py`/`sse.py`.
- `src/worker/executors/chat_completion.py`: client construction and both stream/non-stream paths are inside `try`; `dial_api_key` taken from settings, never logged; `ScheduleRunError` carries both ids.
- `src/worker/executors/sse.py`: exact non-2xx guard; shared `httpx.AsyncClient` from `WorkerContext`; `extra_headers` win over injected `Api-Key`; failure wrapped with both ids.
- No `print()`, no raw SQL, no `.env`/secrets reads in feature code; type hints present on all reviewed signatures.

---

## Counts

- **Critical**: 0
- **Major**: 0
- **Minor**: 1 (M1: `data-model.md`/`plan.md` still describe the deleted `RunResult` — documentation only)

**Verdict: Approved — no remaining code issues.** All 5 round-2 findings (A–E) are resolved correctly in the code and the executor contract. The single remaining item (M1) is a documentation cleanup that mirrors the now-deleted `RunResult` and does not block merge.
