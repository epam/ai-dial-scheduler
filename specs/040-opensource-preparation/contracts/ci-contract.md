# Contract: Repository ↔ `epam/ai-dial-ci` 4.13.2

The reusable workflows treat this repository as a black box with the interface below. Breaking any item breaks CI
or releases.

## 1. Files the workflows require

| File | Used by | Requirement |
|---|---|---|
| `Makefile` | `test / style_checks`, `test / code_checks` | Targets `lint` and `test` exit `0` on a clean checkout with only uv + Python 3.14 installed |
| `pyproject.toml` | release job "Set version" | Exactly one line matching `^version = `; value `"0.0.0"`, never edited by hand |
| `uv.lock` | `make` targets, `test / ort` (`uv export --frozen --all-extras`) | Committed; in sync with `pyproject.toml` on `development` (`uv lock --check` passes). On `release-*` it may lag by the root `version` line, because CI commits only `pyproject.toml` — harmless, all CI uses are `--frozen` |
| `Dockerfile` | `docker_build`, release | Builds from repo root with no build args; no `FROM --platform` |
| `trivy.yaml` | `docker_build` Trivy step | Mirror configuration from the `ai-dial-ci` template |

## 2. Make targets

```makefile
.PHONY: install lint test

install:
	uv sync --all-extras

lint: install
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run mypy src

test: install
	uv run pytest
```

- `test` must not require a database, Docker, network services or secrets. Integration tests skip without
  `TEST_DB_URL`.
- `uv sync` installs the default `dev` dependency group, which contains ruff, mypy and pytest.
- Recipes use tab indentation.

## 3. Caller workflows

All `epam/ai-dial-ci` references: `@00484ead5dc3904a787c5af8d0bdd8f4fdf087c1 # 4.13.2`.

| File | Trigger | Calls | Inputs |
|---|---|---|---|
| `.github/workflows/pr.yml` | `pull_request` → `development`, `release-*` | `python_docker_pr.yml` (job id **`run_tests`**) | `python-package-manager: uv`, `python-version: "3.14"`; `secrets: inherit` |
| `.github/workflows/release.yml` | `push` → `development`, `release-*`; `workflow_dispatch` (`promote`) | `python_docker_release.yml` (job id `release`) | same + `promote`; `secrets: inherit` |
| `.github/workflows/pr-title-check.yml` | `pull_request_target` opened/edited/reopened | `pr-title-check.yml` | secret `ACTIONS_BOT_TOKEN` |
| `.github/workflows/deploy-development.yml` | `workflow_dispatch`, `registry_package`, `workflow_run` of "Release Workflow" | `deploy-development.yml`, matrix ×2 | `gitlab-project-id`, `environment-name`; secrets `DEPLOY_*` |
| `.github/workflows/cleanup-untagged-images.yml` | daily cron | `dataaxiom/ghcr-cleanup-action` (SHA-pinned in template) | — |
| `.github/workflows/dependabot-automation.yml` | `pull_request_target` | `dependabot/fetch-metadata` (SHA-pinned in template) | secret `ACTIONS_BOT_TOKEN` |

The release workflow's `name:` must stay exactly **`Release Workflow`** — `deploy-development.yml` listens to it by name.

## 4. Status checks produced

| Check | Runs |
|---|---|
| `run_tests / test / style_checks` | `make lint` |
| `run_tests / test / code_checks` | `make test` |
| `run_tests / test / ort` | ORT analyse/evaluate on exported `requirements.txt` |
| `run_tests / docker_build` | `docker buildx build` (tag `test`, not pushed) + Trivy (CRITICAL,HIGH) |
| PR-title check | Conventional Commits title validation |

## 5. Versions and artefacts

| Ref | Version | Image tags (Docker Hub `epam/ai-dial-scheduler`, `ghcr.io/epam/ai-dial-scheduler`) | Git side effects |
|---|---|---|---|
| `development` (after tag `0.3.1`) | `0.4.0-dev.N` | `development` | none |
| `release-0.4` push | `0.4.0-rc.N` (N from 0) | `0.4.0-rc.N` | tag, prerelease, `[skip ci] Update version` commit |
| `release-0.4` promote | `0.4.0` | `0.4.0`, `latest` | tag, release |
| `release-0.4` later push | `0.4.1`, … | `0.4.x`, `latest` if newest | tag, release |

## 6. Secrets and variables consumed

| Name | Scope | Consumer |
|---|---|---|
| `ACTIONS_BOT_TOKEN` | repo secret | release (checkout/push, GHCR login), PR-title check, Dependabot automation |
| `DOCKERHUB_LOGIN`, `DOCKERHUB_TOKEN` | repo secrets | release (Docker Hub push) |
| `DEPLOY_HOST` | repo or env secret | deploy-development |
| `DEPLOY_ACCESS_TOKEN`, `DEPLOY_TRIGGER_TOKEN` | env `development` secrets | deploy-development |
| `ACTIONS_BOT_NAME`, `ACTIONS_BOT_EMAIL`, `ORT_CONFIG_VCS_URL`, `ORT_CONFIG_VCS_REVISION` | repo variables (optional) | release, ORT |
