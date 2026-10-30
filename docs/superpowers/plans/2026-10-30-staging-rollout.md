# Staging Auto-Publish and Rollout Script Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A green push-to-main CI run starts the staging image publish by itself, with the owner's
approval still required. A tested script, `scripts/staging_rollout.py`, then plans and applies a
staging rollout, including migrations, in the runbook's safe order.

**Architecture:**
- **Publish workflow:** gains a `workflow_run` trigger. Its existing verification steps are
  unchanged.
- **The script:** a stdlib-only CLI with two subcommands, `plan` (read-only) and `apply`.
  - Every external command goes through one `Runner` object, so tests replace it with a fake.
  - `apply` re-derives the plan and refuses if it no longer matches the confirmation it was given.
- **Supporting files:** a project skill, a checked-in flags file, check SQL files, and runbook and
  PROGRESS updates.

**Tech Stack:** Python 3.12 stdlib (`argparse`, `subprocess`, `json`, `tempfile`, `pathlib`,
`dataclasses`), pytest, GitHub Actions YAML, Docker Compose CLI, `gh` CLI.

**Spec:** `docs/superpowers/specs/2026-10-30-staging-rollout-design.md`

## Global Constraints

**Python and testing**
- Python 3.12, standard library only in `scripts/staging_rollout.py`. The one repo import allowed
  is `from scripts.migrate import discover_migrations`.
- Every external command is an argv list (no shell), run through `Runner.run`. Tests use a fake
  runner only; no test calls Docker, GitHub or staging. The exception is the one test that runs
  `docker compose config`, which needs the CLI but no daemon, as `tests/test_staging_pipeline.py`
  already does.
- Locally, run only `tests/test_staging_rollout.py` and `tests/test_publish_staging_images.py`,
  then `make lint`. Run them through the wrapper
  `bash /tmp/claude-1000/-home-amsyrzmi-CSAV2/7d09c8ff-ee80-4525-b21a-195503f98d9c/scratchpad/ci.sh /home/amsyrzmi/CSAV2/.venv/bin/python -m pytest <file> -q`.
  - Never use `VAR=` prefixes or chained commands.
  - The full suite runs in PR CI.

**Docker and compose**
- Docker always gets `--host unix:///var/run/docker.sock`.
- The compose base argv is exactly, in this order: `docker --host unix:///var/run/docker.sock
  compose --env-file /dev/null --project-name csa-staging --file
  deploy/compose.staging-local-proposal.yml --file deploy/compose.staging-shared-caddy.yml --file
  deploy/compose.staging-pipeline.yml --profile staging-approved --profile pipeline-approved`.
- Migrate is the base argv plus `--profile migration-approved run --rm --no-deps migrate`.
  `CSA_STAGING_MIGRATION_APPROVAL=approved-isolated-staging` is set in that child's env only.
- Recreate runs per service, in the order `gateway`, `worker`, `delivery`, `frontend`:
  `up --detach --wait --wait-timeout 300 --no-deps --pull never <service>`.
- A migration rollout stops all four app services (`gateway frontend worker delivery`) before
  migrate (owner decision 2026-10-30).

**Images**
- Image refs are `ghcr.io/amsyrzmi/csa-python@sha256:<64 hex>` and
  `ghcr.io/amsyrzmi/csa-frontend@sha256:<64 hex>`.
- Pulls are by digest only, with `DOCKER_CONFIG` set to a fresh empty `0700` temp directory that
  is removed afterwards.
- After a pull, `RepoDigests` must contain the ref, and the label
  `org.opencontainers.image.revision` must equal the target SHA.

**Secrets and output**
- Never open, read, print or copy anything under `/srv/csa-staging/secrets`. Secret files are
  checked with `Path.exists()` only.
- The only output is:
  - plan text;
  - step lines;
  - compose `ps` lines;
  - migrate's stdout;
  - digests;
  - the first line of a failed `gh` call's stderr.

  Never print environment values beyond the non-secret flags and the image refs.

