---

description: "Task list for feature 040: Open-Source Preparation"
---

# Tasks: Open-Source Preparation

**Input**: Design documents from `/specs/040-opensource-preparation/`

**Prerequisites**: plan.md, spec.md, research.md, contracts/ci-contract.md, quickstart.md

**Tests**: No production code changes, so there are no TDD test tasks. Constitution III is satisfied by
**CI parity first** (plan.md, Constitution Check): every CI-facing file is verified locally (`make lint`,
`make test`, `docker build`, `actionlint`, `sed` single-line check) before the workflow that depends on it is added,
and the existing test suite must stay green.

**Organization**: Grouped by plan phases. Each task carries its owner and its spec requirement.

## Format: `[ID] [P?] [Owner] [Story] Description (Req)`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Owner]**: `DEV` — development team (repository content) · `OPS` — DevOps (GitHub/GitLab settings)
- **[Story]**: US1 legal & community · US2 PR gating · US3 releases · US4 auto-deploy · US5 nothing internal
- **⏸** — blocked by an external input (named in the task)
- Paths are relative to the repository root

## Constraints for every task

- `src/`, `tests/`, `migrations/` are not modified.
- Never read `.env`, `.envrc`, `secrets.env`.
- No commit, push or tag without explicit approval (Phase 6 / Phase 7 gates).
- All `epam/ai-dial-ci` references: `@00484ead5dc3904a787c5af8d0bdd8f4fdf087c1 # 4.13.2`.

---

## Phase 0: Track A — Already done (verified)

**Purpose**: Record what already satisfies the guide, so it is not redone and regressions are noticed.

- [x] T001 DEV Baseline on `development` @ `2764371`: `ruff check`, `ruff format --check`, `mypy src` clean; `pytest` (unit) 1040 passed (DONE-007)
- [x] T002 DEV `CONTRIBUTING.md` matches the guide template verbatim (DONE-001)
- [x] T003 DEV `README.md` present with quickstart, testing, DB auth, deployment sections (DONE-002)
- [x] T004 DEV `Dockerfile` builds a non-root, hardened image usable by `docker_build` (DONE-003)
- [x] T005 DEV `uv.lock` committed (DONE-004); `pyproject.toml` has `name`/`version` (DONE-005)
- [x] T006 DEV `development` is the primary branch (DONE-006); CVE floors in `[tool.uv] constraint-dependencies` (DONE-008)
- [x] T007 DEV `SECURITY.md` present with guide wording — link fix pending in T020 (DONE-009, partial)

---

## Phase 1: CI parity (local) — blocks Phase 3

**Purpose**: Make the repository satisfy `contracts/ci-contract.md` §1–§2 before any workflow calls it.

- [x] T010 DEV [US2] Create `Makefile` with `.PHONY: install lint test`; `install: uv sync --all-extras`; `lint: install` → `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`; `test: install` → `uv run pytest`. Tab-indented recipes (DEV-FR-010, contract §2)
- [x] T011 DEV [US2] Verify `make lint` and `make test` exit 0; confirm integration tests report *skipped* without `TEST_DB_URL` (DEV-FR-010)
- [x] T012 [P] DEV [US2] Create `trivy.yaml` with the `ai-dial-ci` mirror template (db, java-db, checks bundle) (DEV-FR-013, research R13)
- [x] T013 [P] DEV [US3] `Dockerfile`: change `FROM --platform=linux/amd64 python:3.14-alpine` to `FROM python:3.14-alpine`; reword the comment "1001 uid also used in HELM chart" to a neutral one (DEV-FR-014, DEV-FR-017, research R11)
- [x] T014 DEV [US3] Verify `docker build -t ai-dial-scheduler:local .` succeeds after T013
- [x] T015 [P] DEV [US3] `pyproject.toml`: set `version = "0.0.0"`; confirm exactly one line matches `^version = ` (DEV-FR-012, research R6)
- [x] T016 DEV [US3] Run the `sed` rewrite simulation from quickstart §1 on a copy of `pyproject.toml`; exactly one line changes (contract §1)
- [x] T017 DEV [US2] Verify `uv lock --check` passes after T015 (contract §1)

**Checkpoint**: repository satisfies the CI contract locally.

---

## Phase 2: Legal and community files — parallel with Phase 1

