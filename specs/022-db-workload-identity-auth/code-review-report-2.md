# Code Review Report — Round 2: DB Workload Identity Auth

**Reviewer**: code-review-agent
**Date**: 2026-06-18
**Scope**: specs/022-db-workload-identity-auth
**Mode**: Fresh review of current working-tree state (round 1 fixes verified, then re-reviewed end-to-end)

**Artifacts consulted**: spec.md, plan.md, research.md, contracts/db-auth-config.md, plus round 1 report (`code-review-report.md`).

**Verification env**: `uv run pytest` on the 7 in-scope test modules → **44 passed**. `uv run ruff check` on changed source → clean. `uv run mypy src/lib/db` → no issues (8 files).

## Prior round fixes verified

- **C1 (Alembic not mode-aware)** — RESOLVED. `migrations/env.py` now guards the offline path: `sqlalchemy.url` is only set for URL-based modes (`migrations/env.py:30-31`), and `run_migrations_offline()` raises a clear `RuntimeError` for workload-identity modes (`:42-47`). Online path delegates to `Database().init_engine()` which branches correctly. Matches FR-007 intent given offline SQL generation cannot inject runtime credentials.
- **H1 (force_fresh leaked connector/credential)** — RESOLVED. `init_engine(force_fresh=True)` now calls `_release_credentials_best_effort()` (`engine.py:42`), which closes the GCP connector inline and schedules/awaits the Azure async credential close (`:190-217`). See L1 below for a residual nit.
- **H2 (async getconn closures had no error logging)** — RESOLVED. Both async closures wrap acquisition in `try/except` logging `mode` + `error_type` only, then re-raise (`engine.py:108-115`, `141-148`).
- **H3 (Azure sync token injection unhandled; provider never closed)** — RESOLVED for Azure. `_inject_azure_token` logs failures with mode + error_type (`sync_engine.py:72-79`), and the provider is closed via an `engine_disposed` event listener (`:94-96`). See H1 below — the **GCP** sync path has the analogous gap that was not covered.
- **M2 (async getconn closures untested)** — RESOLVED. `test_db_engine_selection.py` adds `test_gcp_getconn_invokes_connector_with_iam` and `test_azure_getconn_uses_fresh_token_as_password`, invoking the captured `async_creator` and asserting connector/asyncpg call args incl. `enable_iam_auth=True`, `ssl="require"`, token-as-password.
- **M3 (Azure sync missing TLS)** — RESOLVED. `_build_azure_engine` passes `connect_args={"sslmode": "require"}` (`sync_engine.py:88`), mirroring the async `ssl="require"`.
- **M4 (contradictory config accepted)** — RESOLVED. `validate_auth_requirements` now rejects `db_password`/`db_url` under workload-identity modes via `forbidden_fields_by_mode` (`settings.py:54-69`), with three covering tests in `TestContradictoryConfigRejected`.

All round 1 findings are confirmed addressed and working.

## Summary

The feature is in good shape and substantially ready. The architecture (shared `build_jobstore` / `build_sync_engine` factories, single `credentials.py` for lifecycle, mode dispatch in both async and sync engines) is clean, matches the plan and research decisions, and all in-scope tests pass with clean ruff/mypy. Secret-safety (FR-008/SC-006) is upheld: no token, password, or connection string is logged on any path, including failure paths.

One genuine reliability finding survives from the round 1 H3 fix: the **GCP synchronous engine leaks its `Connector`** (no `engine_disposed` cleanup), which is the exact symmetric gap that H3 fixed for the Azure sync provider. The remaining items are medium/low: a fire-and-forget task reference in the force_fresh cleanup, and a few test-quality/consistency nits already noted in round 1 that remain open by choice (acceptable).

## Findings

### Critical (must fix before merge)

None.

### High (should fix before merge)

#### H1 — GCP synchronous engine leaks its `Connector` (no `engine_disposed` cleanup) — `src/lib/db/sync_engine.py:39-65`

`_build_azure_engine` was correctly fixed in round 1 to close its `AzureTokenProvider` on `engine_disposed` (`sync_engine.py:94-96`). The **GCP** sync path was not given the same treatment: `_build_gcp_engine` creates a `Connector` via `create_gcp_connector()` (`:40`) and passes its `getconn` as the `creator`, but never registers a disposal hook to call `connector.close()`. The Cloud SQL `Connector` owns background refresh tasks, an internal HTTP client, and socket/cert state; when the engine is disposed (e.g. Alembic `run_async_migrations` disposes engines; any future job-store rebuild; test teardown) the connector and its refresh machinery leak.

