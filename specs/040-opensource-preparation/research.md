# Phase 0 Research: Open-Source Preparation

Sources: `epam/ai-dial-ci` at tag **4.13.2** (commit `00484ead5dc3904a787c5af8d0bdd8f4fdf087c1`) — `README.md`,
`docs/BRANCHING_STRATEGY.md`, `.github/workflows/python_docker_{pr,release,test}.yml`,
`actions/semantic_versioning/action.yml` (pinned inside 4.13.2 as `@4.12.0`), `SECURITY.md`,
`.github/pull_request_template.md`, `.github/ISSUE_TEMPLATE/config.yml`; the *New GitHub Project Creation Guide*;
the current repository state on `development` @ `2764371`.

## R1. Which reusable workflows

**Decision**: `python_docker_pr.yml` for PRs and `python_docker_release.yml` for releases.

**Rationale**: The project ships a Docker image, not a PyPI package. The `python_package_*` variants require `build`
and `publish` Make targets and a `PYPI_TOKEN`, which are out of scope. `generic_docker_*` would skip the Python style,
code and ORT jobs, which the guide lists as required status checks.

**Alternatives rejected**: our own workflows (the guide asks to reuse `ai-dial-ci`; they would duplicate ORT, Trivy,
semantic versioning and release publishing).

## R2. uv support and Python version

**Decision**: pass `python-package-manager: uv` and `python-version: "3.14"` in both caller workflows.

**Rationale**: uv is supported since `ai-dial-ci` 4.8.0. The inputs default to `poetry` and `"3.11"`; the project
requires `>=3.14,<3.15`, so the defaults would fail at `uv sync`. The workflow's `uv-version` default (`0.11.30`)
is used as is; it is unrelated to the `uv==0.9.28` pinned inside the Dockerfile.

## R3. Make targets are the CI contract

**Decision**: add a `Makefile` with `install`, `lint`, `test` (see `contracts/ci-contract.md`).

**Rationale**: `test / style_checks` runs `make lint` and `test / code_checks` runs `make test`; nothing else is
invoked. `install` is not called by CI but is a prerequisite of both targets, matching the `ai-dial-ci` uv example.

- `lint` adds `mypy src` to the `ai-dial-ci` example, because type checking is an existing quality gate
  (README "Quality Gates", `.gitlab-ci.yml` `lint` job) and must keep running.
- `ruff` is scoped to `src tests` like the current gates, not `.`, so that `scripts/` and `migrations/` keep their
  current (un-gated) status. This matches CLAUDE.md commands.
- `test` runs plain `uv run pytest`. `tests/integration/conftest.py` skips the whole package when `TEST_DB_URL` is
  unset, so CI runs unit tests only. Coverage flags are not added: `ai-dial-ci` does not collect coverage artifacts.

## R4. Required status check names

**Decision**: `run_tests / docker_build`, `run_tests / test / style_checks`, `run_tests / test / code_checks`,
`run_tests / test / ort`, plus the PR-title check.

**Rationale**: the caller job must be named `run_tests`; `python_docker_pr.yml` defines jobs `test` (calling
`python_docker_test.yml` with jobs `style_checks`, `code_checks`, `ort`) and `docker_build`. The guide's example names
match exactly. The PR-title check name is taken from its first run (OPS-008).

## R5. Pinning `ai-dial-ci`

**Decision**: reference reusable workflows by **full commit SHA with a version comment**:
`epam/ai-dial-ci/.github/workflows/python_docker_pr.yml@00484ead5dc3904a787c5af8d0bdd8f4fdf087c1 # 4.13.2`.

**Rationale**: the guide says "replace `@main`" and OPS-010 enables "Require actions to be pinned to a full-length
commit SHA". A SHA satisfies both regardless of whether the policy is evaluated for reusable-workflow references.
Dependabot (`github-actions` ecosystem, group `ai-dial-ci`) updates SHA pins and their trailing version comments.

**Alternatives rejected**: `@4.13.2` tag — readable, but tags are mutable and may be rejected by the SHA policy.

## R6. Version computation

**Decision**: `version = "0.0.0"` in `pyproject.toml`; lightweight tag `0.3.1` on the squashed initial commit; first
public release from `release-0.4`.

**Findings** (`actions/semantic_versioning`):

- The version derives from git tags (`^[0-9]+\.[0-9]+\.[0-9]+$`, `-rc.N`) and the branch name. `pyproject.toml` is
  write-only: the release job runs `sed -i "s/^version = .*/version = \"<v>\"/g" pyproject.toml`.