- [x] T020 [P] DEV [US1] `SECURITY.md`: replace `https://github.com/epam/ai-dial/security/advisories/new` with `https://github.com/epam/ai-dial-scheduler/security/advisories/new` (DEV-FR-002)
- [x] T021 [P] DEV [US1] Create `LICENSE` with the Apache License 2.0 text from `epam/ai-dial-ci` (DEV-FR-001)
- [x] T022 DEV [US1] `pyproject.toml`: add `license = "Apache-2.0"` under `[project]` (after T015 — same file) (DEV-FR-001)
- [x] T023 [P] DEV [US1] Create `.github/pull_request_template.md` from the `ai-dial-ci` template; checklist: tests added/updated, `make lint` and `make test` pass, `CHANGELOG.md`/docs updated if behaviour changes, Alembic migration included if the schema changes; keep the Apache-2.0 contribution sentence (DEV-FR-004)
- [x] T024 [P] DEV [US1] Create `.github/ISSUE_TEMPLATE/config.yml` (`blank_issues_enabled: false`, Discord contact link) (DEV-FR-005)
- [x] T025 [P] DEV [US1] Create `.github/ISSUE_TEMPLATE/01_bug_report.yml` adapted from `ai-dial-ci`; component dropdown: API, Worker, MCP, Migrations, Docs; fields: version/image tag, DB auth mode, steps, expected/actual, logs (DEV-FR-005)
- [x] T026 [P] DEV [US1] Create `.github/ISSUE_TEMPLATE/02_feature_request.yml` adapted from `ai-dial-ci` (DEV-FR-005)
- [ ] T027 DEV [US1] ⏸ Q3 — Create `.github/CODEOWNERS`: `* @<responsible-owner>` and `/.github/ @nepalevov @alexey-ban`. Do not create with a placeholder handle (DEV-FR-003, research R14)

---

## Phase 3: GitHub workflows and Dependabot — after Phase 1

- [x] T030 DEV [US2] Create `.github/workflows/pr.yml` ("PR Workflow"): `pull_request` on `[development, release-*]`; concurrency per PR; job id **`run_tests`** → `python_docker_pr.yml@<sha> # 4.13.2` with `python-package-manager: uv`, `python-version: "3.14"`, `secrets: inherit` (DEV-FR-006, contract §3)
- [x] T031 DEV [US3] Create `.github/workflows/release.yml` with `name: Release Workflow` (exact — consumed by T035): `push` on `[development, release-*]`, `workflow_dispatch` input `promote`; job `release` → `python_docker_release.yml@<sha> # 4.13.2` with the same inputs plus `promote: ${{ github.event_name == 'workflow_dispatch' && inputs.promote }}`, `secrets: inherit` (DEV-FR-007)
- [x] T032 [P] DEV [US2] Create `.github/workflows/pr-title-check.yml` from the `ai-dial-ci` template (`pull_request_target`, `permissions: {}`, secret `ACTIONS_BOT_TOKEN`) (DEV-FR-008)
- [x] T033 [P] DEV [US3] Create `.github/workflows/cleanup-untagged-images.yml` from the `ai-dial-ci` template (daily cron, `dataaxiom/ghcr-cleanup-action` SHA-pinned as in the template, `packages: write`) (DEV-FR-009)
- [x] T034 [P] DEV [US2] Create `.github/workflows/dependabot-automation.yml` from the `ai-dial-ci` template (DEV-FR-009b, research R10)
- [ ] T035 DEV [US4] ⏸ OPS-019 — Create `.github/workflows/deploy-development.yml` (matrix variant): triggers `workflow_dispatch`, `registry_package`, `workflow_run` of `Release Workflow`; matrix entries `dial-scheduler-api` and `dial-scheduler-worker` with their real `gitlab-project-id`; secrets `DEPLOY_HOST`, `DEPLOY_ACCESS_TOKEN`, `DEPLOY_TRIGGER_TOKEN`. No file with fake ids (DEV-FR-009a, research R9)
- [x] T036 [P] DEV [US2] Create `.github/dependabot.yml`: `uv` at `/` (weekly Wed 09:00, `open-pull-requests-limit: 0`, prefix `chore`); `github-actions` at `/` (weekly Wed 09:00, prefix `ci`, `include: scope`, groups `ai-dial-ci` = `epam/ai-dial-ci/*` and `github-actions` = rest, limit 10) (DEV-FR-011, research R10)
- [x] T037 DEV [US2] Run `actionlint .github/workflows/*.yml`; `grep -rn 'ai-dial-ci' .github/workflows` shows only the 4.13.2 SHA, never `@main` or a tag (contract §3)

