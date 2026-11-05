# DevOps Checklist: `epam/ai-dial-scheduler`

Track C of [spec.md](./spec.md), in execution order. It follows the *New GitHub Project Creation Guide*. IDs
(`OPS-0xx`) match the spec, and `Txxx` match [tasks.md](./tasks.md).

The repository content (workflows, `Makefile`, `LICENSE`, templates, `dependabot.yml`, `trivy.yaml`) is prepared by
the development team. The repository already contains the project history; the open-source changes arrive as a pull request, and the tag
`0.3.1` is pushed on the 0.3.1 release commit.

| | |
|---|---|
| Repository | `epam/ai-dial-scheduler` (public) |
| Default branch | `development` |
| Release branches | `release-X.Y` (first one: `release-0.4`) |
| Images | Docker Hub `epam/ai-dial-scheduler`, GHCR `ghcr.io/epam/ai-dial-scheduler` |
| CI | `epam/ai-dial-ci` 4.13.2 (SHA-pinned), `python_docker_pr.yml` / `python_docker_release.yml`, uv, Python 3.14 |

---

## Stage 1 — Before publication (can start now)

Required before the open-source pull request can go green and be merged.

### Repository

- [x] **OPS-001** Repository `epam/ai-dial-scheduler` exists (currently **private**; branch protection requires making it public or GitHub Pro)
- [ ] **OPS-002** About (gear icon, right column):
  - Description: `Scheduling service for AI DIAL: runs chat completion, responses, REST and SSE targets on cron and date triggers`
  - Website: `https://dialx.ai`
  - Topics: `llm`, `ai-dial` (mandatory); optionally `scheduler`, `python`
- [ ] **OPS-003** Settings → General → Features: uncheck **Wikis** and **Discussions**
- [ ] **OPS-004** Settings → General → Pull Requests:
  - [ ] uncheck *Allow merge commits*
  - [ ] check *Allow squash merging*, default commit message: **Pull request title**
  - [ ] uncheck *Allow rebase merging*
  - [ ] check *Allow auto-merge*
  - [ ] check *Automatically delete head branches*
- [ ] **OPS-005** Settings → General: check *Always suggest updating pull request branches*

### Access

- [ ] **OPS-006** Settings → Collaborators and teams → Add teams:
  - `epam/dial-admins` → **Admin**
  - `epam/dial-contributors` → **Write**
