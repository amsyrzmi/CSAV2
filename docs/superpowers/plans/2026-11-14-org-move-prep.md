# Organisation move, prep pull requests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the workflows, the production resolve script and the rollout scripts independent of the repository's owner name, able to use a registry login, and cheaper to run, with no behaviour change while the values stay as they are today.

**Architecture:** One repository variable `IMAGE_NAMESPACE` (lowercase registry prefix) feeds CI, the publish workflow and the production resolve script. The resolve script and the rollout scripts gain an optional registry login that is used only when configured (absent means today's anonymous pull). Pull request CI stops uploading the review image bundle. A read-only script lists old image versions that are safe to delete; it deletes nothing.

**Tech Stack:** GitHub Actions YAML, bash (`deploy/production/resolve_commit.sh`), Python 3 (`scripts/`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-14-org-move-design.md` (sections 5.1, 6.1). Parent: `2026-11-13-mito-rename-and-org-move-design.md`.

## Global Constraints

- The repository is PUBLIC: synthetic data only (use `example-owner`, `ghcr.io/example-owner`), no real hostnames, account ids, tokens or addresses in code, tests or docs.
- Three pull requests, each on its own branch from `origin/main`, each merged by the owner after green CI: **PR 1** = Tasks 1 to 3 (workflows and the resolve script), **PR 2** = Task 4 (rollout scripts), **PR 3** = Tasks 5 and 6 (cost). Merge commits only; a hook blocks rebase and force-push.
- No behaviour change while `IMAGE_NAMESPACE` is `ghcr.io/amsyrzmi` and no registry auth file exists: same registry names, same anonymous pulls, same API paths.
- A registry login (token, password, auth file content) never appears on a command line, in a log line, in a test failure message or in the step summary.
- Registry namespace values are lowercase: `^ghcr\.io/[a-z0-9][a-z0-9-]*$`, no trailing slash, no scheme.
- Python: line length 100, `make lint` green (ruff, format, pyright, lint-imports). A PostToolUse formatter hook mass-reformats after the Edit tool: use Write or a small python replace script, check `git diff --stat`, run `.venv/bin/ruff format` on touched files.
- Run tests one file at a time through `bash /tmp/claude-1000/-home-amsyrzmi-CSAV2/7d09c8ff-ee80-4525-b21a-195503f98d9c/scratchpad/ci.sh /home/amsyrzmi/CSAV2/.venv/bin/python -m pytest <file> -q`; long commands in the foreground (timeout 600000).
- Commit trailers on every commit: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_012Xj1GSBskmizHEtGV54ZTA`.
- Never read anything under `/srv/csa-staging/secrets`. Do not run a staging rollout, touch production or change any GitHub or AWS setting; the owner does those.

## Review Focus

- `IMAGE_NAMESPACE` unset, uppercase, with a trailing slash or with `https://`: the resolve script and the publish workflow refuse with one clear line before any registry call; they never build an invalid registry name.
- The registry refuses the credentialed token request (token lacks access): the script retries anonymously once, and if that fails dies with the existing message; the token is never on argv or in output, with or without `set -x`.
- Auth file present but empty, unreadable or not valid JSON: the rollout stops before pulling and names the path, never the content; file absent: exactly today's anonymous behaviour (the existing empty-config-dir test stays green).
- Keep list: a version with several tags, an untagged version, a staged SHA that is older than the newest N, fewer versions than N, and more than 100 versions (paging): a version tagged with a staged SHA is never a candidate and the newest N are never candidates.
- Removing the pull request upload must not remove the main-run upload: the publish workflow downloads `csa-review-only-<sha>` from the main run.

---

### Task 1: One namespace variable for CI, publish and the resolve script (PR 1)

**Files:**
- Modify: `.github/workflows/ci.yml` (lines 32, 44, 130), `.github/workflows/publish-staging-images.yml` (line 43 and a new validation step), `.github/workflows/deploy-production.yml` (plan job `Resolve and check the commit` env, line 70), `deploy/production/resolve_commit.sh` (lines 40-43 and `registry_digest`), `deploy/PRODUCTION.md` (one short note on the variable)
- Create: `tests/test_image_namespace.py`
- Test: `tests/test_production_resolve.py`, `tests/test_production_deploy_assets.py`

**Interfaces:**
- Produces: repository variable `IMAGE_NAMESPACE` (the owner sets it to `ghcr.io/amsyrzmi` BEFORE this PR merges); `resolve_commit.sh` reads env `IMAGE_NAMESPACE` instead of deriving the owner from `REPOSITORY`; refusal message exactly `IMAGE_NAMESPACE must be set to ghcr.io/<lowercase owner>`.

- [ ] **Step 1: Write the failing tests.** In `tests/test_production_resolve.py` add `IMAGE_NAMESPACE` (`ghcr.io/example-owner`) to the harness default environment, change `test_registry_calls_use_the_owner_the_accept_types_and_keep_the_token_off_argv` to expect the namespace's owner (the assertions stay `example-owner`), and add `test_a_missing_or_malformed_namespace_is_refused_before_any_call` parametrized over: unset, empty, `ghcr.io/Example-Owner`, `ghcr.io/example-owner/`, `https://ghcr.io/example-owner`, `docker.io/example-owner`, each asserting non-zero exit, empty stdout, the exact message above on stderr and `harness.calls() == []`. In `tests/test_image_namespace.py` add `test_workflows_name_no_hardcoded_namespace` (no `ghcr.io/amsyrzmi` and no `repository_owner` registry derivation in the three workflow files), `test_ci_images_use_the_variable` (all three `ci.yml` database image references start with `${{ vars.IMAGE_NAMESPACE }}/csa-pg16-postgis-vector@sha256:`) and `test_publish_requires_a_lowercase_namespace` (the publish workflow has a step before the first `skopeo` call that matches the namespace against the regex in Global Constraints).
- [ ] **Step 2: Run each test file to see them fail** (`tests/test_production_resolve.py`, `tests/test_image_namespace.py`).
- [ ] **Step 3: Implement.** `resolve_commit.sh`: require `IMAGE_NAMESPACE` (validated with the Global Constraints regex via the existing `valid` helper) right after the `REPOSITORY` check; drop `owner=...`; `registry_digest` uses `local repo="${IMAGE_NAMESPACE#ghcr.io/}/csa-$app"`. `deploy-production.yml`: add `IMAGE_NAMESPACE: ${{ vars.IMAGE_NAMESPACE }}` to the `Resolve and check the commit` step env. `ci.yml`: the three image references become `${{ vars.IMAGE_NAMESPACE }}/csa-pg16-postgis-vector@sha256:<same digest>` (workflow-level `env` and both `services` blocks; keep the digest). `publish-staging-images.yml`: `REGISTRY_NAMESPACE: ${{ vars.IMAGE_NAMESPACE }}` plus a first step `Require a lowercase image namespace` that exits 1 with `IMAGE_NAMESPACE must be set to ghcr.io/<lowercase owner>` unless it matches the regex. `PRODUCTION.md`: one paragraph naming the variable and who sets it.
- [ ] **Step 4: Run the three test files and `tests/test_production_deploy_assets.py`; fix any assertion that pinned the old `REPOSITORY` derivation.** Expected: all pass.
- [ ] **Step 5: Commit** `chore(ci): one IMAGE_NAMESPACE variable for the image registry prefix`.

### Task 2: Gitleaks licence variable (PR 1)

**Files:**
- Modify: `.github/workflows/ci.yml` (the `Gitleaks` step, around line 106)
- Test: `tests/test_image_namespace.py`

**Interfaces:**
- Produces: the gitleaks step reads optional repository secret `GITLEAKS_LICENSE`.

- [ ] **Step 1: Write the failing test** `test_gitleaks_step_passes_the_optional_licence` in `tests/test_image_namespace.py`: the `gitleaks/gitleaks-action` step's `env` block contains `GITLEAKS_LICENSE: ${{ secrets.GITLEAKS_LICENSE }}` and still contains `GITHUB_TOKEN`.
- [ ] **Step 2: Run it to see it fail.**
- [ ] **Step 3: Add the env line to the step.**
- [ ] **Step 4: Run `tests/test_image_namespace.py`.** Expected: pass.
- [ ] **Step 5: Commit** `chore(ci): pass an optional gitleaks licence`.

### Task 3: Authenticated registry token with anonymous fallback (PR 1)

**Files:**
- Modify: `deploy/production/resolve_commit.sh` (`registry_digest`), `.github/workflows/deploy-production.yml` (plan job: `permissions` gains `packages: read`; step env gains `REGISTRY_USER: ${{ github.actor }}` and `REGISTRY_TOKEN: ${{ github.token }}`)
- Test: `tests/test_production_resolve.py`, `tests/test_production_deploy_assets.py`

**Interfaces:**
- Consumes: Task 1's `IMAGE_NAMESPACE`.
- Produces: `resolve_commit.sh` uses optional env `REGISTRY_USER` and `REGISTRY_TOKEN`; when both are set the token request carries Basic credentials through a curl config on stdin (never argv); when the credentialed request fails the script retries once with no credentials; both failing keeps the message `could not get a registry token for csa-<app>`.

- [ ] **Step 1: Write the failing tests** in `tests/test_production_resolve.py`: `test_credentials_reach_curl_on_stdin_never_argv` (set both vars to recognisable values, assert neither value appears in `harness.log` argv text and the stub saw a credentialed token request), `test_without_credentials_the_token_request_is_anonymous` (existing behaviour, assert no credential config was passed), `test_a_refused_credentialed_token_request_is_retried_anonymously` (stub fails the first token call, succeeds on the second; script passes), `test_both_token_requests_failing_dies_with_the_existing_message`, and `test_credentials_are_never_printed` (value of `REGISTRY_TOKEN` absent from stdout and stderr, also when a check fails). Extend the harness curl stub only as far as needed to record whether `-K -` stdin carried a `user =` line, never its value.
- [ ] **Step 2: Run `tests/test_production_resolve.py` to see the new tests fail.**
- [ ] **Step 3: Implement** in `registry_digest`: build the token request as a function `registry_token <repo>` that, when both vars are set, runs `printf 'user = "%s:%s"\n' ... | curl -sf --max-time 30 -K - "<token url>"`, and on failure falls through to the existing anonymous `curl`. Update the comment above the anonymous request. Add the permission and env lines to the workflow; the apply job is untouched.
- [ ] **Step 4: Run `tests/test_production_resolve.py` and `tests/test_production_deploy_assets.py`.** Expected: pass; then `make lint`.
- [ ] **Step 5: Commit** `feat(deploy): production resolve uses the workflow token for the registry, anonymous as fallback`.

### Task 4: Optional registry login and scope constants for the rollout scripts (PR 2)

**Files:**
- Create: `scripts/registry_auth.py`
- Modify: `scripts/rollout_targets.py` (`Target` dataclass, `STAGING`, `PRODUCTION` inherits), `scripts/staging_rollout.py` (line 494 path, `main` after `docker_config.chmod`), `scripts/staging_reset.py` (`main` after `docker_config.chmod`), `deploy/PRODUCTION.md` and `deploy/STAGING_PIPELINE.md` (one paragraph: the optional auth file and where it goes)
- Test: `tests/test_staging_rollout.py`, `tests/test_staging_reset.py`, `tests/test_rollout_targets.py`; create `tests/test_registry_auth.py`

**Interfaces:**
- Produces: `Target.package_scope: str` (replaces `package_owner`; value `"users/amsyrzmi"` for staging and production; the package listing path becomes `/{target.package_scope}/packages/container/csa-{app}/versions?per_page=100`); `Target.registry_auth: Path` (staging `/srv/csa-staging/registry/config.json`, production `/srv/csa-prod/registry/config.json`); `scripts.registry_auth.install(docker_config: Path, source: Path) -> bool` copies `source` to `docker_config/config.json` with mode 0600 and returns True when it exists, returns False when it does not, raises `RolloutError("plan", f"registry auth file {source} is not usable")` when it exists but is empty, unreadable or not a JSON object with an `auths` object (the message never contains file content).

- [ ] **Step 1: Write the failing tests.** `tests/test_registry_auth.py`: absent returns False and writes nothing; valid file is copied with mode 0600 and identical bytes; empty, unreadable, non-JSON and `[]` files each raise with the exact message and without the content; the destination is removed with the directory. `tests/test_staging_rollout.py`: keep `test_docker_config_dir_is_empty_private_and_removed` unchanged (no auth file); add `test_docker_config_dir_holds_the_registry_login_when_one_is_configured` (monkeypatch `STAGING` via `dataclasses.replace` to point `registry_auth` at a tmp file; the spy sees `["config.json"]` at mode 0700 dir and 0600 file; removed after); update the package-listing assertions to use `package_scope`. `tests/test_staging_reset.py`: the same two tests for the reset tool. `tests/test_rollout_targets.py`: `PRODUCTION.package_scope == STAGING.package_scope` and `PRODUCTION.registry_auth` differs from staging's.
- [ ] **Step 2: Run the four test files, one at a time, to see the failures.**
- [ ] **Step 3: Implement.** `install` as specified; call it in both `main` functions right after `docker_config.chmod(0o700)` with `STAGING.registry_auth` (reset) or `target.registry_auth` (rollout); `RolloutError` already ends in `_report_stop`. Rename the field and its three users (`rollout_targets.py:56`, `staging_rollout.py:494`, tests that name `package_owner`).
- [ ] **Step 4: Run the four files plus `tests/test_rollout_production.py`, `tests/test_rollout_digests.py`, `tests/test_rollout_smoke_rollback.py`; then `make lint`.** Expected: pass.
- [ ] **Step 5: Commit** `feat(rollout): optional read-only registry login and package scope constant`. The PR description states that its first test is a real staging rollout by the owner (the other developer's rule for `staging_rollout.py`).

### Task 5: Pull requests stop uploading the review bundle (PR 3)

**Files:**
- Modify: `.github/workflows/ci.yml` (`package` job upload step at line 290-295 and its comment at line 254)
- Create: `tests/test_ci_artifacts.py`

**Interfaces:**
- Produces: the `actions/upload-artifact` step of `package` runs only when `github.event_name != 'pull_request'`; `retention-days: 3`; the artifact name stays `csa-review-only-${{ github.sha }}`.

- [ ] **Step 1: Write the failing tests** in `tests/test_ci_artifacts.py`: `test_pull_requests_do_not_upload_the_bundle` (the upload step has `if: github.event_name != 'pull_request'`), `test_the_main_run_still_uploads_the_bundle_the_publish_workflow_downloads` (artifact name is `csa-review-only-${{ github.sha }}` and `publish-staging-images.yml` downloads `csa-review-only-${{ steps.run.outputs.head_sha }}`), `test_the_bundle_is_kept_three_days` (`retention-days: 3`; the publish workflow can wait: assert the value is at least 3).
- [ ] **Step 2: Run it to see the failures.**
- [ ] **Step 3: Implement** the `if:` and retention change; correct the comment ("Build-only artifacts. The image build runs on pull requests to catch Dockerfile breakage; the bundle is uploaded only for a push to main, where the publish workflow needs it").
- [ ] **Step 4: Run `tests/test_ci_artifacts.py`.** Expected: pass.
- [ ] **Step 5: Commit** `chore(ci): pull requests build the images but no longer upload the review bundle`.

### Task 6: Read-only image keep list (PR 3)

**Files:**
- Create: `scripts/image_keep_list.py`, `tests/test_image_keep_list.py`
- Modify: `deploy/RELEASE.md` or `deploy/PRODUCTION.md` (a short "Pruning old images" paragraph: the command, that it deletes nothing, who runs the deletion)

**Interfaces:**
- Produces: `plan_prune(versions: Sequence[Version], staged_shas: frozenset[str], keep_newest: int = 20) -> PrunePlan` where `Version` has `id: int`, `name: str` (digest), `created_at: str` (ISO), `tags: tuple[str, ...]` and `PrunePlan` has `keep: tuple[Keep, ...]` and `delete: tuple[Version, ...]`; each `Keep` carries a `reason` of `"newest"` or `"staged"`. Command line `python -m scripts.image_keep_list --scope users/amsyrzmi [--keep-newest N] [--keep-sha SHA ...]` reads `gh api --paginate /{scope}/packages/container/csa-{python,frontend}/versions` and `gh api repos/<repo>/deployments?environment=staging` (repo from `STAGING.repo`) plus the success statuses of the newest 15 staging deployments, and prints one table per package: version id, digest, tags, created, KEEP(reason) or DELETE. Exit 0. It never calls a delete endpoint and never lists the database package.

- [ ] **Step 1: Write the failing tests** for `plan_prune`: newest N kept; a version tagged with a staged SHA kept even when older than N (reason `staged`); `--keep-sha` extras kept; fewer than N versions gives no delete candidates; a version with two tags and one staged tag is kept; an untagged version outside the newest N is a delete candidate; ordering is newest first; and `test_the_script_never_issues_a_delete` (the fake runner records every `gh` argv; none contains `DELETE` or `-X`).
- [ ] **Step 2: Run `tests/test_image_keep_list.py` to see it fail.**
- [ ] **Step 3: Implement** `plan_prune` as a pure function and a thin `main` with an injectable runner (follow `SubprocessRunner`/`FakeRunner` in `scripts/staging_rollout.py` and `tests/`). `gh api --paginate` output is a concatenation of JSON arrays; parse with `json.JSONDecoder().raw_decode` in a loop.
- [ ] **Step 4: Run `tests/test_image_keep_list.py`, then `make lint`.** Expected: pass.
- [ ] **Step 5: Commit** `feat(deploy): read-only keep list for old registry images`.

## After the three pull requests

Owner steps in the design (section 5.2) take over: the AWS trust change (step 1), GitHub variable `IMAGE_NAMESPACE=ghcr.io/amsyrzmi` set before PR 1 merges, the gitleaks key, one real staging rollout for PR 2, then the window and the transfer. The post-transfer pull request (constants flipped to the organisation and `mito`, database image pins, tests, docs, skills) is planned after the transfer, when the real values exist.