**Checkpoint**: all workflows lint-clean and pinned; `deploy-development.yml` may still be pending on OPS-019.

---

## Phase 4: Clean-up of internal content — parallel with Phase 3

- [x] T040 DEV [US5] `pyproject.toml`: `description` → scheduling-service description (research R15); add `[project.urls]` Homepage `https://dialx.ai`, Repository `https://github.com/epam/ai-dial-scheduler`, Issues `https://github.com/epam/ai-dial-scheduler/issues` (after T022 — same file) (DEV-FR-016)
- [x] T041 DEV [US5] ⏸ authors value — `pyproject.toml` `authors`: replace `"Your Name"` / personal email with the confirmed team value; if no answer by Phase 6, remove the field (DEV-FR-016, plan Open Items)
- [x] T042 [P] DEV [US5] Redact internal references: `specs/025-k8s-worker-probes/research.md` (GitLab CI trigger / HELM mention), `specs/028-responses-schedule-type/code-review-report.md` (personal local file names and paths), `specs/035-schedule-delete/code-review-report-round2.md` (internal CI template include discussion) — keep the technical findings, drop the internal specifics (DEV-FR-017)
- [x] T043 [P] DEV [US5] `dial/.env.template`: replace the literal `NEXTAUTH_SECRET` value with a placeholder and a comment `# generate: openssl rand -base64 32` (DEV-FR-017)
- [x] T044 [P] DEV [US5] `dial/docker-compose.yml`: pin `epam/ai-dial-*` images to released tags where a release contains the needed features; keep `development` only where unreleased features are required, with a comment why (DEV-FR-017)
- [x] T045 [P] DEV [US5] Delete `log.json` (DEV-FR-018)
- [x] T046 [P] DEV [US5] `git mv "DIAL Scheduler.postman_collection.json" documentation/postman/dial-scheduler.postman_collection.json`; update any references (`git grep -n postman_collection`) (DEV-FR-018)
- [x] T047 [P] DEV [US5] `.gitignore`: remove stale internal entries (`src/chat_v2/local_config/config.json`, `/src/utils/test.py`, `.github/instructions/*`, `dial_conf/keycloak/data`) (DEV-FR-017)
- [x] T048 DEV [US5] Manual: review `documentation/deployment/API Logs.png`, `Migrations Logs.png`, `Worker Logs.png`, `DIAL Scheduler Diagram.drawio(.png)` for internal hosts, project ids, personal names; replace or crop if needed (DEV-FR-017)
- [x] T049 DEV [US5] Run the internal-content scan (quickstart §2); only the listed intentional hits remain (SC-004)

**Checkpoint**: tree is clean apart from `.gitlab-ci.yml` (excluded at publication).

---

## Phase 5: Documentation — after Phases 1–4

- [x] T050 DEV [US2] `README.md` "Quality Gates": `make lint` / `make test`; keep the coverage command as an optional local command (DEV-FR-010)
- [x] T051 DEV [US3] `README.md`: add "Releases" section — `development` → `X.Y.0-dev.N` + image `development`; `release-X.Y` → RCs; promote → `X.Y.0` + `latest`; version in `pyproject.toml` is CI-managed, never edited by hand; PR titles follow Conventional Commits (DEV-FR-012)
- [x] T052 [P] DEV [US3] `CHANGELOG.md`: keep entries up to `0.3.1`; under the header add a note that release notes from `0.4.0` on are published as GitHub Releases (DEV-FR-012)
- [x] T053 [P] DEV [US5] `CLAUDE.md` (provisional, Q4b): fix "Project Structure" to the real layout (`src/api/{v1,mcp,dependencies}`, `src/lib/{clients,db,exceptions,models,repositories,schemas,services,utils}`, `src/worker/executors`); add `make lint` / `make test` to Commands; make "Active Feature Plan" generic (DEV-FR-019)
- [x] T054 DEV [US1] `README.md`: add License section (Apache-2.0) and links to `CONTRIBUTING.md` / `SECURITY.md`; update the Postman path after T046

---

## Phase 6: Review and merge on the internal repo — ⚠️ approval gate

