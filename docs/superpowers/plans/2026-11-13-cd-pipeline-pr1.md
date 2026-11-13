# CD pipeline, PR 1: core and production workflow — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `deploy-production` takes the tip of `main` by default, checks the commit before touching the instance, gets image digests from the workflow instead of a GitHub token on the host, smoke-tests after the apply, and rolls an image-only failure back by itself.

**Architecture:** The rollout core (`scripts/staging_rollout.py`) gains optional digest arguments, an instance smoke test and a narrow image-only rollback. Two shell scripts under `deploy/production/` (commit resolution with pre-checks, and a public smoke check) are run by the workflow from `main`. The entry script, runner and the two Systems Manager documents carry the two digests. Staging's own rollout, its staged-record write and the home server are untouched (PR 2).

**Tech Stack:** Python 3.12 (`scripts/`), bash, GitHub Actions, AWS Systems Manager documents, pytest with the existing fake runner and shell-stub harness.

**Spec:** `docs/superpowers/specs/2026-11-13-cd-pipeline-design.md` (PR #115). This plan covers the "Delivery" item 1 only.

## Decisions this plan makes (refining the spec, to confirm in review)

- Digests are **optional** on the rollout CLI. Given, the host trusts them (it still verifies each pulled image's digest and revision label) and makes no `gh` call and no staged check. Absent (break-glass by hand), the host does today's `gh` lookup **and** the staged check, so break-glass keeps its gate; the spec said it would skip it.
- The "production must not already run a newer commit" part of the apply-time staleness check is dropped. With the tip as default it cannot happen, and an explicit older `sha` is a deliberate rollback that the plan already flags with the downgrade warning. Task 8 edits the spec to match.
- The workflow reads the digests from the registry's manifest API with an anonymous pull token (the images are public), not the packages API: `GITHUB_TOKEN` may not be allowed to read a user-owned package. The host's revision-label check still catches a wrong image.
- `ssm_run.sh` takes the digests from the environment (`PYTHON_DIGEST`, `FRONTEND_DIGEST`), not new positional arguments.

## Global Constraints

- The repository is public: no hostname, address, account id, instance id, ARN, bucket or region value in any file, test, commit or PR text. Tests use stand-ins.
- Every action in a workflow is pinned to a commit SHA. Workflow jobs run only for `main` and run scripts from a `ref: main` checkout, never from the commit being deployed.
- Systems Manager document parameters each carry an `allowedPattern`; no value is ever spliced into a command string (parameters are built with `jq`).
- Timeout ordering stays strictly increasing: runner ceiling `SSM_MAX_SECONDS` (2100) < document `timeoutSeconds` (2400) < job `timeout-minutes` (45). Tasks 5 and 6 add time to an apply: Task 6 computes the worst case and, if it no longer fits, raises all three together and updates the tests that pin them.
- `scripts/staging_rollout.py` is shared by both targets: every existing test for staging and production must keep passing.
- Run only changed files' tests (`.venv/bin/python -m pytest <files> -q`), then `.venv/bin/ruff format`, `.venv/bin/ruff check` and `.venv/bin/pyright <touched python files>`. `tests/test_production_deploy_assets.py` takes about 3 minutes: run it in the background. CI runs the full suite.
- Commit trailers: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Two merges in quick succession, default `sha`: the plan must fail with `main's tip <sha7> is not staged yet; wait for deploy-staging`, never deploy the older staged commit. (Task 3)
2. `main` moves between a default run's plan and its approval: apply must refuse and send nothing. (Task 7)
3. A digest that is not `sha256:` plus 64 lowercase hex, or one with a newline, must be refused at every layer (workflow script, runner, entry script, document pattern). (Tasks 2, 3)
4. The registry answers with a digest the instance then fails to verify (wrong image under the tag): the host still stops, nothing is recreated. (Task 1)
5. A smoke failure after migrations were applied must not roll back, and a rollback must never run when the previous images are unknown, mixed across services, or the failure is a check-file failure. (Task 6)
6. A host run by hand with no digests still does the `gh` lookup and refuses an unstaged commit. (Task 1)

## File Structure

- Modify `scripts/staging_rollout.py`: digest options, `images_from_digests`, `_run_smoke`, `_rollback_images`, recovery text.
- Create `deploy/production/resolve_commit.sh`, `deploy/production/public_smoke.sh`.
- Modify `deploy/production/deploy_entry.sh`, `ssm_run.sh`, `ssm-document-plan.json`, `ssm-document-apply.json`, `.github/workflows/deploy-production.yml`.
- Create `tests/test_rollout_digests.py`, `tests/test_rollout_smoke_rollback.py`, `tests/test_production_resolve.py`; modify `tests/test_production_deploy_assets.py`.
- Modify `deploy/PRODUCTION.md`, `.claude/skills/production-deploy/SKILL.md`, the design spec.

---

### Task 1: Digest pass-through in the rollout core

**Files:**
- Modify: `scripts/staging_rollout.py` (`build_plan`, `apply`, `format_plan`, `_parser`, `main`)
- Test: create `tests/test_rollout_digests.py`; reuse `FakeRunner` and fixtures from `tests/test_staging_rollout.py`

**Interfaces:**
- Produces: `images_from_digests(digests: Mapping[str, str], target: Target) -> dict[str, str]` (app name to `<image_owner>/csa-<app>@<digest>`); `DIGEST_RE` (existing) validates each value; `build_plan(..., image_digests: Mapping[str, str] | None = None)` and `apply(..., image_digests: Mapping[str, str] | None = None)`; CLI options `--python-digest` and `--frontend-digest` on both `plan` and `apply`, both or neither (otherwise `parser.error`).
- When `image_digests` is given: no `require_staged`, no `published_images`, no `publish_state`, no `gh` call at all. When absent: behaviour unchanged.
- `format_plan` omits `Staged on:` when the record date is empty, and the printed `apply` line includes the two digest options when they were given.

- [ ] **Step 1: Write the failing tests** in `tests/test_rollout_digests.py`: `test_digests_skip_every_gh_call_and_the_gate_on_production` (a fake runner that fails the test on any `gh` argv; production target; the plan returns and its images equal `images_from_digests`), `test_without_digests_production_still_looks_up_and_refuses_an_unstaged_commit` (existing behaviour, message `commit <sha7> has not been rolled out to staging`), `test_only_one_digest_option_is_a_parser_error`, `test_a_malformed_digest_is_refused_with_a_newline_and_with_uppercase`, `test_a_digest_the_pull_cannot_verify_stops_before_any_recreate` (pull returns a different digest: step `pull`, no `up` argv recorded), `test_the_printed_apply_command_carries_the_digests`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_rollout_digests.py -q`; expect failures for the missing names.
- [ ] **Step 3: Implement** the interface above in `scripts/staging_rollout.py`; validate each digest with `DIGEST_RE.fullmatch`; the parser type function mirrors `_host_digest`.
- [ ] **Step 4: Run** `tests/test_rollout_digests.py tests/test_rollout_gate.py tests/test_rollout_production.py tests/test_staging_rollout.py tests/test_rollout_targets.py -q`; all pass. Format, lint, pyright.
- [ ] **Step 5: Commit** `feat(rollout): take image digests as arguments so a host needs no GitHub token`.

### Task 2: Entry script, runner and the two documents carry the digests

**Files:**
- Modify: `deploy/production/deploy_entry.sh`, `deploy/production/ssm_run.sh`, `deploy/production/ssm-document-plan.json`, `deploy/production/ssm-document-apply.json`
- Test: `tests/test_production_deploy_assets.py` (its `PARAMETERS`, `BAD_VALUES` and harness tables)

**Interfaces:**
- Consumes: Task 1's CLI options.
- Produces: entry script arguments `<action> <sha> [<migrations>] [<host_digest>] [<python_digest> <frontend_digest>]`: the two digests are given together or not at all, each `^sha256:[0-9a-f]{64}$`, under the existing `LC_ALL=C` and no-newline rules; on `plan` and `apply` they become `--python-digest` and `--frontend-digest`. Document parameters `pythonDigest` and `frontendDigest` (same pattern, no default) in both documents, passed as `"{{ pythonDigest }}" "{{ frontendDigest }}"` after the existing arguments. `ssm_run.sh` reads `PYTHON_DIGEST` and `FRONTEND_DIGEST` from the environment, validates them (exit 64 when missing or malformed, nothing sent) and adds them to the `jq`-built parameter map for both actions.

- [ ] **Step 1: Extend the tests first**: add both names to `PARAMETERS` and `BAD_VALUES` (uppercase, 63 and 65 hex digits, `sha512:`, trailing newline, `$(id)`), so the entry script, `ssm_run.sh` and the document pattern tests all cover them; add `test_entry_passes_both_digests_to_the_rollout`, `test_entry_without_digests_runs_the_old_path`, `test_entry_refuses_one_digest_without_the_other`, `test_ssm_run_sends_both_digests_for_plan_and_apply`, `test_ssm_run_refuses_missing_digest_env_and_sends_nothing`.
- [ ] **Step 2: Run** the file in the background; expect the new and extended tests to fail.
- [ ] **Step 3: Implement** the entry script, runner and both documents as specified. The plan document's command becomes `plan "{{ sha }}" "" "" "{{ pythonDigest }}" "{{ frontendDigest }}"`.
- [ ] **Step 4: Run** the file again in the background; all pass. `shellcheck` is not required; `bash -n` each script.
- [ ] **Step 5: Commit** `feat(deploy): the entry script, runner and documents carry the image digests`.

### Task 3: Commit resolution and pre-checks

**Files:**
- Create: `deploy/production/resolve_commit.sh`
- Test: create `tests/test_production_resolve.py` (stub `gh`, `curl` and `git` executables on `PATH` that log argv and answer from fixtures, as in `test_production_deploy_assets.py`)

**Interfaces:**
- Produces: `resolve_commit.sh [<input-sha>]`, environment `GH_TOKEN`, `REPOSITORY`. On success prints `sha=`, `python_digest=`, `frontend_digest=`, `staged_on=`, `explicit=true|false` to stdout (for `$GITHUB_OUTPUT`). On the first failed check prints one line to stderr and exits non-zero; nothing is printed to stdout. Order and exact messages:
  1. input present and not `^[0-9a-f]{40}$` (under `LC_ALL=C`, no newline): `sha must be the full 40 character lowercase commit SHA`; empty input: sha is the tip of `main` from `gh api repos/$REPOSITORY/commits/main`, `explicit=false`.
  2. not on `main` (`gh api repos/$REPOSITORY/compare/<sha>...main`, status `ahead` or `identical`): `commit <sha7> is not on main`.
  3. the commit has a completed `ci` run with conclusion `success` on `main` for event `push` (`actions/workflows/ci.yml/runs?head_sha=<sha>&branch=main&event=push`, any such run): `the ci run for <sha7> has not succeeded on main`.
  4. a `success` status on a Deployment in environment `staging` for the sha (the same two `gh api` calls the workflow makes today): default mode `main's tip <sha7> is not staged yet; wait for deploy-staging`, explicit mode `commit <sha7> has not been rolled out to staging`. `staged_on` is the earliest success date.
  5. both images tagged with the full sha, from `https://ghcr.io/token?service=ghcr.io&scope=repository:<owner>/csa-<app>:pull` then `HEAD https://ghcr.io/v2/<owner>/csa-<app>/manifests/<sha>` with `Accept` for OCI and Docker manifest and index types, reading `Docker-Content-Digest`; a missing image: `images for <sha7> are not published (csa-<app>)`; a digest not matching `^sha256:[0-9a-f]{64}$`: `the registry returned a malformed digest for csa-<app>`. `<owner>` is `${REPOSITORY%%/*}`.

- [ ] **Step 1: Write the failing tests**: `test_default_resolves_the_tip_and_prints_all_five_values`, `test_default_with_an_unstaged_tip_does_not_fall_back_to_an_older_staged_commit` (Review Focus 1), `test_explicit_sha_message_differs`, `test_each_check_stops_with_its_exact_message_and_prints_nothing_to_stdout` (parametrised over the five failures, plus a commit not on main and a failed or in-progress ci run), `test_a_ci_run_that_failed_then_a_later_success_for_the_same_sha_passes`, `test_malformed_input_sha_and_registry_digest_are_refused` (Review Focus 3), `test_gh_and_curl_failures_fail_closed`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_production_resolve.py -q`; expect failures.
- [ ] **Step 3: Implement** `resolve_commit.sh` (mode 755, `set -euo pipefail`, `LC_ALL=C`, `die` and `valid` helpers as in `parse_plan.sh`, `jq` for every JSON read).
- [ ] **Step 4: Run** the tests; pass. Then, read-only against the real repository and registry from this machine, run the script once with `GH_TOKEN="$(gh auth token)" REPOSITORY=amsyrzmi/CSAV2` and no input; it must print five values for the current tip (or the "not staged yet" refusal). Record what it printed in the task report with the ids redacted. This proves the registry and CI endpoints, which the stubs cannot.
- [ ] **Step 5: Commit** `feat(deploy): resolve the commit and check it before anything reaches the instance`.

### Task 4: Public smoke check

**Files:**
- Create: `deploy/production/public_smoke.sh`
- Test: add to `tests/test_production_resolve.py`

**Interfaces:**
- Produces: `public_smoke.sh <origin>`: the origin must match `^https://[A-Za-z0-9.-]+$` (no path, no port, no trailing slash); requests `<origin>/` with `curl --max-time 15 --retry 3 --retry-delay 5`; exit 0 only for a 2xx or 3xx status; anything else exits non-zero. It never prints the origin.

- [ ] **Step 1: Write failing tests**: `test_public_smoke_passes_on_200_and_on_a_login_redirect`, `test_public_smoke_fails_on_4xx_5xx_and_on_a_curl_failure`, `test_public_smoke_refuses_a_malformed_origin_without_calling_curl`, `test_public_smoke_never_prints_the_origin`.
- [ ] **Step 2: Run** them; expect failures.
- [ ] **Step 3: Implement** `public_smoke.sh` (mode 755).
- [ ] **Step 4: Run** `tests/test_production_resolve.py -q`; pass.
- [ ] **Step 5: Commit** `feat(deploy): a public smoke check for the production console`.

### Task 5: Instance smoke test in the rollout

**Files:**
- Modify: `scripts/staging_rollout.py` (new `_run_smoke`, called in `apply` after `_run_checks`, new phase `smoke`)
- Test: create `tests/test_rollout_smoke_rollback.py`

**Interfaces:**
- Produces: `_run_smoke(runner, env, checkout_versions: Sequence[str], host_values: Mapping[str, str], out, progress, *, target) -> None`, raising `RolloutError("smoke", <what failed>, state)`. Checks, read-only, no customer data, no model call: (a) the recorded migration versions equal the checkout's (`applied_versions`); (b) every replica of every app service is healthy and runs the planned image (reuse `_on_new_image`); (c) when `host_values` holds the two origins (production), each answers with a status below 500 when requested from the host with `curl --max-time 15`; (d) the frontend answers over HTTP from the gateway container (python `urllib`, like `READY_PROBE`); the builder finds the frontend service name and port in the compose files and, if it cannot be reached from the gateway's network, checks container health only and says so in the task report. Each passing check prints one `smoke <name>: ok` line.

- [ ] **Step 1: Write failing tests**: `test_smoke_passes_and_prints_one_line_per_check`, `test_a_missing_recorded_migration_stops_at_smoke`, `test_an_unhealthy_replica_stops_at_smoke`, `test_origins_are_checked_only_when_the_host_file_has_them`, `test_a_5xx_from_an_origin_stops_at_smoke`, `test_smoke_makes_no_write_call` (no argv containing `up`, `stop`, `exec ... psql ... -c` with anything but `SELECT`).
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_rollout_smoke_rollback.py -q`; expect failures.
- [ ] **Step 3: Implement** `_run_smoke` and call it from `apply` after `_run_checks`; compute `checkout_versions` once from `discover_migrations()` as `build_plan` does.
- [ ] **Step 4: Run** this file and the five rollout test files from Task 1; pass.
- [ ] **Step 5: Commit** `feat(rollout): a read-only smoke test after the check files`.

### Task 6: Image-only automatic rollback

**Files:**
- Modify: `scripts/staging_rollout.py` (`apply`'s `except RolloutError` path, new `_rollback_images`, `_Progress`, `_report_recovery`)
- Test: `tests/test_rollout_smoke_rollback.py`

**Interfaces:**
- Produces: `_rollback_images(runner: Runner, plan: Plan, sleep: Callable[[float], None], out: Callable[[str], None], *, target: Target) -> bool`: recreates the four services on the previous image refs (`plan.rollback`) and waits for readiness; returns whether the previous version is ready. It runs only when all hold: the error step is `readiness` or `smoke`; `plan.pending` is empty; every service in `plan.rollback` is a known `RunningImage`; gateway, worker and delivery share one ref. Otherwise nothing runs. `apply` always returns 1 after a rollback attempt. Output: `Rolling back to <ref>…` lines, then `Rolled back: the previous images are running; <sha7> is not.` and recovery text `Fix forward, then re-run plan and apply.` (phase `rolled_back` in `_report_recovery`); if the rollback itself fails, print its `Stopped at rollback: ...` and then the existing recovery lines unchanged.
- The environment for the previous refs is a copy of `plan.env` with `IMAGE_ENV["python"]` and `IMAGE_ENV["frontend"]` replaced by the previous refs (the gateway's ref and the frontend's); `plan.env` itself is not mutated.

- [ ] **Step 1: Write failing tests**: `test_a_readiness_failure_on_an_image_only_deploy_rolls_back_and_still_exits_1`, `test_a_smoke_failure_rolls_back`, `test_no_rollback_when_a_migration_was_applied` (Review Focus 5), `test_no_rollback_when_a_previous_image_is_unknown` (first deploy, local tags), `test_no_rollback_when_the_three_python_services_ran_different_images`, `test_no_rollback_for_a_check_file_failure`, `test_a_rollback_that_never_becomes_ready_prints_its_stop_and_the_old_recovery_lines`, `test_a_successful_deploy_makes_no_rollback_call`, `test_staging_never_records_after_a_rollback` (via `main`, exit 1, no deployment POST).
- [ ] **Step 2: Run** the file; expect failures.
- [ ] **Step 3: Implement** as specified. Then compute the worst-case apply time (backup, pull, 4 recreates at up to 300 s wait plus 300 s health, readiness, smoke, and now a second pass of recreates) and compare with 2100 / 2400 / 45 minutes; if it can exceed 2100 s, raise the three values together and update the tests that pin them, saying so in the report.
- [ ] **Step 4: Run** this file and the Task 1 files; pass. Format, lint, pyright.
- [ ] **Step 5: Commit** `feat(rollout): roll an image-only failure back to the previous images`.

### Task 7: Workflow wiring

**Files:**
- Modify: `.github/workflows/deploy-production.yml`
- Test: `tests/test_production_deploy_assets.py` (rewrite the tests that assert the old "newest staged commit" step; do not delete coverage, move it to the new script's tests)

**Interfaces:**
- Consumes: Tasks 2 to 4.
- Produces: input `sha` described as `Full commit SHA on main (empty = the tip of main)`. `plan` job: checkout of `main` first, then `Resolve and check the commit` (runs `deploy/production/resolve_commit.sh "$INPUT_SHA" >> "$GITHUB_OUTPUT"`, `id: target`), then AWS credentials, then the plan with `PYTHON_DIGEST` and `FRONTEND_DIGEST` in the environment; job outputs add `python_digest`, `frontend_digest`, `explicit`. Plan job permissions add `actions: read`. `apply` job: a step before any AWS call, `Check the commit is still current`, which when `needs.plan.outputs.explicit` is `false` reads the tip with `gh api repos/$REPOSITORY/commits/main` and fails with `main moved to <tip7> since this run's plan; start the workflow again` unless it equals the plan's sha; it sends nothing. The apply passes the digests to `ssm_run.sh`; a final step `Public smoke test` runs `deploy/production/public_smoke.sh "$PROD_CONSOLE_ORIGIN"` with the origin from the environment secret `PROD_CONSOLE_ORIGIN` on `production`, and the existing `Require the deploy variables` step also fails when that secret is empty (before anything is sent). Apply permissions add `contents: read` for the tip read (already present).

- [ ] **Step 1: Write failing tests**: `test_the_workflow_resolves_with_the_script_after_checking_out_main`, `test_the_input_description_says_tip_of_main`, `test_apply_checks_the_tip_before_the_aws_credentials_step` (Review Focus 2: extract the step's shell and run it with a stub `gh` returning a moved tip, assert non-zero exit, the exact message, and that the later steps are not run), `test_the_staleness_check_is_skipped_for_an_explicit_sha`, `test_digests_reach_ssm_run_through_the_environment_only`, `test_the_public_smoke_step_runs_last_and_the_secret_is_required_up_front`, `test_permissions_are_unchanged_except_actions_read_on_plan`. Update the existing structural tests that reference the removed shell block.
- [ ] **Step 2: Run** the file in the background; expect the new tests to fail.
- [ ] **Step 3: Implement** the workflow edits. Keep every action's pinned SHA; update the header comment to describe the new behaviour.
- [ ] **Step 4: Run** the file in the background; all pass.
- [ ] **Step 5: Commit** `feat(deploy): deploy-production defaults to the tip of main and checks the commit first`.

### Task 8: Runbook, skill and spec

**Files:**
- Modify: `deploy/PRODUCTION.md`, `.claude/skills/production-deploy/SKILL.md`, `docs/superpowers/specs/2026-11-13-cd-pipeline-design.md`
- Test: `tests/test_production_deploy_assets.py` (doc and skill assertions)

**Interfaces:** none produced.

- [ ] **Step 1: Write failing assertions** in the existing doc and skill tests: the runbook says an empty `sha` means the tip of `main` and names the five refusals from Task 3 and the `main moved to` refusal; it lists the secret `PROD_CONSOLE_ORIGIN` in step 6; it has a section "After the digest change merges" with the owner steps in this order: update both documents (`aws ssm update-document --name <name> --content file://<file> --document-version '$LATEST'`, then `update-document-default-version`), reinstall the entry script (`sudo install -m 0755 -o root -g root ~/CSAV2/deploy/production/deploy_entry.sh /usr/local/sbin/csa-prod-deploy`), set `PROD_CONSOLE_ORIGIN` on `production`, then start a run; it says the `read:packages` token is needed only for break-glass; it explains what a rollback line and a `smoke` stop mean. The skill says an empty `sha` means the tip and that a not-yet-staged tip fails with the message in Task 3.
- [ ] **Step 2: Run** the file in the background; expect failures.
- [ ] **Step 3: Edit** the three files. In the spec, replace the sentence about the staleness check so it matches "Decisions this plan makes", and add the registry-manifest method to "The workflow talks to GitHub".
- [ ] **Step 4: Run** the file in the background; pass.
- [ ] **Step 5: Commit** `docs(deploy): runbook, skill and spec for the tip default, digests, smoke test and rollback`.
