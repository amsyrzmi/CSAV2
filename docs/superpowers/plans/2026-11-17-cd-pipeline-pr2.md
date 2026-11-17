# CD pipeline, PR 2: staging automation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every green merge to `main` rolls out to staging on the home server by itself, through a `deploy-staging` workflow over Systems Manager. The workflow, not the host, writes the staged record, and only after the instance reported success (smoke test included). Production's staged gate counts only that record.

**Architecture:** A new workflow `.github/workflows/deploy-staging.yml` has four jobs: `paused` (a notice), `plan`, `apply` and `record`. It starts on `workflow_run` of `publish-staging-images` and on `workflow_dispatch`. It reuses the production runner scripts (`resolve_commit.sh`, `ssm_run.sh`, `parse_plan.sh`), which gain a required `DEPLOY_TARGET` setting. Two small staging scripts decide whether to roll out and write the record. On the host, `deploy_entry.sh` becomes the shared entry script and reads a root-owned config file installed next to it. Two staging Systems Manager documents and one role template trust only the `staging` environment. `deploy/aws/setup-target.sh` creates or checks the AWS side for either target. The rollout core gains a per-target lock that manual and automatic runs share, counts only `staging rollout` deployments as staged, and leaves the record to the workflow when it was given digests.

**Tech Stack:** bash, GitHub Actions, AWS Systems Manager and IAM (CLI), Python 3.12 (`scripts/`), pytest. The tests use the existing fake runner, the shell-stub harness in `tests/test_production_deploy_assets.py` and `tests/test_production_resolve.py`, and structural tests that read the workflow, document and policy files.

**Spec:** `docs/superpowers/specs/2026-11-13-cd-pipeline-design.md`, "Delivery" item 2 only. Out of scope: the GitHub setup script, `PROD_AUTO_PLAN`, `deploy/DEPLOY.md` and the merged skill, and the stale "owner approves the publish" wording (all PR 3).

## Plan decisions (mine; the owner may overrule)

- **The trigger is a wake-up, the commit is the tip of `main`.** On 2026-11-17, real publish runs showed that a `workflow_run`-started publish carries the tip of `main` at creation as its `head_sha`, not the commit it published. A publish skipped for a cancelled ci run carried the next commit's sha. So the workflow never reads `github.event.workflow_run.head_sha`. Every automatic run resolves the tip of `main`. **Newest wins** in three places:
  - GitHub's concurrency drops older pending runs.
  - An automatic run whose tip is not ready yet (ci not green, or images not published) ends green with `nothing to roll out`; that commit's own publish will start the next run.
  - The apply job ends green with `superseded` when `main` moved since its plan.

  An automatic run whose tip already has a staged record ends green with `already staged`, so a late duplicate never restarts staging twice.
- **An explicit `sha` (dispatch) skips the tip checks.** The plan's downgrade warning still stops it, so staging never goes back through the workflow, even on purpose. Taking staging back is the manual script (break-glass).
- **Pause switch: the repository variable `STAGING_AUTO_PAUSED`.** `true` turns automatic runs into a green `paused` notice; unset or anything else means automatic (the default). Dispatch ignores the pause, so the owner can test while paused. I rejected a required reviewer on `staging`: it would hold every run, dispatch included, and leave a queue of waiting runs. The owner sets the variable to `true` **before** PR 2 merges, so merges made before the host preparation do not each produce a red run.
- **A dedicated deploy user on the home server, `csa-deploy`** (system user, no login shell, member of `docker`). It owns its own checkout, `/srv/csa-staging-deploy/CSAV2`. I did not reuse the owner's account because its `gh` login has the `repo` scope, and its home holds SSH keys and tool sessions. Nothing started by Systems Manager should run where those are reachable. Cost: `csa-deploy` needs to pass through `/srv/csa-staging` (mode 0700 today) and the secret directories. The owner grants that with search-only ACLs (`x` on directories, nothing on files).
- **Install paths on the home server:**
  - entry script `/usr/local/sbin/csa-staging-deploy`, config `/usr/local/sbin/csa-staging-deploy.conf` (next to it);
  - Python environment `/opt/csa-staging-deploy/venv`. The host's `python3` is 3.14, so this is a uv-built 3.12 environment;
  - log directory `/var/log/csa-staging-deploy`, entry lock `/run/lock/csa-staging-deploy.lock`.

  Production keeps its paths, and its config later holds today's defaults.
- **Config format:** `KEY=value` lines, parsed and never sourced, exactly six keys, all required: `TARGET`, `DEPLOY_USER`, `CHECKOUT`, `PYTHON`, `LOCK`, `LOG_DIR`. The file must be a regular file (not a symlink), owned by the uid that owns the script, and not writable by group or others. Environment variables no longer override anything. The tests run a copy of the script with a config next to it, so no root is needed.
- **Production is unaffected by this PR on the host.** Its documents do not change, and the installed `csa-prod-deploy` keeps working. Reinstalling the shared script there needs a config file. `deploy/PRODUCTION.md` gains that procedure for the next time the entry script changes; it is not a prerequisite.
- **A rollout lock in the core**, `/run/lock/<project>-rollout.lock`, taken by `plan` and `apply` and not by `record-staged`. Today's entry-script lock does not cover the owner's manual staging runs from `/home/amsyrzmi/CSAV2`, which take no lock. The file is opened read-only with create, so a file created by the other user can still be locked (`/run/lock` is mode 1777 on the host).
- **The staged record.** The record job writes it with the workflow token. On staging, the rollout core writes it only when it was **not** given digests, which means the owner's manual run with its own `gh` login. The production gate (`resolve_commit.sh`) and the core's break-glass gate count a deployment only when its description is `staging rollout`. GitHub's auto-created deployments therefore never count, even if `deployment: false` does not behave as documented.
- **Shared scripts stay in `deploy/production/`**: no file moves, so production paths, docs and installed copies are untouched. Staging-only files go in `deploy/staging/`, and the AWS script in `deploy/aws/`. PR 3's runbook may move them.
- **`resolve_commit.sh` exits 3** for "not ready yet" (ci not succeeded, images not published) and 1 for every other refusal. Production treats both as a failure; staging's automatic runs treat 3 as `nothing to roll out`.
- **The staging region is its own repository variable, `STAGING_DEPLOY_REGION`**, so the target stays replaceable. Its value equals `PROD_DEPLOY_REGION` today.
- **Noted for PR 3, not changed here:**
  - GitHub runs at most three levels of `workflow_run` chains. ci → publish → deploy-staging is three, so `PROD_AUTO_PLAN` cannot be a fourth `workflow_run` level. deploy-staging would have to dispatch deploy-production with its token (a `workflow_dispatch` from `GITHUB_TOKEN` is allowed).
  - `publish-staging-images.yml` still uses unpinned `@v4` actions.

## Global Constraints

