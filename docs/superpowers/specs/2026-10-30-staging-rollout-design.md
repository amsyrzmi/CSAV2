# Staging auto-publish and rollout script

Design, operations track. Date: 2026-10-30. Base: main `37e7cc5`.

## Intent

Today, getting a merge onto staging takes about ten manual steps, split between the owner and the
assistant:

1. wait for main CI;
2. dispatch `publish-staging-images`;
3. approve it;
4. read the digests from GHCR;
5. copy a long shell block from `deploy/STAGING_PIPELINE.md`;
6. pull;
7. stop the writers;
8. migrate;
9. recreate the services;
10. check readiness and run any check query.

The steps are easy to get wrong. The 0484/0485 rollout first had a gap between migrate and recreate
that let the old gateway write unscrubbed rows. The runbook also still points at the deleted
`/home/amsyrzmi/CSAV2-seed` checkout.

The owner wants fewer manual steps between a merge and staging, without losing the gates:

- CI stays the merge gate;
- the owner still approves every publish;
- the owner approves every change to staging.

## Goals

1. **Auto-publish:** a green push-to-main `ci` run starts `publish-staging-images` by itself. It
   still waits for the owner's approval on the `staging-images` environment.
2. **Rollout script:** `scripts/staging_rollout.py` with two subcommands.
   - `plan` changes nothing. It shows what a rollout to the published images of the checkout's
     HEAD would do.
   - `apply` carries out that plan.
   - It handles both image-only updates and rollouts that apply migrations (owner decision
     2026-10-30).
3. **Project skill:** `.claude/skills/staging-rollout/SKILL.md` lets "roll out to staging" run
   `plan`, show the result and, after the owner says go, run `apply`.
4. **Runbook:** `deploy/STAGING_PIPELINE.md` points at the script for routine rollouts. The
   `CSAV2-seed` path is corrected to `/home/amsyrzmi/CSAV2`.

## Non-goals

- No continuous deployment. Nothing reaches staging without the owner's go.
- No production, AWS or Terraform work.
- The first-time pipeline setup stays manual in the runbook, including:
  - embeddings init;
  - Caddy, the tunnel and webhooks;
  - secret files;
  - admin bootstrap.
- No automatic rollback of a migration; migrations cannot be un-applied.
- No change to how CI builds or verifies images, and no change to the publish job's checks.

## Anti-goals (a failure even if it works)

- The script reads, prints, copies or logs anything under `/srv/csa-staging/secrets`. It never
  opens those files; compose does.
- The script prints environment values other than the non-secret flags it set itself.
- It runs migrate while the old gateway is still able to write.
- `apply` does something `plan` did not show.
- It pulls by tag, or uses or stores registry credentials on the host.
- It touches any project other than `csa-staging`, or any other compose file set.

## Constraints

- Python 3.12, standard library only (`subprocess`, `argparse`, `json`, `pathlib`, `tempfile`).
  It runs on the host with `/home/amsyrzmi/CSAV2/.venv/bin/python -m scripts.staging_rollout`.
- Every external command is an argv list with no shell, run through one small runner object so
  tests can replace it.
- The compose invocation is exactly the runbook's:
  `docker compose --env-file /dev/null --project-name csa-staging` with the three files
  `deploy/compose.staging-local-proposal.yml`, `deploy/compose.staging-shared-caddy.yml`,
  `deploy/compose.staging-pipeline.yml` and profiles `staging-approved`, `pipeline-approved`.
  Migrate adds `--profile migration-approved run --rm --no-deps migrate`.
- Images are public on GHCR (`ghcr.io/amsyrzmi/csa-python`, `ghcr.io/amsyrzmi/csa-frontend`). The
  pull runs with `DOCKER_CONFIG` pointed at a fresh empty `0700` temp directory, removed
  afterwards, so no host credentials are used or written. Pulls are by digest only, and then
  verified:
  - `RepoDigests` must contain the reference;
  - the label `org.opencontainers.image.revision` must equal the SHA.
- Tests use synthetic fixtures and a fake runner only. No test calls Docker, GitHub or staging.
- Locally: run only the new test file, plus `make lint`. The full suite runs in PR CI.

## Approaches considered

- **A. Python script plus skill (chosen by the owner, 2026-10-30).** It matches
  `scripts/staging_migrate.py` and `scripts/staging_secrets.py`, can be unit-tested with a fake
  runner, and keeps secret handling inside compose.
- B. A bash script. It is closer to the runbook text, but harder to test and harder to keep from
  echoing values.
- C. A skill only, no script. Nothing new to build, but each rollout is about ten permission
  prompts, and the step order lives in the assistant's memory.

For auto-publish there was one sensible way: a `workflow_run` trigger on the existing workflow.
A publish job inside `ci.yml` would put registry write access in the test workflow, and the
approval gate would still apply.

## Design