- [x] T060 DEV Run quickstart §1–§3 end to end (make targets, `uv lock --check`, `docker build`, `actionlint`, `sed` check, content scan, community files)
- [x] T061 DEV Run quickstart §4 orphan **dry run** locally (no push); confirm 1 commit, no `.gitlab-ci.yml`, no `.env`/`.idea`/`.coverage` staged; delete the dry-run branch
- [x] T062 DEV Launch `code-review-agent` on the diff; resolve findings until 0 open
- [ ] T063 DEV ⚠️ **With explicit approval only**: commit (Conventional Commit messages) and open an MR to `development` on the internal GitLab; merge after review

---

## Phase 7: Publication — ⚠️ separate approval; needs OPS-001…006, OPS-010, OPS-011, OPS-012

**DevOps prerequisites (Track C, before publication)**

- [ ] T070 [P] OPS Create public repo `epam/ai-dial-scheduler` (OPS-001)
- [ ] T071 [P] OPS About: description, website `https://dialx.ai`, topics `llm`, `ai-dial` (OPS-002)
- [ ] T072 [P] OPS Features: disable Wikis, Discussions (OPS-003)
- [ ] T073 [P] OPS Pull Requests: squash only (PR title), no merge/rebase, allow auto-merge, auto-delete head branches; always suggest updating PR branches (OPS-004, OPS-005)
- [ ] T074 [P] OPS Teams: `epam/dial-admins` Admin, `epam/dial-contributors` Write (OPS-006)
- [ ] T075 OPS ⏸ Q3 — owner's personal account Admin (OPS-007)
- [ ] T076 [P] OPS Actions → General: require full-SHA pinning; approval for all external contributors; read/write workflow permissions; allow Actions to create/approve PRs (OPS-010)
- [ ] T077 [P] OPS Secrets: `ACTIONS_BOT_TOKEN`, `DOCKERHUB_LOGIN`, `DOCKERHUB_TOKEN` (OPS-011)
- [ ] T078 [P] OPS Docker Hub repository `epam/ai-dial-scheduler` exists and bot can push (OPS-012)
- [ ] T079 [P] OPS Label `dependencies` — "Dependencies update", `#FFFFFF` (OPS-015)
- [ ] T080 [P] OPS Provide GitLab project ids for API and worker deploy projects (OPS-019) → unblocks T035

**Publication (DEV)**

- [ ] T081 DEV ⚠️ **With explicit approval only**: on the merged internal `development`, `git checkout --orphan public-initial`; `git rm --cached .gitlab-ci.yml`; verify `git status`; commit `feat: initial open-source release`; `git tag 0.3.1`; push `public-initial:development` and tag `0.3.1` to `github.com/epam/ai-dial-scheduler`. No other refs (DEV-FR-020, DEV-FR-012, research R7)

---

## Phase 8: Post-publication

- [ ] T090 DEV [US2] Open a test PR (e.g. `docs: add badges`); confirm the four `run_tests / …` checks and the PR-title check run and pass; a title like `update stuff` fails the title check (SC-002)
- [ ] T091 OPS [US2] Branch protection `development` with required checks from T090 (OPS-008)
- [ ] T092 OPS [US2] Branch protection `release-*` (OPS-009)
- [ ] T093 OPS [US1] Advanced Security: private vulnerability reporting, CodeQL default, Dependabot alerts + security updates (OPS-014) (SC-006)
- [ ] T094 DEV [US3] Merge T090; confirm `calculate_version` logs `0.4.0-dev.N` and images `epam/ai-dial-scheduler:development` exist on Docker Hub and GHCR (SC-003, SC-007)
- [ ] T095 OPS [US3] GHCR package settings: source repo Admin, inherit access (OPS-016)
- [ ] T096 OPS [US4] Environment `development` with `DEPLOY_ACCESS_TOKEN`, `DEPLOY_TRIGGER_TOKEN`, `DEPLOY_HOST` (OPS-013)
- [ ] T097 OPS [US4] Repoint GitLab deploy projects api/worker to `epam/ai-dial-scheduler` image (OPS-017)
- [ ] T098 DEV [US4] Merge T035 (`deploy-development.yml`) via PR; verify both GitLab pipelines start on the next `development` image
- [ ] T099 DEV [US1] ⏸ Q3 — PR adding `.github/CODEOWNERS` (T027) if not done before publication
- [ ] T100 DEV ORT: if T090 reported licence violations, follow-up PR with justified `.ort.yml` exclusions or a curation request to DevOps (research R12)
- [ ] T101 OPS Archive the internal GitLab repository (keeps full history and `.gitlab-ci.yml`) after T098 is verified (OPS-018)
- [ ] T102 DEV [US1] Check Insights → Community Standards is all green (SC-005); tick SC-001…SC-007 in spec