This is the symmetric counterpart to round 1 H3. Severity is High because it is a resource/credential-lifecycle leak on a path the code already exercises (Alembic online uses the async engine, but the sync GCP engine is used by the worker's job store for the process lifetime, and any dispose leaks it).

Note one subtlety: a sync `Connector` created without an event loop spins up its own background loop/thread; closing it from the SQLAlchemy `engine_disposed` callback (a sync context) is the correct place. Suggested fix mirrors the Azure path:

```python
def _build_gcp_engine(db_settings: DatabaseSettings) -> Engine:
    connector = create_gcp_connector()
    ip_type = gcp_ip_type(private=db_settings.db_gcp_private_ip)
    instance_connection_name = cast(str, db_settings.db_gcp_instance_connection_name)

    def getconn() -> Any:
        try:
            return connector.connect(...)
        except Exception as exc:
            logger.error("DB credential acquisition failed.", mode=DbAuthMode.GCP_IAM.value, error_type=type(exc).__name__)
            raise

    engine = create_engine("postgresql+pg8000://", creator=getconn, **_common_engine_kwargs(db_settings))

    @event.listens_for(engine, "engine_disposed")
    def _close_connector(_engine: Any) -> None:
        connector.close()

    logger.info("Building sync DB engine.", mode=DbAuthMode.GCP_IAM.value)
    return engine
```

If the team consciously decides the sync connector is process-scoped (lives for the worker lifetime and is never disposed in practice), that is a defensible position — but it should be a documented decision in code, identical to the bar round 1 set for H3. Right now it is an accidental asymmetry, not a deliberate one.

### Medium (fix or accept with documented rationale)

#### M1 — `_release_credentials_best_effort` schedules the Azure close as a fire-and-forget task without retaining a reference — `src/lib/db/engine.py:206-216`

When a running loop exists, the cleanup does `loop.create_task(provider.close())` (`:214`) and discards the returned `Task`. Per asyncio semantics a bare task that is not referenced can be garbage-collected before it completes, which would silently skip the credential close (the very leak this method exists to prevent) and may emit a "Task was destroyed but it is pending" warning. This path is only reached on `init_engine(force_fresh=True)` while a loop is running (e.g. a future in-process re-init), so impact is low, but the fix is cheap.

Recommendation: retain the task reference somewhere with the lifetime of the loop, e.g.

```python
if loop is not None:
    task = loop.create_task(provider.close())
    task.add_done_callback(lambda _t: None)  # keep a strong ref via the closure / or store on self
```

A cleaner option: store pending cleanup tasks in a `set` on the `Database` instance and discard on completion (the standard fire-and-forget-safe idiom). Acceptable to defer if the team accepts that `force_fresh` under a running loop is not a real production path today — but note it.

#### M2 — `azure_entra` sync path: no async-equivalent failure test, and no test asserts `sslmode=require` is applied — `tests/unit/lib/test_sync_engine.py:66-92`

`TestAzureSyncEngine` covers URL shape and the token injector, but does not assert that the engine was built with `connect_args={"sslmode": "require"}` (the round 1 M3 fix). Since TLS enforcement is a security property the fix introduced, a regression test would lock it in. Likewise there is no test that `_inject_azure_token` / the GCP `getconn` log-and-reraise on failure (the H2/H3 fixes) — the error-handling branches are currently uncovered.

Recommendation (low effort, raises confidence in the round 1 fixes):
- Assert `engine.dialect`/connect args include `sslmode=require` for the Azure sync engine, or capture `create_engine` kwargs via a mock and assert `connect_args == {"sslmode": "require"}`.
- Add a test where `provider.get_token_password` raises and assert the exception propagates (and optionally that `logger.error` was called with `mode=azure_entra` and no token in the payload).

### Low / Nit (optional polish)

#### L1 — Round 1 L2/L3 test-style items remain open (accepted)

- `_make_settings` is still duplicated verbatim between `test_sync_engine.py:9-24` and `test_jobstore.py:9-24` (and overlaps `clean_db_env` in `test_settings_db_auth.py`). A shared `conftest.py` helper would be DRY-er.
- New tests still `from unittest.mock import MagicMock` (`test_db_credentials.py:1`, `test_sync_engine.py:1`, `test_db_engine_selection.py:2`, `test_jobstore.py:1`) while CLAUDE.md says "Use `pytest-mock`". They already receive `mocker`; `mocker.MagicMock()` would be consistent. Cosmetic and pre-existing.

#### L2 — `TestUnsupportedMode` mutates a frozen-ish model via `object.__setattr__` — `tests/unit/lib/test_sync_engine.py:96-100`

`object.__setattr__(settings, "db_auth_mode", "bogus")` bypasses Pydantic validation to reach the `raise ValueError(...)` fallback in `build_sync_engine`. It works and the defensive branch is worth covering, but it is a slightly fragile test construct. Acceptable; no action required.

#### L3 — `_provide_token` / `_close_provider` decorated closures are defined but never referenced by name — `src/lib/db/sync_engine.py:90-96`

This is correct SQLAlchemy usage (the `@event.listens_for` decorator registers them as a side effect), and ruff is clean, so no change needed — flagging only so a future reader does not mistake them for dead code. Optional: a one-line comment that registration is the side effect.

#### L4 — `gcp_ip_type` PSC-out-of-scope note (round 1 L1) is present — confirmed

The docstring now documents that PSC is intentionally out of scope (`credentials.py:13-17`). Round 1 L1 addressed; no action.

## Verdict

**APPROVE** — with one recommended follow-up.

The feature meets the spec's functional requirements and all round 1 findings are resolved and verified. The single substantive item, **H1 (GCP sync connector not closed on `engine_disposed`)**, is a resource-lifecycle leak that is the direct symmetric gap to the round 1 H3 Azure fix and should be closed (or explicitly documented as process-scoped) for consistency before merge. **M1** and **M2** are cheap, worthwhile hardening/coverage items. None of these block correctness of the happy path, secret-safety, backward compatibility, or the GCP/Azure auth flows, which are all sound.