- No tags: `development` → `0.1.0-dev.N`; `release-X.Y` → `X.Y.0-rc.0`; promote → `X.Y.0`, `is-latest=true`.
- With stable tag `0.3.1`: `development` → `0.4.0-dev.N` (N = commits since the tag); changelog baseline = `0.3.1`.
- The `sed` rewrites **every** column-0 `version = ` line. Today `pyproject.toml` has exactly one (`python_version`
  and `target-version` do not match `^version = `).

**Rationale**: without the tag, public dev builds would report a version lower than the already-deployed `0.3.1`
images. `0.0.0` follows the `ai-dial-ci` convention and makes it obvious the value is managed by CI.

## R7. Publication by squash

**Decision**: build the public tree on the internal repo, then publish via an orphan branch holding one commit.

**Rationale**: the internal history contains internal-only files
(`documentation/deployment/dial-scheduler-external-services-devops-setup.md`, `dial_conf/*`,
`docker-compose-dial.yml`, `.specify/memory/requirements.md`) and references to internal infrastructure, plus
all authors' corporate emails. One orphan commit removes all of it with no history rewriting tools.

**Procedure** (executed only on explicit go-ahead; it pushes to a public remote):

```bash
git switch development                                 # contains merged 040 work
git checkout --orphan public-initial                   # NOT `git switch --orphan`: that empties the tree
git rm --cached --quiet .gitlab-ci.yml                 # DEV-FR-015: excluded from public tree
git commit -m "feat: initial open-source release"      # author = DIAL team / maintainer
git tag 0.3.1
git remote add github git@github.com:epam/ai-dial-scheduler.git
git push github public-initial:development
git push github 0.3.1
```

`git checkout --orphan` keeps the current index and working tree, so the single commit holds the whole cleaned tree;
`.gitlab-ci.yml` is removed from the index only, and nothing changes on `development` in the internal repo. Before
committing, verify with `git status` that no ignored/local files (`.env`, `.coverage`, `.idea/`) were staged.

## R8. `.gitlab-ci.yml` transition

**Decision**: keep it in the internal repo, exclude it from the public commit (see R7). The internal pipeline keeps
deploying until the GitHub `deploy-development.yml` → GitLab trigger is verified (OPS-013, OPS-017); then the
internal repo is archived (OPS-018).

**Rationale**: avoids a window with no deploy path, and nothing internal is published.

## R9. Auto-deploy to GitLab

**Decision**: matrix variant of `deploy-development.yml` with two entries, `dial-scheduler-api` and
`dial-scheduler-worker`, each with its own `gitlab-project-id` (from OPS-019).

**Rationale**: `.gitlab-ci.yml` currently triggers two downstream projects (`trigger_deploy_api`,
`trigger_deploy_worker`) from one image. The guide's single-project example covers one; the README's matrix variant
covers several. Trigger: `registry_package` with tag `development`, plus the `workflow_run` fallback.

## R10. Dependabot

**Decision**: ecosystems `uv` (security updates only, `open-pull-requests-limit: 0`, prefix `chore`) and
`github-actions` (groups `ai-dial-ci` / others, prefix `ci`, scope included), weekly Wednesday 09:00, per the
`ai-dial-ci` template. No `docker` ecosystem: Dependabot security updates do not cover Docker images, and
version updates would propose `python:3.15-alpine`, which breaks `requires-python <3.15`. The base image is gated by
Trivy in `docker_build` instead.

**Rationale**: Dependabot supports `uv` natively, reading `pyproject.toml` + `uv.lock`. The `[tool.uv]
constraint-dependencies` CVE floors remain the manual mechanism for transitive pins.

`dependabot-automation.yml` (DEV-FR-009b) is included: kept identical to the `ai-dial-ci` template. It **approves**
every non-major Dependabot PR (including `uv` security bumps) but **auto-merges** only non-major `ai-dial-ci`
updates; other PRs still need someone to merge them. If the bot's approval counts towards OPS-008, app-dependency
bumps need no human approval — accepted as the DIAL-wide convention. It needs auto-merge enabled (OPS-004) and `ACTIONS_BOT_TOKEN` (OPS-011).

## R11. Dockerfile platform

**Decision**: drop `--platform=linux/amd64` from `FROM`.

**Rationale**: `docker_build` passes `platforms` (default `linux/amd64`) to buildx. A hard-coded `FROM --platform`
would silently produce amd64 layers under a future arm64 build. Local builds on Apple Silicon will build natively
(arm64); developers who need amd64 locally use `docker build --platform linux/amd64`. The `docker-compose.yml`
`build: .` keeps working.