---

## Dependencies & Execution Order

### Phase dependencies

| Phase | Depends on |
|---|---|
| 0 | — (done) |
| 1 CI parity | — |
| 2 Legal/community | — (parallel with 1); T022 after T015 (same file) |
| 3 Workflows | Phase 1 complete (T011, T014, T017) |
| 4 Clean-up | — (parallel with 3); T040 → T041 after T022 (same file) |
| 5 Docs | Phases 1–4 (references their results) |
| 6 Review/merge | Phases 1–5; **approval** |
| 7 Publication | Phase 6 + T070–T074, T076–T078; **separate approval** |
| 8 Post-publication | Phase 7 |

### External blockers

| Blocker | Tasks |
|---|---|
| Q3 owner handle | T027, T075, T099 |
| `authors` value | T041 (fallback: remove field) |
| OPS-019 GitLab ids | T035 → T098 |
| First PR run | T091, T092 |
| First image push | T095 |

### Same-file sequencing

- `pyproject.toml`: T015 → T022 → T040 → T041
- `README.md`: T050 → T051 → T054
- `Dockerfile`: T013 only

### Parallel opportunities

- Phase 1 ‖ Phase 2: T012, T013, T015, T020, T021, T023–T026
- Phase 3 ‖ Phase 4: T032–T034, T036 with T042–T048
- All OPS tasks in Phase 7 (T070–T080) can be done in one DevOps session as soon as Q1 is known (it is)

## Implementation Strategy

### MVP (US1 + US2 + US5): publishable and gated

Phases 1–4 minus T035 and T027 → Phase 5 → Phase 6 → Phase 7 → T090–T092. This yields a licensed, clean public repo
with PR gating. Releases (US3) work from the first merge; auto-deploy (US4) follows when OPS-019 arrives — until then
the internal GitLab pipeline keeps deploying.

### Incremental delivery after MVP

1. T094–T095 — release images verified.
2. T096–T098 — GitHub → GitLab deploy verified.
3. T101 — internal repo archived.
4. T099, T100 — CODEOWNERS and ORT follow-ups as inputs arrive.

## Notes

- Q4b is provisional: `specs/`, `.specify/`, `.claude/`, `CLAUDE.md` stay; their removal later is one deletion PR.
- Conventional-Commit PR titles are enforced from the first public PR (T032); internal MR in T063 should already follow them.
- `code-review-agent` (T062) reviews repository content, not application code; its checklist items about tests/async do not apply.

## Progress log

### 2026-11-03 — Phases 1–5 (no commits)

Verification: `make lint` clean; `make test` 1053 passed, 195 skipped (integration, no `TEST_DB_URL`);
`uv lock --check` OK; `docker build` OK; `actionlint` 1.7.7 (via Docker) clean; YAML of all `.github/*` and
`trivy.yaml` parses; internal-content scan shows only expected hits plus `pyproject.toml` `authors` (T041).

Notes and deviations:

- T013/T014: without `FROM --platform` a local build on Apple Silicon is `arm64` (expected, research R11); CI builds `linux/amd64` via the workflow `platforms` input.
- T015: `uv.lock` updated (`uv lock`) — the root package entry records the project version (`0.3.1` → `0.0.0`). On `release-*` branches CI rewrites only `pyproject.toml`, so `uv.lock` will lag the version there; harmless because the Dockerfile uses `uv sync --frozen` and ORT `uv export --frozen`.
- T021: `LICENSE` copied from `ai-dial-ci`; copyright line set to `2026 EPAM Systems, Inc.` (template says 2023) — **confirm**. `pyproject.toml` also gets `license-files = ["LICENSE"]`.
- T030–T034: `actionlint` run via `docker run rhysd/actionlint:1.7.7` (not installed locally).
- T046/T045: done with plain `mv`/`rm` (index untouched); git shows delete + untracked until staged.
- T048: screenshots and diagram reviewed — only local timestamps, logger names, `0.0.0.0:5000`; nothing internal. Kept.
- T050/T054: README also got a corrected intro and API component description (it still said "Future features will add job scheduling routes").
- **T044 done (decision 2026-11-04)**: tags stay floating (`latest` / `development`); a header comment in `dial/docker-compose.yml` explains why and how to pin for a reproducible setup.
- **T041 done**: `authors` field removed from `pyproject.toml` (decision 2026-11-03; optional field, no PyPI publishing).

