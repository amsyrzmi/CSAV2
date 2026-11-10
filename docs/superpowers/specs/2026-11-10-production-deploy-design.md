# Production deploy from GitHub

Status: draft for owner approval, 2026-11-10. Builds on
`2026-11-04-lightsail-s3-interim-hosting-design.md` (the single-host production stack) and
`deploy/PRODUCTION.md`. No real hostnames, addresses or account names appear here; the repository
is public.

## Intent

Production (one Lightsail instance, project `csa-prod`) went live on 2026-11-09. Every update since
has been typed by hand: images loaded with `docker save | scp | docker load`, migrations run with a
one-shot, services recreated one by one. The owner wants updates to be a short, safe routine run
from GitHub, including from a phone, instead of a terminal session.

The rule the owner chose: **production only receives code that staging has already run.** Staging
(the home server) is where a change is first proven; production must never be ahead of it.

## Goals

- A manually started GitHub workflow, `deploy-production`, shows a plan (target commit, image
  digests, pending migrations, downtime), waits for the owner's approval in a GitHub environment,
  then applies it.
- Production refuses any commit that has no successful staging rollout recorded.
- Staging and production use the same rollout logic, so a fix to one is a fix to both.
- Same safety properties as the staging tool: plan then apply with a confirmation that the plan did
  not change, images verified by digest and revision label, migrations recorded before services
  return, readiness and check files before success, and recovery lines on failure.
- A backup is taken before anything changes.

## Non-goals

- Deploying automatically on merge. A human approves every production deploy.
- Automatic rollback. A migration cannot be safely reversed by a script; the tool stops at the first
  failure, prints the state and the recovery commands, and never retries or rolls back by itself
  (same as staging).
- Zero-downtime migrations. A migration stops the four app services; the plan says so beforehand.
- Moving the GHCR packages to the GitHub organisation, or changing the image publish workflow. The
  image owner stays one constant so a later move is a one-line change.
- A self-hosted runner. The instance has no memory to spare for one.

## Anti-goals

- Any path that puts unstaged code on production, including a flag to skip the check.
- A deploy credential that gives a general shell on the instance.
- A change that makes a staging rollout behave differently from today.
- Secret values in the repository, workflow logs, or the plan output.

## How it works today (facts the design rests on)

- `.github/workflows/publish-staging-images.yml` publishes the exact CI image archives of a green
  `main` run to GHCR (`csa-python`, `csa-frontend`), tagged with the full commit SHA, after the owner
  approves the `staging-images` environment.
- `scripts/staging_rollout.py` (about 1,000 lines, `tests/test_staging_rollout.py` about 1,600)
  runs on the home server from a clean checkout that is on `origin/main`. `plan` is read-only.
  `apply --sha` re-derives the plan, pulls and verifies the images, applies pending migrations
  (stopping gateway, frontend, worker and delivery), recreates the four services in order, waits for
  `/readyz`, runs the SQL files in `deploy/rollout_checks`, and prints recovery lines on failure.
  It resolves a commit to image digests with `gh api` over the package versions.
- Staging constants live in the script: project `csa-staging`, secret directory
  `/srv/csa-staging/secrets`, three compose files, the profiles `staging-approved` and
  `pipeline-approved`, the image owner, the eight switches in `deploy/staging-rollout.env`, the
  container names `csa-staging-<service>-1`, and the database service `staging-db` / database
  `csa_staging`.
- Production is the same three compose files plus `deploy/compose.production.yml`, project `csa-prod`,
  secrets under `/srv/csa-prod/secrets`. Its switches and tuning (`CSA_STAGING_*`, `CSA_PROD_*`)
  currently live in a hand-edited `~/csa-prod-env.sh` on the instance, together with a `csa_compose`
  shell function. The images running there are local tags (`csa/python:prod-<sha7>`) with no registry
  digest. The worker runs as two replicas (`csa-prod-worker-1`, `-2`). The tunnel connector, database
  and embeddings services are never recreated by a rollout.
- `scripts/migrate.py` imports `psycopg`, and the rollout script imports `scripts.migrate`, so the
  rollout needs Python dependencies on the host it runs on. The instance has system Python only.