### 1. Auto-publish (`.github/workflows/publish-staging-images.yml`)

- **Trigger:** add `workflow_run: {workflows: [ci], types: [completed], branches: [main]}` next to
  the existing `workflow_dispatch`.
- **Job condition:** run when `github.event_name == 'workflow_dispatch'`, or when
  `github.event.workflow_run.event == 'push'` and
  `github.event.workflow_run.conclusion == 'success'`. A pull-request `ci` run never publishes.
- **Run id:** `CI_RUN_ID` becomes `inputs.ci_run_id || github.event.workflow_run.id`.
- **Unchanged:**
  - every existing verification step, including the run-identity check and the
    ancestor-of-main check;
  - the `staging-images` environment and its required reviewer (the owner);
  - concurrency (`cancel-in-progress: false`).
- **Effect:** after a merge the owner gets the usual approval request about 30 minutes later.
  Manual dispatch keeps working for re-publishing an older run.

### 2. Rollout script (`scripts/staging_rollout.py`)

**Inputs.** The script runs from a checkout of `main`. The target is the checkout's HEAD SHA. A
clean worktree is required. `plan` and `apply` both refuse if:

- HEAD is not on `origin/main`;
- the worktree has tracked changes.

**Flags file.** The non-secret staging switches live in a checked-in file,
`deploy/staging-rollout.env`. It holds `KEY=value` lines for an allowlist of keys only:

| Key | Value today |
|---|---|
| `CSA_STAGING_CONSOLE_ORIGIN` | `https://staging-console.fourstacks.my` |
| `CSA_STAGING_HOOKS_ORIGIN` | `https://hooks.fourstacks.my` |
| `CSA_STAGING_WORKSPACE_ASSISTANT` | `true` |
| `CSA_STAGING_GATEWAY_MODEL_SECRET` | `staging_model_key` (a secret name, not a value) |
| `CSA_STAGING_INVITE_MAIL` | `true` |
| `CSA_STAGING_EMAIL_ASSISTANT` | `true` |

- Unknown keys are refused.
- The script itself sets:
  - `NO_LOCAL_ENV=1`, `ENV=ci` and an empty `COMPOSE_PROFILES`;
  - `CSA_STAGING_PROJECT=csa-staging` and `CSA_STAGING_SECRET_DIR=/srv/csa-staging/secrets`;
  - the two image references.
- Changing a switch becomes a reviewed one-line PR, rather than something the assistant has to
  remember.

**Digest lookup.** For each of `csa-python` and `csa-frontend`, the script reads
`gh api /users/amsyrzmi/packages/container/<name>/versions` and takes the version whose tags
include the full HEAD SHA.

- If either is missing, the script reports "not published yet". If one exists, it also reports
  the latest `publish-staging-images` run for that SHA and whether it is waiting for approval.
  Then it stops.
- Any `gh` failure stops the script with its exit status, and nothing is printed from its stderr
  beyond the first line.

**Staging state** (read-only):

- **Running images:** for each app service it records the image digest the container is running,
  via `docker inspect` of the `csa-staging-<service>-1` containers, reading `.Image` and the
  image's `RepoDigests`. These are printed as the rollback references.
- **Applied migrations:** `SELECT version FROM schema_migrations ORDER BY version` through
  `compose exec -T staging-db psql -U csa -d csa_staging -tA -v ON_ERROR_STOP=1`.
- **Migrations in the checkout:** the versions in `db/migrations/*.sql`, using the same filename
  rule as `scripts/migrate.py`. The parser is imported, not copied.
- **Pending** = checkout versions minus applied.
- **Refusal:** if staging has a version the checkout does not have (staging is ahead), the script
  refuses.

**`plan` output.** Plain text, with no secrets and no row contents:

- the target SHA;
- both new digests;
- the running digests, as rollback references;
- the pending migrations, or "none";
- the ordered steps `apply` will run;
- for a migration rollout, the downtime warning: console unavailable, bot replies delayed;
- any check files that will run;
- the exact `apply` command to run.

**Extra confirmation for migrations.**

- When migrations are pending, `apply` requires `--migrations <v1,v2,...>` matching the pending
  list exactly. `plan` prints that list.
- A mismatch means staging or the checkout changed since `plan`. The script then refuses and
  says to re-run `plan`.
- With nothing pending, `--migrations` must be absent.

**`apply` steps.** These run in order and stop at the first failure.

1. Re-run everything `plan` checks, and refuse if the result differs from the confirmation
   given.
2. Run `compose config --quiet`.
3. Pull both images by digest with the empty `DOCKER_CONFIG`, then verify the digest and the
   revision label.
4. Only if migrations are pending:
   - `compose stop gateway frontend worker delivery`, so no old code runs against the new schema
     (owner decision 2026-10-30, for every migration). Bot replies queue and are delayed for those
     minutes, not lost;
   - the `migrate` one-shot with `CSA_STAGING_MIGRATION_APPROVAL=approved-isolated-staging`, set
     in the child process's environment only;
   - re-read `schema_migrations`, which must now contain every planned version.