### 2026-11-03 — Phase 6 (no commits)

- T060: quickstart §1–§3 pass (re-run after review fixes): `make lint`, `make test` (1053 passed, 195 skipped), `uv lock --check`, `actionlint`, JSON/YAML parse, community files present.
- T061: orphan dry run replaced by a **no-commit equivalent** (no commits allowed at this stage): the publish set = `git ls-files --cached --others --exclude-standard` minus `.gitlab-ci.yml` → 522 files; no `.env*`, `.idea`, `.venv`, `.coverage`, `__pycache__`, reports; content scan clean except `gflog.xml` (upstream) and `authors` (T041). Run the real §4 dry run right before T081.
- T062: `code-review-agent` — approved with changes, 0 critical, 2 major, 11 minor. All addressed:
  - M1 internal hosts/commit/1Password in this feature's own docs → reworded neutrally.
  - M2 DIAL dev-cluster host in `tests/e2e/http-client.env.json` and `specs/027-*` (7 places) → `https://<dial-core-host>`; `dial\.parts` added to the quickstart §2 pattern. (Deviation: one non-code file under `tests/` changed.)
  - Dependabot `docker` entry removed (security updates do not cover Docker images; version updates would propose 3.15); R10/DEV-FR-011/plan/T036 corrected.
  - Dependabot automation kept template-identical; R10 now states it approves all non-major PRs.
  - CLAUDE.md: tests layout line; known `lib → worker` import exception (`run_launcher.py`) noted.
  - README: Teardown moved after Verifying Worker; `make` added to Prerequisites.
  - Postman: `_exporter_id` removed, `ownerUserId` → `{{OWNER_USER_ID}}` variable, name "DIAL Scheduler".
  - `specs/001-*` personal path, `specs/039-*` production schedule id/title → neutral wording.
  - `pyproject.toml` `hatchling>=1.27` (PEP 639 license fields).
  - Contract `uv.lock` row: may lag on `release-*`.
  - `.claude/agents/code-review-agent.md` paths → `src/lib/...`, `src.lib.utils.logging`.
  - DEV-FR-004 checklist: "docs updated" instead of CHANGELOG.
- T063: skipped — no commits by instruction.

### 2026-11-04 — Final Track B audit (no commits)

- Every DEV-FR checked against the tree: all done except DEV-FR-003 (`CODEOWNERS`, waits Q3) and DEV-FR-009a (`deploy-development.yml`, waits OPS-019); DEV-FR-015 (`.gitlab-ci.yml`) is applied at publication (T081).
- Found during the audit and fixed: `CODESTYLE.md` still showed the old coverage/JUnit command → added `make lint` / `make test`; `documentation/deployment/deployment.md` step 1 now points to the published images (Docker Hub / GHCR) as well as building from the Dockerfile.

### 2026-11-05 — Publication (history kept)

- A squashed commit was force-pushed to GitHub `development` and then reverted the same day: the team chose to keep the history (spec Q4a / DEV-FR-020 updated). GitHub `development` is back at the internal `0.3.0` merge `b10e7c6`.
- First CI run on the squashed commit showed: ORT `UNHANDLED_LICENSE` (psycopg2) + 2 `UNMAPPED_DECLARED_LICENSE` warnings (psycopg2-binary, scramp) → `.ort.yml` resolutions; verified green on a PR run. Trivy: 2 critical + 12 high in authlib, certifi, aiohttp, pyasn1, dnspython, joserfc → floors in `[tool.uv] constraint-dependencies`; local Trivy scan clean.
- Open-source changes are delivered as one PR on top of the history: CRLF fix, `.gitlab-ci.yml` removal, `.ort.yml`, dependency floors, these doc updates.
- `pr-title-check` fails until `ACTIONS_BOT_TOKEN` is configured (OPS-011).