## R12. ORT and licences

**Finding**: ORT exports `uv.lock` to `requirements.txt` and analyses it as PIP with `pythonVersion=3.14`, evaluated
against `epam/ai-dial-ort-config`. Candidate issues: `psycopg2` (LGPL-3.0 with exceptions) in runtime deps;
`psycopg2-binary` is dev-only. Results are only known after the first run.

**Decision**: no pre-emptive `.ort.yml`. If the first run reports violations, handle them in a follow-up PR via a
justified `.ort.yml` exclusion (or a curation in `ai-dial-ort-config`, by DevOps). Replacing `psycopg2` is **not** a
cheap fallback: it backs the sync engine (APScheduler job store, Alembic) for the password, Azure Entra and AWS IAM
modes (`src/lib/db/enums.py`, `src/lib/db/url.py`, `src/lib/db/sync_engine.py`); only GCP uses `pg8000`. During the
bootstrap PR the `ort-bypassed` label may be used once, by DevOps decision.

## R13. Trivy

**Decision**: add root `trivy.yaml` with the `ai-dial-ci` mirror template (DB, Java DB, checks bundle). No
`skip-dirs`: only the built image is scanned by `docker_build`; the scheduled SCA scan is out of scope.

## R14. Community files

**Decision**: copy from `ai-dial-ci` and adapt:

- `LICENSE` — Apache-2.0 verbatim; `pyproject.toml` gets `license = "Apache-2.0"` (PEP 639 SPDX string; supported by
  hatchling, and `[tool.uv] package = false` means no wheel is built anyway).
- `SECURITY.md` — keep current wording, change the link to `epam/ai-dial-scheduler`.
- PR template — the `ai-dial-ci` text with a project checklist; the Apache-2.0 contribution sentence is kept.
- Issue templates — `config.yml` (blank issues disabled, Discord link), `01_bug_report.yml`, `02_feature_request.yml`
  adapted to the scheduler (component dropdown: API, Worker, MCP, Migrations, Docs).
- `CODEOWNERS` — blocked by Q3. It is added only once the handle is known: a placeholder handle is not an option,
  because GitHub flags unknown owners as errors and the file would not work.

## R15. Clean-up scope

| Item | Action |
|---|---|
| `pyproject.toml` `authors` (`"Your Name"`, personal email) | Team/org name and a team mailbox — **value to confirm** with the team (open item); fallback: drop the field (it is optional and the project is not published to PyPI) |
| `pyproject.toml` `description` | "Scheduling service for AI DIAL: cron/interval/date triggers dispatching DIAL applications, MCP and HTTP targets" |
| `[project.urls]` | Homepage `https://dialx.ai`, Repository / Issues on `github.com/epam/ai-dial-scheduler` |
| 3 spec files with internal refs | Redact the sentences (keep the files — Q4b provisional) |
| `Dockerfile` "HELM chart" comment | Reword to "uid 1001 is the documented non-root runtime uid" |
| `dial/.env.template` `NEXTAUTH_SECRET` | Placeholder + `openssl rand -base64 32` hint |
| `dial/docker-compose.yml` `:latest` / `:development` | Pin to current released DIAL tags where available; keep `development` only for components that need unreleased features, with a comment |
| `documentation/deployment/*.png` | Manual review by a human; replace or crop if internal hosts/ids are visible |
| `log.json` | Delete |
| Postman collection | `git mv` to `documentation/postman/dial-scheduler.postman_collection.json`; update references |
| `CLAUDE.md` | Fix "Project Structure"; make "Active Feature Plan" generic |
| `.gitignore` | Remove stale internal entries (`src/chat_v2/...`, `.github/instructions/*`, `dial_conf/...`) |

## Resolved unknowns summary

| Unknown | Resolution |
|---|---|
| Poetry vs uv in `ai-dial-ci` | uv supported (≥ 4.8.0) via `python-package-manager: uv` |
| Required repo files for CI | `Makefile` (`lint`, `test`), `pyproject.toml` `name`/`version`, `uv.lock`, `Dockerfile` |
| Version source | git tags + branch name; pyproject write-only |
| Pin format | full SHA `00484ead…` with `# 4.13.2` comment |
| Integration tests in CI | not run (no DB service); self-skip |
| Image registries | Docker Hub `epam/ai-dial-scheduler` + `ghcr.io/epam/ai-dial-scheduler` |
| Deploy | matrix `deploy-development.yml`, 2 GitLab projects |
| Open | Q3 owner handle; authors value (R15); ORT outcome (R12) |