**Flags file**
- `deploy/staging-rollout.env` allows exactly these keys, and all six are required:
  `CSA_STAGING_CONSOLE_ORIGIN`, `CSA_STAGING_HOOKS_ORIGIN`, `CSA_STAGING_WORKSPACE_ASSISTANT`,
  `CSA_STAGING_GATEWAY_MODEL_SECRET`, `CSA_STAGING_INVITE_MAIL`, `CSA_STAGING_EMAIL_ASSISTANT`.
- The script sets these itself:
  - `NO_LOCAL_ENV=1`, `ENV=ci` and `COMPOSE_PROFILES=` (empty);
  - `CSA_STAGING_PROJECT=csa-staging` and `CSA_STAGING_SECRET_DIR=/srv/csa-staging/secrets`;
  - `CSA_STAGING_PYTHON_IMAGE` and `CSA_STAGING_FRONTEND_IMAGE`.

## Review Focus

1. **`apply` run long after `plan`, after a new migration merged.** `apply` must refuse because the
   pending list no longer matches `--migrations`, and must not run that unseen migration. Test:
   `test_apply_refuses_when_pending_changed_since_plan` (Task 3).
2. **A previous `apply` failed with the gateway stopped and the migration applied.** Re-running
   `plan` must work, showing the rollback reference as "unknown (not running)" and pending as
   none. The re-run `apply` must still run every check file. Tests:
   `test_plan_tolerates_stopped_containers` (Task 2) and `test_every_check_file_runs_on_each_apply`
   (Task 3).
3. **A secret file the new compose file needs is missing.** `plan` lists it by name, and `apply`
   refuses before stopping or pulling anything. Test:
   `test_apply_refuses_missing_secrets_before_any_change` (Task 3).
4. **Images published for an older SHA but not for HEAD** (the publish is still waiting for
   approval). `plan` says "not published yet", reports the publish run's state, and exits
   non-zero without reading staging. Test: `test_plan_reports_unpublished_head` (Task 2).
5. **The migrate one-shot exits non-zero.** No retry, no recreate. Exit non-zero, saying the four
   services are stopped and to check `schema_migrations`. Test:
   `test_migrate_failure_stops_without_retry_or_recreate` (Task 3).

---

### Task 1: Auto-publish on green main CI

