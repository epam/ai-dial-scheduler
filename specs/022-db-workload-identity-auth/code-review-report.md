# Code Review Report — 022 Workload-Identity Database Authentication

**Date**: 2026-06-18
**Reviewer**: code-review-agent
**Branch**: `022-db-workload-identity-auth` (changes uncommitted in working tree; reviewed `git diff HEAD`)
**Spec dir**: `specs/022-db-workload-identity-auth/`
**Artifacts consulted**: spec.md, plan.md, research.md, data-model.md, contracts/db-auth-config.md, constitution.md (v1.2.0)

## Summary

Overall assessment: **Requires changes.** The async/sync engine branching, settings validation, the
shared `build_jobstore` factory, and secret handling are well-designed and align with the
spec/plan/research intent. Tests are present and pass (33 passed). However there are correctness
gaps that defeat stated requirements: the **Alembic offline path and `sqlalchemy.url` main-option
are not mode-aware** (FR-007 / plan says `migrations/env.py` MODIFIED — it was not), a **resource/
credential leak on `init_engine(force_fresh=True)`**, and a couple of reliability/observability
items around the async connect hooks. None of the issues are secret-leakage; the secret-safety
requirement (FR-008/SC-006) is met.

Counts: Critical 1 · High 3 · Medium 4 · Low 3 · Nit 3.

---

## Critical

### C1 — Alembic does not honor `gcp_iam` / `azure_entra` (offline path + `sqlalchemy.url`) — `migrations/env.py:23,34-37`
**Status: Open**

