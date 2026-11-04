# Implementation Plan: Open-Source Preparation

**Branch**: `040-opensource-preparation` | **Date**: 2026-11-03 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/040-opensource-preparation/spec.md`

## Summary

Make the repository publishable as `epam/ai-dial-scheduler` and runnable on the standard DIAL GitHub tooling, without
changing application behaviour. The work is repository content only (Track B); GitHub settings (Track C) are a
DevOps checklist this plan sequences against.

Three ideas carry the design:

1. **The `Makefile` is the CI contract.** `epam/ai-dial-ci` only ever runs `make lint` and `make test`, so the
   existing quality gates (ruff, ruff format, mypy, pytest) move behind those two targets and CI on GitHub runs
   exactly what developers run locally.
2. **Versions come from tags, not files.** `pyproject.toml` becomes `0.0.0` and CI-owned; a plain `0.3.1` tag on the
   first public commit keeps the version line continuous (`0.4.0-dev.N`, first release `0.4.0`).
3. **Clean first, then publish once.** All clean-up lands on the internal repo; the public repository is created from
   that tree as a single orphan commit, with `.gitlab-ci.yml` excluded. The internal pipeline keeps deploying until
   the GitHub → GitLab deploy is proven, so there is no deploy gap.

## Technical Context

**Language/Version**: Python 3.14 (unchanged)

**Primary Dependencies**: no changes to runtime or dev dependencies. New external dependency: `epam/ai-dial-ci`
reusable workflows @ `00484ead5dc3904a787c5af8d0bdd8f4fdf087c1` (tag 4.13.2).

**Storage**: N/A — no schema change, no migration.

**Testing**: existing pytest suite via `make test` (unit only in CI); CI/config verified by local parity checks and
an `actionlint` pass (see quickstart.md), then by the first real PR/release runs.

**Target Platform**: GitHub Actions (`ubuntu-24.04`), Docker Hub + GHCR, GitLab deploy projects (unchanged).

**Project Type**: Repository/infra change on a web service (API + worker).

**Constraints**:
- No application code changes: `src/`, `tests/` and `migrations/` are not modified.
- No secrets in the tree; `.env` files are never read.
- Nothing is pushed to any remote and nothing is committed without explicit approval; publication (R7) is a
  separately approved step.
- `ai-dial-ci` references SHA-pinned (OPS-010 policy).
- Exactly one `^version = ` line in `pyproject.toml`.

**Scale/Scope**: ~15 new files (mostly `.github/`), ~12 edited files, 2 deletions/moves. No code paths change.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Reliability First | ✅ | No runtime change. Deploy continuity preserved by keeping the internal pipeline until GitHub deploy is verified (research R8). |
| II. Observability | ✅ N/A | No logging/metrics change. |
| III. Test-First | ✅ with justification | No production code is written, so there is nothing to TDD. The equivalent gate is "CI parity first": `make lint`/`make test` must pass locally before the workflows that call them are added, and the existing 1040 tests must stay green. |
| IV. Contract-Driven API | ✅ | No API change. The repository ↔ CI interface is written down as a contract (`contracts/ci-contract.md`). |
| V. Simplicity & YAGNI | ✅ | Reuse `ai-dial-ci` instead of custom workflows; no integration-test DB in CI, no PyPI, no Helm, no review envs (spec Out of Scope). |

Post-design re-check: ✅ no violations; Complexity Tracking empty.

## Project Structure

### Documentation (this feature)

```text
specs/040-opensource-preparation/
├── spec.md               # requirements, tracks A/B/C, clarifications
├── plan.md               # this file
├── research.md           # R1–R15 decisions with ai-dial-ci evidence
├── contracts/
│   └── ci-contract.md    # repo ↔ ai-dial-ci interface: files, make targets, workflows, checks, versions, secrets
├── quickstart.md         # validation steps before and after publication
├── devops-checklist.md   # Track C as a step-by-step checklist for DevOps, by stage
└── tasks.md              # next step (/speckit-tasks)
```

No `data-model.md`: the feature has no data entities.

### Repository changes

```text
LICENSE                                   NEW   Apache-2.0                                  DEV-FR-001
Makefile                                  NEW   install / lint / test                       DEV-FR-010
trivy.yaml                                NEW   ai-dial-ci mirror template                  DEV-FR-013
SECURITY.md                               EDIT  advisory link → epam/ai-dial-scheduler      DEV-FR-002
pyproject.toml                            EDIT  version 0.0.0, license, authors,            DEV-FR-001/012/016
                                                description, [project.urls]
Dockerfile                                EDIT  drop FROM --platform; reword HELM comment   DEV-FR-014/017
README.md                                 EDIT  Quality Gates → make; Release section;      DEV-FR-010/012/018
                                                Postman path
CHANGELOG.md                              EDIT  note: later notes in GitHub Releases        DEV-FR-012
CLAUDE.md                                 EDIT  real structure; generic active plan;        DEV-FR-019
                                                make commands
.gitignore                                EDIT  drop stale internal entries                 DEV-FR-017
dial/.env.template                        EDIT  NEXTAUTH_SECRET placeholder                 DEV-FR-017
dial/docker-compose.yml                   EDIT  pin image tags where released               DEV-FR-017
specs/025-…/research.md                   EDIT  redact internal CI refs                     DEV-FR-017
specs/028-…/code-review-report.md         EDIT  redact personal file refs                   DEV-FR-017
specs/035-…/code-review-report-round2.md  EDIT  redact internal CI refs                     DEV-FR-017
log.json                                  DEL                                               DEV-FR-018
DIAL Scheduler.postman_collection.json    MOVE  → documentation/postman/                    DEV-FR-018
                                                dial-scheduler.postman_collection.json