**Files:**
- Modify: `.github/workflows/publish-staging-images.yml` (the `on:` block; the job gets an `if:`;
  `env.CI_RUN_ID`; `download-artifact`'s `run-id`; the header comment)
- Modify: `tests/test_publish_staging_images.py` (replace `test_trigger_is_manual_dispatch_only`;
  update the `run-id` assertion in `test_publishes_exact_archives_by_sha_with_preserved_digest`)

**Interfaces:**
- Consumes: nothing.
- Produces: the workflow triggers on `workflow_dispatch` and on `workflow_run` of `ci`.

- [ ] **Step 1: Write the failing tests**

Replace `test_trigger_is_manual_dispatch_only` with:

```python
RUN_ID_EXPR = "${{ inputs.ci_run_id || github.event.workflow_run.id }}"


def test_triggers_are_manual_dispatch_and_completed_main_ci() -> None:
    text = _text()
    on_block = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "workflow_dispatch:" in on_block
    run_block = on_block.split("workflow_run:", 1)[1]
    assert "workflows: [ci]" in run_block
    assert "types: [completed]" in run_block
    assert "branches: [main]" in run_block
    for trigger in ("push:", "pull_request", "schedule:", "release:"):
        assert trigger not in on_block


def test_automatic_runs_publish_only_successful_push_runs() -> None:
    job = _text().split("\n  publish:\n", 1)[1].split("    steps:", 1)[0]
    condition = job.split("    if:", 1)[1].split("\n    ", 1)[0]
    assert "github.event_name == 'workflow_dispatch'" in condition
    assert "github.event.workflow_run.event == 'push'" in condition
    assert "github.event.workflow_run.conclusion == 'success'" in condition


def test_run_id_comes_from_the_input_or_the_triggering_run() -> None:
    text = _text()
    assert f"CI_RUN_ID: {RUN_ID_EXPR}" in text
    assert f"run-id: {RUN_ID_EXPR}" in text
    assert "${{ inputs.ci_run_id }}" not in text
```

In `test_publishes_exact_archives_by_sha_with_preserved_digest`, delete the line
`assert "run-id: ${{ inputs.ci_run_id }}" in text`. The new test covers it.

- [ ] **Step 2: Run them to verify they fail**

Run: `bash …/ci.sh /home/amsyrzmi/CSAV2/.venv/bin/python -m pytest tests/test_publish_staging_images.py -q`
Expected: the three new tests FAIL; the others pass.

- [ ] **Step 3: Edit the workflow**

- **`on:`:** add, next to `workflow_dispatch`:
  `workflow_run: {workflows: [ci], types: [completed], branches: [main]}`, in block YAML style.
- **`jobs.publish`:** add `if:` with
  `github.event_name == 'workflow_dispatch' || (github.event.workflow_run.event == 'push' && github.event.workflow_run.conclusion == 'success')`.
- **Run id:** set `env.CI_RUN_ID` and `download-artifact`'s `run-id:` to the `RUN_ID_EXPR` value.
- **Header comment:** change "Manual dispatch only" to say a green push-to-main `ci` run also
  starts it, and that the `staging-images` approval is still required.
- **Leave unchanged:** every `run:` block, the permissions, the environment and concurrency.

- [ ] **Step 4: Run them to verify they pass**

Same command. Expected: all tests in the file pass, including
`test_untrusted_inputs_are_not_interpolated_into_scripts`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/publish-staging-images.yml tests/test_publish_staging_images.py
git commit -m "ci(staging): publish images after a green main CI run, approval still required"
```

---

### Task 2: Rollout script, the read-only `plan`

**Files:**
- Create: `scripts/staging_rollout.py`
- Create: `deploy/staging-rollout.env`
- Create: `tests/test_staging_rollout.py`

**Interfaces:**
- Consumes: `scripts.migrate.discover_migrations() -> list[tuple[str, Path]]`.
- Produces (used by Task 3):

```python
SOCKET = "unix:///var/run/docker.sock"
SECRET_DIR = Path("/srv/csa-staging/secrets")
APP_SERVICES = ("gateway", "worker", "delivery", "frontend")   # recreate order
STOP_SERVICES = ("gateway", "frontend", "worker", "delivery")
FLAG_KEYS: frozenset[str]                                      # the six keys
CHECKS_DIR = REPO_ROOT / "deploy" / "rollout_checks"

class RolloutError(Exception):
    def __init__(self, step: str, message: str, state: str | None = None): ...

@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str

class Runner(Protocol):
    def run(self, argv: Sequence[str], *, env: Mapping[str, str] | None = None,
            input: str | None = None) -> Completed: ...

class SubprocessRunner:   # subprocess.run(list(argv), env=..., input=..., capture_output=True, text=True, check=False)

def load_flags(path: Path) -> dict[str, str]
def compose_base() -> list[str]
def build_env(flags: Mapping[str, str], images: Mapping[str, str], *, docker_config: Path,
              secret_dir: Path = SECRET_DIR) -> dict[str, str]
    # PATH and HOME from os.environ, the fixed keys, the flags, the images and DOCKER_CONFIG;
    # nothing else
def target_sha(runner: Runner, repo: Path) -> str
def published_images(runner: Runner, sha: str) -> dict[str, str] | None   # keys "python", "frontend"
def publish_state(runner: Runner, sha: str) -> str
def running_images(runner: Runner, env: Mapping[str, str]) -> dict[str, str | None]  # per APP_SERVICES
def applied_versions(runner: Runner, env: Mapping[str, str]) -> list[str]
def pending_versions(checkout: Sequence[str], applied: Sequence[str]) -> list[str]
def secret_paths(runner: Runner, env: Mapping[str, str]) -> dict[str, Path]  # name -> file path
def missing_secrets(paths: Mapping[str, Path]) -> list[str]                  # names only, sorted
def check_files(directory: Path = CHECKS_DIR) -> list[Path]                  # sorted *.sql

@dataclass(frozen=True)
class Plan:
    sha: str
    images: dict[str, str]             # "python"/"frontend" -> full digest ref
    rollback: dict[str, str | None]    # service -> running digest ref, or None
    pending: list[str]
    missing: list[str]
    checks: list[str]                  # check file names
    steps: list[str]

def build_plan(runner: Runner, repo: Path, *, docker_config: Path,
               secret_dir: Path = SECRET_DIR, checks_dir: Path = CHECKS_DIR) -> Plan
def format_plan(plan: Plan) -> str
def main(argv: list[str] | None = None, runner: Runner | None = None) -> int
```

**Rules the bodies must follow:**
- **`target_sha`** runs, in `repo`:
  - `git rev-parse HEAD`;
  - `git status --porcelain --untracked-files=no`, which must be empty;
  - `git merge-base --is-ancestor HEAD origin/main`, which must exit 0.

  It never fetches.
- **`published_images`** runs `gh api /users/amsyrzmi/packages/container/csa-<app>/versions?per_page=100`
  (with no `env`, so `gh` keeps the user's login). It takes the version whose
  `metadata.container.tags` contains the full SHA. Its `name` must match `^sha256:[0-9a-f]{64}$`.
  It returns `None` if either app has no match.
- **`publish_state`** reads `gh run list --workflow publish-staging-images.yml --limit 20 --json
  databaseId,headSha,status,conclusion` and returns, for the newest run with `headSha == sha`:
  - `"waiting for approval"` when the status is `waiting`;
  - `"in progress"` for `queued` or `in_progress`;
  - `"finished: <conclusion>"`;
  - `"no publish run found"` when there is no such run.
- **`running_images`:**
  - `docker --host SOCKET inspect --format {{.Image}} csa-staging-<service>-1`, then
    `docker --host SOCKET image inspect --format {{json .RepoDigests}} <image id>`;
  - take the entry starting `ghcr.io/amsyrzmi/`;
  - any non-zero exit or no entry gives `None`.
- **`applied_versions`:** compose base +
  `exec -T staging-db psql -U csa -d csa_staging -tA -v ON_ERROR_STOP=1 -c "SELECT version FROM schema_migrations ORDER BY version"`.
- **`pending_versions`:** raises `RolloutError("plan", ...)` when `applied` holds a version that is
  not in `checkout`, naming those versions.
- **`secret_paths`:** compose base + `--profile migration-approved config --format json`, then
  `secrets.<name>.file`.
- **`gh` failures:** a non-zero `gh` exit raises `RolloutError` carrying only the first stderr
  line.
- **`build_plan`:**
  - it calls `target_sha`, then `published_images`;
  - if unpublished, it raises `RolloutError("plan", "images for <sha7> are not published yet (<publish_state>)")`
    before any docker call;
  - only then does it read staging.
- **Plan steps:**
  - image-only: `pull and verify images`, `recreate gateway, worker, delivery, frontend`,
    `check readiness`, `run checks: <names or none>`;
  - with migrations, inserted after the pull: `stop gateway, frontend, worker, delivery`,
    `apply migrations <list>`, `confirm migrations recorded`.
- **`format_plan` output:**
  - the SHA;
  - both image refs;
  - one rollback line per service (`unknown (not running)` for `None`);
  - `Pending migrations: <comma list>` or `Pending migrations: none`;
  - when there are missing secrets, `Missing secret files: <names>` and
    `apply will refuse until they exist`;
  - the numbered steps;
  - when there are migrations: `Downtime: console unavailable and bot replies delayed (queued, not lost) until the services are back.`;
  - last line, the exact command:
    `.venv/bin/python -m scripts.staging_rollout apply` with ` --migrations <comma list>` when
    there are migrations.
- **`main`:**
  - `plan` prints `format_plan` and returns 0, or 2 when secrets are missing;
  - a `RolloutError` prints `Stopped at <step>: <message>` and returns 1;
  - it creates the empty `0700` `DOCKER_CONFIG` with `tempfile.mkdtemp`, and removes it in
    `finally`.

`deploy/staging-rollout.env` has a header comment ("non-secret staging switches; one `KEY=value`
per line; reviewed by PR"). It holds the six values from the spec's table:

```
CSA_STAGING_CONSOLE_ORIGIN=https://staging-console.fourstacks.my
CSA_STAGING_HOOKS_ORIGIN=https://hooks.fourstacks.my
CSA_STAGING_WORKSPACE_ASSISTANT=true
CSA_STAGING_GATEWAY_MODEL_SECRET=staging_model_key
CSA_STAGING_INVITE_MAIL=true
CSA_STAGING_EMAIL_ASSISTANT=true
```

`load_flags` skips blank lines and `#` comments. It raises `RolloutError("flags", ...)` on:
- a line without `=`;
- an unknown key;
- a duplicate key;
- a missing key.

- [ ] **Step 1: Write the failing tests** in `tests/test_staging_rollout.py`

Build a `FakeRunner` in the test file:
- it records `(argv, env, input)` for every call;
- it answers from an ordered list of `(predicate, Completed)` rules, where the predicate matches
  on argv content;
- an unmatched call fails the test.

Use synthetic SHAs, `sha256:` digests of `"a"*64` / `"b"*64`, and `tmp_path` for every directory.

```python
def test_load_flags_reads_the_six_keys(tmp_path): ...  # returns all six exactly
@pytest.mark.parametrize("bad", ["UNKNOWN=1", "NO_EQUALS", "CSA_STAGING_INVITE_MAIL=true (dup)", "missing one key"])
def test_load_flags_refuses_bad_files(tmp_path, bad): ...  # raises RolloutError
def test_checked_in_flags_file_loads(): ...  # load_flags(REPO_ROOT/"deploy/staging-rollout.env") has FLAG_KEYS
def test_build_env_has_only_expected_keys(
    tmp_path,
): ...  # keys == FLAG_KEYS | fixed keys | images | {"PATH","HOME","DOCKER_CONFIG"}; COMPOSE_PROFILES == ""
def test_compose_base_is_exact(): ...  # equals the Global Constraints argv
@pytest.mark.parametrize("case", ["dirty", "off_main"])
def test_target_refuses_dirty_or_off_main(case): ...
def test_published_images_match_the_full_sha(): ...  # picks the version tagged with the SHA, not a prefix match
def test_plan_reports_unpublished_head(
    capsys,
): ...  # main(["plan"]) == 1; output has "not published yet (waiting for approval)"; no argv starts with "docker"
def test_pending_versions_refuse_staging_ahead(): ...
def test_plan_with_migrations_lists_steps_downtime_and_command(capsys, tmp_path):
    ...
    # pending ["0486","0487"]; output has "stop gateway, frontend, worker, delivery",
    # "Downtime:", and ends with "apply --migrations 0486,0487"


def test_plan_image_only_has_no_migration_steps(
    capsys, tmp_path
): ...  # "Pending migrations: none"; ends with "staging_rollout apply"
def test_plan_tolerates_stopped_containers(capsys, tmp_path): ...  # inspect exits 1 -> "unknown (not running)"
def test_plan_lists_missing_secrets_by_name(capsys, tmp_path): ...  # exit 2, names shown, no secret paths opened
def test_plan_runs_no_mutating_command(tmp_path):
    ...
    # no recorded argv contains any of "pull", "stop", "up", "run --rm", "rm"
    # after the compose base


def test_secret_paths_from_real_compose_config(tmp_path):
    ...
    # SubprocessRunner + synthetic secret dir built like tests/test_staging_pipeline.render;
    # expects "staging_owner_dsn" among the names, every path under tmp secret dir


def test_docker_config_dir_is_empty_private_and_removed(tmp_path, monkeypatch):
    ...
    # capture DOCKER_CONFIG from recorded env: was 0700 and empty during the run, gone after
```

- [ ] **Step 2: Run to verify they fail**

Run: `bash …/ci.sh /home/amsyrzmi/CSAV2/.venv/bin/python -m pytest tests/test_staging_rollout.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement** `scripts/staging_rollout.py` (constants, `Runner`, all functions above,
  `main` with the `plan` subcommand and an `apply` subparser stub that Task 3 fills in), plus
  `deploy/staging-rollout.env`.

- [ ] **Step 4: Run to verify they pass**

Same command, then `make lint`. Expected: all pass, lint clean.

- [ ] **Step 5: Commit**

```bash
git add scripts/staging_rollout.py deploy/staging-rollout.env tests/test_staging_rollout.py
git commit -m "feat(staging): rollout plan reads published digests, migrations and secrets"
```

---

### Task 3: Rollout script, `apply`

**Files:**
- Modify: `scripts/staging_rollout.py`
- Modify: `tests/test_staging_rollout.py`

**Interfaces:**
- Consumes: everything Task 2 produces.
- Produces:

```python
def parse_migrations(value: str | None) -> list[str]   # "0486,0487" -> ["0486","0487"]; None -> []
def apply(runner: Runner, repo: Path, confirmed: list[str], *, docker_config: Path,
          secret_dir: Path = SECRET_DIR, checks_dir: Path = CHECKS_DIR,
          sleep: Callable[[float], None] = time.sleep, out: Callable[[str], None] = print) -> int
```

`main(["apply", "--migrations", "0486,0487"])` calls `apply`.

**Rules the body must follow** (the order is binding):

1. **Re-derive and confirm.**
   - Call `plan = build_plan(...)`. Refuse with `RolloutError("confirm", "pending migrations changed since plan; run plan again")`
     when `confirmed != plan.pending`. That covers a missing, extra or mismatched list.
   - Refuse with `RolloutError("preflight", "missing secret files: <names>")` when `plan.missing`
     is non-empty.
   - Both refusals happen before any mutating call.
2. **Validate the config:** compose base + `config --quiet`, which must exit 0.
3. **Pull and verify:** for each ref, `docker --host SOCKET pull <ref>`, then
   `docker --host SOCKET image inspect <ref>`. Parse its JSON:
   - `RepoDigests` must contain the ref;
   - `Config.Labels["org.opencontainers.image.revision"]` must equal `plan.sha`.

   Otherwise raise `RolloutError("pull", ...)`.
4. **Migrate, only when `plan.pending`:**
   - compose base + `stop gateway frontend worker delivery`;
   - the migrate argv, with `CSA_STAGING_MIGRATION_APPROVAL=approved-isolated-staging` added to
     this one call's env only. Print its stdout and `migrate exit=<code>`;
   - on a non-zero exit, raise immediately with the state
     `gateway, frontend, worker and delivery are stopped; check schema_migrations before retrying`;
   - then `applied_versions` must include every `plan.pending` version
     (`RolloutError("migrate", ...)` otherwise).
5. **Recreate** each of `APP_SERVICES` in order: compose base +
   `up --detach --wait --wait-timeout 300 --no-deps --pull never <service>`. Stop at the first
   failure.
6. **Readiness:**
   `docker --host SOCKET exec csa-staging-gateway-1 python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=5)"`
   - Exit 0 means ready.
   - It tries 5 times with `sleep(3)` between tries, then raises `RolloutError("readiness", ...)`.
