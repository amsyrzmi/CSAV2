# Staging reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An owner-run tool that backs up and wipes `csa_staging`, rebuilds it, and creates the platform admins, companies, packages and invitations from a roster kept out of git.

**Architecture:** `scripts/staging_reset.py` runs on the host from the main checkout (`plan`, `wipe`, `accounts`) and reuses the helpers in `scripts/staging_rollout.py`. `scripts/staging_accounts.py` runs inside the running gateway, the only service that can send mail, and uses the console's own company, package and invitation code. `scripts/staging_roster.py` validates the roster for both.

**Tech Stack:** Python 3.12, psycopg3, pydantic, Docker Compose, Postgres 16 testcontainers.

**Spec:** `docs/superpowers/specs/2026-11-03-staging-reset-design.md`

## Global Constraints

- Staging only. The project `csa-staging`, database `csa_staging`, host `staging-db`, owner role `csa` and the volumes `csa-staging_staging-uploads` and `csa-staging_staging-hr-uploads` are constants in code, never arguments or environment.
- **The repository is public.** No real email address in code, tests, docs, commits or the PR. Tests use `example.test`.
- Synthetic fixtures and disposable testcontainers only. Tests never send mail, contact Telegram or Google, or touch staging containers. Never delete Testcontainers you didn't start.
- Never read, print or move anything under `/srv/csa-staging/secrets`. Never connect to production, its bot tokens or n8n. An implementer never runs `wipe` or `accounts` for real, and never starts or stops a staging container.
- No migration, no new role, never FORCE RLS, no change to the default for new companies, and no widened grant. If the app role can't do something, stop and report it.
- No password, invitation link or token in output, logs, files or argv.
- Don't edit `lib/navigation.ts` or `components/AppShell.tsx`. No console (web) change is expected.
- CI is the merge gate. Locally run each changed file's tests one at a time with `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q <file>`, plus `make lint`. Don't pipe `make lint`, because a pipe hides its exit code. Run `uv sync --frozen` first if the worktree has no `.venv`. If the shell guard refuses `VAR=value` prefixes, use a wrapper script in the scratchpad that exports them and `exec "$@"`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A wipe that failed part way is run again.** The database may be missing or have no schema, and services are stopped. `plan` and `wipe` must still work, and the new backup must not overwrite or hide the earlier good one. (Task 4)
2. **`accounts` is run on staging that was not wiped.** Real people would land beside the demo companies. It must refuse when any company outside the roster exists. (Task 2)
3. **`accounts` is run before the first admin changed their password, or with the wrong password.** It must explain what to do and create nothing. (Tasks 2 and 4)
4. **Mail fails for one person half way.** The others still get theirs, the output and exit code show who didn't, and a re-run says "already there" and points to the Team page rather than sending duplicates. (Task 2)
5. **Addresses differ only by case, or the roster is broken at the moment of `wipe`.** Duplicates are caught after normalising, and a broken roster refuses before anything is touched. (Tasks 1 and 3)

---

### Task 1: The roster

