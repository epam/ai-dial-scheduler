# DevOps Checklist: `epam/ai-dial-scheduler`

Track C of [spec.md](./spec.md). It follows the *New GitHub Project Creation Guide*. IDs (`OPS-0xx`) match the spec,
and `Txxx` match [tasks.md](./tasks.md).

**Status as of 2026-11-05.** The open-source changes are merged into `development` (PR #18), and the tag `0.3.1` is on
the 0.3.1 release commit. The first release build computed `0.4.0-dev.7` and stopped at image publishing because no
secrets are set yet. The dev team, which has repository Admin, has done every setting it could; each item below says
who owns it.

Legend: ✅ done · 🔑 **DevOps** · 🌐 blocked until the repository is **public** (GitHub Free does not offer these on
private repos) · 👥 dev team

| | |
|---|---|
| Repository | `epam/ai-dial-scheduler` (currently **private**) |
| Default branch | `development` |
| Release branches | `release-X.Y` (first one: `release-0.4`) |
| Images | Docker Hub `epam/ai-dial-scheduler`, GHCR `ghcr.io/epam/ai-dial-scheduler` |
| CI | `epam/ai-dial-ci` 4.13.2 (SHA-pinned), `python_docker_pr.yml` / `python_docker_release.yml`, uv, Python 3.14 |

---

## Still needed from DevOps

1. 🔑 **OPS-011 — repository secrets** (Settings → Secrets and variables → Actions):
   - [ ] `ACTIONS_BOT_TOKEN`: PAT of the `ai-dial-actions` bot, scopes `repo` and `write:packages`. Used for release push and tags, GHCR login, the PR-title check and Dependabot automation.
   - [ ] `DOCKERHUB_LOGIN`
   - [ ] `DOCKERHUB_TOKEN`
2. 🔑 **OPS-012 — Docker Hub:** [ ] repository `epam/ai-dial-scheduler` exists and the `DOCKERHUB_LOGIN` account can push to it.
3. 🔑 **OPS-006 (part):** [ ] add team `epam/dial-admins` with role **Admin**. The team is not visible to the dev team (the API returns 404 and asks for `admin:org`).
4. 🔑 **Repository visibility:** [ ] make the repository **public**, or name who decides and when. Note: the full git history becomes public with it. This unblocks every 🌐 item below.
5. 🔑 **OPS-019 — hand-off:** [ ] send the GitLab **project IDs** of the two deploy projects (API and worker). The dev team needs them for `deploy-development.yml` (matrix with two entries).
6. 🔑 **OPS-013 — environment `development`** (Settings → Environments):
   - [ ] Deployment branches and tags → *Selected branches and tags* → Ref type `Branch`, pattern `development`
   - [ ] Environment secrets: `DEPLOY_ACCESS_TOKEN` (GitLab project access token, role `Guest`, scopes `read_api`, `read_repository`), `DEPLOY_TRIGGER_TOKEN` (pipeline trigger token), `DEPLOY_HOST` (GitLab host)
7. 🔑 **OPS-017:** [ ] switch the two GitLab deploy projects (API and worker) to the public `epam/ai-dial-scheduler` image.
8. 🔑 **OPS-016 — GHCR package** (after the first image is published):
   - [ ] Manage Actions access → add the source repository with role **Admin** (required by `cleanup-untagged-images.yml`)
   - [ ] Inherited access → check *Inherit access from source repository*
9. 🔑 **OPS-010 — SHA pinning:** [ ] the guide's *Require actions to be pinned to a full-length commit SHA* is incompatible with `ai-dial-ci` 4.13.2 (see Actions below). How is this handled in other DIAL repositories?
10. 🔑 **OPS-018:** [ ] once GitHub → GitLab deploy is confirmed, archive the internal GitLab repository. Until then its `.gitlab-ci.yml` keeps deploying, so there is no deploy gap.

---

## Full status by guide step

### Repository settings

- [x] ✅ **OPS-001** Repository exists (private).
- [x] ✅ **OPS-002** About: description, website `https://dialx.ai`, topics `llm`, `ai-dial`. *(dev team, 2026-11-05)*
- [x] ✅ **OPS-003** Wikis and Discussions are disabled.
- [ ] **OPS-004** Pull Requests:
  - [x] ✅ merge commits off, squash on (default message: **Pull request title**), rebase off
  - [x] ✅ automatically delete head branches
  - [ ] 🌐 allow auto-merge. The API accepts the setting but GitHub keeps it off on a private Free repository. Turn it on after going public. *(dev team)*
- [x] ✅ **OPS-005** Always suggest updating pull request branches.

### Access

- [ ] **OPS-006** Teams:
  - [x] ✅ `epam/dial-contributors` → **Write** *(dev team)*
  - [ ] 🔑 `epam/dial-admins` → **Admin** (see "Still needed" #3)
- [x] ✅ **OPS-007** Owner accounts with Admin: `kyrylosiusko`, `ihorlahutin-epam`.
- [ ] 👥 **CODEOWNERS** (guide step 21): branch `chore/codeowners` is ready (`* @kyrylosiusko @ihorlahutin-epam`, `/.github/ @nepalevov @alexey-ban`, all four have write access) together with this checklist. Its PR still has to be opened.

### Actions

- [ ] **OPS-010** Settings → Actions → General:
  - [ ] 🔑 Require actions to be pinned to a full-length commit SHA. **Turned off again: it breaks CI.** `ai-dial-ci` 4.13.2 calls its own actions by tag (`epam/ai-dial-ci/actions/build_docker@4.12.0`, `python_prepare@4.12.0`), and GitHub rejects them with *"all actions must be pinned to a full-length commit SHA"*. Re-enable once `ai-dial-ci` pins internally, or follow the approach other DIAL repositories use.
  - [x] ✅ Workflow permissions: read and write; Actions may create and approve pull requests
  - [ ] 🌐 Fork PR workflows: require approval for all external contributors. Not available on private repositories. *(dev team after going public)*

### Secrets, registries, labels

- [ ] 🔑 **OPS-011** Secrets (see "Still needed" #1)
- [ ] 🔑 **OPS-012** Docker Hub (see "Still needed" #2)
- [x] ✅ **OPS-015** Label `dependencies`: description `Dependencies update`, color `#FFFFFF` *(dev team)*

### Protection and security

The required checks have already run on PR #18, so they can be selected.

- [ ] 🌐 **OPS-008** Branch protection for **`development`** *(dev team after going public)*:
  - Require a pull request → approvals; dismiss stale approvals on new commits
  - Require status checks, branch up to date:
    - `run_tests / docker_build`
    - `run_tests / test / style_checks`
    - `run_tests / test / code_checks`
    - `run_tests / test / ort`
    - the PR-title check (it runs from `development` now that PR #18 is merged)
  - Require conversation resolution
  - Require linear history
  - Restrict pushes and branch creation to `epam/dial-contributors`
- [ ] 🌐 **OPS-009** Branch protection for **`release-*`**: same as OPS-008, without *dismiss stale* and *linear history*. *(dev team after going public)*
- [ ] **OPS-014** Advanced Security:
  - [ ] 🌐 Private vulnerability reporting. The API returns 404 on a private repository. *(dev team after going public)*
  - [ ] 🌐 CodeQL default setup. On a private repository it needs Advanced Security. *(dev team after going public)*
  - [x] ✅ Dependabot alerts and security updates are enabled; `.github/dependabot.yml` is in the repository. Dependabot Automation fails on the private repo (`permissions: {}` → 403 when reading PR commits); it works once the repo is public, and it needs `ACTIONS_BOT_TOKEN`
- [x] ✅ **ORT:** licence findings (`psycopg2`, `psycopg2-binary`, `scramp`) are resolved in `.ort.yml`, and ORT is green.

### Deploy and switch-over

- [ ] 🔑 **OPS-019** GitLab project IDs → 👥 then the dev team adds `deploy-development.yml`
- [ ] 🔑 **OPS-013** Environment `development` with `DEPLOY_*` secrets
- [ ] 🔑 **OPS-017** Repoint the GitLab deploy projects to the public image
- [ ] 🔑 **OPS-016** GHCR package access (after the first image)
- [ ] 👥 + 🔑 Confirm that a new `development` image starts **both** GitLab deploy pipelines
- [ ] 🔑 **OPS-018** Archive the internal GitLab repository

---

## Release flow reference

| Action | Result |
|---|---|
| Merge to `development` | version `0.4.0-dev.N`, image tag `development` (verified: `0.4.0-dev.7`) |
| Create / push `release-0.4` | `0.4.0-rc.0`, `-rc.1`, …: git tag, GitHub prerelease, image `0.4.0-rc.N` |
| *Release Workflow* → Run workflow on `release-0.4` with **promote** | `0.4.0`: tag, GitHub release, images `0.4.0` and `latest` |
| Further pushes to `release-0.4` | `0.4.1`, `0.4.2`, … |

The version is calculated from git tags and the branch name. `pyproject.toml` holds `0.0.0`, and CI overwrites it.