7. **Checks:** every file from `check_files(checks_dir)`, every run.
   - Run compose base + `exec -T staging-db psql -U csa -d csa_staging -tA -v ON_ERROR_STOP=1 -f -`,
     with the file text as `input`.
   - The stripped stdout must be `0`.
   - Otherwise run compose base + `stop gateway frontend worker delivery`, then raise
     `RolloutError("check <name>", "printed <value>, expected 0", state="services stopped; investigate before the console is used again")`.
8. **Finish:** print compose base + `ps --format {{.Service}} {{.Status}}` output lines, then
   `Rollout of <sha7> finished.`, and return 0.

**On any `RolloutError` during `apply`:**
- print `Stopped at <step>: <message>`;
- print the state when given;
- print `Rollback images:` with each non-`None` rollback ref;
- print `Re-run plan, then apply, once the cause is fixed.`;
- return 1.

Never retry migrate. Never recreate services after a migrate failure.

- [ ] **Step 1: Write the failing tests**

```python
def test_apply_image_only_runs_steps_in_order(tmp_path):
    ...
    # recorded mutating argv sequence == [config --quiet, pull python, pull frontend,
    # up gateway, up worker, up delivery, up frontend, readiness exec, check exec(s), ps]
    # with no "stop" and no "migrate"


def test_apply_with_migrations_stops_all_four_then_migrates_then_verifies(tmp_path):
    ...
    # "stop gateway frontend worker delivery" precedes the migrate argv, which precedes
    # the version re-read, which precedes the first "up"


def test_migration_approval_is_only_in_the_migrate_env(tmp_path):
    ...
    # CSA_STAGING_MIGRATION_APPROVAL present in exactly one recorded env: the migrate call


@pytest.mark.parametrize("confirmed", [[], ["0486"], ["0486", "0488"]])
def test_apply_refuses_when_pending_changed_since_plan(tmp_path, confirmed):
    ...
    # pending is ["0486","0487"]; returns 1; no mutating call recorded


def test_apply_refuses_migrations_flag_when_nothing_pending(tmp_path): ...
def test_apply_refuses_missing_secrets_before_any_change(tmp_path): ...
@pytest.mark.parametrize("bad", ["digest", "revision"])
def test_pull_verification_rejects_mismatch(tmp_path, bad): ...  # returns 1, no "up"/"stop"
def test_migrate_failure_stops_without_retry_or_recreate(tmp_path, capsys):
    ...
    # one migrate call, no "up", output mentions "check schema_migrations"


def test_failed_check_stops_all_four_and_exits_nonzero(tmp_path, capsys):
    ...
    # check prints "3"; last mutating call is "stop gateway frontend worker delivery"; returns 1


def test_every_check_file_runs_on_each_apply(tmp_path): ...  # two check files, nothing pending -> both run
def test_readiness_retries_then_fails(tmp_path): ...  # 5 exec attempts, 4 sleeps of 3, returns 1
def test_failure_prints_rollback_refs_and_rerun_hint(tmp_path, capsys):
    ...
    # "up worker" fails after a migration; output has the step, the rollback refs and
    # "Re-run plan, then apply"


def test_apply_output_has_no_secret_values(tmp_path, capsys):
    ...
    # synthetic secret files with "SYNTHETIC-ONLY"; that text appears in no output and in no
    # recorded argv/env/input
```