**Files:**
- Create: `scripts/staging_roster.py`, `deploy/staging-accounts.example.json`, `tests/test_staging_roster.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces, as pydantic models with `extra="forbid"`, frozen: `PlatformAdmin(email, display_name)`; `Member(email, display_name, role)`; `Company(slug, name, timezone, packages: tuple[str, ...], deployment_mode: Literal["shadow","suggest","auto"] | None = None, members: tuple[Member, ...])`; `Roster(platform_admins: tuple[PlatformAdmin, ...], companies: tuple[Company, ...])`.
- Produces: `class RosterError(ValueError)`; `parse_roster(data: object) -> Roster`; `load_roster(path: Path) -> Roster`; `ROSTER_PATH = REPO_ROOT / "deploy" / "staging-accounts.local.json"`; `Company.ordered_packages() -> tuple[str, ...]`, with each package after the ones it requires.

- [ ] **Step 1: Write the failing tests** in `tests/test_staging_roster.py`:
  - `test_example_roster_is_valid`: `load_roster` on the example gives three platform admins and three companies with slugs `team-jo`, `kedai-mawar`, `syarikat-cekap`; packages exactly as the spec's table; modes `auto`, `auto`, `None`; seven members in all; and every address ending `@example.test`.
  - `test_ordered_packages_puts_customer_support_first`: for a company listing `hr, subscriptions, customer_support`, `customer_support` comes before `subscriptions`.
  - `test_refusals`, parametrised, each raising `RosterError`: an unknown field; no platform admin; a company with no `tenant_admin`; role `platform_admin` for a member; unknown package key; unsellable package `marketplaces`; package missing its requirement (`subscriptions` without `customer_support`); mode `live`; bad slug `Team Jo`; bad timezone; two companies with the same slug; an address repeated with different case (`A@example.test`, `a@example.test`) across members; an address that is both platform admin and member; invalid email.
  - `test_errors_never_echo_the_roster_address`: the `RosterError` text for an invalid entry names its position, not the address.
  - `test_local_roster_is_ignored_by_git`: `git check-ignore deploy/staging-accounts.local.json` exits 0.
- [ ] **Step 2: Run them to confirm they fail**: `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_staging_roster.py`. Expected: import error.
- [ ] **Step 3: Implement.** Validate email with `csa.console.contracts.EmailInput` (it normalises) and slug, name and timezone with the same rules as `ClientIn` in `csa/console/onboarding.py`; reuse rather than repeat them. Packages come from `csa.console.packages.PACKAGES` (`sellable`, `requires`). Add `/deploy/staging-accounts.local.json` to `.gitignore`. The example has fake addresses, Team Jo's five members (tenant admin `tenant_admin`; HR owner, manager, staff `tenant_staff`; read-only `read_only`) with role-based display names such as "Team Jo HR Owner", and one `tenant_admin` each for Kedai Mawar and Syarikat Cekap.
- [ ] **Step 4: Run the tests.** Expected: all pass.
- [ ] **Step 5: Commit** with `feat(staging): a validated roster for the staging reset, kept out of git`.

### Task 2: The company step inside the gateway

**Files:**
- Create: `scripts/staging_accounts.py`, `tests/test_staging_accounts.py`
- Modify: `csa/console/onboarding.py`, `csa/console/auth.py`, `csa/console/identity.py`

**Interfaces:**
- Consumes: `Roster`, `Company` from Task 1.
- Produces in `csa/console/onboarding.py`: `async def create_client(pool: AsyncConnectionPool, actor: ConsoleActor, body: ClientIn) -> ClientOut`, holding the body of the route. It raises `UniqueViolation` as today, and the route keeps turning that into its 409.
- Produces in `csa/console/auth.py`: `async def invite_facts(pool, actor, client_id, *, mail_on) -> InviteFacts`, which `_invite_facts(request, ...)` delegates to; and `invitation_notify`, the current `_invitation_notify` renamed public with all callers updated.
- Produces in `csa/console/identity.py`: `async def verify_platform_admin(self, email: str, password: str, *, ip: str = "operator-cli") -> ConsoleActor`, with the same throttle (`purpose="admin_add"`) and the same checks as the approver in `add_platform_admin`: valid password, enabled, platform admin, no pending password change. On refusal it calls `_refuse_admin_add`. Also `async def account_exists(self, email: str) -> bool`. Refactor `add_platform_admin` to share the check.
- Produces in `scripts/staging_accounts.py`: `class AccountsStop(Exception)`; `approved_target(dsn: str, *, user: str) -> bool`; `async def run(pool, identity_pool_service: IdentityService, mailer: Mailer | None, password: str, roster: Roster, out: Callable[[str], None]) -> int`, returning 0 when everything is created or already there, 2 when any invitation was not sent; `main() -> int`.
- stdin format, one line of password, then the roster as JSON: Task 4 writes it and `main` reads it.

- [ ] **Step 1: Write the failing tests** in `tests/test_staging_accounts.py`, using `migrated_dsn` and the pool setup from `tests/test_seed_staging.py`, a synthetic admin created through `IdentityService.bootstrap` and then `change_password`, a roster from `deploy/staging-accounts.example.json`, and a fake `Transport` as in `tests/test_console_mail.py`:
  - `test_run_creates_the_roster_and_invites_each_member`: exit 0. Each company exists with exactly its packages `active` and each package's capabilities enabled; `clients.deployment_mode` and `config->'policy'->>'deployment_mode'` are `auto` for `team-jo` and `kedai-mawar`, `shadow` for `syarikat-cekap`; `console_audit` has one `tenant.deployment_mode_set` row per changed company naming the actor; the fake transport got exactly seven mails, one per member.
  - `test_output_has_no_token_or_link`: no line of the collected output contains `http` or the raw invitation token taken from the fake mail.
  - `test_second_run_changes_nothing_and_sends_nothing`: exit 0, zero new mails, counts of companies, packages and tokens unchanged, every line `already there`.
  - `test_refuses_with_mail_off`: with `mailer=None`, and again with a `Mailer(None, ...)`: `AccountsStop`, no company created.
  - `test_refuses_wrong_password` and `test_refuses_admin_who_must_change_password`: nothing created. The second's message says to sign in and change the password first.
  - `test_refuses_company_outside_the_roster`: an existing company `demo-x` gives `AccountsStop` naming the slug, nothing created (Review Focus 2).
  - `test_slug_with_a_different_name_stops` and `test_address_in_another_company_stops`: `AccountsStop`; the second names the position in the roster, and no mail goes out for it.
  - `test_one_mail_failure_continues_and_reports`: a transport that fails for one address: all seven accounts exist, six mails sent, that line says `mail not sent`, exit 2. A second run says `already there` for that person, sends nothing, and prints that an unsent invitation is resent from the Team page (Review Focus 4).
  - `test_approved_target`, parametrised like `test_seed_requires_approval_and_exact_isolated_target`: only `staging-db`, port 5432 or none, `/csa_staging`, the named user, no query string.
  - `test_main_refuses_a_non_staging_target_before_reading_stdin`.
  - In the existing route tests, confirm nothing changes: run `tests/test_console_mail.py` and the onboarding route test that `grep -l "admin/clients" tests/` finds.
- [ ] **Step 2: Run them to confirm they fail.** Expected: import error.
- [ ] **Step 3: Extract `create_client`, `invite_facts`, `invitation_notify`, `verify_platform_admin` and `account_exists`** with no change to route behaviour.
- [ ] **Step 4: Implement `run`:**
  1. Refuse if the mailer is not enabled. 2. `verify_platform_admin` using the first platform admin. 3. Read `console/client_list.sql`; refuse any company outside the roster, or a roster slug with another name. 4. Check all members before writing anything: an address whose account exists but isn't already a member or invitee of that company with that role stops the run. 5. For each company: `create_client` if missing; `activate_package` in `ordered_packages()` with `ActivateIn(reference="staging-reset")`, skipping active ones; if a mode is named and differs, `set_deployment_mode` from `csa/ops/runbooks.py` inside `tx(pool, str(client_id))` with one `console/audit.sql` row `tenant.deployment_mode_set` in the same transaction. 6. For each member not yet there: `identity.invite(actor, InviteIn(...), may_grant_hr=False, notify=invitation_notify(...))`. Read the delivery outcome from `IssuedTokenOut.delivery`; a `manual` delivery means the mail failed, so the line says `mail not sent`, and the returned token is discarded and never printed.
  One line per item, in the form `team-jo: package hr: created`. If the app role can't update `clients`, stop and report; don't widen a grant.
- [ ] **Step 5: Implement `main`:** `get_settings()`; refuse unless `approved_target` holds for `DATABASE_URL` (user `csa_app`) and `IDENTITY_DATABASE_URL` (user `csa_identity`), before reading stdin; read the password line and the roster from stdin; build the pools, `IdentityService` and `Mailer.from_settings` the way `apps/gateway/main.py` does; catch `AccountsStop`, print its message, return 1.
- [ ] **Step 6: Run** `tests/test_staging_accounts.py`, `tests/test_console_mail.py`, the onboarding route test and `make lint`. Expected: all pass, lint exit 0 (import-linter included).
- [ ] **Step 7: Commit** with `feat(staging): the company step creates the roster's companies and invitations inside the gateway`.