- [ ] **OPS-007** ⏸ *Waiting for the dev team (owner's GitHub handle not yet known)*. Add the responsible owner's personal account → **Admin**

### Actions

- [ ] **OPS-010** Settings → Actions → General:
  - [ ] Actions permissions: check *Require actions to be pinned to a full-length commit SHA* → Save
  - [ ] Fork PR workflows: *Require approval for all external contributors* → Save
  - [ ] Workflow permissions: *Read and write permissions*, check *Allow GitHub Actions to create and approve pull requests* → Save

### Secrets and registries

- [ ] **OPS-011** Settings → Secrets and variables → Actions → New repository secret:
  - [ ] `ACTIONS_BOT_TOKEN`: PAT of the `ai-dial-actions` bot, scopes `repo` and `write:packages`. Used for release push and tags, GHCR login, PR-title check and Dependabot automation
  - [ ] `DOCKERHUB_LOGIN`
  - [ ] `DOCKERHUB_TOKEN`
  - Not needed: `PYPI_TOKEN`, `NPM_TOKEN`
- [ ] **OPS-012** Docker Hub: the repository `epam/ai-dial-scheduler` exists, and the `DOCKERHUB_LOGIN` account can push to it
- [ ] **OPS-015** Labels → New label: name `dependencies`, description `Dependencies update`, color `#FFFFFF`

### Hand-off to the dev team

- [ ] **OPS-019** Send the GitLab **project IDs** of the two deploy projects (API and worker) that currently receive the trigger from the internal pipeline. They are needed for `deploy-development.yml` (matrix with two entries).
- [ ] Confirm when Stage 1 is done, so the dev team can re-run the checks on the open-source pull request.

---

## Stage 2 — After the first pull request

The dev team opens a test PR right after publication. Required checks can only be selected once they have run at
least once.

- [ ] **OPS-008** Settings → Branches → Add classic branch protection rule, pattern **`development`**:
  - [ ] Require a pull request before merging → Require approvals; Dismiss stale pull request approvals when new commits are pushed
  - [ ] Require status checks to pass before merging → Require branches to be up to date before merging; add:
    - `run_tests / docker_build`
    - `run_tests / test / style_checks`
    - `run_tests / test / code_checks`
    - `run_tests / test / ort`
    - the PR-title check (pick its name from the test PR's checks)
  - [ ] Require conversation resolution before merging
  - [ ] Require linear history
  - [ ] Restrict who can push to matching branches → Restrict pushes that create matching branches → add `epam/dial-contributors`
- [ ] **OPS-009** Add classic branch protection rule, pattern **`release-*`**:
  - [ ] Require a pull request before merging → Require approvals (no *dismiss stale*)
  - [ ] Require status checks (up to date): the same four `run_tests / …` checks
  - [ ] Require conversation resolution before merging
  - [ ] Restrict who can push → Restrict pushes that create matching branches → `epam/dial-contributors`
  - (no *linear history* requirement)
- [ ] **OPS-014** Settings → Advanced Security:
  - [ ] Private vulnerability reporting → **Enable** (`SECURITY.md` points to it)
  - [ ] CodeQL analysis → Set up → **Default** → Enable
  - [ ] Dependabot alerts → Enable
  - [ ] Dependabot security updates → Enable
  - [ ] Dependabot version updates → Enable (optional; `.github/dependabot.yml` is already in the repo)
- [ ] **ORT decision, if needed.** If `run_tests / test / ort` reports licence violations on the test PR (a likely candidate is `psycopg2`, LGPL), decide together with the dev team between:
  - a curation in `epam/ai-dial-ort-config`;
  - an `.ort.yml` exclusion in the repo;
  - a one-time `ort-bypassed` label for the bootstrap PR.

---

## Stage 3 — After the first `development` image

Merging the test PR publishes `epam/ai-dial-scheduler:development` (version `0.4.0-dev.N`) to Docker Hub and GHCR.

- [ ] **OPS-016** GHCR package `ai-dial-scheduler` → Package settings:
  - [ ] Manage Actions access → add the source repository with role **Admin** (required by `cleanup-untagged-images.yml`)
  - [ ] Inherited access → check *Inherit access from source repository (recommended)*
- [ ] **OPS-013** Settings → Environments → New environment **`development`**:
  - [ ] Deployment branches and tags → *Selected branches and tags* → rule: Ref type `Branch`, pattern `development`
  - [ ] Environment secrets:
    - `DEPLOY_ACCESS_TOKEN`: GitLab project access token, role `Guest`, scopes `read_api`, `read_repository`
    - `DEPLOY_TRIGGER_TOKEN`: GitLab pipeline trigger token
    - `DEPLOY_HOST`: the GitLab host
- [ ] **OPS-017** In the two GitLab deploy projects (API and worker), switch the image source from the internal registry to the public `epam/ai-dial-scheduler` image (Docker Hub or GHCR)
- [ ] Together with the dev team: after `deploy-development.yml` is merged, confirm that a new `development` image starts **both** GitLab deploy pipelines

---

## Stage 4 — Switch-over

- [ ] **OPS-018** Once GitHub → GitLab deploy is confirmed, archive the internal GitLab repository and make it read-only. It keeps the full history and the old `.gitlab-ci.yml`, which goes on deploying until this point, so there is no deploy gap.

---

## Release flow reference

| Action | Result |
|---|---|
| Merge to `development` | version `0.4.0-dev.N`, image tag `development` |
| Create / push `release-0.4` | `0.4.0-rc.0`, `-rc.1`, …: git tag, GitHub prerelease, image `0.4.0-rc.N` |
| *Release Workflow* → Run workflow on `release-0.4` with **promote** | `0.4.0`: tag, GitHub release, images `0.4.0` and `latest` |
| Further pushes to `release-0.4` | `0.4.1`, `0.4.2`, … |

The version is calculated from git tags and the branch name. `pyproject.toml` holds `0.0.0`, and CI overwrites it.

## Open items blocking DevOps

| Item | Blocks | Owner |
|---|---|---|
| Responsible owner's GitHub handle | OPS-007 (and `CODEOWNERS`) | Dev team lead |
| First test PR | OPS-008, OPS-009 | Dev team (after publication) |
| First `development` image | OPS-016 | automatic after first merge |
