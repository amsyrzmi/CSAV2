# Production Deploy Implementation Plan

> Superseded in places by the spec's "Changes made while building"; see the spec (docs/superpowers/specs/2026-11-10-production-deploy-design.md).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A manually started GitHub workflow plans a production deploy, waits for the owner's approval, and applies it through AWS Systems Manager, and production only accepts commits that staging has already rolled out.

**Architecture:** `scripts/staging_rollout.py` gains a frozen `Target` (staging, production) and a `--target` option; staging's behaviour stays byte-identical. A successful staging rollout records a GitHub Deployment in an environment named `staging`; the production target refuses any SHA without one. The workflow reaches the instance through Systems Manager Run Command with GitHub OIDC (no inbound port, no stored AWS keys), running a root-owned entry script that drives the rollout.

**Tech Stack:** Python 3.12 (pytest, ruff, pyright), bash, GitHub Actions, `gh` CLI, AWS Systems Manager (hybrid activation, custom command document), IAM OIDC.

**Spec:** `docs/superpowers/specs/2026-11-10-production-deploy-design.md`

## Global Constraints

- The staging rollout must behave exactly as today: `tests/test_staging_rollout.py` passes with no edits to existing tests (new tests are additive), and `python -m scripts.staging_rollout plan|apply` keeps working with `--target` defaulting to `staging`.
- No bypass flag or environment variable lets production take an unstaged SHA. The check runs inside the script, for `plan` and `apply`.
- The repository is public: no AWS account id, instance id, role ARN, bucket name, region, token or IP in any committed file. Hostnames only where `deploy/staging-rollout.env` already puts them. The role ARN and instance id are GitHub repository variables; bucket and region live in a host file on the instance.
- Secret values never appear in the repository, workflow logs or plan output. Nothing under the secret directory is opened (existence checks only).
- Every external command is an argv list through `Runner`; no `shell=True`. Tests use `FakeRunner` from `tests/test_staging_rollout.py`.
- Third-party GitHub actions are pinned to a 40-character commit SHA. Workflow permissions: `contents: read`, `deployments: read`, `id-token: write` only.
- Shell entry points validate arguments with these exact patterns: action `^(plan|apply)$`; sha `^[0-9a-f]{40}$`; migrations `^([0-9]{4}(,[0-9]{4})*)?$`.
- The deploy adds no resident process of its own; the Systems Manager agent is the only new one and its memory is measured first (Task 0).
- Lint gate before every commit: `uv run ruff check`, `uv run ruff format --check`, `uv run pyright`, `uv run lint-imports` (`make lint`). Locally run only the changed files' tests (CI runs the full suite).
- No automatic rollback, retry or restart anywhere. Stop at the first failure and print recovery lines.

## Review Focus

Failure modes the spec implies that no task would otherwise exercise, most likely first. Each has a pinning test in the task named.

1. The staged-record lookup fails or returns nothing useful (`gh` error, empty list, a deployment whose latest status is `failure`, malformed JSON): production must refuse, never pass. (Task 7)
2. The backup lock is held by the 6-hourly cron, so `backup.sh` exits 0 with a `skipped` line, or only an `ok` line from before the deploy started exists: the deploy must refuse before changing anything. (Task 5)
3. The host file on the instance contains a switch key, `CSA_PROD_GROQ_CHAT_URL`, a duplicate, an unknown key or a line without `=`: the load must refuse and name the line. (Task 3)
4. A SHA, action or migrations value carrying shell metacharacters, uppercase hex, a short SHA, whitespace or a newline reaches the entry script, the Systems Manager document or `ssm_run.sh`: it must be rejected before any command runs. (Tasks 8, 9)
5. Two replicas of `worker`: one healthy on the new image and one still on the old image (or one missing) must fail the rollout, and a host-file change of the replica count between `plan` and `apply` must be refused. (Tasks 2, 3)

---

## File Structure

| File | Responsibility |
| --- | --- |
| `scripts/rollout_targets.py` (new) | `Target` dataclass and the two instances `STAGING`, `PRODUCTION`; pure data, no I/O, no imports from `staging_rollout`. |
| `scripts/staging_rollout.py` (modify) | The rollout core, now target-aware; flags/host-file loading, backup pre-step, downgrade warning, staged gate, `record-staged`. Stays one module (the file is large already; do not split). |
| `deploy/production-rollout.env` (new) | Reviewed production switches (the eight `CSA_STAGING_*` keys) and any `CSA_PROD_*` tuning that today lives in the host file. |
| `deploy/production/backup.sh` (new) | Source of truth for the backup script, installed on the instance by copy. |
| `deploy/production/deploy_entry.sh` (new) | Root-owned entry script: validate, lock, fetch, checkout, run the rollout. |
| `deploy/production/ssm-document.json` (new) | The Systems Manager command document `csa-production-deploy`. |
| `deploy/production/ssm_run.sh` (new) | Sends the document, waits, prints output, exits with the command's status. Used by both workflow jobs. |
| `deploy/production/iam/trust-policy.json`, `deploy/production/iam/permissions-policy.json` (new) | The OIDC role's policies, with `<placeholders>` for account, region, instance id. |
| `deploy/production/requirements.txt` (new) | Pinned Python dependency of the deploy venv. |
| `.github/workflows/deploy-production.yml` (new) | The workflow. |
| `deploy/PRODUCTION.md`, `.claude/skills/staging-rollout/SKILL.md`, `.claude/skills/production-deploy/SKILL.md` (modify/new) | Docs and skills. |
| `tests/test_rollout_targets.py`, `tests/test_rollout_production.py`, `tests/test_rollout_gate.py`, `tests/test_production_deploy_assets.py` (new) | Tests; the existing `tests/test_staging_rollout.py` is the regression guard. |

## Decisions that change the spec slightly (flag to the owner at handoff)

- **A second GitHub environment, `production-plan`, with no required reviewer.** The `plan` job needs an OIDC token too, and a token's subject names the environment. If only `production` were trusted, `plan` would need the owner's approval before anyone could read the plan. Both environments are restricted to the `main` branch, and the trust policy accepts exactly those two subjects. `plan` still only runs read-only commands because the entry script maps the action to the rollout command.
- **Host file path:** `/srv/csa-prod/host.env` on the instance holds `CSA_PROD_S3_BUCKET`, `CSA_PROD_S3_REGION` (required) and optional tuning overrides.