`migrations/env.py` was **not modified** in this change set, but plan.md (Project Structure) and
research.md Decision 5 both list `migrations/env.py` as MODIFIED to "build sync engine via shared
factory", and FR-007 requires the selected mode to apply to "Alembic migrations alike"
(contract guarantee #3 "Uniformity").

- `migrations/env.py:23` unconditionally calls
  `build_database_url(DatabaseSettings(), hide_password=True)` and sets it as the
  `sqlalchemy.url` main option. For `gcp_iam` `db_password` is `None` and `db_host` is the
  unused default; for `azure_entra` no token is present. The resulting URL is wrong/passwordless.
- `run_migrations_offline()` (`migrations/env.py:34-37`) then runs entirely off that broken URL —
  `alembic upgrade --sql` (offline) cannot work for the new modes.
- The online path (`run_async_migrations`, line 46-54) *does* delegate to `Database().init_engine()`
  which branches correctly, so online `alembic upgrade head` will work for the new modes. But the
  offline path and the main-option assignment are still broken, and the plan's intended sync-engine
  delegation (`build_sync_engine`) was not wired in.

**Impact**: Offline migrations are unusable under the new modes, and `build_database_url(DatabaseSettings())`
on line 23 will raise during construction for `gcp_iam`/`azure_entra` only if those env vars are set
at migration time; otherwise it silently produces a meaningless URL. Either way it contradicts the
stated uniformity requirement and the plan.

**Recommendation**: Make `env.py` mode-aware. Two concrete options:
- Guard offline mode so it is only supported for `password`/`connection-string`, raising a clear
  error for workload-identity modes (offline SQL generation cannot inject runtime credentials anyway).
- For the `sqlalchemy.url` main option and `run_migrations_offline`, route through the shared
  sync-engine factory: e.g. build `build_sync_engine(DatabaseSettings())` and use
  `engine.url.render_as_string(hide_password=True)` for logging, and for online mode keep the
  existing `Database` path. At minimum, do not call `build_database_url` for the workload-identity
  modes.

---

## High

### H1 — `init_engine(force_fresh=True)` leaks the GCP connector and Azure credential — `src/lib/db/engine.py:38-43`
**Status: Open**

```python
if self._engine is not None and force_fresh:
    self._engine.sync_engine.dispose()
    self._engine = None
    self._sessionmaker = None
    self._gcp_connector = None        # dropped without closing
    self._azure_credential = None     # dropped without closing
```

`dispose()` (lines 146-168) correctly closes the connector (via `asyncio.to_thread`) and the async
credential, but `init_engine(force_fresh=True)` nulls both references **without** closing them. The
Cloud SQL `Connector` owns background refresh tasks/sockets and the Azure async credential owns an
`aiohttp` session; dropping the reference leaks those resources and their in-flight token-refresh
tasks.

This path is exercised by `migrations/env.py:48` (`force_fresh=True`) and any future re-init. It
violates Reliability First (resource/credential lifecycle) and the spec edge case on credential
lifecycle.

**Recommendation**: Make `force_fresh` await the same teardown as `dispose()` before recreating —
e.g. extract the connector/credential teardown into helpers and call them here (note this requires
`init_engine` to dispose them; since `init_engine` is sync you may need an async re-init path, or
close the connector synchronously via `self._gcp_connector.close()` and schedule the async
credential close). Do not silently drop the references.

### H2 — Async credential-acquisition / connect failures are not logged with actionable context — `src/lib/db/engine.py:99-107,119-128`
**Status: Open**

FR-010 and the spec edge case "Credential acquisition latency or failure at runtime" require a
"clear, actionable error identifying the auth mode and the likely cause". The async `getconn`
closures for both `gcp_iam` and `azure_entra` call `connector.connect_async(...)` /
`provider.get_token_password()` + `asyncpg.connect(...)` with **no try/except and no log entry** on
failure. A failed token fetch or IAM connect will surface as a raw SQLAlchemy/asyncpg traceback with
no mention of the auth mode or that this is a workload-identity acquisition failure.

The plan's Constitution Check (II. Observability) claims "new paths add structured log entries for
credential acquisition outcome (success/failure, mode)". That instrumentation is **absent** — only a
one-time `init` log exists (`engine.py:46`), not per-acquisition outcome.

**Recommendation**: Wrap the credential acquisition in each `getconn` (and the sync hooks, see H3)
with a `try/except` that logs `mode`, a reason code, and the exception type (NOT the token/exception
args that could contain a secret), then re-raises. Keep it lightweight to avoid hot-path overhead;
log only on failure. Ensure no token value is included.

### H3 — Sync Azure token injection failure is silent / unlogged; `do_connect` listener leaks across engines — `src/lib/db/sync_engine.py:59-74`
**Status: Open**

Two issues in `_build_azure_engine`:

1. **No error handling/observability** on `_inject_azure_token` — same gap as H2 on the sync path
   (`do_connect`). A token-fetch failure during job-store connection raises an unannotated error in
   the APScheduler thread. Per the spec job-store edge case and FR-010, this should fail gracefully
   with a clear, mode-tagged log.
2. The `AzureTokenProvider` created here (line 65) is **never closed**. Unlike the async credential
   (disposed in `Database.dispose`), the sync provider built for the job-store engine has no owner
   that calls `provider.close()`, leaking the underlying `DefaultAzureCredential` transport for the
   lifetime of the worker. Consider attaching it to the engine (e.g. close on engine dispose via a
   `dispose`/`engine_disposed` event) or documenting that the credential lives for process lifetime
   intentionally (it is a process-long singleton in practice, so this is lower severity than H1 but
   should be a conscious decision, not accidental).

**Recommendation**: Add failure logging in `_inject_azure_token` (mode + reason, no secret); decide
and document the sync provider lifecycle (close it on engine dispose, or note it is process-scoped).

---

## Medium

### M1 — `pool_recycle` interaction with token expiry not enforced for `azure_entra` — `src/lib/db/engine.py:77,113-132`; `src/lib/db/sync_engine.py:64-74`
**Status: Open**

Entra tokens live 5–60 min (research.md Decision 3/4). The async/sync hooks correctly fetch a fresh
token **per new connection**, so newly-opened connections are fine. But `pool_recycle` defaults to
`1800s` (`settings.py:26`) and `pool_pre_ping` only validates liveness, not token freshness. A
pooled connection that authenticated with a token does **not** re-authenticate on checkout; once the
TCP/TLS session is established, PostgreSQL does not re-check the bearer token, so a long-lived pooled
connection generally keeps working past token expiry — this is usually fine. The risk is the reverse:
if Azure issues a short (e.g. 5-min) token while `pool_recycle=1800`, the connection itself stays up
regardless, so the dominant correctness factor is that **new** connections always get a fresh token,
which the code does. This is acceptable, but it is an undocumented assumption.

**Recommendation**: Add a short comment (and ideally a test) noting that token freshness is only
required at connect time and that pooled connections are unaffected by token expiry; confirm
`pool_recycle` is independent of token TTL. No code change strictly required, but the reasoning
should be captured so a future reader does not "fix" it incorrectly.

### M2 — `async_creator` closures for GCP/Azure are untested (only branch dispatch is covered) — `tests/unit/lib/test_db_engine_selection.py`
**Status: Open**

`test_db_engine_selection.py` mocks `_init_engine_gcp_iam` / `_init_engine_azure_entra` and asserts
only which branch is taken. The actual `getconn` closures — which carry the real logic
(`connect_async` args, `enable_iam_auth=True`, asyncpg `ssl="require"`, token-as-password, IP type) —
have **no async test** equivalent to the sync `test_sync_engine.py::TestGcpSyncEngine` /
`TestAzureSyncEngine`. Test-First (Principle III) and the plan ("connect-hook token injection with
mocked connector/credential" written before implementation) are only partially satisfied: the sync
hook is covered, the async hooks are not.

**Recommendation**: Add unit tests that mock `create_gcp_connector` / `AsyncAzureTokenProvider` and
`asyncpg.connect`, invoke the `getconn` closure, and assert the connector/asyncpg call args
(instance name, `"asyncpg"`, `enable_iam_auth=True`, `user`, `db`, `ip_type`; and for Azure: host/
port/user/database, `password=<token>`, `ssl="require"`).

### M3 — Azure async path hardcodes `ssl="require"`; sync path does not enforce TLS — `src/lib/db/engine.py:127`; `src/lib/db/url.py:32-45`
**Status: Open**

The async asyncpg creator passes `ssl="require"` (good — Azure mandates TLS). The **sync** psycopg2
path (`build_azure_entra_sync_url` + `_build_azure_engine`) builds a plain URL with **no `sslmode`**.
psycopg2 defaults to `sslmode=prefer`, which will silently fall back to a non-TLS connection if the
server allowed it. Azure PostgreSQL requires TLS, so it will likely still negotiate SSL, but relying
on server enforcement rather than client requirement is a reliability/security gap and is
inconsistent with the async path. Note also `ssl="require"` (asyncpg) verifies encryption but not the
server certificate; consider whether `verify-full`/`verify-ca` is desired for both paths.

**Recommendation**: Add `sslmode=require` (or `verify-full` with a CA bundle) to the Azure sync URL
query args, and document the chosen TLS verification level for both sync and async to keep them
consistent.

### M4 — Settings allow contradictory config (`db_password` set in workload-identity modes) — `src/lib/settings.py:44-57`
**Status: Open**

The contract (`db-auth-config.md`) states for `gcp_iam` and `azure_entra`: "**Must NOT be set**:
`DB_PASSWORD`", and the spec edge case "Misconfiguration across modes" calls out "Selecting a
workload-identity mode while also providing a password ... must be validated at startup with a clear
message." `validate_auth_requirements` only checks for **missing** required fields; it does not
reject a `db_password`/`db_url` that is set in a workload-identity mode. The contradiction is
silently ignored rather than flagged.

**Recommendation**: Extend the validator to raise when `db_password` (and arguably `db_url`) is set
under `gcp_iam`/`azure_entra`, with a clear message. Add a unit test.

---

## Low

### L1 — `gcp_ip_type` maps non-private to PUBLIC, silently ignoring other IP types — `src/lib/db/credentials.py:12-14`
**Status: Open**

The boolean `db_gcp_private_ip` only distinguishes PUBLIC vs PRIVATE; Cloud SQL also supports PSC.
This matches data-model.md (boolean flag) and is acceptable per YAGNI, but worth a one-line docstring
note that PSC is out of scope.

### L2 — Duplicated env-clearing test helper across new test modules — `tests/unit/lib/test_sync_engine.py:9-24`, `tests/unit/lib/test_jobstore.py:9-24`
**Status: Open**

`_make_settings` is duplicated almost verbatim between `test_sync_engine.py` and `test_jobstore.py`
(and overlaps the `clean_db_env` fixture in `test_settings_db_auth.py`). DRY: consider a shared
fixture/helper in `conftest.py`.

### L3 — Tests use `unittest.mock.MagicMock` directly instead of `pytest-mock` — `tests/unit/lib/test_db_credentials.py:1`, `test_sync_engine.py:1`, `test_db_engine_selection.py:2`, `test_jobstore.py:1`
**Status: Open**

CLAUDE.md / Testing convention says "Use `pytest-mock` for mocking." The new tests import
`from unittest.mock import MagicMock` while also receiving the `mocker` fixture. Prefer
`mocker.MagicMock()` for consistency with the stated convention (minor; the existing test in
`test_worker_app.py` already uses `mocker.MagicMock`).

---

## Nit

### N1 — `_build_url_engine` line could read mode-conditional comment — `src/lib/db/sync_engine.py:34-36`
For `connection-string` mode, `build_database_url(..., driver=SYNC)` rewrites the driver; a one-line
comment (as exists in `url.py`) would aid the reader. Optional.

### N2 — `Database._azure_credential` attribute name vs type — `src/lib/db/engine.py:30`
The attribute is typed `AsyncAzureTokenProvider | None` but named `_azure_credential`, while the
provider wraps the credential. `_azure_token_provider` would read more precisely. Cosmetic.

### N3 — Unused logging import surface — `src/lib/db/sync_engine.py:55,73`
The two `logger.info("Building sync DB engine with <mode> auth mode.")` lines are good and secret-free.
Consider including a `mode=` structured field for queryability consistent with the rest of the
codebase's structured logging style. Cosmetic.

---

## Intent Alignment

- **FR-001/FR-002/FR-003 (modes)**: Met — enum, async branching (`engine.py`), sync branching
  (`sync_engine.py`) implement `gcp_iam` (Cloud SQL Connector pg8000/asyncpg) and `azure_entra`
  (Entra token-as-password) exactly per research.md Decisions 1-4.
- **FR-004 / SC-004 (auto-refresh)**: Met for new connections — GCP connector handles rotation;
  Azure fetches a fresh token per connect via `async_creator` / `do_connect`. See M1 for the
  documented assumption.
- **FR-005 / SC-005 (fail-fast naming missing fields)**: Met (`validate_auth_requirements`,
  tests in `test_settings_db_auth.py`). But see **M4** — contradictory config (password set in
  workload-identity mode) is NOT validated, contradicting the contract's "Must NOT be set" and the
  spec misconfiguration edge case.
- **FR-006 (backward compat / default password)**: Met — `password`/`connection-string` paths
  unchanged; default remains `PASSWORD`.
- **FR-007 (uniform across async engine, job store, Alembic)**: **Partially met.** Async engine and
  job store: met (shared `build_jobstore` removes the prior duplication between `scheduler.py` and
  `worker/app.py` — matches research Decision 5). **Alembic: NOT met for offline mode / main-option**
  — see **C1**. Plan listed `migrations/env.py` as MODIFIED; it was not.
- **FR-008 / SC-006 (never log secrets)**: Met — no token/password is logged anywhere; log lines
  emit only the mode. Token provider returns are used as `cparams["password"]` / asyncpg `password`
  and never logged.
- **FR-010 (clear actionable error on acquisition failure)**: **Not met** — see **H2/H3**; no
  mode-tagged failure logging on the credential acquisition / connect hooks.
- **Async / no event-loop blocking**: Async path uses `azure.identity.aio` async credential and
  `connect_async` (good, per plan constraint). GCP connector created on the running loop
  (`engine.py:93`). Dispose uses `asyncio.to_thread` with timeout (good). No blocking sync token call
  on the async path. ✅
- **Simplicity & YAGNI / ≤3 params**: Met — factories take a single `DatabaseSettings`; closures
  keep signatures small; no premature abstraction.

## Checklist Pass

Passed without issues:
- **Security — secret logging** (FR-008/SC-006): no credential/token/password logged. ✅
- **Security — no `.env`/secret file reads by new code**, no raw SQL, no `assert` in production. ✅
- **Async I/O on async path** (no event-loop-blocking credential calls; `azure.identity.aio` used). ✅
- **Backward compatibility** (`password`/`connection-string` untouched; default preserved). ✅
- **Type hints & Google-style docstrings** on new public functions/classes. ✅
- **Absolute imports; empty `__init__.py`; import ordering.** ✅
- **Shared factory removes prior job-store duplication** (DRY; research Decision 5). ✅
- **Dependencies added via `pyproject.toml` + lockfile** (`cloud-sql-python-connector[pg8000]`,
  `azure-identity`). ✅ (CVE scan is a CI gate — out of scope for this static review.)

Requires attention: **C1** (Alembic uniformity), **H1** (force_fresh resource leak),
**H2/H3** (acquisition-failure observability + sync provider lifecycle), **M2** (async hook tests),
**M3** (sync TLS enforcement), **M4** (contradictory-config validation).