5. For each of `gateway`, `worker`, `delivery` and `frontend`, in that order:
   `compose up --detach --wait --wait-timeout 300 --no-deps --pull never <service>`.
6. Readiness: `GET http://127.0.0.1:8080/readyz` inside the gateway container (the runbook's
   `docker exec … python -c` probe) must return the ready body.
7. Check files: for each version applied in this run that has a file
   `deploy/rollout_checks/<version>.sql`, run it through the same `psql` exec. The file prints one
   integer, which must be `0`.
   - The 0485 check moves from the runbook into `deploy/rollout_checks/0485.sql`.
   - Check files are the only SQL besides `SELECT version` that the script sends, and they must
     be read-only `SELECT`s. That is review policy, stated in the file header; the script does
     not parse them.
8. Print `compose ps --format '{{.Service}} {{.Status}}'` and a one-line result.

**Failure output.** On any failure the script prints:

- which step failed;
- what state staging is in, for example "gateway, frontend, worker and delivery are stopped; migration 0486 applied
  or not, see `schema_migrations`";
- the recovery commands: restart the stopped services, or recreate them on the previous images
  using the recorded rollback digests.

It never retries a migrate and never tries a rollback by itself.

**Output hygiene.** Command output is shown only for compose status lines and for migrate's
`exit=` status. Migrate's own output is shown, because it prints versions and not values. Nothing
from `docker inspect` is printed except digests.

### 3. Skill (`.claude/skills/staging-rollout/SKILL.md`, committed)

When the owner asks to roll out to staging:

1. In the main checkout: `git pull --ff-only origin main`.
2. Run `plan` and show the owner a short summary: what changes, pending migrations, downtime.
3. Wait for the owner's go. A go covers this one plan only.
4. Run `apply` with the exact flags `plan` printed, and report the result.

If a command is blocked, give the owner the numbered commands to run instead. The skill also
covers where to start: the main checkout, never a worktree.

**Permission rule.** Add `Bash(.venv/bin/python -m scripts.staging_rollout plan:*)` to the
host-local `.claude/settings.local.json` (not committed). `apply` is not allowlisted, so it always
asks.

### 4. Docs

- `deploy/STAGING_PIPELINE.md`:
  - a short "Routine rollouts" section pointing at the script;
  - §2's path fixed;
  - §11.1's check query replaced by a pointer to `deploy/rollout_checks/0485.sql`.
- `docs/PROGRESS.md`: one entry, including the 0484/0485 and PR #54 staging rollouts that are not
  yet recorded.

## Settled after the builder check (assistant's calls, 2026-10-30)

- **Secret-file preflight.** Compose refuses to recreate the gateway when a declared secret file is
  missing. Without a preflight, a migration rollout could stop the console, migrate, and only then
  fail. So both `plan` and `apply` read the `file:` paths of every secret in
  `compose config --format json`, and refuse if any path does not exist.
  - It uses existence only (`Path.exists`). It never opens, reads or prints a file, and never
    prints a path's contents or size.
  - A path that cannot be checked is treated as missing.
  - The secret directory is `0700`, owned by the host user, so existence checks work without sudo.
- **A failed check file.** The check runs after the services are back up. A non-zero result stops
  `gateway`, `frontend`, `worker` and `delivery` again, per the runbook's "stop and investigate before the console is
  used again", and the script then exits non-zero.
- **Re-running after a partial failure** is the supported recovery path.
  - `plan` tolerates stopped or absent app containers; their rollback reference shows as
    "unknown (not running)".
  - Every check file in `deploy/rollout_checks/` runs on every `apply`, not only for versions
    applied in that run. They are read-only and cheap, so a check skipped by an interrupted run
    still runs on the retry.
- **Digest lookup** uses the host's existing `gh` login. Staging runs on the same host, and the
  token has `write:packages`, which includes read. `gh` stays a lookup tool; Docker never gets a
  credential.
- **Docker context.** The host uses the `default` context (`unix:///var/run/docker.sock`).
  With an empty `DOCKER_CONFIG` the CLI falls back to that same socket. The script passes
  `--host unix:///var/run/docker.sock` explicitly, so the pull and compose always reach the same
  daemon.
- **The permission rule** is host-local, so the PR cannot add it. After merge, the assistant
  proposes it to the owner, who approves the change to `.claude/settings.local.json`.
- **Publish workflow:** `actions/download-artifact`'s `run-id` must use the same
  `inputs.ci_run_id || github.event.workflow_run.id` expression as `CI_RUN_ID`.

## Left to the builder

- Module layout inside the script, and the exact output wording.
- How the fake runner records and answers calls.
- The `--wait-timeout` and readiness retry details: a few tries a few seconds apart is fine.