- [ ] **Step 2: Run to verify they fail.** Same command. Expected: the new tests FAIL.

- [ ] **Step 3: Implement** `parse_migrations` and `apply`, and wire the `apply` subcommand
  (`--migrations` is optional, a comma list).

- [ ] **Step 4: Run to verify they pass.** Same command, then `make lint`. Expected: all pass,
  lint clean.

- [ ] **Step 5: Commit**

```bash
git add scripts/staging_rollout.py tests/test_staging_rollout.py
git commit -m "feat(staging): rollout apply stops writers, migrates, recreates and checks"
```

---

### Task 4: Check file, skill and docs

**Files:**
- Create: `deploy/rollout_checks/0485.sql`
- Create: `.claude/skills/staging-rollout/SKILL.md`
- Modify: `deploy/STAGING_PIPELINE.md`:
  - §2 replace `/home/amsyrzmi/CSAV2-seed` with `/home/amsyrzmi/CSAV2`;
  - add a new "Routine rollouts" section before §1;
  - §11.1 step 4 points at the check file.
- Modify: `docs/PROGRESS.md` (a new top entry)
- Modify: `tests/test_staging_rollout.py`

**Interfaces:**
- Consumes: the `CHECKS_DIR` and CLI from Tasks 2 and 3.
- Produces: nothing for code.