### Task 3: `plan` and the refusals

**Files:**
- Create: `scripts/staging_reset.py`, `tests/test_staging_reset.py`

**Interfaces:**
- Consumes from `scripts/staging_rollout.py`: `Runner`, `SubprocessRunner`, `Completed`, `RolloutError`, `compose_base`, `docker_base`, `build_env`, `load_flags`, `running_images`, `target_sha`, `secret_paths`, `missing_secrets`, `_report_stop`, `STOP_SERVICES`, `APP_SERVICES`, `PROJECT`, `FLAGS_FILE`, `REPO_ROOT`. Consumes `load_roster`, `ROSTER_PATH`, `RosterError`.
- Produces:
  - `DATABASE = "csa_staging"`, `UPLOAD_VOLUMES = ("csa-staging_staging-uploads", "csa-staging_staging-hr-uploads")`, `CONFIRM_PHRASE = "wipe csa-staging"`, `DROP_DATABASE_SQL`, `CREATE_DATABASE_SQL`, `BACKUP_ROOT = Path.home() / "csa-staging-backups"`.
  - `class ResetRunner(Runner, Protocol)`, adding `run_tty(argv, *, env) -> int` (stdin, stdout and stderr inherited) and `run_to_file(argv, *, env, path: Path) -> int` (stdout written as bytes to a new file with mode 0600). `class SubprocessResetRunner(SubprocessRunner)` implements it.
  - `@dataclass(frozen=True) class StagingState(database_exists: bool, has_schema: bool, slugs: tuple[str, ...], accounts: int, database_bytes: int, upload_files: dict[str, int], upload_bytes: dict[str, int])`.
  - `read_state(runner, env, python_image) -> StagingState`.
  - `@dataclass(frozen=True) class ResetContext(sha, env, python_image, roster, state)`, and `prepare(runner, repo, *, docker_config, roster_path=ROSTER_PATH, free_bytes=...) -> ResetContext`, which raises `RolloutError` for any refusal below.
  - `format_plan(context) -> str`; `main(argv=None, runner=None) -> int`.