---

### Task 0: Facts first (owner-gated; no code)

Read-only checks the later tasks depend on. The builder gives the owner numbered, machine-labelled, copy-ready commands (the owner runs them on the instance; never ask for secret values) and records the answers in the PR description.

- [ ] **Step 1 (instance): capture the live settings.** Print the non-secret lines of `~/csa-prod-env.sh` with values for every key except the bucket and region (print those two keys' names only): `grep -E '^export CSA_' ~/csa-prod-env.sh | sed -E 's/^(export CSA_PROD_S3_(BUCKET|REGION))=.*/\1=<hidden>/'`. Record which `CSA_PROD_*` keys are set and to what, and which `CSA_STAGING_*` switch values production uses. Task 4 copies them into `deploy/production-rollout.env` so the first scripted deploy changes no setting.
- [ ] **Step 2 (instance): capture the backup script and log format.** `cat /srv/csa-prod/backup/backup.sh` (it reads key files by path; confirm it prints no key value) and `tail -5 /srv/csa-prod/backup/backup.log`. Task 5 copies the script into the repository verbatim and parses this log format. The known facts: lines start with an ISO timestamp as the first space-separated field and contain ` ok ` on success; a lock-held run logs a `skipped` line and exits 0.
- [ ] **Step 3 (AWS docs, builder): cost.** Fetch the current AWS Systems Manager pricing page and the hybrid-activation page; record whether Run Command on a hybrid-registered node is free or priced in 2026 and the rate. If it is priced above a few cents a month for roughly ten deploys, stop and ask the owner before Task 8.
- [ ] **Step 4 (instance): agent memory.** After the owner installs the agent (Task 10 runbook, step 4), measure its resident memory: `ps -o rss,cmd -C amazon-ssm-agent` and `free -m`. If resident size is above 100 MB or `free` shows less than 250 MB available afterwards, stop and ask the owner.
- [ ] **Step 5 (builder): confirm IAM details** against the AWS service authorization reference and record them in the PR: the exact resource ARN forms for `ssm:SendCommand` (document ARN and managed-instance ARN), whether `ssm:GetCommandInvocation` and `ssm:ListCommandInvocations` accept resource-level scoping, and whether IAM accepts the `job_workflow_ref` OIDC condition key. Task 9 uses the answers.

Nothing in Tasks 1 to 7 depends on steps 3 to 5, so they run in parallel with the owner's answers.

---

### Task 1: The `Target` type, with staging unchanged

**Files:**
- Create: `scripts/rollout_targets.py`, `tests/test_rollout_targets.py`
- Modify: `scripts/staging_rollout.py` (constants become derived from `STAGING`; signatures grow a trailing `target: Target = STAGING` keyword in the next task, not this one)
- Test: `tests/test_rollout_targets.py`, `tests/test_staging_rollout.py`

**Interfaces:**
- Produces, in `scripts/rollout_targets.py`:
  ```python
  @dataclass(frozen=True)
  class Target:
      name: str                                   # "staging" | "production"
      project: str                                # compose project
      secret_dir: Path
      compose_files: tuple[str, ...]              # repo-relative, in --file order
      profiles: tuple[str, ...]
      image_owner: str                            # "ghcr.io/amsyrzmi"
      package_owner: str                          # "amsyrzmi" (for gh api /users/<owner>/packages/...)
      repo: str                                   # "amsyrzmi/CSAV2" (for deployments API)
      app_services: tuple[str, ...]               # recreate order
      stop_services: tuple[str, ...]
      replicas: Mapping[str, tuple[str, int]]     # service -> (env var, default count); {} = one each
      db_service: str
      db_name: str
      db_user: str
      flags_file: str                             # repo-relative
      required_keys: frozenset[str]
      optional_keys: frozenset[str]
      host_file: Path | None
      host_required_keys: frozenset[str]
      host_override_keys: frozenset[str]          # subset of optional_keys a host file may set
      migration_approval: tuple[str, str]
      staged_gate: bool                           # production True
      downgrade_warning: bool                     # production True
      pre_change: tuple[str, ...] | None          # argv run before anything changes
      live_checkout: str                          # path printed in rollback text

  STAGING: Target
  PRODUCTION: Target      # defined here with its final values in Task 4; in this task only STAGING
  def get_target(name: str) -> Target             # KeyError-free: raises ValueError("unknown target <name>")
  ```
- Consumes: nothing.

- [ ] **Step 1: Write the characterization test first.** In `tests/test_rollout_targets.py`, `test_staging_target_matches_current_module_constants`: assert `STAGING.project == sr.PROJECT`, `STAGING.compose_files == ("deploy/compose.staging-local-proposal.yml", "deploy/compose.staging-shared-caddy.yml", "deploy/compose.staging-pipeline.yml")`, `STAGING.profiles == ("staging-approved", "pipeline-approved")`, `STAGING.app_services == sr.APP_SERVICES`, `STAGING.required_keys == sr.FLAG_KEYS`, `STAGING.migration_approval == sr.MIGRATION_APPROVAL`, `STAGING.db_service == "staging-db"`, `STAGING.db_name == "csa_staging"`, `STAGING.staged_gate is False`, `STAGING.pre_change is None`. Also `test_get_target_rejects_unknown`: `get_target("prod")` raises `ValueError` whose text names `prod`.
- [ ] **Step 2: Capture a golden of today's behaviour before touching the module.** Add `test_staging_compose_argv_and_env_are_unchanged` to the same file: it asserts `sr.compose_base()` equals the exact list literal in today's `compose_base` (docker, `--host`, socket, `compose`, `--env-file`, `/dev/null`, `--project-name`, `csa-staging`, three `--file` pairs, two `--profile` pairs) and that `sr.build_env(...)` for fixed inputs equals the exact dict (copy the expectation from the existing `test_build_env` in `tests/test_staging_rollout.py`). Run `uv run pytest tests/test_rollout_targets.py tests/test_staging_rollout.py -q`; the `Target` tests fail on import, the golden test passes. This is the guard for the refactor.
- [ ] **Step 3: Implement `scripts/rollout_targets.py`** with the `Target` dataclass exactly as above and `STAGING` holding today's values (flags file `deploy/staging-rollout.env`, `optional_keys=frozenset()`, `host_file=None`, `live_checkout="/home/amsyrzmi/CSAV2"`, `replicas={}`, `package_owner="amsyrzmi"`, `repo="amsyrzmi/CSAV2"`). In `staging_rollout.py` replace the literal constants `PROJECT`, `SECRET_DIR`, `APP_SERVICES`, `STOP_SERVICES`, `FLAGS_FILE`, `FLAG_KEYS`, `MIGRATION_APPROVAL`, `GHCR_OWNER`, `LIVE_CHECKOUT` with assignments from `STAGING` (keep every module-level name, because the existing tests monkeypatch and import them).
- [ ] **Step 4: Run** `uv run pytest tests/test_rollout_targets.py tests/test_staging_rollout.py -q`. Expected: all pass, `tests/test_staging_rollout.py` untouched.
- [ ] **Step 5: Commit** `refactor(rollout): add the Target type; staging constants derive from it`.

---

### Task 2: Make the core target-aware, including replicas

**Files:**
- Modify: `scripts/staging_rollout.py`
- Test: `tests/test_rollout_production.py` (new), `tests/test_staging_rollout.py` (must pass untouched)

**Interfaces:**
- Consumes: `Target`, `STAGING` (Task 1).
- Produces: a trailing keyword `target: Target = STAGING` on `compose_base`, `build_env`, `load_flags` (Task 3 changes its other parameters), `running_images`, `applied_versions`, `secret_paths`, `plan_steps`, `build_plan`, `format_plan`, `apply`, and the private helpers `_running_image`, `_migrate`, `_recreate`, `_await_ready`, `_run_checks`, `_report_rollback`, `_report_recovery`, `_print_target`, `published_images`. Also:
  ```python
  def container_names(target: Target, env: Mapping[str, str], service: str) -> list[str]
  # "<project>-<service>-<n>" for n in 1..count; count = int(env[var]) if the service is in target.replicas and the variable is set and is a positive integer, else its default; a non-integer value raises RolloutError("flags", ...)
  ```
  `main(argv, runner)` still defaults the target to staging (the CLI option arrives in Task 4).

- [ ] **Step 1: Write failing tests** in `tests/test_rollout_production.py` using a local `Target` built with `dataclasses.replace(STAGING, name="production", project="csa-prod", secret_dir=Path("/srv/csa-prod/secrets"), compose_files=(*STAGING.compose_files, "deploy/compose.production.yml"), replicas={"worker": ("CSA_PROD_WORKER_REPLICAS", 2)}, db_name="csa_staging")`:
  - `test_compose_base_uses_the_target_project_files_and_profiles`: the argv contains `--project-name csa-prod` and the four `--file` pairs in order, and `compose_base()` with no argument still equals the staging golden.
  - `test_container_names_default_and_override`: worker gives `csa-prod-worker-1`, `csa-prod-worker-2`; with `{"CSA_PROD_WORKER_REPLICAS": "1"}` gives one; with `"x"` or `"0"` raises `RolloutError` with step `flags`; gateway always gives one name.
  - `test_recreate_unhealthy_path_checks_every_replica`: worker with two replicas; both report `healthy`, but replica 2's image ref is the old one: `_recreate` raises `RolloutError` (`recreate worker`, "not healthy on the new image"); with both on the expected ref it returns.
  - `test_messages_name_the_target`: a failed `applied_versions` for production says `could not read schema_migrations from production`; `pending_versions` ahead-of-checkout text starts `production has migrations this checkout lacks`; the unchanged-recovery line reads `Nothing on production was changed.`; for staging the three strings are unchanged.
  - `test_production_rollback_is_unknown_for_local_images`: with a running container whose image has no `ghcr.io/amsyrzmi/` digest, `format_plan` prints `unknown (a local image with no registry digest, or not running)` and `_report_recovery` for phase `recreating` prints a line saying the previous images are local and the manual runbook in `deploy/PRODUCTION.md` is the way back; the same input for staging still prints `unknown (not running)`.
- [ ] **Step 2: Run** `uv run pytest tests/test_rollout_production.py -q`. Expected: FAIL (`target` keyword not accepted).
- [ ] **Step 3: Implement the threading.** Every `PROJECT`, `SECRET_DIR`, `staging-db`, `csa_staging`, `-1` container name, compose file or profile use inside the functions listed above reads from `target`; `container_names` replaces the hard-coded `f"{PROJECT}-{service}-1"`. `_running_image` keeps inspecting replica 1 for the rollback record. `_recreate` calls `container_names(target, env, service)` in its unhealthy-wait loop and requires every name to be `healthy` and on the expected ref. Text that says "staging" becomes `target.name` only where the target is not staging-specific; `STAGING` output must stay byte-identical. The production-only rollback wording is selected by `target.name != "staging"`. `RESTART_COMMAND` becomes a function of the target with the same staging result.
- [ ] **Step 4: Run** `uv run pytest tests/test_rollout_production.py tests/test_rollout_targets.py tests/test_staging_rollout.py -q`. Expected: PASS, no edits to existing tests.
- [ ] **Step 5: Commit** `refactor(rollout): thread the target through the rollout core; verify every replica`.

---

### Task 3: Switch files with a host overlay

**Files:**
- Modify: `scripts/staging_rollout.py` (`load_flags`, `build_plan`, `Plan`)
- Create: `deploy/production-rollout.env` is written in Task 4; this task uses temp files
- Test: `tests/test_rollout_production.py`

**Interfaces:**
- Produces:
  ```python
  def load_flags(path: Path, target: Target = STAGING) -> dict[str, str]
  # repo file: every key in target.required_keys exactly once; keys in target.optional_keys allowed once; anything else, a duplicate, a missing '=' or a missing required key raises RolloutError("flags", ...) naming file and line
  def load_host_values(target: Target) -> dict[str, str]
  # {} when target.host_file is None; otherwise reads target.host_file: lines KEY=VALUE or `export KEY=VALUE`; blank and # lines skipped; every key in target.host_required_keys must be present; every key must be in host_required_keys | host_override_keys; duplicates, unknown keys and malformed lines raise RolloutError("flags", ...) naming the line; values are never echoed in the message
  ```
  `Plan` gains `host_values: dict[str, str]` (default `{}`, excluded from repr) and `build_env` merges, in order: repo flags, then host values (host wins only for `host_override_keys`; host required keys are simply added). `format_plan` prints a line `Host overrides: KEY, KEY` (names only, sorted, `none` when empty) for non-staging targets, and `apply` refuses with `host settings changed since plan; run plan again` if `host_values` differ from what `plan` printed. To make that checkable, `plan` prints a line `Host settings digest: <first 12 hex of sha256 over sorted KEY=VALUE lines>`, and `apply` accepts `--host-digest` (required for non-staging targets, absent for staging) and compares.

- [ ] **Step 1: Write failing tests**:
  - `test_host_file_refuses_switches_groq_url_unknown_duplicate_and_malformed` (parametrized): content `CSA_STAGING_INVITE_MAIL=true`, `CSA_PROD_GROQ_CHAT_URL=http://x`, `CSA_PROD_NOPE=1`, a duplicated `CSA_PROD_MEM_DB`, and a line `CSA_PROD_MEM_DB` each raise `RolloutError` whose message names the line number and never contains the value; a file missing `CSA_PROD_S3_REGION` is refused.
  - `test_host_file_accepts_export_prefix_comments_and_tuning`: parsed dict equals the expected.
  - `test_repo_file_allows_optional_tuning_keys_only_once`.
  - `test_host_override_wins_for_tuning_only`: repo says `CSA_PROD_MEM_DB=640m`, host says `1000m`; env has `1000m`; a host `CSA_STAGING_*` key never gets that far (refused above).
  - `test_apply_refuses_when_host_digest_differs` and `test_plan_prints_host_digest_and_override_names` (names only, no values).
  - `test_replica_count_change_between_plan_and_apply_is_refused`: host file sets `CSA_PROD_WORKER_REPLICAS=1` after plan with 2; the digest differs so apply stops at `confirm` before any docker command.
- [ ] **Step 2: Run** `uv run pytest tests/test_rollout_production.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the two loaders, the merge in `build_plan`, the `Host settings digest` line and the `--host-digest` argument. Staging's `load_flags(path)` output and error text stay exactly as today.
- [ ] **Step 4: Run** the three test files from Task 2. Expected: PASS.
- [ ] **Step 5: Commit** `feat(rollout): production switch file with an allow-listed host overlay`.

---

### Task 4: The production target and `--target`

**Files:**
- Modify: `scripts/rollout_targets.py` (define `PRODUCTION`, `get_target`), `scripts/staging_rollout.py` (`_parser`, `main`)
- Create: `deploy/production-rollout.env`
- Test: `tests/test_rollout_targets.py`

**Interfaces:**
- Produces `PRODUCTION: Target` with: `name="production"`, `project="csa-prod"`, `secret_dir=Path("/srv/csa-prod/secrets")`, `compose_files=(*STAGING.compose_files, "deploy/compose.production.yml")`, `profiles=("staging-approved", "pipeline-approved")`, `replicas={"worker": ("CSA_PROD_WORKER_REPLICAS", 2)}`, `flags_file="deploy/production-rollout.env"`, `required_keys` = the eight staging switch keys, `optional_keys` = `PROD_TUNING_KEYS` (below), `host_file=Path("/srv/csa-prod/host.env")`, `host_required_keys={"CSA_PROD_S3_BUCKET", "CSA_PROD_S3_REGION"}`, `host_override_keys=PROD_TUNING_KEYS | {"CSA_PROD_DB_IMAGE"}`, `staged_gate=True`, `downgrade_warning=True`, `live_checkout="/home/ubuntu/CSAV2"`, `pre_change` set in Task 5.
  ```python
  PROD_TUNING_KEYS = frozenset({"CSA_PROD_WORKER_REPLICAS", "CSA_PROD_MEM_DB", "CSA_PROD_MEM_GATEWAY", "CSA_PROD_MEM_WORKER", "CSA_PROD_MEM_DELIVERY", "CSA_PROD_MEM_FRONTEND", "CSA_PROD_MEM_EMBEDDINGS", "CSA_PROD_MEM_CLOUDFLARED", "CSA_PROD_PG_SHARED_BUFFERS", "CSA_PROD_PG_EFFECTIVE_CACHE", "CSA_PROD_PG_WORK_MEM", "CSA_PROD_PG_MAINTENANCE_MEM", "CSA_PROD_PG_MAX_CONNECTIONS"})
  ```
  CLI: `python -m scripts.staging_rollout [--target staging|production] plan` and `... apply --sha <40 hex> [--migrations a,b] [--host-digest <12 hex>]`. `--target` default `staging`; `get_target` supplies the `Target`. A non-staging apply without `--host-digest` is an argparse error.

- [ ] **Step 1: Write failing tests** in `tests/test_rollout_targets.py`:
  - `test_every_compose_prod_variable_is_accounted_for`: regex `CSA_PROD_[A-Z0-9_]+` over `deploy/compose.production.yml`; every name is in `PROD_TUNING_KEYS`, `PRODUCTION.host_required_keys`, `{"CSA_PROD_DB_IMAGE"}` or the explicit excluded set `{"CSA_PROD_GROQ_CHAT_URL"}`. A new variable in the compose file fails this test until someone decides where it lives.
  - `test_groq_url_is_in_no_allow_list`: not in `optional_keys`, `host_override_keys` or `host_required_keys`.
  - `test_production_env_file_loads`: `load_flags(REPO_ROOT / PRODUCTION.flags_file, PRODUCTION)` returns the eight switches, and no hostname other than the two the staging file already commits.
  - `test_main_target_option`: `main(["--target", "production", "plan"], runner)` builds its first docker/compose command with `--project-name csa-prod`; `main(["plan"], runner)` uses `csa-staging`; `apply --target production` without `--host-digest` exits with argparse error 2.
- [ ] **Step 2: Run** `uv run pytest tests/test_rollout_targets.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `PRODUCTION`, `get_target`, the parser changes, and `deploy/production-rollout.env` filled with the values captured in Task 0 step 1 (the eight switches; add any `CSA_PROD_*` tuning key the instance currently sets). If Task 0 shows a key not in `PROD_TUNING_KEYS`, stop and ask.
- [ ] **Step 4: Run** the four test files. Expected: PASS.
- [ ] **Step 5: Commit** `feat(rollout): production target, switch file and --target option`.

---

### Task 5: Backup before anything changes

**Files:**
- Create: `deploy/production/backup.sh` (verbatim copy of the instance's script from Task 0 step 2), `tests/test_production_deploy_assets.py`
- Modify: `scripts/rollout_targets.py` (`PRODUCTION.pre_change`), `scripts/staging_rollout.py`
- Test: `tests/test_rollout_production.py`, `tests/test_production_deploy_assets.py`

**Interfaces:**
- Produces in `staging_rollout.py`:
  ```python
  BACKUP_LOG = Path("/srv/csa-prod/backup/backup.log")
  BACKUP_INSTALLED = Path("/srv/csa-prod/backup/backup.sh")
  @dataclass(frozen=True)
  class BackupResult:
      timestamp: str            # as logged
      object_name: str          # "backups/csa_staging-<timestamp>.dump"
  def run_pre_change(runner: Runner, target: Target, repo: Path, *, now: Callable[[], datetime] = ..., log_path: Path = BACKUP_LOG, installed: Path = BACKUP_INSTALLED) -> BackupResult | None
  # None when target.pre_change is None. Otherwise: refuse (RolloutError("backup", ...)) if the installed script's bytes differ from repo/deploy/production/backup.sh; record start = now(); run target.pre_change argv (never sudo); non-zero exit refuses; then read the LAST line of log_path; require it to contain ` ok ` and its first field (ISO timestamp) to be later than `start`; otherwise refuse with the line's text. Build object_name from that timestamp using the same format backup.sh uses (copy it from the script).
  ```
  `PRODUCTION.pre_change = ("/srv/csa-prod/backup/backup.sh",)`. `apply` calls `run_pre_change` after the plan confirmation and compose-config check and before the image pull, prints `Backup taken: <object_name>`, and stores it in `_Progress.backup`. `_report_recovery`: when the backup step is what failed, prints `Nothing on production was changed. The backup step failed; the deploy stopped before any change.`; when migrations were applied, prints `The backup taken at the start of this deploy is <object_name> (restore: docs/runbooks/…, deploy/PRODUCTION.md)`. Production `plan` never runs a backup.

- [ ] **Step 1: Write failing tests**:
  - `test_backup_skipped_line_refuses`: the script exits 0 and the log's last line is `<ts> skipped …`: `RolloutError` step `backup`, message includes the line.
  - `test_backup_stale_ok_line_refuses`: last line is `ok` with a timestamp before `start`.
  - `test_backup_nonzero_exit_refuses_before_any_docker_command`: no `docker` call appears in `FakeRunner.argvs` and the recovery text says nothing was changed.
  - `test_backup_ok_after_start_passes_and_names_the_object`: output contains `Backup taken: backups/csa_staging-<timestamp>.dump`.
  - `test_backup_installed_copy_must_match_repo`: a differing installed file refuses with `reinstall`.
  - `test_pre_change_argv_never_contains_sudo`.
  - `test_recovery_after_migration_names_the_backup_object`.
  - In `tests/test_production_deploy_assets.py`: `test_backup_script_is_bash_strict_and_has_no_secrets` (starts with `#!/usr/bin/env bash` or `#!/bin/bash`, contains `set -euo pipefail`, takes the flock on `/var/tmp/csa-backup.lock`, contains no 20-character `AKIA` string).
- [ ] **Step 2: Run** the two files. Expected: FAIL.
- [ ] **Step 3: Implement** the above. The log timestamp comparison uses `datetime.fromisoformat` on the first field; an unparseable first field refuses.
- [ ] **Step 4: Run** the tests plus Task 2 and 3 files. Expected: PASS.
- [ ] **Step 5: Commit** `feat(rollout): production takes and verifies a backup before any change; backup.sh moves into the repo`.

---

### Task 6: Downgrade warning

**Files:**
- Modify: `scripts/staging_rollout.py` (`Plan.warnings`, `build_plan`, `format_plan`)
- Test: `tests/test_rollout_production.py`

**Interfaces:**
- Produces `Plan.warnings: list[str]` (default empty). In `build_plan` when `target.downgrade_warning`: for each distinct running revision `R` from `plan.rollback` with `R != sha`, run `git -C <repo> merge-base --is-ancestor <sha> <R>`; exit 0 means the target is older than what runs, so add `WARNING: <sha7> is older than the running <R7>; this is a downgrade. A downgrade across a migration is refused.`. A `git` exit other than 0/1 or an unknown `R` adds `WARNING: could not compare <sha7> with the running revision` and does not stop the plan. `format_plan` prints the warnings after the target lines. The existing refusal for a database ahead of the checkout is unchanged and still stops a downgrade across a migration.

- [ ] **Step 1: Write failing tests**: `test_older_commit_prints_downgrade_warning`, `test_newer_commit_prints_no_warning`, `test_unknown_running_revision_warns_cannot_compare_and_continues`, `test_staging_never_runs_the_ancestor_check` (FakeRunner has no rule for it; an extra call fails the test), `test_downgrade_across_a_migration_is_still_refused` (applied versions include one the checkout lacks).
- [ ] **Step 2: Run** `uv run pytest tests/test_rollout_production.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run** the test files from Tasks 2 to 5. Expected: PASS.
- [ ] **Step 5: Commit** `feat(rollout): warn when production would be downgraded`.

---

### Task 7: The staged gate

**Files:**
- Modify: `scripts/staging_rollout.py`
- Test: `tests/test_rollout_gate.py` (new)

**Interfaces:**
- Produces:
  ```python
  GATE_ENVIRONMENT = "staging"
  def staged_record(runner: Runner, target: Target, sha: str) -> str | None
  # GET repos/<target.repo>/deployments?sha=<sha>&environment=staging&per_page=100 via `gh api`; for each deployment GET its statuses; returns the created_at of the earliest successful status if any status of any deployment for exactly this sha has state "success", else None. Any gh failure, non-JSON, wrong shape, or a deployment whose sha differs raises RolloutError("gate", ...). It never returns a record on doubt.
  def require_staged(runner: Runner, target: Target, sha: str) -> str
  # target.staged_gate False -> returns "" and makes no call. Otherwise raises RolloutError("gate", f"commit {sha[:7]} has not been rolled out to staging") when staged_record is None.
  def record_staged(runner: Runner, target: Target, sha: str) -> None
  # POST repos/<repo>/deployments with `gh api --method POST --input -` and JSON body {"ref": sha, "environment": "staging", "auto_merge": false, "required_contexts": [], "transient_environment": false, "production_environment": false, "description": "staging rollout"}; then POST .../deployments/<id>/statuses with {"state": "success", "environment": "staging", "auto_inactive": false, "description": "staging rollout finished"}. Failure raises RolloutError("gate", ...).
  ```
  `build_plan` calls `require_staged` right after `target_sha` and before any image lookup, so both `plan` and `apply` are held to it, and `format_plan` prints `Staged on: <created_at>` for production. `main()` calls `record_staged` after `apply(...)` returns 0 for the staging target (not inside `apply`, so the existing `apply` tests see no new command); if it raises, `main` prints `WARNING: staging is fine but the staged record was not written: <message>` and `Retry: .venv/bin/python -m scripts.staging_rollout record-staged --sha <sha>` and still returns 0 (the rollout itself succeeded). A failed rollout writes nothing. New subcommand `record-staged --sha <40 hex>` (staging target only, refuses `--target production`) runs `record_staged` and prints `Recorded <sha7> as staged`.

- [ ] **Step 1: Write failing tests** in `tests/test_rollout_gate.py` with `FakeRunner` rules on `deployments`:
  - `test_require_staged_passes_with_a_success_status` and `test_no_deployments_refuses_with_the_exact_message` (`commit a1b2c3d has not been rolled out to staging`).
  - `test_failure_only_statuses_refuse`; `test_older_success_still_counts_after_a_newer_deployment` (two deployments on different SHAs, newer one `inactive`; the old SHA's `success` status is still found).
  - `test_gh_error_malformed_json_and_wrong_shape_all_refuse` (parametrized: exit 1, `not json`, `{}`, a list of strings).
  - `test_deployment_for_another_sha_is_not_counted`.
  - `test_staging_target_makes_no_gate_call`.
  - `test_record_staged_posts_deployment_then_success_with_auto_inactive_false`: inspect the two `input` JSON bodies and argv.
  - `test_main_apply_on_staging_records_after_success`, `test_main_apply_failure_records_nothing`, `test_main_record_failure_warns_with_retry_command_and_exits_zero`.
  - `test_record_staged_subcommand_and_its_refusal_for_production`.
  - `test_production_apply_without_a_record_changes_nothing`: refuses before `docker pull`, before the backup, with no docker command in `argvs`.
- [ ] **Step 2: Run** `uv run pytest tests/test_rollout_gate.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** with `_gh`/`_gh_json` for reads; writes use `runner.run([...], input=json.dumps(body))`. Step errors use step name `gate` and print via `_report_stop` like the others.
- [ ] **Step 4: Run** all rollout test files plus `tests/test_staging_rollout.py`. Expected: PASS with no edits to existing tests.
- [ ] **Step 5: Commit** `feat(rollout): record staging successes and refuse unstaged commits on production`.

---

### Task 8: Entry script and Systems Manager document

**Files:**
- Create: `deploy/production/deploy_entry.sh`, `deploy/production/ssm-document.json`, `deploy/production/requirements.txt`
- Test: `tests/test_production_deploy_assets.py`

**Interfaces:**
- `deploy_entry.sh <action> <sha> [<migrations>] [<host_digest>]`: installed root-owned at `/usr/local/sbin/csa-prod-deploy`, run as the deploy user. Top of file defines `CHECKOUT`, `PYTHON`, `LOCK`, `LOG_DIR` with `: "${NAME:=default}"` (defaults `/home/ubuntu/CSAV2`, `/opt/csa-deploy/venv/bin/python`, `/var/lock/csa-prod-deploy.lock`, `/var/log/csa-prod-deploy`). Behaviour: `set -euo pipefail`; validate every argument with the Global Constraints patterns before doing anything (host digest `^[0-9a-f]{12}$`, required only for `apply`); `exec 9>"$LOCK"; flock -n 9` or exit 75 with `another deploy is running`; `git -C "$CHECKOUT" fetch origin main`, `git -C "$CHECKOUT" checkout --detach "$sha"`; run `"$PYTHON" -m scripts.staging_rollout --target production plan` or `apply --sha "$sha" [--migrations "$m"] --host-digest "$d"` from `$CHECKOUT`; tee all output to `$LOG_DIR/<UTC timestamp>-<action>-<sha7>.log`; exit with the rollout's status; never reads stdin (`< /dev/null`).
- `ssm-document.json`: a Command document (`schemaVersion` `2.2`, `mainSteps` of `aws:runShellScript`) named in AWS as `csa-production-deploy`; parameters `action` (`allowedPattern` `^(plan|apply)$`), `sha` (`^[0-9a-f]{40}$`), `migrations` (default `""`, `^([0-9]{4}(,[0-9]{4})*)?$`), `hostDigest` (default `""`, `^([0-9a-f]{12})?$`); `executionTimeout` 1800; its single command line is `exec runuser -u ubuntu -- /usr/local/sbin/csa-prod-deploy "{{ action }}" "{{ sha }}" "{{ migrations }}" "{{ hostDigest }}"` preceded by `set -eu`. No other command and no inline script.
- `requirements.txt`: `psycopg[binary]==3.3.6`.

- [ ] **Step 1: Write failing tests** in `tests/test_production_deploy_assets.py`:
  - `test_entry_rejects_bad_arguments` (parametrized, run the real script with `bash` and a temp `CHECKOUT`/`LOCK`/`PYTHON` stub that records calls; assert exit non-zero and the stub never ran): action `destroy`, `plan ; id`, empty; sha uppercase, 39 chars, 41 chars, `$(id)`, a trailing newline, a leading space; migrations `0486;id`, `0486,`, `abc`, `04861`; digest of the wrong length on apply; apply with no digest.
  - `test_entry_plan_checks_out_the_sha_and_runs_the_production_plan` (stub `git` and python on PATH: assert the argv sequence is `fetch origin main`, `checkout --detach <sha>`, then `-m scripts.staging_rollout --target production plan`).
  - `test_entry_apply_passes_exactly_the_validated_arguments`.
  - `test_second_deploy_exits_75_while_the_lock_is_held` (hold the lock with `flock` in the test).
  - `test_entry_ignores_stdin` (a stub that would read stdin gets EOF).
  - `test_document_parameters_have_the_exact_patterns_and_the_one_command`: parse JSON; assert each `allowedPattern`, `executionTimeout`, that `mainSteps` has exactly one step with exactly the `set -eu` line and the one `exec runuser` line, and that every `{{ ... }}` in it is one of the four parameters and is double-quoted.
  - `test_document_patterns_reject_injection`: apply each `allowedPattern` with `re.fullmatch` to the same bad values as above; none match.
  - `test_requirements_pin_matches_pyproject`: the psycopg version equals the one in `pyproject.toml`.
  - `test_entry_script_is_bash_strict_and_has_no_sudo`.
- [ ] **Step 2: Run** `uv run pytest tests/test_production_deploy_assets.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the three files.
- [ ] **Step 4: Run** the file. Expected: PASS; also `shellcheck deploy/production/*.sh` if available (record if not).
- [ ] **Step 5: Commit** `feat(deploy): production entry script and Systems Manager document`.

---

### Task 9: IAM policies, `ssm_run.sh` and the workflow

**Files:**
- Create: `deploy/production/iam/trust-policy.json`, `deploy/production/iam/permissions-policy.json`, `deploy/production/ssm_run.sh`, `.github/workflows/deploy-production.yml`
- Test: `tests/test_production_deploy_assets.py`

**Interfaces:**
- `ssm_run.sh <action> <sha> [<migrations>] [<host_digest>]`: needs `AWS_REGION`, `INSTANCE_ID` in the environment (fails closed with a clear message if either is empty), validates the arguments with the same patterns, then `aws ssm send-command --document-name csa-production-deploy --instance-ids "$INSTANCE_ID" --parameters ...` (arguments passed as separate `Name=value` parameters, never concatenated into a shell string), polls `aws ssm get-command-invocation` every 5 seconds until the status is not `Pending`, `InProgress` or `Delayed` (give up with exit 124 after 35 minutes), prints `StandardOutputContent` and `StandardErrorContent`, writes `command_id=<id>` to `$GITHUB_OUTPUT` when set, and exits 0 only when the status is `Success`. It never prints credentials.
- Policies: `trust-policy.json` allows `sts:AssumeRoleWithWebIdentity` for the GitHub OIDC provider ARN placeholder with `StringEquals` on `token.actions.githubusercontent.com:aud` = `sts.amazonaws.com` and on `...:sub` = the two values `repo:amsyrzmi/CSAV2:environment:production` and `repo:amsyrzmi/CSAV2:environment:production-plan` (plus `job_workflow_ref` pinned to this workflow file on `main` if Task 0 step 5 says IAM accepts it). `permissions-policy.json` allows only `ssm:SendCommand` on the document ARN and the one managed-instance ARN (placeholders `<account-id>`, `<region>`, `<managed-instance-id>`), and `ssm:GetCommandInvocation`, `ssm:ListCommandInvocations` with the resource scoping Task 0 step 5 found.
- Workflow `deploy-production.yml`: `on: workflow_dispatch` with optional input `sha`; workflow-level `permissions: contents: read`; `concurrency: {group: deploy-production, cancel-in-progress: false}`; job `plan` (`environment: production-plan`, permissions `contents: read`, `deployments: read`, `id-token: write`) resolves the SHA (input if given and valid; else newest successful `staging` deployment SHA via `gh api repos/${{ github.repository }}/deployments?environment=staging`, then its statuses), checks out nothing from the SHA, assumes the role (`aws-actions/configure-aws-credentials` pinned to a commit SHA, role from `vars.PROD_DEPLOY_ROLE_ARN`, region from `vars.PROD_DEPLOY_REGION`), runs `ssm_run.sh plan <sha>`, appends the output to `$GITHUB_STEP_SUMMARY` inside a code fence, and exposes outputs `sha`, `migrations`, `host_digest` parsed from the plan output's last `apply` command line and `Host settings digest:` line (fail the job if either is missing); job `apply` (`environment: production`, `needs: plan`, the same permissions) assumes the role again and runs `ssm_run.sh apply "$SHA" "$MIGRATIONS" "$HOST_DIGEST"` with the values passed through `env:`, never interpolated into the script text. Every `run:` step starts with `set -euo pipefail`. User-controlled input (`inputs.sha`) is only ever read through `env:`.

- [ ] **Step 1: Write failing tests**:
  - `test_ssm_run_fails_closed_without_instance_or_region` and `test_ssm_run_rejects_bad_arguments` (same bad-value list as Task 8) using a stub `aws` on PATH that records calls and fails the test if invoked for a bad argument.
  - `test_ssm_run_sends_the_document_with_separate_parameters_and_exits_with_the_status` (stub `aws` returns `Success` then `Failed`; assert exit 0 / non-zero, assert the stub saw `--document-name csa-production-deploy` and `--instance-ids`, no `eval`, no unquoted expansion).
  - `test_ssm_run_polls_through_inprogress_and_times_out` (stub sleep via `SSM_POLL_SECONDS=0` and `SSM_MAX_POLLS=3`; exit 124).
  - `test_trust_policy_is_scoped_to_this_repo_and_the_two_environments`: parse JSON; `aud` equals `sts.amazonaws.com`; the `sub` values are exactly the two strings; no `*` anywhere in the condition values; no wildcard principal.
  - `test_permissions_policy_has_only_the_allowed_actions`: the action set is exactly the three; `SendCommand` statement resources are exactly two ARN strings that contain the document name and the `<managed-instance-id>` placeholder; no `Resource: "*"` on `SendCommand`.
  - `test_workflow_triggers_permissions_and_pinning`: `workflow_dispatch` only; top-level and job permissions match the Global Constraints exactly; every `uses:` ref matches `@[0-9a-f]{40}`; `apply` has `environment: production` and `needs: plan`; `plan` has `environment: production-plan`; concurrency group is `deploy-production` with `cancel-in-progress: false`.
  - `test_workflow_never_interpolates_inputs_into_run_text`: no `${{ inputs.` or `${{ needs.` or `${{ steps.` expression appears inside any `run:` block (they appear only under `env:`).
  - `test_workflow_fails_closed_on_missing_variables`: the workflow text references `vars.PROD_DEPLOY_ROLE_ARN`, `vars.PROD_DEPLOY_REGION` and `vars.PROD_INSTANCE_ID`, and `ssm_run.sh` is what refuses empties (previous test).
- [ ] **Step 2: Run** `uv run pytest tests/test_production_deploy_assets.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the four files. Resolve the pinned SHAs for `actions/checkout` and `aws-actions/configure-aws-credentials` with `gh api repos/<owner>/<repo>/git/ref/tags/<tag>` and put the tag in a trailing comment; the test only checks the 40-character form.
- [ ] **Step 4: Run** the file and `uv run ruff check`; run `actionlint` on the workflow if it is installed and say so if it is not. Expected: PASS.
- [ ] **Step 5: Commit** `feat(deploy): deploy-production workflow, OIDC role policies and the Systems Manager runner`.

---

### Task 10: Documentation, skills and the owner's one-time setup

**Files:**
- Modify: `deploy/PRODUCTION.md`, `.claude/skills/staging-rollout/SKILL.md`, `deploy/STAGING_PIPELINE.md` (one paragraph under "Routine rollouts")
- Create: `.claude/skills/production-deploy/SKILL.md`
- Test: `tests/test_production_deploy_assets.py`

**Interfaces:** consumes the file names, role/variable names and commands from Tasks 4 to 9.

- [ ] **Step 1: Write failing tests**: `test_production_doc_mentions_every_one_time_step` (the headings `Deploying from GitHub`, `One-time setup`, `Break-glass` exist and the break-glass section contains the sentence that it is outside the staged-first rule); `test_docs_contain_no_account_ids_ip_addresses_or_arns` (regex scan of the new and changed files for 12-digit account ids, `arn:aws:...:<digits>`, IPv4 addresses and `mi-[0-9a-f]{17}`); `test_staging_skill_mentions_the_staged_record`.
- [ ] **Step 2: Run** the file. Expected: FAIL.
- [ ] **Step 3: Write the docs.** `deploy/PRODUCTION.md` gains "Deploying from GitHub" (the flow: start `deploy-production`, read the plan summary, approve `production`, read the result; what each refusal means, including `has not been rolled out to staging`) and "One-time setup" as numbered, machine-labelled steps for the owner: (1) AWS: create the Systems Manager service role for hybrid activation with the managed-instance core policy and an activation limited to one registration with a short expiry; (2) instance: install the SSM agent and register it with the activation code and region (code typed at a hidden prompt, never written to history); (3) AWS: create the GitHub OIDC provider and the role from `deploy/production/iam/*.json` with placeholders filled; (4) AWS: create the document from `deploy/production/ssm-document.json`; (5) instance: install `deploy_entry.sh` as root-owned `/usr/local/sbin/csa-prod-deploy`, the venv at `/opt/csa-deploy` from `requirements.txt`, `/srv/csa-prod/host.env` with the bucket and region, copy `deploy/production/backup.sh` over `/srv/csa-prod/backup/backup.sh`, and the `gh`/`docker` login with the read token from the developer (token via hidden prompt); (6) GitHub: create environments `production` (required reviewers: the owner `amsyrzmi` and the other developer `DanSan0408`; one approval is enough; "Prevent self-review" off; deployment branches `main` only; `DanSan0408` needs at least read access, which they have) and `production-plan` (no reviewer, `main` only) and the repository variables `PROD_DEPLOY_ROLE_ARN`, `PROD_DEPLOY_REGION`, `PROD_INSTANCE_ID`; (7) verify with a `plan` run. The existing manual procedure is kept as "Break-glass" with a plain statement that it bypasses the staged-first rule and is for the owner only when staging is unavailable. `.claude/skills/production-deploy/SKILL.md` mirrors the staging skill (plan, wait for the owner's go, apply, report, never run compose by hand, give copy-ready commands when blocked). The staging skill and pipeline doc gain the one line that a successful staging rollout records the commit for production, plus the `record-staged` retry command.
- [ ] **Step 4: Run** the file plus `make lint`. Expected: PASS.
- [ ] **Step 5: Commit** `docs(deploy): production deploy runbook, one-time setup and skills`.

---

### Task 11: Roll out, in this order (owner-run, builder guides)

No new code. Each step is a gate for the next; give the owner numbered, machine-labelled commands and stop at any failure.

- [ ] **Step 1:** Open the PR; wait for green CI; owner merges. `make lint` and the changed files' tests pass locally first.
- [ ] **Step 2 (staging, home server):** run the normal staging rollout of the merged `main` through the `staging-rollout` skill. This proves the shared core on staging and writes the first `staging` deployment record. Confirm with `gh api repos/amsyrzmi/CSAV2/deployments?environment=staging`. The `record-staged` retry command is the fallback. The home server belongs to the other developer: only the owner's normal rollout touches it, as today.
- [ ] **Step 3 (AWS, GitHub, instance):** the owner follows "One-time setup" from `deploy/PRODUCTION.md`; Task 0 steps 3 and 4 are re-checked against the live agent.
- [ ] **Step 4:** start `deploy-production` with an empty `sha`; read the plan summary; the plan must show the staged date, the images by digest, `unknown (a local image with no registry digest, or not running)` as the rollback record, and the migrations (expected: none). Approve `production`; the first run is watched live.
- [ ] **Step 5:** after a successful first deploy, remove the old local image tags on the instance and retire `~/csa-prod-env.sh` and its `csa_compose` function in favour of the repo file plus `host.env`; update `deploy/PRODUCTION.md` in a follow-up PR if any step differs.