- The instance can reach the public repository over HTTPS without credentials, and pull the public
  GHCR images by digest without a registry login. Only `gh`'s package-version lookup needs a token
  (GitHub's package API asks for one even for public packages); the developer who owns the packages
  must supply it.
- The instance is a Lightsail instance, which Systems Manager does not manage by default. AWS
  documents registering one through a hybrid activation (an agent on the host that calls out to AWS;
  no inbound port).

## Constraints

- 2 GB instance with little free memory; the deploy must not add a resident process.
- The instance's firewall allows SSH only from the owner's home address, and GitHub runner
  addresses are not stable. The deploy transport must therefore need no inbound port. The owner
  asked for the industry-standard approach, not a custom one, and prefers AWS-only tooling.
- The home server belongs to the other developer. Changing `staging_rollout.py` changes what staging
  rolls out with; it goes through a PR and CI, and its first test is a real staging rollout.
- `build_env` gives docker and compose an empty inherited environment on purpose. Production's
  `CSA_PROD_*` values therefore cannot stay only in a host file the script does not read.

## Approaches considered

- **A. One shared rollout core, two targets (chosen).** The constants above become a `Target`
  definition; staging and production are two instances of it. The existing tests guard the shared
  logic. Cost: a change to the script staging uses, mitigated by keeping the staging target's
  behaviour byte-identical and rolling staging first.
- **B. A separate `production_rollout.py`.** Lowest risk to staging, but about 1,000 duplicated
  lines and every fix made twice. Rejected.
- **C. A small shell script on the instance.** Quickest, no tests, loses the plan/confirm guard.
  Rejected.

Transport (how the workflow runs the script on the instance), considered separately:

- **AWS Systems Manager Run Command with GitHub OIDC (chosen).** The workflow exchanges its
  short-lived GitHub identity token for a temporary AWS role (no stored AWS keys), and sends a
  command through Systems Manager to the registered instance. The instance's agent polls outward,
  so no port is opened and no SSH key exists. The role may run one named document on one instance.
  Every command is recorded in AWS (CloudTrail and command history).
- **SSH from the runner with a forced-command key.** Needs port 22 open to the world and a long-lived
  private key stored in GitHub. Rejected by the owner.
- **A custom poller on the instance reading GitHub for approved deploys.** Works, but is bespoke
  machinery the owner would have to trust and maintain. Rejected.
- **SSH through a Cloudflare tunnel.** Adds a second vendor for access control. Rejected.

## Design

### 1. Rollout core with targets

`scripts/staging_rollout.py` keeps its command line and its behaviour for staging. The module's
constants move into a frozen `Target` (name, compose project, secret directory, compose files,
profiles, container name pattern with replica count, database service/database/user, image owner,
the app services in recreate order, flags file, allowed flag keys, the migration approval pair,
whether the staged gate applies, and an optional command to run before anything changes). Two targets
exist: `staging` (today's values) and `production`. A `--target` option selects one; the default is
`staging`, so the staging skill and docs keep working unchanged. Whether the module is renamed or
split (for example `scripts/rollout/`) is left to the builder, provided the old entry point
`python -m scripts.staging_rollout` keeps working for staging.

Production specifics the core must handle:

- Replicated services: `worker` has two containers. Recreate uses compose (which handles replicas);
  the "running the planned image" verification checks every replica.
- No registry digest on the running images the first time (they are local tags). The rollback record
  is then "unknown" for that first deploy, and the recovery text says so rather than failing.
- Production profile set and compose file list (above), and the production switch file.

### 2. Production switches in the repository

A reviewed file `deploy/production-rollout.env` holds every non-secret production setting the script
passes to compose, including the `CSA_PROD_*` tuning and `CSA_PROD_S3_*` names that today live in
`~/csa-prod-env.sh`. The first plan step is to read the instance's current file and capture its
values exactly, so the first scripted deploy changes no setting. After the first deploy the host file
and the `csa_compose` function are retired (manual maintenance can use a function that sources the
repo file). Values that are not secret but identify the deployment (bucket name, hostnames) are
allowed in the file only if the owner confirms they may be public; otherwise they come from a file
on the instance outside the repository that the script reads in addition. This is the owner's call
(see "Left to the owner and the developer").

### 3. The staged gate

- When a staging `apply` finishes successfully (after the check files), it records the commit as a
  successful GitHub Deployment in an environment named `staging`: create the deployment for the full
  SHA with no required contexts, then post a `success` status with `auto_inactive` set to false, so
  an older commit keeps its success after a newer one is deployed. The write uses the `gh` login the
  script already uses. A failed rollout records nothing.
- If recording fails after a successful rollout, the tool prints a loud warning with the exact
  command to retry, exits 0 (staging is fine), and offers `record-staged --sha <sha>` as a separate
  subcommand for the retry.
- The production target's `plan` and `apply` both check the record for the exact target SHA and stop
  with `commit <sha7> has not been rolled out to staging` if there is none. There is no bypass flag.
  The check runs inside the script, not only in the workflow, so running the script by hand on the
  instance is held to the same rule.
- Deploying an older staged commit is allowed by the gate; the existing "database has migrations this
  checkout lacks" check is what stops a downgrade across a migration.

### 4. The instance

- One dedicated checkout (the existing `~/CSAV2`, clean, detached) used only for deploys. The entry
  script runs `git fetch origin main` and `git checkout --detach <sha>` before planning; the repo is
  public so no credential is needed for git.
- A Python environment with the rollout's dependencies (at least `psycopg`), created once and kept
  outside the repository; the builder decides between a venv on the host and running the script
  inside an image, weighing disk and memory.
- `gh` installed and logged in with a read-only token (a classic token with only `read:packages`;
  the repository's deployments are public), stored for the `ubuntu` user in
  `~/.config/gh/hosts.yml`. No `docker login`: the rollout pulls the public images by digest with an
  empty `DOCKER_CONFIG`. The token is the one dependency on the developer.
- The Systems Manager agent, registered once through a hybrid activation (a service role with the
  managed-instance core policy, an activation code, the agent installed and registered). It adds a
  small resident process; its memory use is measured on the instance before the first deploy through
  it, against the capacity sampler's headroom numbers.
- Two custom Systems Manager command documents, kept in the repository and created in AWS from those
  files: `csa-production-plan` (`deploy/production/ssm-document-plan.json`; one parameter, the commit
  SHA; the action `plan` is written into its command) and `csa-production-apply`
  (`deploy/production/ssm-document-apply.json`; the SHA, an optional migrations list and the host
  settings digest; the action `apply` is written into its command). Every parameter has an
  `allowedPattern`, so Systems Manager itself rejects anything else. Each document's only step runs
  the entry script below as the deploy user (commands run as root by default). They are split
  because IAM cannot restrict a document's parameter values: one document taking an `action` would
  let the role that may plan also apply (see section 5).
- A root-owned entry script `deploy/production/deploy_entry.sh`, installed outside the checkout. It
  re-validates the arguments, takes a `flock` so two deploys cannot overlap, runs `git fetch` and
  `git checkout --detach` in the dedicated checkout, and runs the rollout. It never reads anything
  interactive: a prompt would hang until the command times out.
- The production target's pre-change command is the existing backup script, run without `sudo`
  (the lock file is owned by the deploy user). A failed backup stops the deploy before anything
  changes.

### 5. The workflow

`.github/workflows/deploy-production.yml`, `workflow_dispatch` only:

- Input `sha` (optional). Empty means the newest commit on `main` with a successful `staging`
  deployment and published images; the workflow resolves it on the runner.
- Job `plan`: assumes the AWS role through OIDC, sends the `plan` command to the instance, waits for
  it, writes the target, digests, pending migrations, downtime, and check files to the job summary,
  and exposes the SHA and migration list as outputs.
- Job `apply`: `environment: production` (required reviewer: the owner), needs `plan`, assumes the
  role again (credentials are short-lived), and sends `apply` with exactly the SHA and migrations
  `plan` printed. The script's own confirmation refuses if either changed. The job fails if the
  command's status is not success and prints its output either way.
- Both jobs share `concurrency: deploy-production` with no cancel-in-progress. Permissions are the
  minimum (`contents: read`, `deployments: read`, `id-token: write`). Third-party actions are pinned to
  a commit SHA.
- The AWS side, created once: an OIDC identity provider for GitHub and two roles. The plan role's
  trust policy allows only this repository and only the `production-plan` environment, and its
  permissions allow only `ssm:SendCommand` on the plan document and the one instance, plus
  `ssm:GetCommandInvocation`. The apply role's trust policy allows only the `production` environment
  (which has the reviewers), and its permissions allow only `ssm:SendCommand` on the apply document
  and the one instance, plus `ssm:GetCommandInvocation`. Neither role has any other AWS access. Held
  in GitHub as environment secrets (masked in the logs of a public repository): `PROD_PLAN_ROLE_ARN`
  on `production-plan`, `PROD_APPLY_ROLE_ARN` on `production`, `PROD_INSTANCE_ID` on both; the region
  is the repository variable `PROD_DEPLOY_REGION`. The workflow fails closed if any is missing. Both
  environments are restricted to deployments from `main`, and both jobs also check `github.ref`.
  The `plan` job runs in a second environment, `production-plan`, with no required reviewer, because
  an OIDC token's subject names the environment.
- The summary also shows the staged record it found (date of the staging rollout).

### 6. First deploy

The first production deploy is a normal one: the running images are local tags, the plan says the
rollback record is unknown for this deploy, the new images come from GHCR by digest, and the host's
local tags can be removed afterwards. It must be a commit that staging has already run, which means
the first staging rollout after this feature merges (the one that proves the shared core on staging)
also writes the first record.

### 7. Failure handling

Unchanged from staging: stop at the first failure, print the step, the state the host is in, and the
recovery lines that match how far it got. Added for production: if the backup step fails nothing was
changed (say so); if migrations were applied, the text says fix forward or restore the backup taken
at the start (name it). No automatic retry, restart or rollback.

### 8. Tests

- The existing staging tests must pass unchanged in behaviour (they are the regression guard).
- New tests for: the production target's rendered compose command and environment; replica-aware
  verification; the unknown-rollback first deploy; the gate (record present, absent, recording
  failure, `auto_inactive`); the entry script's validation (rejects any command that is not
  `plan`/`apply` with valid arguments, including injection attempts); the Systems Manager document's
  `allowedPattern` rules; the workflow's permission rules checked as text (including pinned actions
  and the OIDC trust condition in the role's policy file).
- A synthetic end-to-end on a throwaway compose project is desirable; the capacity tooling already
  shows a pattern for a stack that is not the real one.

### 9. Documentation and skills

`deploy/PRODUCTION.md` gains the deploy section and the instance setup steps (token, key, entry
script, environment in GitHub); the manual procedure used on 2026-11-08 stays as a break-glass
runbook. The `staging-rollout` skill gains one line (staging success is recorded for production) and a
sibling production skill is optional.

## Decided by the owner

- A human approves every production deploy; production only receives commits staging has run.
- Approach A (one shared rollout core, two targets).
- The transport follows the industry standard for AWS and needs no inbound port (the owner rejected
  an open SSH port and a custom poller). The specific mechanism, Systems Manager with GitHub OIDC,
  is the builder's recommendation, recorded under "Calls made after the builder check".
- The manual break-glass procedure stays as an owner-only path for an urgent fix when staging is
  unavailable. It sits outside the script and so outside the "staged first" rule, and the runbook says
  so in plain words.

## Calls made after the builder check (the owner may change any of them)

- **Recording the staged commit.** The home server's `gh` login already has the `repo` scope, which
  covers creating deployments, so no new token is needed on staging. The owner runs staging rollouts
  through the existing `staging-rollout` flow; the first rollout after this merges writes the first
  record.
- **Entry script location.** The deploy entry script is a root-owned copy installed outside the
  checkout (for example under `/usr/local/sbin`), so a deploy that moves the checkout cannot change
  the program Systems Manager runs. Updating it is a documented manual reinstall.
- **Transport.** Systems Manager Run Command with GitHub OIDC, chosen as the industry-standard,
  AWS-only option that needs no inbound port and no stored keys. The same design carries over if the
  host later becomes EC2, where the instance role replaces the hybrid activation. Two things are
  unverified and checked first in the plan: the cost of Run Command on a hybrid-registered node (AWS's
  documentation gives conflicting dates for a move to pay-as-you-go pricing in 2026), and the agent's
  memory use on this host.
- **The backup step.** The backup script moves into the repository (`deploy/production/backup.sh`) as
  the source of truth and is installed onto the instance by copy, so the deploy and the 6-hourly cron
  run the same file and share its lock. It takes no arguments, exits 0 on success, and takes about 3
  seconds. It exits 0 with a "skipped" log line when another backup holds the lock, so the deploy must
  not trust the exit code alone: it reads the newest log line, requires `ok` with a timestamp after
  the deploy started, and prints the S3 object name (`backups/csa_staging-<timestamp>.dump`, derived
  from that timestamp) in the recovery text. The deploy never lists S3, because the application key is
  put-only there.
- **Tuning without a pull request.** `CSA_PROD_*` tuning (memory limits, Postgres settings, worker
  replicas) stays editable on the instance in an allow-listed host file that overrides the repository
  file for those keys only. It never accepts switches or `CSA_PROD_GROQ_CHAT_URL` (a capacity-test
  setting that sends the model key to whatever URL it names). The plan prints the host file's
  effective values, and `apply` refuses if they changed since `plan`.
- **Public or private values.** Hostnames go in the repository file, as the staging file already does
  with its hostnames. The bucket name and region stay in the host file.
- **Downgrade.** The gate allows any staged commit, because rolling back to an older staged commit is
  the normal recovery from a bad deploy that has no migration. The plan warns loudly when the target
  is older than what production is running, using the running image's revision label, and the owner's
  approval is the decision. A downgrade across a migration is still refused.

## Left to the builder

- Rename/split of the rollout module; exact `Target` fields; how replicas are verified.
- Venv on the host versus running the script in an image.
- Whether production's digest lookup uses `gh` (as staging) or the registry directly.
- Shape of the job summary; the test layout.

## Left to the owner and the developer

- The developer provides the read-only `gh` token (`read:packages`; no registry login is needed,
  the packages are public) and a way to renew it.
- The owner (who holds the AWS admin login) creates the Systems Manager activation, the GitHub OIDC
  provider, the two deploy roles and the two command documents; the plan lists the exact console or
  CLI steps. The owner also creates the two GitHub environments, both limited to `main`:
  `production-plan` (no reviewer) and `production` (the owner and the other developer as required
  reviewers; one approval is enough, self-review allowed). The environment secrets are
  `PROD_PLAN_ROLE_ARN` (on `production-plan`), `PROD_APPLY_ROLE_ARN` (on `production`) and
  `PROD_INSTANCE_ID` (on both); the region is the repository variable `PROD_DEPLOY_REGION`.
- The owner decides whether the bucket name and hostnames may appear in the public repository (if not,
  they stay in a file on the instance).
- Each production deploy is approved by the owner; the first one is watched live.

## Changes made while building (2026-11-10)

- Two roles and two documents instead of one (a review found that one role trusted by both
  environments could send `apply` from the reviewer-less `production-plan` environment).
- The role ARNs and the instance id are environment secrets, not repository variables, so a public
  repository's logs do not show them.
- `deploy_entry.sh` refuses a commit that is not an ancestor of `origin/main`, and pins `LC_ALL=C`
  for its validation.
- Production hostnames (console and hooks origins) live in the host file on the instance, not in the
  repository, to keep real hostnames out of the public repository.
- After the final review: the runner (`ssm_run.sh`) gives up with exit 124 after 2100 seconds of
  elapsed time since the send (bash `SECONDS`), not after a count of polls, so its wait stays below
  the documents' 2400-second timeout, itself below the jobs' 45 minutes. Apply leaves an empty
  `migrations` parameter out (the document defaults it to empty). The workflow brackets instance
  output in `::stop-commands::` so it cannot issue workflow commands. The entry script starts with
  `cd /` and exits 73 before any step when its log cannot be written. `record-staged --sha` refuses
  a commit that is not on `origin/main` in the live checkout.