- [ ] **Step 1: Write the failing tests** in `tests/test_staging_reset.py`, reusing the `FakeRunner` shape from `tests/test_staging_rollout.py` and adding recorded `run_tty` and `run_to_file`:
  - `test_plan_runs_only_read_commands`: every recorded call to `run`, `run_tty` and `run_to_file` is read-only. No `DROP`, `CREATE DATABASE`, `stop`, `up`, `find`, `-delete`, `pg_dump`, `run --rm`; only `inspect`, `image inspect`, `config`, `git`, `exec -T staging-db psql` with `SELECT`, and `docker run --network none` with `:ro` mounts to count files.
  - `test_plan_shows_what_will_go`: output has the short SHA, the current schema version and the newest migration it will rebuild up to, slugs, account count, each volume's file count, the roster's companies, packages and role counts, the "must be redone" list, and free disk against the backup size. It contains no member address. Ruling on the spec's "addresses are shown only in the terminal": `plan` may run through an agent, so it shows none, and the owner reads the local file.
  - `test_refusals`, parametrised on the fake's answers, each `RolloutError` with no destructive command recorded: a dirty checkout; HEAD not on `origin/main`; a service whose image revision differs from HEAD; a service whose revision can't be read; a missing roster; an invalid roster (Review Focus 5); a missing secret file; too little disk.
  - `test_plan_works_when_the_database_is_missing`: psql on `postgres` says `csa_staging` doesn't exist: `database_exists=False`, zero counts, exit 0, and the plan says so.
  - `test_plan_works_when_the_database_has_no_schema`: `has_schema=False`.
  - `test_cli_takes_no_target_arguments`: `main(["wipe", "--project", "x"])`, `main(["wipe", "--database", "x"])`, `main(["plan", "--sha", "x"])` exit 2 via argparse, with no runner call.
  - `test_targets_are_fixed_when_env_is_set`: with `CSA_STAGING_PROJECT=other` and `COMPOSE_PROJECT_NAME=other` in the process environment, every compose argv has `--project-name csa-staging`.
- [ ] **Step 2: Run them to confirm they fail.** Expected: import error.
- [ ] **Step 3: Implement.** Images for `build_env` come from `running_images`. `read_state` asks the `postgres` maintenance database whether `csa_staging` exists and its size, then reads slugs and accounts only if `to_regclass('public.clients')` is set. Upload counts and sizes come from `docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true --user 1000:1000 -v <volume>:/data:ro --entrypoint sh <python image> -c ...`. The disk check: `shutil.disk_usage(Path.home())`, free must exceed `database_bytes + sum(upload_bytes) + 1 GiB`. `SubprocessResetRunner` sets `cwd=REPO_ROOT` like its parent.
- [ ] **Step 4: Run the tests.** Expected: all pass.
- [ ] **Step 5: Confirm the image has what the commands need**, with no staging data involved, using the image the staging gateway runs: `docker run --rm --network none --entrypoint sh <that python image ref from docker inspect> -c 'command -v tar find du wc'`. Expected: four paths. If one is missing, change the commands.
- [ ] **Step 6: Commit** with `feat(staging): the reset tool's read-only plan and its refusals`.

### Task 4: `wipe` and `accounts`

**Files:**
- Modify: `scripts/staging_reset.py`, `tests/test_staging_reset.py`
- Create: `tests/test_staging_reset_database.py`