.github/
├── CODEOWNERS                            NEW   blocked by Q3                               DEV-FR-003
├── pull_request_template.md              NEW                                               DEV-FR-004
├── dependabot.yml                        NEW   uv, github-actions                          DEV-FR-011
├── ISSUE_TEMPLATE/
│   ├── config.yml                        NEW                                               DEV-FR-005
│   ├── 01_bug_report.yml                 NEW                                               DEV-FR-005
│   └── 02_feature_request.yml            NEW                                               DEV-FR-005
└── workflows/
    ├── pr.yml                            NEW   job id run_tests                            DEV-FR-006
    ├── release.yml                       NEW   name "Release Workflow"                     DEV-FR-007
    ├── pr-title-check.yml                NEW                                               DEV-FR-008
    ├── cleanup-untagged-images.yml       NEW                                               DEV-FR-009
    ├── deploy-development.yml            NEW   matrix api/worker; ids from OPS-019         DEV-FR-009a
    └── dependabot-automation.yml         NEW                                               DEV-FR-009b
.gitlab-ci.yml                            KEEP  internal only; excluded at publication      DEV-FR-015
```

`src/`, `tests/`, `migrations/` are not modified.

## Implementation Phases

### Phase 1 — CI parity (local, blocking everything in Phase 3)

`Makefile` (contract §2), `trivy.yaml`, `Dockerfile` platform removal, `pyproject.toml` version → `0.0.0`.
Gate: `make lint`, `make test`, `docker build`, the `sed` single-line check (quickstart §1).

### Phase 2 — Legal and community files (independent, parallel with Phase 1)

`LICENSE`, `pyproject.toml` `license`, `SECURITY.md` link, PR template, issue templates. `CODEOWNERS` deferred (Q3).

### Phase 3 — GitHub workflows and Dependabot (after Phase 1)

`pr.yml`, `release.yml`, `pr-title-check.yml`, `cleanup-untagged-images.yml`, `dependabot-automation.yml`,
`dependabot.yml`. `deploy-development.yml` is written with both matrix entries and **placeholder-free** only once
OPS-019 delivers the two GitLab project ids; until then the task stays open (no file with fake ids).
Gate: `actionlint`; every `epam/ai-dial-ci` ref is the 4.13.2 SHA; release `name:` is `Release Workflow`.

### Phase 4 — Clean-up of internal content (parallel with Phase 3)

Metadata (`authors` pending confirmation, `description`, `[project.urls]`), three spec redactions, Dockerfile
comment, `dial/.env.template`, `dial/docker-compose.yml` tags, `log.json`, Postman move, `.gitignore`.
Manual step: human review of `documentation/deployment/*.png`.
Gate: internal-content scan (quickstart §2).

### Phase 5 — Documentation (after Phases 1–4)

README "Quality Gates" → `make lint` / `make test` (coverage command kept as an optional local command); new
"Releases" section (branching, `release-X.Y`, promote, no manual version edits); Postman path; CHANGELOG note;
CLAUDE.md structure/commands/active plan.

### Phase 6 — Review and merge on the internal repo

Full quickstart §1–§4 (including the orphan **dry run**), `code-review-agent` pass, commit(s) and MR to `development`
— **only on explicit approval**.

### Phase 7 — Publication (separately approved; depends on OPS-001 … OPS-006, OPS-010, OPS-011)

Orphan commit, tag `0.3.1`, push to `github.com/epam/ai-dial-scheduler` (research R7).

### Phase 8 — Post-publication (with DevOps)

First PR → OPS-008/009 required checks; first `development` release → OPS-016 (GHCR access), SC-007 version check;
OPS-019 ids → finish `deploy-development.yml` → OPS-013/017 → verify both GitLab deploys → OPS-018 archive internal
repo. Q3 answered → `CODEOWNERS` + OPS-007. ORT findings, if any → follow-up PR (research R12).

## Key Design Decisions (see research.md for rationale)

| Decision | Research |
|---|---|
| `python_docker_pr/release.yml` with `uv` + `3.14` | R1, R2 |
| `Makefile` `lint` includes mypy; ruff scoped to `src tests` | R3 |
| SHA-pin `ai-dial-ci` with `# 4.13.2` comment | R5 |
| `version = "0.0.0"` + tag `0.3.1`; first release `release-0.4` | R6 |
| Orphan-commit publication; `.gitlab-ci.yml` excluded, kept internally | R7, R8 |
| Matrix deploy for API and worker | R9 |
| Dependabot: `uv` security-only, `github-actions` grouped; no `docker` | R10 |
| Drop `FROM --platform` | R11 |
| No pre-emptive `.ort.yml` | R12 |

## Complexity Tracking

None.

## Open Items

| Item | Blocks | Owner |
|---|---|---|
| Q3 — responsible owner GitHub handle | DEV-FR-003, OPS-007 | Team lead |
| `pyproject.toml` `authors` value | part of DEV-FR-016 (fallback: drop field) | Team |
| GitLab project ids for API and worker (OPS-019) | DEV-FR-009a | DevOps |
| Screenshot review `documentation/deployment/*.png` | Phase 4 gate | Developer (manual) |
| ORT result on first run | SC-002 | Dev + DevOps |
| Q4b (artifacts) is provisional | nothing now; later removal is one PR | Team |