- [ ] **Step 1: Write the failing tests**

```python
FORBIDDEN_SQL = ("insert", "update", "delete", "alter", "drop", "create", "truncate", "grant", "revoke")


def test_check_files_are_read_only_selects():
    ...
    # every CHECKS_DIR/*.sql has a leading "--" header comment mentioning "read-only";
    # with comments stripped and lowercased, none of FORBIDDEN_SQL appears as a word,
    # and it starts with "select"


def test_0485_check_keeps_the_scrub_predicates():
    ...
    # text contains "kind = 'email_export'", "Save email draft to Gmail",
    # "chr(8220)" and "AS old_rows"


def test_runbook_points_at_the_script_and_the_live_checkout():
    ...
    # STAGING_PIPELINE.md contains "scripts.staging_rollout plan", "deploy/rollout_checks/0485.sql",
    # "/home/amsyrzmi/CSAV2" and not "CSAV2-seed"


def test_skill_requires_plan_then_owner_go_then_apply():
    ...
    # SKILL.md has frontmatter name "staging-rollout"; contains "plan", "apply", "--ff-only"
```

- [ ] **Step 2: Run to verify they fail.** Same command. Expected: the four new tests FAIL.

- [ ] **Step 3: Write the files**

- **`0485.sql`:** the §11.1 query, unchanged, under a header saying:
  - it is a read-only check run by `scripts/staging_rollout.py` on every apply;
  - it must print a single `0`;
  - check files may contain only `SELECT`s (review policy).
