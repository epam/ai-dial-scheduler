# Feature Specification: Open-Source Preparation

**Branch**: `040-opensource-preparation` | **Date**: 2026-11-03 | **Status**: Draft

**Origin**: The project is moving from the internal GitLab
to a public repository in the `epam` GitHub organization, following the *New GitHub Project Creation Guide* and the
reusable workflows of [`epam/ai-dial-ci`](https://github.com/epam/ai-dial-ci) (pinned release **4.13.2**).

## Problem

The codebase is technically healthy — 1040 unit tests pass, `ruff check`, `ruff format --check` and `mypy` are
clean, and no credential patterns (`sk-…`, `AKIA…`, `glpat-…`, private keys) were found in the git history. It is
nevertheless not publishable as-is:

- There is **no `LICENSE`**, so the code cannot legally be reused by anyone outside EPAM.
- CI lives in `.gitlab-ci.yml` and depends on internal infrastructure: an internal shared template include,
  an internal container registry, internal runners and triggers into internal deploy projects.
  None of it runs on GitHub.
- The `ai-dial-ci` reusable workflows call `make lint` and `make test`; the repository has **no `Makefile`**.
- The release workflow owns the version in `pyproject.toml` (it rewrites it with `sed`), whereas this project bumps
  it by hand ("Prepare 0.3.1" commits).
- Internal references and work artifacts remain in the tree and in history (see DEV-FR-016 – DEV-FR-020).
- Repository-level settings (branch protection, security scanning, secrets, teams) must be configured in the GitHub
  UI and can only be done by DevOps.

The work therefore splits into three tracks:

| Track | Owner | Where |
|---|---|---|
| **A — Already done** | — | Existing files that already satisfy the guide |
| **B — Development team** | Dial Scheduler developers | Files in this repository (one or more PRs) |
| **C — DevOps** | DevOps engineers | GitHub repository settings, secrets, packages, GitLab |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - An external developer can legally use and contribute (Priority: P1)

A developer outside EPAM finds the repository, reads the README, sees the license, and opens an issue or a PR using
the provided templates.

**Why this priority**: Without a license the publication is pointless; without templates and CONTRIBUTING the
project receives unstructured input.

**Acceptance**: The repository root contains `LICENSE` (Apache-2.0), `README.md`, `CONTRIBUTING.md`, `SECURITY.md`
pointing at this repository's advisory form; "New issue" offers only the bug-report and feature-request forms; a
new PR is pre-filled with the PR template.

### User Story 2 - Every PR is gated by the standard DIAL checks (Priority: P1)

A contributor opens a PR against `development`. The standard DIAL checks run and must pass before merge.

**Why this priority**: Branch protection cannot reference status checks that have never run, so CI must work before
DevOps can finish track C.

**Acceptance**: A PR triggers `run_tests / docker_build`, `run_tests / test / style_checks`,
`run_tests / test / code_checks` and `run_tests / test / ort`; all four are green on a clean branch; a PR title not
following Conventional Commits fails the title check.

### User Story 3 - Releases and images are produced by GitHub Actions (Priority: P1)

A maintainer merges to `development` or cuts `release-X.Y`. An image is published without manual version edits.

**Acceptance**: A push to `development` publishes `epam/ai-dial-scheduler:development` to Docker Hub and GHCR; a push to
`release-X.Y` publishes `X.Y.0-rc.N`; a manual "promote" dispatch publishes `X.Y.0`, creates the git tag and GitHub
release.

### User Story 4 - The development environment keeps auto-deploying (Priority: P2)

After the move, the GitLab-hosted development environment (API and worker) still updates automatically when a new
`development` image appears.

**Acceptance**: Publishing a `development` image triggers the GitLab deploy pipelines for both API and worker.

### User Story 5 - Nothing internal is published (Priority: P1)

A reviewer scanning the public tree and history finds no internal hosts, personal test files, internal
infrastructure documents or real-looking secrets.

**Acceptance**: `git grep -i -E 'deltix|gitlab|ihor|kyrylo'` over the published tree returns only intentional hits
(e.g. the upstream ai-dial-core logging class names), and the published history satisfies DEV-FR-020.

### Edge Cases

- **Integration tests in CI.** `tests/integration` skips itself when `TEST_DB_URL` is unset, and the reusable
  workflow provides no PostgreSQL service. `make test` will therefore run only unit tests on GitHub; integration
  tests stay a local/manual gate.
- **ORT licence policy.** ORT evaluates every resolved dependency against `epam/ai-dial-ort-config`. Copyleft
  candidates (e.g. `psycopg2`, LGPL) may produce violations; these are only known after the first run.
- **Python version.** `python_docker_*` defaults to Python 3.11; the project requires `>=3.14,<3.15`. Every caller
  workflow must pass `python-version: "3.14"`.
- **Required checks bootstrap.** Branch protection rules (OPS-008/OPS-009) can only select checks that have run at
  least once, so the first `pr.yml` run must precede them.
- **GHCR package access** (OPS-016) can only be configured after the first image has been pushed.

## Requirements *(mandatory)*

### Track A — Already done

| ID | Guide item | Evidence | Note |
|---|---|---|---|
| DONE-001 | `CONTRIBUTING.md` | Root file; identical to the guide's template | — |
| DONE-002 | `README.md` | Root file with quickstart, testing, DB auth, deployment sections | Links reviewed under DEV-FR-017 |
| DONE-003 | `Dockerfile` usable by `docker_build` | Root `Dockerfile`; non-root user, `apk upgrade`, build tools and pip/uv removed from the final image | See DEV-FR-014 on `--platform` |
| DONE-004 | `uv.lock` committed (prerequisite of uv workflows and ORT) | Root `uv.lock` | — |
| DONE-005 | `pyproject.toml` with `name` and `version` | `name = "aidial-scheduler"` | Version handling changes in DEV-FR-012 |
| DONE-006 | `development` as primary branch | Default branch is `development` | Matches `primary-branch-pattern: ^development$` |
| DONE-007 | Lint/format/type-check tooling | `ruff`, `mypy` configured in `pyproject.toml`; currently clean | Wired into CI by DEV-FR-010 |
| DONE-008 | Vulnerable transitive pins | `[tool.uv] constraint-dependencies` with CVE floors | Helps Trivy and Dependabot pass |
| DONE-009 | `SECURITY.md` exists | Root file, guide wording | **Partially**: link must point to this repo (DEV-FR-002) |

### Track B — Development team (repository changes)

**Legal and community files**

- **DEV-FR-001**: Add `LICENSE` with the Apache License 2.0 text copied from `epam/ai-dial-ci`. Add
  `license = "Apache-2.0"` to `pyproject.toml`.
- **DEV-FR-002**: In `SECURITY.md`, replace the advisory link `github.com/epam/ai-dial/security/advisories/new`
  with `github.com/epam/ai-dial-scheduler/security/advisories/new`.
- **DEV-FR-003** *(blocked by Q3)*: Add `.github/CODEOWNERS`:
  ```text
  * @<responsible-owner>
  /.github/ @nepalevov @alexey-ban
  ```
- **DEV-FR-004**: Add `.github/pull_request_template.md` based on the `ai-dial-ci` example, with a project checklist
  (tests added, `make lint` / `make test` pass, docs updated, migration included if the schema changed).
- **DEV-FR-005**: Add `.github/ISSUE_TEMPLATE/config.yml` (`blank_issues_enabled: false`, Discord contact link) and
  `.github/ISSUE_TEMPLATE/01_bug_report.yml`, `02_feature_request.yml` adapted from `ai-dial-ci`.

**GitHub Actions** (all `epam/ai-dial-ci` references pinned to the full SHA of release 4.13.2 with a `# 4.13.2`
comment, never `@main` — see research R5)

- **DEV-FR-006**: Add `.github/workflows/pr.yml` calling `python_docker_pr.yml` with
  `python-package-manager: uv` and `python-version: "3.14"`, triggered on PRs to `development` and `release-*`.
- **DEV-FR-007**: Add `.github/workflows/release.yml` calling `python_docker_release.yml` with the same inputs plus
  the `promote` dispatch input, triggered on pushes to `development` and `release-*`.
- **DEV-FR-008**: Add `.github/workflows/pr-title-check.yml` (Validate PR title, `pull_request_target`).
- **DEV-FR-009**: Add `.github/workflows/cleanup-untagged-images.yml` (daily GHCR cleanup), since the release
  workflow publishes to GHCR.
- **DEV-FR-009a**: Add `.github/workflows/deploy-development.yml` using the matrix variant with two entries
  (API and worker), each with its GitLab project id from OPS-019. *Conditional on OPS-013.*
- **DEV-FR-009b** *(optional)*: Add `.github/workflows/dependabot-automation.yml`.
- **N/A**: `dependency-review.yml` (Java/Gradle only); `slash-command-dispatch.yml` (no review environments planned).

**CI prerequisites not stated in the guide**

- **DEV-FR-010**: Add a `Makefile` with targets `install`, `lint`, `test`:
  - `install`: `uv sync --all-extras`
  - `lint`: `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`
  - `test`: `uv run pytest` (integration tests self-skip without `TEST_DB_URL`)
  Document the targets in `README.md` ("Quality Gates") and `CONTRIBUTING.md`-adjacent docs.
- **DEV-FR-011**: Add `.github/dependabot.yml` with ecosystems `uv` (security updates only,
  `open-pull-requests-limit: 0`, prefix `chore`) and `github-actions` (grouped `ai-dial-ci` / others, prefix `ci`),
  per the `ai-dial-ci` example.
- **DEV-FR-012**: Hand version ownership to CI (see Clarifications Q4c for the analysis):
  - set `version = "0.0.0"` in `pyproject.toml`. The value is never read: `python_docker_release.yml` derives the
    version from git tags plus the branch name and only *writes* it with
    `sed -i "s/^version = .*/version = \"…\"/g" pyproject.toml`. Keep exactly one column-0 `version = ` line in
    `pyproject.toml`, because that `sed` rewrites every such line;
  - when publishing (DEV-FR-020), create a lightweight tag **`0.3.1`** (plain `X.Y.Z`, no `v` prefix) on the squashed
    initial commit and push it. It marks the last internal release, so `development` builds continue as
    `0.4.0-dev.N` instead of restarting at `0.1.0-dev.N`, and the first release changelog uses it as the baseline;
  - the first public release is cut from branch `release-0.4` → `0.4.0-rc.0`, `0.4.0-rc.1`, …; promote → `0.4.0`
    (tagged `latest`); later pushes to `release-0.4` → `0.4.1`, `0.4.2`, …;
  - stop the manual "Prepare X.Y.Z" commits; update the release section of `README.md` accordingly;
  - `CHANGELOG.md`: keep the existing entries up to `0.3.1` and add a note that later release notes are published as
    GitHub Releases (generated by `ai-dial-ci`).
- **DEV-FR-013**: Add `trivy.yaml` in the repository root with the `ai-dial-ci` mirror configuration.
- **DEV-FR-014**: Remove the hard-coded `--platform=linux/amd64` from the `Dockerfile` `FROM` line; platforms are
  controlled by the workflow `platforms` input.
- **DEV-FR-015**: `.gitlab-ci.yml` is **not part of the published tree** (it references internal infrastructure). It is
  excluded from the squashed public commit (DEV-FR-020) but stays in the internal repository, which keeps building
  and deploying until the GitHub-based deploy (DEV-FR-009a, OPS-013, OPS-017) is confirmed; then the internal
  repository is archived (OPS-018).

**Clean-up of internal content**

- **DEV-FR-016**: Fix `pyproject.toml` metadata: real `authors` (or the DIAL team), meaningful `description`,
  `[project.urls]` (Homepage `https://dialx.ai`, Repository, Issues).
- **DEV-FR-017**: Remove or neutralise internal references in the tree:
  - `specs/025-k8s-worker-probes/research.md`, `specs/028-responses-schedule-type/code-review-report.md`,
    `specs/035-schedule-delete/code-review-report-round2.md` (GitLab CI, personal files);
  - `Dockerfile` comment "1001 uid also used in HELM chart";
  - `dial/.env.template`: replace the literal `NEXTAUTH_SECRET` value with a placeholder and a generation hint;
  - `dial/docker-compose.yml`: pin image tags instead of `:latest` / `:development` where feasible;
  - review `documentation/deployment/*.png` screenshots for internal hosts, names, ids.
- **DEV-FR-018**: Remove `log.json` from the repository root; rename `DIAL Scheduler.postman_collection.json` to a
  space-free name (e.g. `documentation/postman/dial-scheduler.postman_collection.json`).
- **DEV-FR-019** *(provisional — decision may change, see Clarifications Q4b)*: AI/SDLC artifacts — `specs/`,
  `.specify/`, `.claude/`, `CLAUDE.md` — are **kept** for now. Until the decision is final:
  - they are cleaned like the rest of the tree (DEV-FR-017), so keeping them does not leak anything;
  - `CLAUDE.md` "Project Structure" is updated to the real layout (`src/api`, `src/lib/*`, `src/worker/*`) and the
    "Active Feature Plan" pointer is made generic;
  - nothing outside these paths may depend on them (they are already excluded from the image by `.dockerignore`),
    so removing them later is a single deletion PR with no other changes.
- **DEV-FR-020**: Publish the repository as a **single squashed initial commit** — no internal history is pushed to
  GitHub. The internal history contains files absent from the tree (e.g.
  `documentation/deployment/dial-scheduler-external-services-devops-setup.md`, `dial_conf/*`,
  `docker-compose-dial.yml`, `.specify/memory/requirements.md`) and references to internal infrastructure; squashing removes all of it at once. Procedure:
  - finish DEV-FR-001 … DEV-FR-019 on the internal repo first, so the squashed tree is already clean;
  - create an orphan branch from that tree (`git checkout --orphan`), commit once
    (`feat: initial open-source release`), and push it as `development` to `epam/ai-dial-scheduler`;
  - push no internal tags (`0.1.0`, `0.2.0`) and no other branches; push only the new tag `0.3.1` on the squashed
    commit (DEV-FR-012);
  - the internal GitLab repository keeps the full history for reference (OPS-018).

### Track C — DevOps (GitHub settings and infrastructure)

| ID | Guide step | Action | Depends on |
|---|---|---|---|
| OPS-001 | Create project | Create `epam/ai-dial-scheduler` (public) | Clarification Q1 |
| OPS-002 | About | Description; Website `https://dialx.ai`; Topics `llm`, `ai-dial` (+ `scheduler`, `python`) | OPS-001 |
| OPS-003 | Features | Uncheck Wikis, Discussions | OPS-001 |
| OPS-004 | Pull Requests | Squash only (default message: PR title); no merge commits, no rebase; allow auto-merge; auto-delete head branches | OPS-001 |
| OPS-005 | General | Enable "Always suggest updating pull request branches" | OPS-001 |
| OPS-006 | Teams | `epam/dial-admins` → Admin; `epam/dial-contributors` → Write | OPS-001 |
| OPS-007 | People | Responsible owner's personal account → Admin | Q3 (pending) |
| OPS-008 | Branch protection `development` | PR required + approvals + dismiss stale; required checks (up to date): `run_tests / docker_build`, `run_tests / test / ort`, `run_tests / test / style_checks`, `run_tests / test / code_checks`, plus the PR-title check job (exact name as shown after its first run); conversation resolution; linear history; restrict push and branch creation to `epam/dial-contributors` | First green run of DEV-FR-006/008 |
| OPS-009 | Branch protection `release-*` | Same as OPS-008 minus "dismiss stale" and "linear history" | First green run of DEV-FR-006 |
| OPS-010 | Actions → General | Require full-length SHA pinning; require approval for all external contributors; read/write workflow permissions; allow Actions to create/approve PRs | OPS-001 |
| OPS-011 | Repository secrets | `ACTIONS_BOT_TOKEN` (PAT of the `ai-dial-actions` bot); `DOCKERHUB_LOGIN`, `DOCKERHUB_TOKEN` (release publishes to Docker Hub). `PYPI_TOKEN`, `NPM_TOKEN` — not needed | OPS-001 |
| OPS-012 | Docker Hub | Ensure repository `epam/ai-dial-scheduler` exists on hub.docker.com and the bot account can push | OPS-011 |
| OPS-013 | Environment `development` | Create, restrict to branch `development`; secrets `DEPLOY_ACCESS_TOKEN` (GitLab project access token, Guest, `read_api`+`read_repository`), `DEPLOY_TRIGGER_TOKEN`, `DEPLOY_HOST` (internal GitLab host, provided by DevOps) | Clarification Q2 |
| OPS-014 | Advanced Security | Enable private vulnerability reporting; CodeQL default setup; Dependabot alerts + security updates (+ version updates optional) | OPS-001, DEV-FR-011 |
| OPS-015 | Labels | Create `dependencies` — "Dependencies update", `#FFFFFF` | OPS-001 |
| OPS-016 | GHCR package | After the first image push: Manage Actions access → add source repo with Admin; enable "Inherit access from source repository" | First run of DEV-FR-007 |
| OPS-017 | GitLab deploy projects | Repoint `dial-scheduler-api` / `dial-scheduler-worker` deploy projects from the internal registry image to the public `epam/ai-dial-scheduler` image; issue the trigger tokens for OPS-013 | OPS-013 |
| OPS-018 | Decommission | Archive / make read-only the internal GitLab repository after the switch-over | DEV-FR-015 |
| OPS-019 | Hand-off to dev team | Provide: GitLab project ids for API and worker deploy (for DEV-FR-009a); GitHub handle of the responsible owner (DEV-FR-003) | — |

### Cross-track order

1. Clarifications Q1, Q2, Q4 answered → OPS-001 … OPS-006, OPS-010, OPS-011, OPS-015 (no repo content needed).
   Q3 (pending) gates only DEV-FR-003 and OPS-007.
2. Track B content prepared on the internal repo (DEV-FR-001 … DEV-FR-019), then pushed to GitHub as one squashed
   commit (DEV-FR-020).
3. First PR run → OPS-008, OPS-009 (select required checks); OPS-014.
4. First `development` release → OPS-016; then OPS-013 + OPS-017 + DEV-FR-009a for auto-deploy.
5. Everything green → OPS-018 (archive the internal GitLab repository, which still holds `.gitlab-ci.yml`).

### Key Entities

- **Caller workflows** (`pr.yml`, `release.yml`, …): thin files in this repo delegating to `epam/ai-dial-ci`.
- **Required status checks**: names produced by the caller job `run_tests` and the reusable jobs
  (`docker_build`, `test / style_checks`, `test / code_checks`, `test / ort`).
- **Published image**: `epam/ai-dial-scheduler` on Docker Hub and `ghcr.io/epam/ai-dial-scheduler`; tags `development`, `X.Y.Z-rc.N`,
  `X.Y.Z`, `latest`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All Track B requirements merged; all Track C rows done; Track A rows still valid.
- **SC-002**: A test PR to `development` shows the four `run_tests / …` checks and the PR-title check, all green,
  and cannot be merged while any of them is red or the branch is out of date.
- **SC-003**: The first push to `development` publishes the `development` image to both registries without any
  manual version edit.
- **SC-004**: `git grep -i -E 'deltixhub|gitlab|ihor_|kyrylo_|Your Name'` on the published tree returns no
  unintended hits; the public repository contains exactly one commit before the first PR and only the tag `0.3.1`.
- **SC-007**: For the first `development` build, the `calculate_version` job outputs `0.4.0-dev.N` (visible in the
  workflow log); the first promote of `release-0.4` publishes `0.4.0` with the `latest` tag.
- **SC-005**: GitHub "Community Standards" page for the repo shows README, License, Contributing, Security policy,
  Issue templates and PR template as present.
- **SC-006**: CodeQL, Dependabot and private vulnerability reporting show as enabled under Security.

## Clarifications

### Session 2026-11-03

- **Q1 — Repository name**: **`epam/ai-dial-scheduler`**. Image: `epam/ai-dial-scheduler` (Docker Hub) and
  `ghcr.io/epam/ai-dial-scheduler`.
- **Q2 — Auto-deploy to GitLab**: **kept**. DEV-FR-009a, OPS-013, OPS-017 and OPS-019 are in scope.
- **Q4a — Git history**: **squashed** into a single initial commit (DEV-FR-020).
- **Q4b — AI/SDLC artifacts**: **kept for now, provisional** (DEV-FR-019). The decision may be revisited before or
  after publication; the requirements make a later removal a self-contained deletion.
- **Q4c — Version**: verified against `epam/ai-dial-ci@4.13.2` (`python_docker_release.yml` →
  `actions/semantic_versioning@4.12.0`):
  - the version is computed from **git tags + branch name**; `pyproject.toml` is write-only, so its initial value
    is irrelevant → set to `0.0.0` per the `ai-dial-ci` convention;
  - with no tags at all: `development` → `0.1.0-dev.N`, `release-X.Y` → `X.Y.0-rc.0`, promote → `X.Y.0` + `latest`.
    No tag is required for the scheme to work;
  - a plain `0.3.1` tag on the initial commit makes `development` continue as `0.4.0-dev.N` and serves as the
    changelog baseline; promoting `release-0.4` still yields `0.4.0` marked latest;
  - **Decision**: `version = "0.0.0"` + tag `0.3.1` on the squashed commit; first public release `0.4.0` from
    `release-0.4` (DEV-FR-012).

### Open

- **Q3 — Responsible owner** [NEEDS CLARIFICATION — pending, no answer yet]: GitHub handle for CODEOWNERS
  (DEV-FR-003) and OPS-007. Does not block other work; DEV-FR-003 and OPS-007 wait for it, and CODEOWNERS must be in
  place before branch protection relies on code-owner review.

## Assumptions

- `epam/ai-dial-ci` **4.13.2** is the pinned version; uv support exists since 4.8.0.
- The image is published to both Docker Hub and GHCR, as `python_docker_release.yml` does by default.
- `development` / `release-X.Y` branching with Conventional-Commit PR titles is acceptable to the team.
- The default ORT configuration (`epam/ai-dial-ort-config`) applies; any violations are handled via `.ort.yml`
  exceptions in a follow-up PR.
- Integration tests are not run in GitHub CI in this iteration.

## Out of Scope

- Running integration tests (PostgreSQL) in GitHub Actions.
- Publishing a Python package to PyPI.
- Helm chart publication.
- Review environments (`/deploy-review`).
- Functional changes to the scheduler.