- The repository is public. No hostname, address, account id, managed-node id, ARN, bucket or region value goes in any file, test, commit or PR text; tests use stand-ins (`mi-0123456789abcdef0`, `test-region-1`, `repo:example-owner@1001/CSAV2@2002`). Never read `/srv/csa-staging/secrets` or anything under `/srv/csa-prod`. The only host commands a builder may run are the read-only ones a task names.
- No secret ever reaches a log, the job summary or the repository. Role ARNs, the node id and origins are environment secrets. Values reach scripts through `env:` and never through `${{ }}` in run text. Instance output is bracketed in `::stop-commands::`, as in deploy-production.
- Every `uses:` is pinned to a 40-hex commit with a `# vX.Y.Z` comment. The new workflow uses exactly the two pins deploy-production uses.
- Workflows run only from `main`. Every job has `if: github.ref == 'refs/heads/main'`, and scripts come from an `actions/checkout` with `ref: main`, never from the commit being deployed.
- Systems Manager parameters each carry an `allowedPattern`, and parameter maps are built with `jq`, never spliced into a command string.
- Timeout order is unchanged and shared: runner `SSM_MAX_SECONDS` 6900 < document `timeoutSeconds` 7200 < job `timeout-minutes` 130.
- `scripts/staging_rollout.py` and `scripts/rollout_targets.py` are shared by both targets. Every existing staging and production test keeps passing; a test changes only where this plan changes behaviour, and the task says which.
- Run only the changed files' tests: `.venv/bin/python -m pytest <files> -q`. Run `uv sync --frozen` first if `.venv` is missing. If the session guard refuses `ENV=` prefixes or compound commands, use the `ci.sh` wrapper. Then run `.venv/bin/ruff format`, `.venv/bin/ruff check` and `.venv/bin/pyright <touched python files>`, plus `bash -n` on every touched shell script, and `make lint` before the final review. `tests/test_production_deploy_assets.py` takes about 3 minutes: run it in the background. CI runs the full suite.
- Commit trailers: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and the session line the controller gives.
- The builder never touches the home server's stack, AWS or GitHub settings. Every host, AWS or GitHub step is in Part B for the owner.

## Review Focus

1. **A burst of merges.** Three merges inside one ci run must produce at most one rollout, of the newest green commit:
   - an older pending run is dropped by concurrency;
   - a run whose tip is not published yet ends green with `nothing to roll out`;
   - an apply whose plan was overtaken ends green with `superseded` and sends nothing;
   - a failed or skipped publish run gets its own concurrency group and cannot cancel a pending good run.

   (Tasks 6, 7)
2. **A superseded run must never write a staged record.** The record job runs only when the apply job's `applied` output is `true`, and a later step sets that output only after `ssm_run.sh apply` succeeded. (Tasks 6, 7)
3. **The staged record must never be written early.** The plan and apply jobs set `deployment: false`. The record job names no environment. The production gate and the core count only description `staging rollout`, so a GitHub auto-deployment with `success` after the plan job is ignored. With digests, the core on staging makes no `gh` call at all. (Tasks 1, 3, 7)
4. **A plan with a missing secret file** (plan exit 2) fails the run before apply, and nothing is sent. `parse_plan.sh` also refuses any `Missing secret files:` or `Stopped at` line, and a staging downgrade warning, as a second guard. (Tasks 5, 7)
5. **A run during the owner's manual rollout.** The core lock held by the owner's manual `apply` makes the workflow's plan stop with `Stopped at lock: another rollout of staging is running; nothing was changed` and a red run. Nothing is pulled or recreated, and the lock is released when either process ends. (Task 2)
6. **Merging before host preparation.** With the pause variable unset and no secrets, the plan job fails at `Require the deploy variables` before any AWS call. The checklist sets the pause before the merge. (Task 7, Part B step 1)
7. **Injection and spoofing.** A `sha` input with a newline, uppercase or 39 characters is refused by every layer (gate script, runner, entry script, document pattern). `github.event.workflow_run.head_sha` appears nowhere. A config file that is group-writable, a symlink, or has an unknown or repeated key is refused before git runs. An entry script run as the wrong user is refused before the lock. (Tasks 4 to 7)
8. **Cross-target changes.** The production workflow, the production documents and the installed production script behave exactly as before, apart from `DEPLOY_TARGET: production` and the description filter. The existing staged records all carry `staging rollout` (Task 3 checks this against the real repository). (Tasks 3, 5, 7)

## File Structure

- Modify:
  - `scripts/staging_rollout.py`: `STAGED_DESCRIPTION`, the description filter, the record only without digests, `ROLLOUT_LOCK_DIR` and `hold_rollout_lock`;
  - `scripts/rollout_targets.py`: staging `downgrade_warning=True`.
- Modify: `deploy/production/resolve_commit.sh`, `ssm_run.sh`, `parse_plan.sh`, `deploy_entry.sh`, `.github/workflows/deploy-production.yml` (job env only).
- Create:
  - `deploy/staging/ssm-document-plan.json`, `ssm-document-apply.json`;
  - `deploy/staging/iam/trust-policy.json`, `permissions-policy.json`;
  - `deploy/staging/rollout_gate.sh`, `record_staged.sh`;
  - `deploy/staging/deploy.conf.example`, `deploy/production/deploy.conf.example`;
  - `deploy/aws/setup-target.sh`, `.github/workflows/deploy-staging.yml`, `.github/pull_request_template.md`.
- Create tests: `tests/test_rollout_lock.py`, `tests/test_staging_deploy_assets.py`, `tests/test_staging_gate_scripts.py`, `tests/test_deploy_staging_workflow.py`, `tests/test_aws_setup_target.py`.
- Modify tests: `tests/test_rollout_gate.py`, `tests/test_rollout_targets.py`, `tests/test_staging_rollout.py`, `tests/test_production_resolve.py`, `tests/test_production_deploy_assets.py`, `tests/test_staging_compose.py`, `tests/conftest.py`.
- Modify docs: `deploy/STAGING_PIPELINE.md`, `deploy/PRODUCTION.md`, `.claude/skills/staging-rollout/SKILL.md`, `.claude/skills/production-deploy/SKILL.md`, the design spec.

---

# Part A: built and tested against fakes

### Task 1: Staged record by description; the host leaves it to the workflow; staging warns on a downgrade

**Files:**
- Modify: `scripts/staging_rollout.py` (`staged_record`, `record_staged`, `main`), `scripts/rollout_targets.py` (`STAGING.downgrade_warning`)
- Test: `tests/test_rollout_gate.py`, `tests/test_rollout_targets.py`, `tests/test_staging_rollout.py` (the staging fakes now answer the `merge-base --is-ancestor <sha> <revision>` call that `downgrade_warnings` makes)

**Interfaces:**
- Produces:
  - `STAGED_DESCRIPTION = "staging rollout"`, used by `record_staged` and `staged_record`;
  - `staged_record` skips, without error, any deployment whose `description` is not exactly `STAGED_DESCRIPTION`. Its other shape checks stay fail-closed;
  - in `main`, the staging record after a successful apply is written only when `image_digests is None`. With digests it prints `Staged record: written by the deploy-staging workflow after this run.` and makes no `gh` call;
  - `STAGING.downgrade_warning` becomes `True`, so a staging plan prints the existing `WARNING: <sha7> is older than the running <rev7>; this is a downgrade. ...` line. It never stops a manual plan by itself.