- **§11.1 step 4:** keep the explanation, and replace the inline SQL block with "run by the
  rollout script; the query is `deploy/rollout_checks/0485.sql`".
- **"Routine rollouts" section:** it covers:
  - start in the main checkout with `git pull --ff-only origin main`;
  - run `.venv/bin/python -m scripts.staging_rollout plan`, then `apply`, with the `--migrations`
    rule;
  - what a migration rollout stops;
  - re-run as recovery;
  - change switches by PR in `deploy/staging-rollout.env`;
  - first-time setup stays in the sections below.
- **`SKILL.md`:** frontmatter `name: staging-rollout` and a description beginning "Use when the
  owner asks to roll out, deploy or update staging". The body gives the four steps from the
  spec's §3:
  - pull `--ff-only` in `/home/amsyrzmi/CSAV2`, never a worktree;
  - run `plan` and summarise it;
  - wait for the owner's go, which covers this plan only;
  - run `apply` with exactly the printed flags, and report.

  It also covers:
  - if blocked, give numbered copy-ready commands;
  - never read the secret dir;
  - after a failure, show the script's recovery lines.
- **`docs/PROGRESS.md`:** a new top entry, "Staging rollout tooling (2026-10-30, branch
  `feat/staging-rollout`)". It records:
  - the auto-publish trigger;
  - the script;
  - the skill;
  - the owner decision that every migration stops all four services;
  - the not-yet-recorded staging rollouts. The 0484/0485 rollout: main `ba4963c`, publish run
    40993506691, python `sha256:e53a6749833a3d2ee88fb3f1bb2756b0c1e896ba4c146466019dce44170d252c`,
    frontend `sha256:07d5cecf0b6509ab36de1dfcd5172167033a2554a7cb0d747dabb287de7c4c2c`,
    `migrate exit=0`, versions through 0485, `old_rows` 0. The PR #54 image update gets one line,
    with its digests filled in by the controller after that rollout.

- [ ] **Step 4: Run to verify they pass.** Same command, then `make lint`. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/rollout_checks/0485.sql .claude/skills/staging-rollout/SKILL.md deploy/STAGING_PIPELINE.md docs/PROGRESS.md tests/test_staging_rollout.py
git commit -m "docs(staging): rollout skill, 0485 check file and routine rollout runbook"
```

After merge (controller, not a task): propose to the owner the host-local permission rule
`Bash(.venv/bin/python -m scripts.staging_rollout plan:*)` in `.claude/settings.local.json`.