**Interfaces:**
- Consumes: Task 3's `prepare`, `ResetContext`, `ResetRunner`; rollout's `_recreate`, `_await_ready`, `MIGRATION_APPROVAL`; `scripts.migrate.discover_migrations`. Consumes Task 2's stdin format.
- Produces: `wipe(runner, repo, *, docker_config, read_line=input, is_terminal=sys.stdin.isatty, free_bytes=..., clock=..., sleep=time.sleep, out=print) -> int`; `accounts(runner, repo, *, docker_config, is_terminal=..., read_password=getpass.getpass, out=print) -> int`.
- Fixed order for `wipe`: confirm → backup → stop → drop → create → empty volumes → baseline → migrate → confirm version → bootstrap → recreate → readiness → next step.

- [ ] **Step 1: Write the failing tests** in `tests/test_staging_reset.py`:
  - `test_wipe_refuses_without_a_terminal` and `test_wipe_refuses_a_wrong_phrase`, parametrised on `""`, `"yes"`, `"WIPE CSA-STAGING"` and `"wipe csa-staging "` with only the trailing space differing: returns non-zero, nothing but read commands recorded.
  - `test_wipe_runs_the_steps_in_order`: a fake that answers everything gives exit 0, and the recorded destructive calls appear in the fixed order. Assert exact fragments: `pg_dump -U csa -d csa_staging -Fc` through `run_to_file`; a `tar` of each volume mounted `:ro`; `stop` with all four services; `-d postgres` with `DROP DATABASE IF EXISTS csa_staging WITH (FORCE)`, then `CREATE DATABASE csa_staging OWNER csa`; for each of the exact two volumes `--network none` with `find /data -mindepth 1 -delete`; `to_regclass` emptiness read; `cat /app/db/schema_baseline.sql`, then psql with `-d csa_staging -v ON_ERROR_STOP=1` and that text as `input`; `--profile migration-approved run --rm --no-deps migrate` with the approval env; `run_tty` of `admin-bootstrap` with the first roster admin's email, `--create-first-platform-admin` and `CSA_STAGING_ADMIN_BOOTSTRAP_APPROVAL=approved-isolated-staging`.
  - `test_backup_failure_changes_nothing`: `pg_dump` exits 1: `Stopped at backup`, `Nothing on staging was changed.`, no `stop` or `DROP` recorded.
  - `test_each_failure_reports_its_state`, parametrised on drop, create, empty volume, baseline, migrate, version mismatch, bootstrap, recreate and readiness: stops there, runs nothing later, prints the step, the state (for example `csa_staging has no schema`, `services are stopped`), the backup directory, and `Run wipe again, or restore from the backup (deploy/STAGING_PIPELINE.md, "Resetting staging").`
  - `test_baseline_refuses_a_database_with_tables`: the emptiness read says no: stops without running the baseline.
  - `test_wipe_after_a_partial_wipe`: the state says the database is missing and services are stopped: the backup skips `pg_dump`, says so, still tars the volumes, and carries on (Review Focus 1).
  - `test_second_wipe_keeps_the_first_backup`: two runs at different clock values create two directories, and the earlier one isn't touched. The directory has mode 0700.
  - `test_accounts_adds_admins_then_runs_the_company_step`: `run_tty` of `admin-add` once for each further admin in order with `--approver-email <first>`, `--email`, `--display-name`, `--add-platform-admin` and `CSA_STAGING_ADMIN_ADD_APPROVAL=approved-isolated-staging`; then `docker exec -i csa-staging-gateway-1 python -m scripts.staging_accounts` through `run`, with `input` = password line + roster JSON. The password appears in no argv and no env value. The child's stdout is printed, and its exit code returned.
  - `test_accounts_stops_when_admin_add_fails`: the company step doesn't run, and the message says the first admin must have signed in and changed their password (Review Focus 3).
  - `test_accounts_skips_an_admin_who_already_exists`: read through `console_users` before each add. A re-run doesn't prompt for that admin.
  - `test_accounts_refuses_without_a_terminal`.
- [ ] **Step 2: Write the failing database tests** in `tests/test_staging_reset_database.py`, marked `requires_docker`, on `pg_container`:
  - `test_rebuild_sequence_ends_at_the_newest_migration`: create `csa_staging` in the disposable cluster, apply the baseline with psql (as `_apply_baseline` in `tests/conftest.py`, parameterised on the database), run migrations against it, insert one synthetic `clients` row; run `DROP_DATABASE_SQL` and `CREATE_DATABASE_SQL` from `scripts.staging_reset` while a second connection is open; apply the baseline and run migrations again. Then `max(version)` equals the newest of `discover_migrations` and equals `apps.gateway.main.EXPECTED_SCHEMA_VERSION`, `clients` is empty, immutable tables exist with their triggers, and `csa_app` and `csa_identity` can log in with the passwords the gate set and read what they could before.
  - `test_backup_restores`: from a migrated `csa_staging` with one `clients` row, using the exact `pg_dump -Fc` and `pg_restore` commands the docs print: dump, drop, create, restore. The row is back, the immutable-table triggers exist, and `csa_app` can read the row.