- [ ] **Step 1: Write the failing tests:**
  - `test_an_auto_created_deployment_without_the_description_is_not_staged` (a success status on a deployment with `description: null`: production `require_staged` refuses);
  - `test_a_staging_rollout_deployment_still_counts`;
  - `test_staging_apply_with_digests_writes_no_record_and_calls_no_gh` (a fake runner that fails on any `gh` argv; `main` exits 0 and prints the new line);
  - `test_staging_apply_without_digests_still_records` (today's behaviour);
  - `test_staging_plan_warns_on_a_downgrade`;
  - `test_staging_targets_downgrade_warning_is_on`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_rollout_gate.py tests/test_rollout_targets.py -q`; expect the new tests to fail.
- [ ] **Step 3: Implement** the interface. Update the staging fakes in `tests/test_staging_rollout.py` that now see the extra `merge-base` call, and no other assertion.
- [ ] **Step 4: Run** `tests/test_rollout_gate.py tests/test_rollout_targets.py tests/test_staging_rollout.py tests/test_rollout_digests.py tests/test_rollout_production.py tests/test_rollout_smoke_rollback.py -q`; all pass. Format, lint, pyright.
- [ ] **Step 5: Commit** `feat(rollout): only a staging rollout counts as staged; the workflow records digest runs`.

### Task 2: A rollout lock shared by manual and automatic runs

**Files:**
- Modify: `scripts/staging_rollout.py` (`ROLLOUT_LOCK_DIR`, `hold_rollout_lock`, `main`), `tests/conftest.py` (an autouse fixture that points `ROLLOUT_LOCK_DIR` at a temporary directory, so no test touches `/run/lock`)
- Test: create `tests/test_rollout_lock.py`

**Interfaces:**
- Produces:
  - `ROLLOUT_LOCK_DIR = Path("/run/lock")`; the lock file is `ROLLOUT_LOCK_DIR / f"{target.project}-rollout.lock"`, which gives `csa-staging-rollout.lock` and `csa-prod-rollout.lock`;
  - `hold_rollout_lock(target: Target) -> contextlib.AbstractContextManager[None]`. It opens the file `O_RDONLY | O_CREAT | O_CLOEXEC`, mode `0o644`, then `fcntl.flock(LOCK_EX | LOCK_NB)`;
  - when the lock is held, it raises `RolloutError("lock", f"another rollout of {target.name} is running; nothing was changed")`;
  - when the file cannot be opened, it raises `RolloutError("lock", f"cannot open the rollout lock {path}")`;
  - `main` holds the lock around everything after argument parsing, for `plan` and `apply` (the record write included), and not for `record-staged`. A lock stop prints `Stopped at lock: ...` and returns 1, before any runner call.

- [ ] **Step 1: Write the failing tests:**
  - `test_plan_and_apply_stop_at_lock_when_another_process_holds_it` (hold the lock from a subprocess; the fake runner records zero calls; exit 1; exact message);
  - `test_the_lock_is_released_after_a_run_and_after_a_stop`;
  - `test_a_lock_file_another_user_created_read_only_can_still_be_locked` (chmod 0444 first);
  - `test_an_unopenable_lock_directory_stops_without_runner_calls`;
  - `test_record_staged_takes_no_lock`;
  - `test_staging_and_production_use_different_lock_files`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_rollout_lock.py -q`; expect failures.
- [ ] **Step 3: Implement** the interface and the conftest fixture.
- [ ] **Step 4: Run** this file and the six rollout test files from Task 1; all pass. Format, lint, pyright.
- [ ] **Step 5: Commit** `feat(rollout): a per-target lock so a manual and an automatic rollout never overlap`.

### Task 3: `resolve_commit.sh` per target, description filter, "not ready" exit code

**Files:**
- Modify: `deploy/production/resolve_commit.sh`
- Test: `tests/test_production_resolve.py` (its stubs and fixtures; give the existing tests `DEPLOY_TARGET=production`)

**Interfaces:**
- Consumes: the `staging rollout` description from Task 1.
- Produces:
  - a required environment variable `DEPLOY_TARGET`, either `staging` or `production`, validated first (`DEPLOY_TARGET must be staging or production`, exit 1);
  - step 4 selects only deployments with `.description == "staging rollout"`;
  - on `production`, step 4 refuses exactly as today;
  - on `staging`, step 4 never refuses: `staged_on` is the earliest such success, or empty;
  - step 3 (`the ci run for <sha7> has not succeeded on main`) and the "not published" case of step 5 (`images for <sha7> are not published (csa-<app>)`) exit **3**; every other failure exits 1, with the messages unchanged;
  - the stdout keys are unchanged.

- [ ] **Step 1: Write the failing tests:**
  - `test_deploy_target_is_required_and_validated`;
  - `test_production_ignores_a_success_deployment_without_the_staging_rollout_description`;
  - `test_not_ready_checks_exit_3_and_every_other_refusal_exits_1` (parametrised over all refusals);
  - `test_staging_does_not_require_a_staged_record_and_reports_one_when_present`;
  - `test_staging_still_runs_checks_1_2_3_and_5`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_production_resolve.py -q`; expect failures.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the file; pass. Then make two read-only checks against the real repository from this machine (the owner's `gh` login, nothing written):
  - run the script once for each target: `GH_TOKEN="$(gh auth token)" REPOSITORY=amsyrzmi/CSAV2 IMAGE_NAMESPACE=ghcr.io/amsyrzmi DEPLOY_TARGET=<target>`, no input;
  - run `gh api "repos/amsyrzmi/CSAV2/deployments?environment=staging&per_page=50" --jq '.[].description' | sort | uniq -c`, which must show only `staging rollout`.

  Record both outcomes in the task report (sha7 only, no digests needed). If any other description appears, stop and report it: the owner must decide before the filter ships.
- [ ] **Step 5: Commit** `feat(deploy): resolve per target, count only staging rollout records, exit 3 when not ready`.

### Task 4: The shared entry script reads a root-owned config

**Files:**
- Modify: `deploy/production/deploy_entry.sh`
- Create: `deploy/production/deploy.conf.example`, `deploy/staging/deploy.conf.example` (placeholders only)
- Test: `tests/test_production_deploy_assets.py` (`Harness` installs a copy of the script as `<tmp>/sbin/csa-<target>-deploy` with `<name>.conf` next to it, instead of setting `CHECKOUT`/`PYTHON`/`LOCK`/`LOG_DIR` in the environment; every existing entry test keeps its assertion)

**Interfaces:**
- Produces: `csa-<target>-deploy <plan|apply> <sha> [<migrations>] [<host_digest>] [<python_digest> <frontend_digest>]`. The argument rules are unchanged, except:
  - on `staging`, `apply` requires the host digest to be **empty** (`staging takes no host digest`);
  - on `production`, `apply` still requires it.

  Before any argument is used:
  - `self="${BASH_SOURCE[0]}"` must be absolute, and the config is `${self}.conf`;
  - the config must exist (else exit 78, `<name>: no config at <path>`);
  - it must be a regular file and not a symlink, owned by the uid that owns `$self` (`stat -c %u`), and not writable by group or others (exit 78 with the reason, never the content);
  - it is parsed line by line, never sourced. Blank and `#` lines are skipped, and every other line is `KEY=value`;
  - the keys are exactly `TARGET` (`^(staging|production)$`), `DEPLOY_USER` (`^[a-z_][a-z0-9_-]{0,31}$`), and `CHECKOUT`, `PYTHON`, `LOCK`, `LOG_DIR` (`^/[A-Za-z0-9._/-]+$`, with no `/../`, `/./` or `//` segment). A missing, repeated, unknown or malformed key exits 78, naming the key;
  - `id -un` must equal `DEPLOY_USER` (else exit 64, `run as <user>, not <actual>`), checked before the lock.

  Message prefix: `$(basename "$self")`. The rollout command becomes `"$PYTHON" -m scripts.staging_rollout --target "$TARGET" ...`; the `--host-digest` option is passed only on production. Environment variables no longer change any path.

- [ ] **Step 1: Write the failing tests:**
  - `test_entry_reads_every_path_from_its_config_and_ignores_the_environment` (set `CHECKOUT` in the environment to another directory; git is called with the config's);
  - `test_entry_without_a_config_exits_78_before_git`;
  - `test_entry_refuses_a_group_writable_or_symlinked_config` (exit 78);
  - `test_entry_refuses_unknown_repeated_missing_or_malformed_keys` (parametrised; the output never contains the bad value);
  - `test_entry_refuses_a_wrong_user_before_taking_the_lock`;
  - `test_staging_entry_runs_the_staging_target_without_a_host_digest`;
  - `test_staging_entry_refuses_a_host_digest_on_apply`;
  - `test_production_entry_is_unchanged_with_the_production_config` (the existing production argv assertions);
  - `test_the_example_configs_hold_placeholders_and_parse`.

  Extend `NEW_FILES` in the no-ids test with the two example files.
- [ ] **Step 2: Run** the file in the background; expect the new tests to fail.
- [ ] **Step 3: Implement.** Keep `cd /`, `LC_ALL=C`, the log-file pre-check (exit 73), the entry lock (exit 75) and fd 3. Update the header comment to describe the config.
- [ ] **Step 4: Run** the file in the background; all pass. `bash -n deploy/production/deploy_entry.sh`.
- [ ] **Step 5: Commit** `feat(deploy): one entry script for both targets, configured by a root-owned file next to it`.

### Task 5: Staging documents and role template; `ssm_run.sh` and `parse_plan.sh` per target

**Files:**
- Create: `deploy/staging/ssm-document-plan.json`, `deploy/staging/ssm-document-apply.json`, `deploy/staging/iam/trust-policy.json`, `deploy/staging/iam/permissions-policy.json`, `tests/test_staging_deploy_assets.py` (imports `Harness`, `BAD_VALUES`, `_filled_policy`, `_trust_allows` and the quote checker from `tests.test_production_deploy_assets`)
- Modify: `deploy/production/ssm_run.sh`, `deploy/production/parse_plan.sh`, `tests/test_production_deploy_assets.py` (`SSM_ENV` and the parse helpers gain `DEPLOY_TARGET=production`)

**Interfaces:**
- Produces documents:
  - `csa-staging-plan` takes parameters `sha`, `pythonDigest` and `frontendDigest`. Its command is `exec runuser -u csa-deploy -- /usr/local/sbin/csa-staging-deploy plan "{{ sha }}" "" "" "{{ pythonDigest }}" "{{ frontendDigest }}"`;
  - `csa-staging-apply` takes `sha`, `migrations` (default `""`), `pythonDigest` and `frontendDigest`. Its command is `... apply "{{ sha }}" "{{ migrations }}" "" "{{ pythonDigest }}" "{{ frontendDigest }}"`;
  - the patterns are identical to production's, `timeoutSeconds` is `"7200"`, and the descriptions name the AWS document.
- Produces the role `csa-staging-deploy`:
  - its trust policy is `sts:AssumeRoleWithWebIdentity` from `<oidc-provider-arn>`, with `aud` `sts.amazonaws.com` and `sub` exactly `<repo-subject>:environment:staging`;
  - its permissions are `ssm:SendCommand` on `document/csa-staging-plan`, `document/csa-staging-apply` and `managed-instance/<managed-instance-id>` (placeholders `<region>` and `<account-id>`), plus `ssm:GetCommandInvocation` on `*`. Nothing else.
- Produces in `ssm_run.sh`: a required environment variable `DEPLOY_TARGET` (`staging|production`, exit 64 otherwise, nothing sent).
  - production: unchanged;
  - staging: `plan` sends `csa-staging-plan`. `apply` sends `csa-staging-apply` with `{sha, migrations (omitted when empty), pythonDigest, frontendDigest}`, and refuses a non-empty host digest (`staging takes no host digest`, exit 64).
- Produces in `parse_plan.sh`: a required environment variable `DEPLOY_TARGET`.
  - production still needs exactly one `Host settings digest:` line;
  - staging needs none and prints `host_digest=` empty;
  - both targets refuse (exit 65) any line starting `Stopped at ` or `Missing secret files: `;
  - staging also refuses any line starting `WARNING: ` that contains `this is a downgrade`, with `the plan is a downgrade; staging never goes back by itself (roll back by hand, deploy/STAGING_PIPELINE.md)`;
  - production keeps accepting a downgrade line, because the owner's approval decides.

- [ ] **Step 1: Write the failing tests** in `tests/test_staging_deploy_assets.py`:
  - `test_staging_documents_have_the_exact_patterns_and_fix_their_action`;
  - `test_staging_documents_run_the_staging_entry_as_csa_deploy_with_an_empty_host_digest`;
  - `test_staging_document_patterns_reject_injection` (every `BAD_VALUES` entry);
  - `test_staging_document_timeout_matches_production`;
  - `test_staging_trust_admits_only_the_staging_environment` (refuses `production`, `production-plan`, `ref:refs/heads/main` and another repository's subject);
  - `test_staging_permissions_name_only_the_two_staging_documents_and_one_node`;
  - `test_staging_files_hold_no_account_node_region_or_address`;
  - `test_ssm_run_staging_sends_the_staging_documents_without_a_host_digest`;
  - `test_ssm_run_refuses_a_missing_or_unknown_deploy_target_and_sends_nothing`;
  - `test_ssm_run_staging_refuses_a_host_digest`;
  - `test_parse_plan_staging_reads_a_plan_without_a_host_digest`;
  - `test_parse_plan_refuses_stopped_and_missing_secret_lines` (both targets);
  - `test_parse_plan_staging_refuses_a_downgrade_and_production_accepts_it`;
  - `test_parse_plan_staging_shows_other_warnings_without_refusing` (a `could not compare` line).

  Use real `format_plan` output from a staging `Plan` (as `_plan_output` does) for the parse tests.
- [ ] **Step 2: Run** the new file, and the production file in the background; expect failures.
- [ ] **Step 3: Implement** the four JSON files and the two script changes. Update both scripts' header comments.
- [ ] **Step 4: Run** both files; all pass. Run `bash -n` on both scripts.
- [ ] **Step 5: Commit** `feat(deploy): staging documents and role, and per-target runner and plan parser`.

### Task 6: The staging gate and the record script

**Files:**
- Create: `deploy/staging/rollout_gate.sh`, `deploy/staging/record_staged.sh` (both mode 755, `set -euo pipefail`, `LC_ALL=C`, `valid` helper, `jq` for every JSON read and write)
- Test: create `tests/test_staging_gate_scripts.py` (reuse the `gh`/`curl` stubs and fixtures of `tests/test_production_resolve.py` by import)

**Interfaces:**
- Produces `rollout_gate.sh start`. Environment: `EVENT_NAME` (`workflow_run|workflow_dispatch`, else exit 1), `INPUT_SHA` (may be empty), and the environment `resolve_commit.sh` needs. It runs `DEPLOY_TARGET=staging deploy/production/resolve_commit.sh "$INPUT_SHA"` from its own directory's `../production`. It prints the resolver's five keys plus `proceed=true|false` and `reason=<one line>`:
  - resolver exit 0 on `workflow_run` with a non-empty `staged_on`: `proceed=false`, reason `main's tip <sha7> is already staged`;
  - resolver exit 0 otherwise: `proceed=true`, reason `rolling out <sha7>`;
  - resolver exit 3 on `workflow_run`: `proceed=false`, reason `nothing to roll out: <resolver message>`, exit 0;
  - resolver exit 3 on `workflow_dispatch`: the resolver's message, exit 1;
  - any other resolver exit: its message, exit 1, nothing on stdout.

  The reason is built from fixed text, sha7 and the resolver's own fixed messages only.
- Produces `rollout_gate.sh current <sha> <explicit>`. Environment: `GH_TOKEN`, `REPOSITORY`.
  - `explicit=true`: prints `current=true` and makes no call;
  - `explicit=false`: reads `gh api repos/$REPOSITORY/commits/main --jq .sha`. If it equals `<sha>`, it prints `current=true`. Otherwise it prints `current=false` and, on stderr, `superseded: main moved to <tip7>; the run for the newer commit rolls it out`;
  - a malformed or unreadable tip, or a bad argument, exits 1.
- Produces `record_staged.sh <sha>`. Environment: `GH_TOKEN`, `REPOSITORY`.
  - It POSTs `repos/$REPOSITORY/deployments` with exactly the body `sr.record_staged` sends: ref, environment `staging`, `auto_merge` false, `required_contexts` `[]`, `transient_environment` false, `production_environment` false, description `staging rollout`;
  - it validates the returned numeric id, then POSTs `.../deployments/<id>/statuses` with state `success`, environment `staging`, `auto_inactive` false and description `staging rollout finished`;
  - success prints `Recorded <sha7> as staged`;
  - any failure prints `staging is fine but the staged record was not written; use Re-run failed jobs on this run` and exits 1;
  - `GH_TOKEN` is never printed.

- [ ] **Step 1: Write the failing tests:**
  - `test_start_proceeds_for_a_ready_tip_and_prints_all_keys`;
  - `test_start_on_workflow_run_skips_an_already_staged_tip`;
  - `test_start_on_dispatch_rolls_out_an_already_staged_commit_again`;
  - `test_start_on_workflow_run_turns_not_ready_into_nothing_to_roll_out` (ci in progress; images missing);
  - `test_start_on_dispatch_fails_when_not_ready`;
  - `test_start_fails_closed_on_any_other_refusal_with_nothing_on_stdout`;
  - `test_start_refuses_an_unknown_event_and_a_bad_sha_input` (newline, uppercase, 39 characters);
  - `test_current_true_false_and_explicit`;
  - `test_current_fails_on_an_unreadable_tip`;
  - `test_record_sends_the_same_bodies_as_the_rollout_core` (compare with the body `sr.record_staged` sends to a fake runner);
  - `test_record_refuses_a_malformed_id_and_reports_the_retry_line`;
  - `test_scripts_are_bash_strict_quoted_and_executable` (the quote checker).
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_staging_gate_scripts.py -q`; expect failures.
- [ ] **Step 3: Implement** both scripts.
- [ ] **Step 4: Run** the file and `tests/test_production_resolve.py`; pass.
- [ ] **Step 5: Commit** `feat(deploy): the staging gate (newest wins) and the workflow's staged record`.

### Task 7: The `deploy-staging` workflow

**Files:**
- Create: `.github/workflows/deploy-staging.yml`, `tests/test_deploy_staging_workflow.py` (import `_children`, `_jobs`-style helpers, `PIN`, `_run_blocks`, `_run_step` and the stubs from `tests.test_production_deploy_assets`; generalise a helper there to take the workflow path if needed, without changing its production assertions)
- Modify: `.github/workflows/deploy-production.yml` (`DEPLOY_TARGET: production` in both jobs' `env`), `tests/test_production_deploy_assets.py` (the new env entry)

**Interfaces:**
- Consumes: Tasks 3, 5, 6.
- Produces `deploy-staging.yml`. Its header comment says what it does, where the secrets live, the pause switch and the newest-wins rules.
  - `on:`: `workflow_run` (`workflows: [publish-staging-images]`, `types: [completed]`, `branches: [main]`) and `workflow_dispatch` with one input, `sha` (`Full commit SHA on main (empty = the tip of main)`, optional string).
  - Top-level `permissions: contents: read`.
  - Workflow-level `concurrency`, `cancel-in-progress: false`, with the group exactly `${{ github.ref == 'refs/heads/main' && (github.event_name == 'workflow_dispatch' || (github.event.workflow_run.conclusion == 'success' && vars.STAGING_AUTO_PAUSED != 'true')) && 'deploy-staging' || format('deploy-staging-idle-{0}', github.run_id) }}`.
  - Job `paused`:
    - `if` main and `workflow_run` and conclusion success and `vars.STAGING_AUTO_PAUSED == 'true'`;
    - `permissions: {}`, no `uses`;
    - one step writes `Automatic staging rollouts are paused (repository variable STAGING_AUTO_PAUSED). Nothing was sent. Run deploy-staging by hand, or set the variable to false.` to the summary.
  - Job `plan`:
    - `if` main and (dispatch, or (`workflow_run` with conclusion `success`, `head_branch == 'main'`, `head_repository.full_name == github.repository` and the pause not `'true'`));
    - `environment: {name: staging, deployment: false}`;
    - `timeout-minutes: 130`;
    - permissions `actions: read`, `contents: read`, `deployments: read`, `id-token: write`, `packages: read`;
    - env `ROLE_ARN` (`secrets.STAGING_DEPLOY_ROLE_ARN`), `AWS_REGION` (`vars.STAGING_DEPLOY_REGION`), `INSTANCE_ID` (`secrets.STAGING_INSTANCE_ID`), `DEPLOY_TARGET: staging`.
    - Steps:
      1. checkout `main` (`persist-credentials: false`);
      2. `Decide what to roll out` (`id: gate`, `rollout_gate.sh start >> "$GITHUB_OUTPUT"`, with `EVENT_NAME`, `INPUT_SHA`, `GH_TOKEN`, `REPOSITORY`, `IMAGE_NAMESPACE`, `REGISTRY_USER` and `REGISTRY_TOKEN` from env), and the reason written to the summary;
      3. every later step has `if: steps.gate.outputs.proceed == 'true'`: `Require the deploy variables` (fails naming the missing secret or variable, before any AWS call), `configure-aws-credentials` (`role-session-name: deploy-staging-plan`, `mask-aws-account-id: true`), `Plan on the staging node` (`ssm_run.sh plan`, the digests in env, stop-commands brackets, the plan in the summary under `### Staging plan for <sha>`), and `Read the plan` (`id: parse`, `parse_plan.sh`).
    - Job outputs: `proceed`, `sha`, `explicit`, `migrations`, `python_digest`, `frontend_digest`.
  - Job `apply`:
    - `needs: plan`, with `if` main and `needs.plan.outputs.proceed == 'true'`;
    - the same environment block and timeout;
    - permissions `contents: read`, `id-token: write`.
    - Steps:
      1. `Require the deploy variables`;
      2. `Check the commit is still current` (`id: current`, `rollout_gate.sh current "$SHA" "$EXPLICIT" >> "$GITHUB_OUTPUT"`, after a checkout of `main` that comes first). A `current=false` writes `superseded` to the summary and ends the job green;
      3. every later step has `if: steps.current.outputs.current == 'true'`: AWS credentials (`deploy-staging-apply`), `Apply on the staging node` (`ssm_run.sh apply "$SHA" "$MIGRATIONS"`, digests in env, stop-commands, summary), and `Mark applied` (`id: done`, `echo applied=true >> "$GITHUB_OUTPUT"`), which is the last step with no `continue-on-error` anywhere.
    - Job output: `applied: ${{ steps.done.outputs.applied }}`.
  - Job `record`:
    - `needs: [plan, apply]`, with `if` main and `needs.apply.outputs.applied == 'true'`;
    - no `environment`;
    - permissions `contents: read`, `deployments: write`;
    - steps: checkout `main`, then `record_staged.sh "$SHA"` with `GH_TOKEN` and `REPOSITORY` in env.
  - No run text contains `${{ inputs.`, `${{ github.event.` or `workflow_run.head_sha`.

- [ ] **Step 1: Write the failing tests:**
  - `test_triggers_permissions_concurrency_and_pins` (exact top-level keys; exact job permission sets; one `concurrency:` with the exact group expression; every `uses:` matches `PIN` and equals deploy-production's pins);
  - `test_plan_and_apply_use_staging_without_a_deployment_and_record_names_no_environment`;
  - `test_every_job_runs_only_for_main_and_plan_honours_the_pause_only_for_workflow_run`;
  - `test_no_run_text_interpolates_inputs_or_event_fields` (and `head_sha` appears nowhere);
  - `test_missing_variables_fail_before_the_aws_step` (extract and run the step with each value missing);
  - `test_instance_output_is_bracketed_in_stop_commands` (both instance steps; reuse the production checks);
  - `test_skip_reasons_end_the_plan_job_green_and_skip_every_later_step` (structural: every step after the gate carries the `proceed` condition);
  - `test_apply_checks_the_tip_before_aws_and_a_superseded_run_never_marks_applied` (extract and run with a stub `gh` returning a moved tip; assert `current=false` and that the AWS, apply and mark steps all carry the condition);
  - `test_record_runs_only_after_applied_true_and_applied_is_set_only_after_the_apply_step`;
  - `test_scripts_come_from_a_main_checkout_with_deploy_target_staging`;
  - `test_the_paused_job_has_no_permissions_and_sends_nothing`;
  - `test_deploy_production_sets_deploy_target_production` (in the production file).
- [ ] **Step 2: Run** the new file, and the production file in the background; expect failures.
- [ ] **Step 3: Implement** the workflow and the production env lines. Look up GitHub's documentation for `deployment: false` on a job's environment (WebFetch on docs.github.com), quote the paragraph in the task report, and keep the setting only if it is documented. If it is not, drop it and say so: the description filter still keeps the gate correct (Review Focus 3).
- [ ] **Step 4: Run** both files; all pass. Validate the YAML with `.venv/bin/python -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" .github/workflows/deploy-staging.yml`, and `actionlint` if it is installed (do not install it).
- [ ] **Step 5: Commit** `feat(deploy): deploy-staging rolls every green merge out to staging and records it`.

### Task 8: `deploy/aws/setup-target.sh` for both targets

**Files:**
- Create: `deploy/aws/setup-target.sh` (mode 755), `tests/test_aws_setup_target.py` (a stateful fake `aws` on `PATH`: a JSON state file of providers, roles, inline policies, documents with versions, activations and nodes; it logs argv and answers like the AWS CLI's JSON)

**Interfaces:**
- Produces `setup-target.sh <staging|production> <activate|apply|check> --region <region> [--node <mi-id>] [--subject <prefix>]`. It is run in CloudShell from a `git clone --depth 1` of the repository and reads its templates relative to its own path.
- Validation runs before any AWS call:
  - region `^[a-z]{2}(-[a-z]+)+-[0-9]$`;
  - node `^mi-[0-9a-f]{17}$` (required for `apply` and `check`);
  - subject `^repo:[A-Za-z0-9_.@/-]+$` (required for `apply` and `check`);
  - the account comes from `aws sts get-caller-identity` and must match `^[0-9]{12}$`.
- Fill and guard:
  - the templates are copied to a `mktemp -d` directory (removed on exit; never written under the repository);
  - `<region>`, `<account-id>`, `<managed-instance-id>`, `<oidc-provider-arn>` and `<repo-subject>` are filled;
  - the script refuses if any `<...>` placeholder remains.
- Targets (a table at the top of the script):
  - `production`: node role `csa-prod-ssm-node` (instance name `csa-prod`); roles `csa-prod-deploy-plan` (`iam/plan-trust-policy.json`, inline policy `send-plan-document` from `iam/plan-permissions-policy.json`) and `csa-prod-deploy-apply` (`send-apply-document`); documents `csa-production-plan` and `csa-production-apply` from `deploy/production/`;
  - `staging`: node role `csa-staging-ssm-node` (instance name `csa-staging`); role `csa-staging-deploy` (inline policy `send-staging-documents`); documents `csa-staging-plan` and `csa-staging-apply` from `deploy/staging/`.
- `activate`:
  - creates the node role if it is missing, with trust `ssm.amazonaws.com` and conditions `aws:SourceAccount` and `aws:SourceArn` `arn:aws:ssm:<region>:<account>:*`, as in `deploy/PRODUCTION.md` step 1;
  - attaches `AmazonSSMManagedInstanceCore`;
  - creates one activation (`--registration-limit 1`, expiry in 4 hours);
  - prints the activation id and code once, with `type the code at the hidden prompt on the host; never paste it anywhere else`.
- `apply` is idempotent:
  - the OIDC provider for `token.actions.githubusercontent.com` (client id `sts.amazonaws.com`) is created only if it is missing;
  - each role is created if missing, else `update-assume-role-policy`; then `put-role-policy`;
  - each document is created if missing. If present, its default version's content (normalised with `jq -S`) is compared with the filled file. When it differs: `update-document --document-version '$LATEST'`, then `update-document-default-version` to the returned version. When identical: `unchanged`, with no update call;
  - it ends by printing the role ARNs (CloudShell only).
- `check` is read-only (`get-*`, `list-*` and `describe-*` only):
  - it reports `ok`, `missing` or `differs` for the provider, each role's trust and inline policy, each document's default content, and the node's `PingStatus` (`Online`);
  - it exits 0 only when everything is `ok`.
- Output: every step prints `[<n>/<total>] <what>: <result>` before and after its calls. Nothing else of AWS's output is printed, apart from the role ARNs (apply) and the activation id and code (activate).

- [ ] **Step 1: Write the failing tests:**
  - `test_inputs_are_validated_before_any_aws_call` (parametrised: empty and malformed region, node, subject and account);
  - `test_apply_on_an_empty_account_creates_provider_roles_policies_and_documents_in_order` (both targets);
  - `test_apply_twice_makes_no_create_or_update_call_the_second_time`;
  - `test_a_changed_document_is_updated_and_made_the_default`;
  - `test_check_makes_only_read_calls_and_reports_each_missing_or_differing_item` (exit 1), and `test_check_passes_on_a_complete_target` (exit 0);
  - `test_filled_files_have_no_placeholder_and_never_land_in_the_repository`;
  - `test_production_names_match_the_runbook` (role, policy and document names equal `deploy/PRODUCTION.md` steps 3 and 4);
  - `test_the_staging_role_trusts_only_staging_and_names_only_staging_documents` (read the filled file the fake received);
  - `test_activate_creates_the_node_role_once_and_a_single_use_activation`;
  - `test_every_step_is_printed_and_nothing_else_from_aws`;
  - `test_script_is_bash_strict_quoted_and_executable`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_aws_setup_target.py -q`; expect failures.
- [ ] **Step 3: Implement.** It uses no `eval` and no `sed` on a JSON value with an unvalidated replacement: every value is validated against its pattern first, and the replacement escapes `|` and `&`.
- [ ] **Step 4: Run** the file; pass. Run `bash -n`.
- [ ] **Step 5: Commit** `feat(deploy): setup-target.sh creates or checks the AWS side of a deploy target`.

### Task 9: The compose project moves to the dedicated checkout without touching a volume

**Files:**
- Modify: `tests/test_staging_compose.py`
- Test: the same file (the docker test is skipped where the docker CLI is missing, like its neighbours)

**Interfaces:**
- Produces two guards:
  - `test_app_services_mount_nothing_from_the_checkout` (YAML only, no docker): for every service in `STAGING.app_services`, across the three staging compose files, every `volumes:` entry is a named volume declared at the top level, with no `./`, `../` or absolute host source. No declared volume has a `name:` override, so compose names it `<project>_<volume>` whatever the directory. The only relative bind mounts in the three files belong to `staging-bot-setup`, which the rollout never recreates. Secrets are `file:` entries under `${CSA_STAGING_SECRET_DIR}`, absolute by construction;
  - `test_the_staging_stack_renders_the_same_from_a_copy_of_the_checkout` (docker CLI): render `docker compose --env-file /dev/null --project-name csa-staging -f ... --profile staging-approved --profile pipeline-approved config --format json` from the repository and from a copy in `tmp_path`. The copy holds `deploy/`, `scripts/staging_test_bot.py` and `db/sql/staging`. Both renders use `_compose_env` with `_make_synthetic_secrets`, and stand-in image refs. Replace the copy's root with the repository's root, then assert the two documents are equal, and that the volume names are exactly `csa-staging_<name>` for every declared volume.

- [ ] **Step 1: Write both tests**; they should pass on the current files. If one fails, stop and report: the move is not safe as designed.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_staging_compose.py -q`.
- [ ] **Step 3: Read-only check on this host** (it is the home server). For each of `gateway`, `worker`, `delivery`, `frontend` and `training-worker`, run `docker inspect --format '{{.Name}} {{range .Mounts}}{{.Type}}:{{.Name}}->{{.Destination}} {{end}}' csa-staging-<service>-1` and `docker volume ls --filter label=com.docker.compose.project=csa-staging --format '{{.Name}}'`. Put the output in the task report. Every mount must be `volume` with a `csa-staging_` name, or a `bind` of a secret file whose destination is under `/run/secrets`. No source path may be printed or read beyond what `inspect` shows. Do not run anything else against the stack.
- [ ] **Step 4:** If Step 3 shows a mount the tests did not predict, stop and report it.
- [ ] **Step 5: Commit** `test(staging): the stack renders the same from any checkout and mounts no checkout path`.

### Task 10: Runbooks, skills, PR template, spec

**Files:**
- Modify: `deploy/STAGING_PIPELINE.md`, `deploy/PRODUCTION.md`, `.claude/skills/staging-rollout/SKILL.md`, `.claude/skills/production-deploy/SKILL.md`, `docs/superpowers/specs/2026-11-13-cd-pipeline-design.md`
- Create: `.github/pull_request_template.md`
- Test: the existing doc and skill assertions in `tests/test_staging_rollout.py`, `tests/test_rollout_production.py` and `tests/test_production_deploy_assets.py`; add assertions as below

**Interfaces:** none produced.

- [ ] **Step 1: Write failing assertions:**
  - `deploy/STAGING_PIPELINE.md`'s new opening subsection under "Routine rollouts", "Automatic rollouts (deploy-staging)", must say:
    - every green merge rolls out by itself;
    - what each run outcome means: `nothing to roll out`, `already staged`, `superseded`, `paused`, `Stopped at lock`, a missing secret file, a downgrade refusal, `Recorded <sha7> as staged`, the record retry (`Re-run failed jobs`);
    - how to pause and unpause (`gh variable set STAGING_AUTO_PAUSED --body true|false`) and to catch up after a pause (dispatch with an empty `sha`);
    - that the record is written by the workflow;
    - that a migration stops all services on every such merge.
  - A new section "Host setup for automatic rollouts" holds Part B steps 3 to 14, as the permanent record.
  - The manual steps stay, retitled "Manual rollout (break-glass)", with "pause first, unpause after", and the note that a manual run writes the record with the owner's `gh` login.
  - `deploy/PRODUCTION.md`:
    - "Deploying from GitHub" says the staged record comes from `deploy-staging` and only a `staging rollout` description counts;
    - "One-time setup" says `deploy/aws/setup-target.sh production check` verifies steps 1, 3 and 4;
    - a new section "Reinstalling the shared entry script" gives the config file (the six keys with today's production values from `deploy/production/deploy.conf.example`), `sudo install -m 0644 -o root -g root`, then the script install, in that order;
    - it mentions the core lock `/run/lock/csa-prod-rollout.lock`.
  - Both skills:
    - the staging skill says rollouts are automatic. It checks the latest run (`gh run list --workflow deploy-staging.yml --limit 5`, `gh run view <id> --log-failed`) and reports outcomes verbatim. It dispatches (`gh workflow run deploy-staging.yml --ref main [-f sha=<sha>]`) only after the owner's go. It uses the manual steps only when the owner asks, pausing first;
    - the production skill says `not staged yet; wait for deploy-staging` means: check the latest deploy-staging run.
  - The PR template has a line `- [ ] Needs host preparation before merge (new secret file, switch or host step): <what>`.
- [ ] **Step 2: Run** the three test files (production in the background); expect failures.
- [ ] **Step 3: Edit.** Leave the publish-approval sentences as they are (PR 3). In the spec, add a "Changes made while building PR 2" list: every item of "Plan decisions" above, including the three-level `workflow_run` limit for PR 3.
- [ ] **Step 4: Run** the files; pass. Run `make lint`.
- [ ] **Step 5: Commit** `docs(deploy): automatic staging rollouts, host setup, entry config, skills`.

---

# Part B: Owner host preparation (handed to the owner by the controller)

Each step says **who** runs it and **where**. "Owner" is Amsyar. "Dev" is the other developer, who owns the home server, and who must be present for every home-server step. Placeholders: `<region>` is the production region, `<node-id>` is the `mi-...` id from step 6, and `<subject>` comes from step 8. Nothing printed here goes into the repository or a chat, apart from yes/no results and sha7 values. In every block, `REPO=amsyrzmi/CSAV2` (change it if the repository moves).

**Before PR 2 merges**

1. **Owner, own terminal (GitHub):** pause automatic rollouts, so merges made before the preparation do not each produce a red run.
   ```bash
   gh variable set STAGING_AUTO_PAUSED --repo amsyrzmi/CSAV2 --body true
   gh variable list --repo amsyrzmi/CSAV2 | grep STAGING_AUTO_PAUSED
   ```
2. **Owner, GitHub settings (web):** Settings, Environments, `staging`. No required reviewers. Under "Deployment branches and tags", choose "Selected branches and tags" with one rule for the branch `main`. Check from a terminal:
   ```bash
   gh api repos/amsyrzmi/CSAV2/environments/staging --jq '{protection_rules, deployment_branch_policy}'
   gh api repos/amsyrzmi/CSAV2/environments/staging/deployment-branch-policies --jq '.branch_policies[].name'
   ```
   The second command must print only `main`.

**After PR 2 merges (pause still on)**

3. **Owner + Dev, home server (sudo):** take the baseline memory and check the prerequisites.
   ```bash
   free -m
   stat -c '%a %U' /run/lock
   command -v snap docker setfacl || true
   ```
   `/run/lock` must be `1777 root`. If `setfacl` is missing: `sudo apt-get install -y acl`.
4. **Owner, AWS CloudShell (admin login):** create the node role and a single-use activation.
   ```bash
   git clone --depth 1 https://github.com/amsyrzmi/CSAV2.git ~/csa-setup && cd ~/csa-setup
   deploy/aws/setup-target.sh staging activate --region <region>
   ```
   Keep the tab open. The activation id and code are valid for 4 hours and for one registration.
5. **Dev (or Owner with Dev present), home server (sudo):** install and register the agent. Type the code at the hidden prompt.
   ```bash
   snap list amazon-ssm-agent || sudo snap install amazon-ssm-agent --classic
   sudo snap stop amazon-ssm-agent
   read -rp "Activation id: " ACT_ID
   read -rsp "Activation code: " ACT_CODE; echo
   sudo /snap/amazon-ssm-agent/current/amazon-ssm-agent -register -id "$ACT_ID" -code "$ACT_CODE" -region <region>
   unset ACT_ID ACT_CODE
   sudo snap start amazon-ssm-agent
   sleep 60; ps -eo rss=,args= | grep -E '[s]sm-agent'; free -m
   ```
   Stop, and tell the controller, if the agent processes together use more than 100 MB resident (first column, in KB), or `available` dropped by more than 150 MB from step 3.
6. **Owner, CloudShell:** find the node and delete the used activation.
   ```bash
   aws ssm describe-instance-information --region <region> --query 'InstanceInformationList[].[InstanceId,PingStatus,ComputerName]' --output table
   aws ssm delete-activation --region <region> --activation-id <ActivationId from step 4>
   ```
   Note the new `mi-...` id with `PingStatus` `Online` as `<node-id>`.
7. **Owner + Dev, home server (sudo):** create the deploy user and grant search-only access to the secrets. The `x` bit lets `csa-deploy` see that a file exists; it cannot list a directory or read any file. Joining `docker` is equivalent to root on this machine; Dev agrees to this.
   ```bash
   sudo adduser --system --group --home /srv/csa-staging-deploy --shell /usr/sbin/nologin csa-deploy
   sudo usermod -aG docker csa-deploy
   sudo setfacl -m u:csa-deploy:x /srv/csa-staging
   sudo find /srv/csa-staging/secrets -type d -exec setfacl -m u:csa-deploy:x {} +
   sudo -u csa-deploy test -e /srv/csa-staging/secrets/database/staging_owner_password && echo "exists: ok"
   sudo -u csa-deploy test -r /srv/csa-staging/secrets/database/staging_owner_password && echo "READABLE: stop" || echo "not readable: ok"
   ls -l /srv/csa-staging/registry/config.json 2>&1 | cut -c1-10
   ```
   The last line must say `No such file` (it did on 2026-11-17; the images are public). If the file exists, stop and tell the controller. Later, whenever you create a new secret **directory**, re-run the `find` line. A forgotten one shows up as a red run with `Missing secret files`.
8. **Owner, own terminal:** read the OIDC subject prefix for step 12.
   ```bash
   gh api repos/amsyrzmi/CSAV2/actions/oidc/customization/sub --jq .sub_claim_prefix
   ```
9. **Owner + Dev, home server (sudo):** create the dedicated checkout and the Python 3.12 environment. The host's `python3` is 3.14, so uv builds a 3.12 environment under `/opt`, readable by `csa-deploy`.
   ```bash
   sudo -u csa-deploy git clone https://github.com/amsyrzmi/CSAV2.git /srv/csa-staging-deploy/CSAV2
   sudo -u csa-deploy git -C /srv/csa-staging-deploy/CSAV2 checkout --detach origin/main
   sudo -u csa-deploy git -C /srv/csa-staging-deploy/CSAV2 status --porcelain --untracked-files=no
   sudo install -d -m 0755 -o root -g root /opt/csa-staging-deploy
   sudo env UV_PYTHON_INSTALL_DIR=/opt/csa-staging-deploy/python /home/amsyrzmi/.local/bin/uv venv --python 3.12 /opt/csa-staging-deploy/venv
   sudo env UV_PYTHON_INSTALL_DIR=/opt/csa-staging-deploy/python /home/amsyrzmi/.local/bin/uv pip install --python /opt/csa-staging-deploy/venv/bin/python -r /srv/csa-staging-deploy/CSAV2/deploy/production/requirements.txt
   sudo -u csa-deploy /opt/csa-staging-deploy/venv/bin/python -c "import sys, psycopg; print(sys.version.split()[0], psycopg.__version__)"
   ```
   `status` must print nothing, and the last line must start with `3.12`.
10. **Owner + Dev, home server (sudo):** install the entry script, its config and the log directory. The config is the one in `deploy/staging/deploy.conf.example`.
    ```bash
    printf '%s\n' TARGET=staging DEPLOY_USER=csa-deploy CHECKOUT=/srv/csa-staging-deploy/CSAV2 PYTHON=/opt/csa-staging-deploy/venv/bin/python LOCK=/run/lock/csa-staging-deploy.lock LOG_DIR=/var/log/csa-staging-deploy | sudo tee /usr/local/sbin/csa-staging-deploy.conf > /dev/null
    sudo chown root:root /usr/local/sbin/csa-staging-deploy.conf && sudo chmod 0644 /usr/local/sbin/csa-staging-deploy.conf
    sudo install -m 0755 -o root -g root /srv/csa-staging-deploy/CSAV2/deploy/production/deploy_entry.sh /usr/local/sbin/csa-staging-deploy
    sudo install -d -m 0750 -o csa-deploy -g csa-deploy /var/log/csa-staging-deploy
    ```
11. **Owner + Dev, home server:** run a plan by hand, as Systems Manager will. First, in `/home/amsyrzmi/CSAV2` (pulled to the merged `main`), resolve the tip and its digests. This is read-only:
    ```bash
    cd /home/amsyrzmi/CSAV2 && git pull --ff-only origin main
    GH_TOKEN="$(gh auth token)" REPOSITORY=amsyrzmi/CSAV2 IMAGE_NAMESPACE=ghcr.io/amsyrzmi DEPLOY_TARGET=staging deploy/production/resolve_commit.sh
    sudo runuser -u csa-deploy -- /usr/local/sbin/csa-staging-deploy plan <sha> "" "" <python_digest> <frontend_digest>
    ```
    The plan must end with the apply command line, with no `Stopped at` and no `Missing secret files` line. Then record the volumes and mounts for comparison after the first run:
    ```bash
    docker volume ls --filter label=com.docker.compose.project=csa-staging --format '{{.Name}}' | sort > ~/csa-volumes-before.txt
    for s in gateway worker delivery frontend training-worker; do docker inspect --format '{{.Name}} {{range .Mounts}}{{.Type}}:{{.Name}}->{{.Destination}} {{end}}' "csa-staging-$s-1"; done > ~/csa-mounts-before.txt
    ```
12. **Owner, CloudShell:** create the staging role and documents, then check both targets.
    ```bash
    cd ~/csa-setup && git pull --ff-only
    deploy/aws/setup-target.sh staging apply --region <region> --node <node-id> --subject '<subject>'
    deploy/aws/setup-target.sh staging check --region <region> --node <node-id> --subject '<subject>'
    deploy/aws/setup-target.sh production check --region <region> --node <production node id> --subject '<subject>'
    ```
    The staging check must say `ok` on every line; copy the printed `csa-staging-deploy` ARN for step 13. The production check is read-only. If it shows `differs` or `missing`, do **not** run `production apply`; tell the controller.
13. **Owner, own terminal (GitHub):** set the secrets on `staging`, each typed at the hidden prompt, and the region variable.
    ```bash
    gh secret set STAGING_DEPLOY_ROLE_ARN --repo amsyrzmi/CSAV2 --env staging
    gh secret set STAGING_INSTANCE_ID --repo amsyrzmi/CSAV2 --env staging
    gh variable set STAGING_DEPLOY_REGION --repo amsyrzmi/CSAV2 --body <region>
    gh secret list --repo amsyrzmi/CSAV2 --env staging
    ```
14. **Owner, GitHub settings:** confirm that `IMAGE_NAMESPACE` is still `ghcr.io/amsyrzmi` (`gh variable list --repo amsyrzmi/CSAV2`).

# Part C: First run, watched live (acceptance)

Owner and Dev together. The pause stays **on** until C4.

- **C1. Owner, own terminal:** `gh workflow run deploy-staging.yml --repo amsyrzmi/CSAV2 --ref main`, then `gh run watch --repo amsyrzmi/CSAV2 $(gh run list --repo amsyrzmi/CSAV2 --workflow deploy-staging.yml --limit 1 --json databaseId --jq '.[0].databaseId')`. **Dev, home server:** `sudo tail -f /var/log/csa-staging-deploy/*.log` and `watch -n 10 free -m`.
- **C2. Pass criteria:**
  - the plan summary shows the tip, both images by digest, the pending migrations, and no `Stopped at` or `Missing secret files`;
  - apply ends with the `smoke <name>: ok` lines and `Rollout of <sha7> finished.`;
  - the record job prints `Recorded <sha7> as staged`;
  - then `gh api "repos/amsyrzmi/CSAV2/deployments?environment=staging&sha=<sha>" --jq '.[] | [.description, .creator.login] | @tsv'` shows exactly one `staging rollout` from `github-actions[bot]`, and no other deployment for that sha (this proves `deployment: false`).

  If GitHub created extra deployments, the gate is still correct, but tell the controller.
- **C3. Owner + Dev, home server:** redo the two commands from step 11 into `~/csa-volumes-after.txt` and `~/csa-mounts-after.txt`; `diff` against the before files must print nothing. Then run `docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' csa-staging-gateway-1`; it now names the dedicated checkout's `deploy` directory, which is expected. Check the agent memory again (`ps -eo rss=,args= | grep -E '[s]sm-agent'`).
- **C4. Owner:** unpause with `gh variable set STAGING_AUTO_PAUSED --repo amsyrzmi/CSAV2 --body false`. Watch the run the next merge starts by itself. It proves the three-level `workflow_run` chain, ci → publish → deploy-staging.
- **C5 (optional). Owner:** start `deploy-production` with an empty `sha`, read the plan (the tip is now staged by the workflow's record), and **reject** the approval.
- **If anything fails:** pause again (step 1), keep the red run's log, and roll staging out by hand as before (`deploy/STAGING_PIPELINE.md`, "Manual rollout (break-glass)"). To withdraw entirely: `sudo snap stop amazon-ssm-agent` on the home server, and `aws ssm deregister-managed-instance --region <region> --instance-id <node-id>` in CloudShell.
