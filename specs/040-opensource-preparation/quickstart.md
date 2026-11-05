# Quickstart: Validating Open-Source Preparation

## Prerequisites

- `uv` installed; Python 3.14 available to uv.
- `make` (macOS: Xcode CLT; Linux: build-essential).
- Docker (for the image build check).
- Optional: [`actionlint`](https://github.com/rhysd/actionlint) for workflow syntax.

## 1. Local CI parity (before any push)

```bash
make lint          # ruff check, ruff format --check, mypy — must exit 0
make test          # unit tests; integration tests report "skipped" without TEST_DB_URL
uv lock --check    # uv.lock in sync with pyproject.toml
docker build -t ai-dial-scheduler:local .
actionlint .github/workflows/*.yml
```

Simulate the CI version rewrite and confirm exactly one line changes:

```bash
cp pyproject.toml /tmp/pyproject.check
sed -i.bak 's/^version = .*/version = "0.4.0.dev1"/g' /tmp/pyproject.check
diff /tmp/pyproject.check.bak /tmp/pyproject.check   # exactly one changed line
```

## 2. Internal-content scan (on the final tree)

```bash
git grep -n -i -E 'deltix|gitlab|dial\.parts|ihor|kyrylo|siusko|Your Name|scheduler\.internal' -- ':!uv.lock'
```

Expected hits only:

- `dial/settings/gflog.xml` — upstream ai-dial-core logging classes (`com.epam.deltix.gflog…`), intentional;
- `src/lib/constants.py` / tests — `http://scheduler.internal`, a non-routable placeholder host for in-process MCP
  calls, intentional;
- `specs/040-opensource-preparation/*` — only the search-pattern words themselves (e.g. this command); no hosts.

`.gitlab-ci.yml` still exists on the internal repo; it must be absent from the orphan commit (step 4).

Also verify manually: `documentation/deployment/*.png` show no internal hosts, project ids or personal names.

## 3. Community files

```bash
ls LICENSE SECURITY.md CONTRIBUTING.md README.md \
   .github/pull_request_template.md .github/ISSUE_TEMPLATE/{config.yml,01_bug_report.yml,02_feature_request.yml} \
   .github/dependabot.yml trivy.yaml Makefile
grep -c 'epam/ai-dial-scheduler/security/advisories/new' SECURITY.md   # 1
```

`.github/CODEOWNERS` is expected only once Q3 is answered.

## 4. Publication dry run (local, no push)

```bash
git checkout --orphan public-initial-dryrun
git rm --cached --quiet .gitlab-ci.yml
git status --short | grep -v '^A ' || true    # expected: only "?? .gitlab-ci.yml" (.env, .idea, .coverage are ignored)
git commit -m "feat: initial open-source release"
git ls-files | grep -c gitlab-ci              # 0
git log --oneline | wc -l                     # 1
git switch development && git branch -D public-initial-dryrun
```

The real publication (push to `github.com/epam/ai-dial-scheduler` + tag `0.3.1`) is a separate, explicitly approved
step — see `research.md` R7.

## 5. After publication (with DevOps)

| Check | Expected |
|---|---|
| Open a test PR to `development` | 4 `run_tests / …` checks + PR-title check run and pass |
| PR titled `update stuff` | PR-title check fails |
| Merge to `development` | Release Workflow → `calculate_version` logs `0.4.0-dev.N`; images `epam/ai-dial-scheduler:development` on Docker Hub and GHCR |
| Same merge | Deploy development runs for both matrix entries; GitLab API and worker pipelines start |
| Create `release-0.4` | `0.4.0-rc.0` tag, prerelease, image `0.4.0-rc.0` |
| Run Release Workflow with `promote` on `release-0.4` | `0.4.0` tag + release, images `0.4.0` and `latest` |
| Repo → Insights → Community Standards | All items green |
| Repo → Security | CodeQL, Dependabot, private vulnerability reporting enabled |

## Rollback

- Before publication: nothing external exists; discard the branch.
- After publication but before archive: the internal GitLab pipeline still deploys (R8); a broken GitHub release can
  be ignored while fixing forward.
- A wrong first public commit cannot be "unpublished" reliably (forks/caches); this is why steps 1–4 gate the push.