- [ ] **Step 3: Run them to confirm they fail.** Expected: `wipe` and `accounts` don't exist, and the constants fail or are absent.
- [ ] **Step 4: Implement `wipe` and `accounts`**, with `main` calling them. A single `_Stage` tracker gives the state line for each failure. The backup directory is `BACKUP_ROOT/<UTC timestamp>/`, created with mode 0700 and never reused or deleted. The baseline text comes from the running Python image through `docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true --entrypoint cat <image> /app/db/schema_baseline.sql`, as in `deploy/RELEASE.md`. The recreate order is `APP_SERVICES`, using `_recreate` with `expected` set to the running image, and `_await_ready`. `accounts` calls `prepare`, then asks for the password with `read_password`, then runs the steps.
- [ ] **Step 5: Run** `tests/test_staging_reset.py`, `tests/test_staging_reset_database.py`, `tests/test_staging_rollout.py` and `make lint`. Expected: all pass, lint exit 0.
- [ ] **Step 6: Commit** with `feat(staging): the reset tool wipes, rebuilds and sets up accounts`.

### Task 5: Docs

**Files:**
- Modify: `deploy/STAGING_PIPELINE.md`, `deploy/STAGING_SEED.md`, `docs/PROGRESS.md`

**Interfaces:**
- Consumes: the commands in Task 4, and the restore commands proven by `test_backup_restores`.

- [ ] **Step 1: Add the section "Resetting staging" to `deploy/STAGING_PIPELINE.md`**, in this order:
  - What it wipes and what stays: secret files, role passwords, `CREDENTIALS_KEY`, the embeddings cache and Google test users stay.
  - Before you start: the code is merged, published and rolled out (`staging_reset.py` runs from the checkout, but `staging_accounts.py` must be in the image); the roster file is in place with mode 0600; the invited mailboxes can receive mail within the 48 hours.
  - Run: `plan`, `wipe`, sign in and change the password, `accounts`, as copy-ready commands from `/home/amsyrzmi/CSAV2`. State that `wipe` and `accounts` need a real terminal and the owner's passwords, so an agent can only run `plan`.
  - The spec's "After the tool" list, all seven steps, with section 5's bot command using `--tenant team-jo`.
  - Restore from a backup: exactly the commands from `test_backup_restores`, plus untarring the two volumes.
  - Deleting a backup when no longer needed: the tool never does it, and backups hold staging's data.
- [ ] **Step 2: Correct section 11.** The Google client is External, in testing: only listed test users can connect, at most 100, and their access expires every 7 days. Publishing past testing needs Google's verification and a yearly security assessment.
- [ ] **Step 3: Add a line to `deploy/STAGING_SEED.md`:** a reset doesn't run the seed, and running it afterwards would add demo tenants beside the real ones, which `accounts` would then refuse on a re-run.
- [ ] **Step 4: Add the top entry to `docs/PROGRESS.md`** in the file's existing form: what changed, rulings, tests, the rollout order, and that deferred minors are in the PR description. No real addresses.
- [ ] **Step 5: Check.** Run `git grep -n -E "@(fourstacks\.my|gmail\.com|graduate\.utm\.my)" -- scripts tests deploy docs/superpowers/specs/2026-11-03-staging-reset-design.md docs/superpowers/plans/2026-11-03-staging-reset.md`. Expected: no line naming a new real person's address in anything this branch added (compare with `git diff origin/main...HEAD`). Run `make lint`, expected exit 0.
- [ ] **Step 6: Commit** with `docs(staging): how to reset staging, restore a backup, and the External Google client`.

## After merge (not tasks; the owner gates each one)

1. Publish approval, then the normal scripted rollout (no migrations).
2. The real roster file is written locally in the main checkout with mode 0600. It never goes in git.
3. The owner reads `plan`, then runs `wipe` and `accounts` at a terminal.
4. Acceptance from the spec: all ten accounts sign in, each company sees only its tabs, Team Jo's HR owner can be set on the Team page, and a Telegram message reaches Team Jo's Inbox and gets a reply from the bot.
