# Deploying CSA: staging and production

This is the entry runbook for getting a merged commit onto the two deploy targets: staging (the
home server) and production (the interim single host). It holds what the two targets share: the
pipeline, the routine for one release, the auto-planned production runs, rollback, the one-time
setup with its two scripts, and break-glass. What belongs to one target only (host paths, secret
files, install steps, restore, pruning, feature rollouts) stays in `deploy/PRODUCTION.md` and
`deploy/STAGING_PIPELINE.md`, and those two documents point here for the rest. "Where each fact
lives", at the end, says which document holds what.

The repository is public. Every value that identifies a deployment (account, region, hostname,
address, instance, role ARN, bucket, origin) is written here as a placeholder such as `<region>`,
`<managed-instance-id>`, `<login-1>` or `<login-2>`, and lives only in AWS, on the hosts or in
GitHub secrets. The repository itself is named literally, `amsyrzmi/CSAV2`, in the commands.

## The pipeline

```
PR -> CI -> merge -> CI on main -> publish-staging-images        (automatic)
  -> deploy-staging   (pre-checks, rollout over SSM, smoke, record staged)   automatic
  -> deploy-production-auto   (starts the plan when PROD_AUTO_PLAN is true)   automatic, off by default
  -> deploy-production (plan -> approval -> apply -> smoke)   "Run workflow", or started by the line above
```

Each workflow starts the next one by a `workflow_run` event, except the last link: the
`deploy-production-auto` workflow starts `deploy-production` by a `workflow_dispatch` that it makes
with its own `GITHUB_TOKEN`. Who starts what, and who clicks:

| Workflow | Starts when | Environments | Who clicks |
| --- | --- | --- | --- |
| `ci` | a pull request, a push to `main`, or a manual dispatch (the full suite on any branch, never publishable) | none | nobody |
| `publish-staging-images` | a green push-to-main `ci` run completes, or a dispatch with a `ci_run_id` | `staging-images` (no reviewer; `main`-only once the setup script has run) | nobody |
| `deploy-staging` | `publish-staging-images` completes, or a dispatch | `staging` for `plan` and `apply` (no reviewer, `main` only); the `record` job names none | nobody |
| `deploy-production-auto` | `deploy-staging` completes and the repository variable `PROD_AUTO_PLAN` is `true` | none | nobody |
| `deploy-production` | "Run workflow" (a person), or `deploy-production-auto` | `production-plan` for `plan` (no reviewer, `main` only); `production` for `apply` (reviewers, `main` only) | one reviewer approves the `apply` |

**The rule:** production only receives a commit that staging has already run. The `deploy-staging`
workflow rolls every green merge out to staging and, after the instance reported success, records
the commit as a successful GitHub Deployment in the environment `staging`, described
`staging rollout`. The production plan refuses any commit without that record, and only a
deployment with that description counts (GitHub's own auto-created deployments never do). The one
exception is the owner's break-glass procedure, which is outside that rule (see "Break-glass").
This rule is stated again in `deploy/PRODUCTION.md` and in `deploy/STAGING_PIPELINE.md` on purpose.

The image publish needs no approval today (the `staging-images` environment has no reviewer). If a
reviewer is added to `staging-images`, every merge waits on that click.

## The routine for one release

1. **Open and merge a pull request.** `ci` must be green on the pull request. A migration in the
   change stops the app services on staging during the automatic rollout, so say so in the pull
   request when it carries one.
2. **`ci` runs on `main`.** The push to `main` runs the full suite again and builds the two images
   as OCI archives (artifacts of the run; nothing is pushed to a registry).
3. **The images are published.** `publish-staging-images` starts by itself when that `ci` run is
   green. It needs no approval today, so there is nothing to click.
4. **`deploy-staging` runs by itself.** Open the run from the Actions tab, or list the latest runs
   with `gh run list --repo amsyrzmi/CSAV2 --workflow deploy-staging.yml --limit 5`. Green with
   `Recorded <sha7> as staged` means staging runs the commit and production may take it. Green
   with `nothing to roll out`, `already staged`, `superseded` or `paused` means nothing was
   changed on staging by that run (see "Staging: automatic rollouts (deploy-staging)").
5. **Start the production plan.** Either "Run workflow" on `deploy-production`, branch `main`, with
   `sha` left empty (the tip of `main`), or nothing at all when `PROD_AUTO_PLAN` is `true`: the
   `deploy-production-auto` workflow has started the plan by itself (see "Auto-planned
   production runs (PROD_AUTO_PLAN)").
6. **Read the plan** in the `plan` job's summary: the target sha, both image digests, any
   `WARNING:` line, the pending migrations and the rollback references.
7. **Approve or reject** the `production` deployment on the run page ("Review deployments"). One
   approval from either reviewer is enough. Approval is the decision, so the plan comes first. A
   run that is no longer wanted is rejected.
8. **Read the result** in the `apply` job's summary: `Rollout of <sha7> finished.` and a passing
   public smoke test mean production runs the commit. Anything else is explained under "What a
   failure means" and "What a smoke stop or a rollback means".

## Staging: automatic rollouts (deploy-staging)

This section is completed by the next change in this series.

## Production: deploying from GitHub

A deploy is the manually started workflow `deploy-production` (`.github/workflows/deploy-production.yml`).
It has two jobs. `plan` runs in the environment `production-plan`, which has no reviewer on
purpose: it is read-only, and its AWS role can send only the plan document, whose action is fixed
to `plan`. `apply` runs in the environment `production` and waits for one approval from either
reviewer (`<login-1>` or `<login-2>`). Each job gets a short-lived AWS role session through GitHub
OIDC and sends a Systems Manager command to the instance; no port is open and no key is stored.
On the instance the root-owned entry script `/usr/local/sbin/csa-prod-deploy` checks out the
commit in `/home/ubuntu/CSAV2` and runs
`python -m scripts.staging_rollout --target production plan` (or `apply`) as `ubuntu`.
The workflow can also be started by `deploy-production-auto` when the repository variable
`PROD_AUTO_PLAN` is `true` (see "Auto-planned production runs (PROD_AUTO_PLAN)"): that starts the plan
only, and the approval is unchanged. The header of `deploy-production.yml` still reads "Manual dispatch
only", which describes its own triggers: the auto workflow starts it by a `workflow_dispatch`.

The workflow, not the instance, talks to GitHub. The `plan` job first runs
`deploy/production/resolve_commit.sh`, which picks the commit, checks it (refusals below) and reads
both image digests from the registry's manifest API. Its pull token is requested with the
workflow's own token (the `plan` job has `packages: read`); if the registry refuses that, the
script asks once more anonymously (the images are public). The script is shared with
`deploy-staging` and needs `DEPLOY_TARGET` (`production` or `staging`) in its environment: the
workflow sets `DEPLOY_TARGET: production`, and only production enforces the staged check.
The rollout core on the instance also takes a per-target lock, `/run/lock/csa-prod-rollout.lock`, in
`plan` and `apply`, shared with break-glass runs by hand (see `deploy/PRODUCTION.md`, "Reinstalling the shared entry script"). The two digests go to the
instance with the plan and the apply. The instance still pulls by digest with an empty Docker
config and checks each pulled image's digest and revision label against the commit. A host run by
hand without digests keeps the old path (see `deploy/PRODUCTION.md`, "Break-glass: manual deploy (owner only)").

The registry prefix of the images is the repository variable `IMAGE_NAMESPACE` (Settings, Secrets
and variables, Actions, Variables), for example `ghcr.io/example-owner`: lowercase, no scheme, no
trailing slash. The repository owner sets it. `publish-staging-images` and `resolve_commit.sh`
read it and refuse with `IMAGE_NAMESPACE must be set to ghcr.io/<lowercase owner>` before any
registry call when it is unset or malformed. CI reads it too, but has no such check: with the
variable unset, the `gate` and `tests` jobs fail at container initialisation with a Docker
image-reference error (the image reference becomes `/csa-pg16-postgis-vector@sha256:...`).

1. **Before you start.** The commit must be on `main`, its images published (the normal
   `publish-staging-images` run), and it must have been rolled out to staging by the
   `deploy-staging` workflow, whose `record` job writes the staged record (a manual staging
   rollout writes it too; see `deploy/STAGING_PIPELINE.md`). Only a deployment described
   `staging rollout` counts. If the tip is not staged yet, check the latest `deploy-staging` run
   (`gh run list --repo amsyrzmi/CSAV2 --workflow deploy-staging.yml --limit 5`).
2. **Start it.** GitHub, Actions, `deploy-production`, "Run workflow", branch `main`. Leave `sha`
   empty to deploy the tip of `main`, the commit you just merged, and only that commit: if its
   staging rollout has not finished, the run fails and nothing older is picked. Or give the full
   40-character SHA of an older staged commit on `main` (a deliberate rollback; the plan shows the
   downgrade warning). The commit must be one that includes the digest change (it runs that
   commit's own rollout CLI, which must accept the digests the workflow sends); an older revision
   can only be restored with break-glass, see `deploy/PRODUCTION.md`, "Rolling back to a revision older than the digest
   change". From a terminal:

   ```bash
   gh workflow run deploy-production --repo amsyrzmi/CSAV2 --ref main
   gh workflow run deploy-production --repo amsyrzmi/CSAV2 --ref main -f sha=<full 40-character sha>
   ```

   Both jobs run only for `main`, and their runner scripts always come from a checkout of `main`,
   never from the commit being deployed.
   A run may already exist: when `PROD_AUTO_PLAN` is `true`, the `deploy-production-auto` workflow
   starts the plan by itself after a staged merge. Look at the latest runs before starting another
   one (`gh run list --repo amsyrzmi/CSAV2 --workflow deploy-production.yml --limit 5`). The
   `triggering_actor` of an auto-started run is `github-actions[bot]` (expected, and unverified until
   the first auto run; see "Auto-planned production runs (PROD_AUTO_PLAN)").
3. **Read the plan** in the `plan` job's summary. It lists `Target SHA:`, both image digests
   (the staged check already ran in the `Resolve and check the commit` step; `Staged on:` appears
   only when a host run by hand did the check itself), any `WARNING:` lines, the rollback
   references (the images running now), `Pending migrations:`, any missing secret files,
   `Host overrides:` (which `CSA_PROD_*` keys `/srv/csa-prod/host.env` sets; values are never
   printed), `Host settings digest:`, the steps, the downtime line when a migration is pending, and
   the apply command line. The workflow sends exactly the target SHA, migrations, host digest and
   image digests from the plan job; you never type them. The printed command line (`.venv/bin/python -m
   scripts.staging_rollout --target production apply ...`) is for reference only: the instance
   runs the rollout with `/opt/csa-deploy/venv/bin/python` through `csa-prod-deploy`.
4. **Approve or reject** the `production` deployment on the run page ("Review deployments"). One
   approval from either reviewer is enough; self-approval is allowed. Approval is the decision,
   so read the plan first: a migration stops gateway, frontend, both workers, delivery and the
   training worker (console unavailable, bot replies delayed, not lost), and a downgrade warning
   means the target is older than what runs now. Reject to cancel; nothing on production has changed (only
   the deploy checkout moved to the commit). A run waiting for approval holds the workflow's
   concurrency group: a new dispatch queues behind it, and a third dispatch cancels the queued
   second. A dispatch made by `deploy-production-auto` counts as a dispatch here and can replace a
   person's *pending* run (an accepted limit; see "Auto-planned production runs (PROD_AUTO_PLAN)"). Reject a stale waiting run before you start a new one (the common case now that the
   default is the tip of `main`), and after the digest change merges, reject any run still
   waiting from before it.
5. **Read the result** in the `apply` job's summary. `apply` first checks that `main` is still at
   the planned commit (a default run only; an explicit `sha` is not checked), checks out `main`
   again for its runner scripts, then sends the planned values; on the instance the rollout
   re-derives the plan and refuses if anything changed. It checks the compose config, takes and
   verifies a backup (`Backup taken: backups/csa_staging-<timestamp>.dump`), pulls and verifies
   the images by digest, applies the pending migrations (services stopped), recreates gateway,
   worker (both replicas), delivery, frontend and training worker, waits for `/readyz`, runs the check files, runs
   the smoke test and ends with `Rollout of <sha7> finished.`. On a stop it prints `Stopped at <step>: ...`, the state
   production is in, the recovery lines for how far it got, and
   `Re-run plan, then apply, once the cause is fixed.` It never retries or restarts by itself, and
   rolls back only in the one case described under "What a smoke stop or a rollback means". Every run is also logged on the instance in
   `/var/log/csa-prod-deploy/<timestamp>-<action>-<sha7>.log`. Systems Manager's
   `GetCommandInvocation` returns at most 24,000 characters of output, so a long apply loses its
   tail (the recovery lines) from the workflow log and summary: the complete log is always the
   one on the instance in `/var/log/csa-prod-deploy/`.
6. **The public smoke test** is the last step of the `apply` job and runs only when the apply
   succeeded. It asks the console origin (the environment secret `PROD_CONSOLE_ORIGIN`) for `/` and
   passes on a 2xx or 3xx status (a login redirect counts and is not followed), retrying a failed
   request up to 3 times, 5 s apart; the worst case is about 75 seconds. It never prints the origin.

### What a smoke stop or a rollback means

The smoke test runs on the instance after the check files, and a failure of it, or of `/readyz`,
stops the rollout. In order, it checks: the migrations recorded in the database equal the
checkout's; every replica of gateway, worker, delivery, frontend and training worker is healthy on
the planned image; the console origin and the hooks origin from `host.env` each answer below 500 when
requested from the instance (up to 3 attempts, 5 s apart); the frontend answers over HTTP from
the gateway container. It prints one `smoke <name>: ok` line per passing check and stops at the
first failure with `Stopped at smoke: <reason>`.

When the failure was at readiness or smoke, the rollout puts the previous images back, but only if
all of this holds: no migration was pending or applied by this run, every replica of every
previous app container was running, all of them were on known registry image refs that agree
per app, and those refs differ from the images just deployed (a re-run of a failed deploy never
rolls back onto the same images). Anything else gets the ordinary recovery lines instead. A rollback always leaves the run
red (exit 1), so nothing is recorded as deployed. What you see and what to do:

- `Rolling back to <ref>…` (one line per app), then
  `Rolled back: the previous images are running; <sha7> is not.` and
  `Fix forward, then re-run plan and apply.`: production is back on the previous images and
  `/readyz` answers. Read the `Stopped at smoke:` or `Stopped at readiness:` line above to see why
  the new commit failed. Fix forward in a new commit, and start a new run once it is on `main`
  and staged. Nothing else is needed on the host. The rollback does not run the smoke test or the
  check files for the previous version, which was running before this deploy.
- `Stopped at smoke: <reason>` with no `Rolling back` line: the failure is real but the rollback
  was not allowed (a migration was applied, or the previous images are unknown or not all
  running). The new images are still running. The recovery lines that follow say which case it is:
  with migrations applied the output says not to roll back the schema, and to investigate and fix
  forward (it also names the backup taken at the start, for an owner decision, not as a step to
  take); for the first deploy from local image tags, follow `deploy/PRODUCTION.md`, "Rolling back to the local images" (under "Break-glass: manual deploy (owner only)").
- `Stopped at rollback: <reason>` after `Rolling back to ...`: the rollback itself did not
  come up healthy. Do not start another run. Check the host (`docker ps` as `ubuntu`, the
  newest log in `/var/log/csa-prod-deploy/`) and decide by hand whether to fix forward or use the
  break-glass procedure. The ordinary recovery lines follow it.

A failed public smoke test comes after a successful apply: the services are up on the new commit
and the instance's own smoke test passed, but the console origin did not answer as expected from
outside. Check the tunnel and the console origin before anything else; nothing was rolled back.

### What a failure means

The runner (`deploy/production/ssm_run.sh`) exits with:

- **0**: the command succeeded on the instance.
- **124**: the command was still `Pending`, `InProgress` or `Delayed` when the runner stopped
  waiting, 115 minutes (by the clock, not a count of polls) after it sent the command. The command
  may still be running or may still start. **Check the
  host; do not start the workflow again.** On the instance: `ls -t /var/log/csa-prod-deploy | head -n 3`,
  read the newest log, and `pgrep -af csa-prod-deploy`. In AWS: Systems Manager, Run Command,
  Command history, the command id from the line `ssm_run: sent <action> for <sha> as command <id>`.
  Systems Manager itself stops a command that runs longer than 120 minutes (the documents'
  `timeoutSeconds`, longer than the runner waits), so a command still running at 124 may yet
  finish or be stopped. Each job is limited to 130 minutes, longer than both. The waits are sized
  for the worst case of a deploy that fails and rolls back, which runs two passes of the five
  recreates (gateway, worker, delivery, frontend, training worker; each up to 300 s up plus 300 s
  health, 100 minutes in all); a deploy that rolls back needs the longer budget.
- **64**: the runner refused an argument or setting; nothing was sent.
- **1**: anything else, including a command that failed on the instance. The line
  `ssm_run: command <id> finished with status <status> (exit code <N>)` gives the instance's exit
  code: **75** means another deploy holds the lock (`another deploy is running`); wait for it to
  finish and start again. **64** means `csa-prod-deploy` refused its arguments. **73** means it
could not write its log in `/var/log/csa-prod-deploy` (`cannot write the log file`); nothing ran,
and the directory must be owned by `ubuntu`. **78** is a refused config: see `deploy/PRODUCTION.md`, "Reinstalling the shared
entry script". **2** is a plan
  that found missing secret files or, with `unrecognized arguments` in the output, a commit older
  than the digest change (its rollout CLI has no digest options): see `deploy/PRODUCTION.md`, "Break-glass: manual deploy (owner only)", "Rolling back
  to a revision older than the digest change". **1** is a rollout stop, `commit is not on main`, or a failed
  `git fetch`. A `TimedOut` status also arrives as runner exit 1, not 124: either the instance did
  not pick the command up within 10 minutes (it never ran), or Systems Manager stopped it at its
  120-minute limit (an apply may have stopped part-way). Check the host as for 124 before any new
  run; the instance's log shows which.

A `Configure AWS credentials` step that fails with `Not authorized to perform
sts:AssumeRoleWithWebIdentity` means the role's trust policy does not match the token the job
presented: the subject (see `deploy/PRODUCTION.md`, "One-time setup", step 3, for how to look up the prefix GitHub issues for
this repository) or the environment name. Nothing was sent to the instance.
Fix it with `aws iam update-assume-role-policy --role-name <role> --policy-document file://<policy>`
and start the workflow again.

A `Require the deploy variables` step that fails names the secret or variable that is not set
(see `deploy/PRODUCTION.md`, "One-time setup", step 6); the apply job also needs `PROD_CONSOLE_ORIGIN`. A failed `Read the plan` step (`parse_plan: ...`) means the
plan output lacked one of its `Target SHA:`, `Pending migrations:` or `Host settings digest:`
lines, or named another commit; `apply` does not run.

### What each refusal means

The `Resolve and check the commit` step of the `plan` job runs five checks in this order. The first
that fails prints one line and fails the run; nothing was sent to the instance. Start again once
the cause is fixed. `resolve_commit.sh` exits 3 for "not ready yet" (the `ci` run has not
succeeded, or the images are not published) and 1 for every other refusal. Production treats both
as a failure; `deploy-staging`'s automatic runs treat exit 3 as `nothing to roll out`.

1. `sha must be the full 40 character lowercase commit SHA`: the `sha` input is not a full lowercase
   SHA. Fix the input, or leave it empty.
2. `commit <sha7> is not on main`: the commit is not an ancestor of `main`. Only commits on `main`
   deploy.
3. `the ci run for <sha7> has not succeeded on main`: no completed `ci` run on `main` for this
   commit concluded success. Wait for it, or fix the failure.
4. `main's tip <sha7> is not staged yet; wait for deploy-staging` (an empty `sha`) or
   `commit <sha7> has not been rolled out to staging` (an explicit `sha`): staging has no success
   record for this commit. Wait for `deploy-staging` (check its latest run), or roll it out to
   staging first. There is no bypass. If the `deploy-staging` run's `record` job failed, staging
   is fine: use "Re-run failed jobs" on that run. If a manual staging rollout succeeded but
   printed `WARNING: staging is fine but the staged record was not written`,
   run its `Retry:` line on the home server:
   `.venv/bin/python -m scripts.staging_rollout record-staged --sha <full sha>`. It refuses a
   commit that is not on `origin/main` in that checkout.
5. `images for <sha7> are not published (csa-<app>)`: the registry has no image tagged with the
   full SHA. Wait for the `publish-staging-images` run for that commit to finish. Other messages
   from this step (`could not get a registry token`, `the registry answered HTTP <n>`, `the
   registry returned no single digest`, a failed GitHub read) mean the answer could not be
   trusted; the run fails closed. Start again.

Refusals at the later steps:

- `main moved to <sha7> since this run's plan; start the workflow again`: the `apply` job's first
  step, after approval, found a newer commit on `main` than the one planned (a default run only).
  Nothing was sent. Start the workflow again to plan the new tip. An explicit `sha` skips this
  check.
- On a host run by hand with no digests: `Stopped at gate: commit <sha7> has not been rolled out
  to staging` and `images for <sha7> are not published yet (<state>)` come from the instance's
  own `gh` lookup (`deploy/PRODUCTION.md`, "Break-glass: manual deploy (owner only)").
- `Stopped at confirm: host settings changed since plan; run plan again`: `/srv/csa-prod/host.env`
  changed between plan and approval. Start the workflow again.
- `HEAD changed since plan` or `pending migrations changed since plan`: production or `main`
  moved between plan and approval. Start the workflow again.
- `WARNING: <sha7> is older than the running <sha7>; this is a downgrade. A downgrade across a
  migration is refused.`: a warning, not a stop. Approve only if you mean to go back.
  `WARNING: could not compare <sha7> with the running revision` is expected on the first deploy,
  when the running images are local tags.
- `<target> has migrations this checkout lacks: ...; update the checkout before rolling out`
  (`production has migrations ...`): the target commit is older than the database schema, a
  downgrade across a migration. It is refused; fix forward instead.
- Host file stops (`Stopped at flags: ...`): `cannot read host.env`, `host.env is missing: <keys>`,
  `host.env line <n>: unknown key <KEY>`, `duplicate key`,
  `quoted value; write KEY=value without quotes`, or
  `set in both the repo file and the host file: <keys>` (a switch belongs in
  `deploy/production-rollout.env` only). Fix the file on the instance and start again.
- Backup stops (`Stopped at backup: ...`, always followed by
  `Nothing on production was changed. The backup step failed; the deploy stopped before any change.`):
  - `/srv/csa-prod/backup/backup.sh is missing; reinstall it: install -m 0755 deploy/production/backup.sh /srv/csa-prod/backup/backup.sh`
    or `... differs from deploy/production/backup.sh; reinstall it: ...`: the commit changed the
    backup script. As `ubuntu`, run `cd ~/CSAV2 && install -m 0755 deploy/production/backup.sh /srv/csa-prod/backup/backup.sh`
    (the plan left the checkout at the target commit), then start the workflow again.
  - `the backup did not report ok: <timestamp> skipped: previous backup still running`: the
    6-hourly cron backup held the lock. Wait a minute and start again.
  - `/srv/csa-prod/backup/backup.sh failed (exit <n>); run it on the instance to see why`, or a
    log line that is old or has no timestamp: run `/srv/csa-prod/backup/backup.sh` as `ubuntu`
    (no `sudo`) and read its error. The deploy does not print the script's output, because an AWS
    error can carry the bucket name.
- `missing secret files: ...`: create the files (`deploy/PRODUCTION.md`, "Secret files the overlay needs"), then start again.
- After a stop with migrations applied, the recovery text names the backup taken at the start
  (`The backup taken at the start of this deploy is backups/csa_staging-<timestamp>.dump`); fix
  forward, or restore it (`deploy/PRODUCTION.md`, "Restoring a backup").
- Image-only stop after the recreate that was not rolled back automatically (see What a smoke
  stop or a rollback means): `To roll back: git checkout <sha> in /home/ubuntu/CSAV2, run
  plan and apply, then git checkout main.` On production that means: start `deploy-production`
  with `sha` set to that revision (it is a staged commit); do not touch the checkout. This works
  only for a revision that includes the digest change; for an older one (what production runs
  before its first deploy after that change) use break-glass, `deploy/PRODUCTION.md`, "Rolling back to a revision older
  than the digest change". On the first
  deploy the previous images are local tags: the text says `follow the manual rollback runbook`,
  which is `deploy/PRODUCTION.md`, "Rolling back to the local images" under "Break-glass: manual deploy (owner only)".

## Auto-planned production runs (PROD_AUTO_PLAN)

The repository variable `PROD_AUTO_PLAN` switches the automatic start of the production **plan**.
Unset or any value but `true` means off, and it is off by default: with it on, both reviewers get a
GitHub review request for every waiting apply, at most one per merge. The production apply is
never approved by an agent and never skips the approval: the approval is a property of the `production`
environment (its required reviewers and its `main`-only branch policy), not of any workflow file,
so no workflow change can remove it.

**What the workflow does.** `.github/workflows/deploy-production-auto.yml` starts when
`deploy-staging` completes on `main`. The chain is `ci`, `publish-staging-images`,
`deploy-staging` and this workflow: three `workflow_run` links, which is the most GitHub allows;
the `deploy-production` run it makes is a dispatch, and a dispatch made with the workflow's own
token is documented to create a run (`start` fails red when none appears). It has one job, `plan`,
which runs `deploy/production/auto_plan.sh` three times:

1. `check` decides whether there is anything to plan, with the staging mode of
   `deploy/production/resolve_commit.sh`, and ends green with a notice when there is not. It looks
   at the tip of `main`, never at the sha GitHub attaches to the triggering run, which can be a later
   commit than the one that run handled.
2. `supersede` cancels earlier auto-started runs whose apply is still waiting.
3. `start` dispatches `deploy-production` with no input (so an empty `sha`: the tip of `main`; the
   auto path can never request an explicit rollback) and checks that a run appeared.

**What each line means.**

- `nothing to plan: the ci run for <sha7> has not succeeded on main` or
  `nothing to plan: images for <sha7> are not published (csa-<app>)` (green): the tip is not ready
  yet. Its own publish run starts the next staging rollout, and that starts the next check.
  `nothing to plan: the commit is not ready yet` is the same case for any other resolver message.
- `nothing to plan: main's tip <sha7> is not staged yet` (green): staging is paused, the rollout was
  superseded or failed, or the record job failed. Nothing is planned until a staged record exists.
- `a deploy-production run for <sha7> already exists: <run id>` (green): a run for the tip is
  requested, queued, in progress, waiting, pending or already succeeded. A run that failed or was
  cancelled does not count, so an early manual dispatch that failed with "not staged yet" does not
  stop the auto plan. The guard counts a run by its `head_sha`, which is the tip of `main` when the
  run was dispatched, not necessarily the commit it plans. So a rollback a person dispatched with
  an explicit sha while the tip was T, and which is still waiting, in progress or succeeded, makes
  `check` report that a run for T already exists, and the plan for T is then started by hand. The
  opposite case, a run that planned the tip while its `head_sha` is an older tip, is not seen by
  the guard, which is safe: `supersede` cancels that run while it is waiting and the new plan starts.
- `cancelled run <id> (planned <sha7>)`: a stale auto-started run was cancelled.
- `left alone: run <id> (<reason>)`: a waiting run was not cancelled. The reason is
  `started by <login>` (a person's run, including a rollback), `same commit`, `apply is not
  waiting`, `not a dispatch`, `not on main` or `unreadable fields`.
- `run <id> moved on; left alone`: the run changed state (approved, finished) between the listing
  and the cancel, so it was not cancelled, or the cancel was refused because it had just done so.
  The run and its jobs are read again just before the cancel, and once more after a refusal; there
  is never a second cancel.
- `warning: could not cancel run <id>; it stays waiting`: the cancel was refused and the run is
  still waiting. The new run queues behind it, as for a manual run: reject the stale one.
- `started deploy-production run <id>: <url>`: the plan was started. The apply waits for approval on
  the `production` environment.
- `the dispatch was accepted but no deploy-production run started (check that workflow_run chaining
  is not limited)` (red): GitHub accepted the dispatch and made no run. This is the one that means
  the chain itself failed: start `deploy-production` by hand and say so. The run is also red for any
  other failure of the resolver, an unreadable run list, an unreadable listing of the runs to
  supersede, or a refused dispatch; each says what it could not do. Start `deploy-production` by hand
  after any of them.

With the switch off or unset, the job is skipped, so the whole run concludes `skipped` (grey), not
success. A failed or cancelled `deploy-staging` run also leaves the job skipped.

**Cancelling stale waiting runs, and its limits.** A waiting run holds the `deploy-production`
concurrency group, and GitHub's concurrency rules cannot cancel only a run whose apply is waiting,
so `supersede` uses the REST call that cancels a run. It cancels a run only when all of these
hold, read just before the call: the run is `waiting`; its event is `workflow_dispatch`; its
`triggering_actor` is `github-actions[bot]` (a person's run is never touched); its branch is `main`;
its `head_sha` is not the commit being planned; its jobs are exactly `plan` (completed, success)
and `apply` (waiting). Every field must be present and well-formed, or the run is left alone. A run
whose apply is in progress is never knowingly cancelled. The limits, stated plainly:

- The cancel is a read followed by a request, not one atomic step, so a window of one API round
  trip remains in which an approval can land first. The apply's first steps re-check that `main`
  has not moved (a stale run then fails with `main moved to <sha7> since this run's plan`), but a
  late cancel does not stop an SSM command that was already sent to the instance: the run then
  reads cancelled while the instance may still deploy, with no workflow smoke test. After a
  cancelled run that was approved, check the host log and the public smoke test by hand.
- The pass runs once, before `start`: an earlier auto run with its `plan` job still running is not
  listed as waiting and is missed. The new run queues behind it and the owner rejects the older one.
- The `deploy-production` run that the auto workflow dispatches shares the `deploy-production`
  concurrency group with manual runs, and GitHub keeps at most one pending run in a group. An auto
  dispatch made while one manual run waits and another is pending replaces the pending manual run.
  `supersede` never cancels a manual run, but this rule can replace a person's pending one;
  dispatch again. The auto workflow itself has its own `deploy-production-auto` concurrency group
  (an idle run gets a group of its own, so it cannot replace a real one), and a cancelled
  `deploy-production-auto` run, replaced while it was pending, is normal.
- A run that was cancelled has already sent its review request to both reviewers; cancelling does
  not recall it. That is why the switch is off by default.
- A duplicate completion of `deploy-staging` for a tip whose auto run failed or was rejected plans
  again, because blocking on failures would break the early-dispatch case above.
- `deploy-production.yml`'s own header still says "Manual dispatch only". That is true of its
  triggers and the file is left as it was; the auto workflow starts it by a `workflow_dispatch`.
  Reading the header alone would miss that a run can already exist: in "Run workflow", look at the
  latest `deploy-production` runs first (the `triggering_actor` of an auto-started run is
  `github-actions[bot]`).

**Security.** The auto workflow has no `environment`, no `id-token` permission and no secret. Its
token can read the repository, read Actions and deployments, read packages (`packages: read`, for
the check that the images exist), and write Actions (cancel and dispatch). Approving a pending
deployment needs a user who is a required reviewer, and the token is not one; the script and the
workflow contain no call that approves. It never checks out or runs anything from the triggering
run: it checks out `main`, takes no artifact, and uses no field of the triggering event in a
command.

**Turning it on and off** (an owner action; the value is `true` or `false`):

```bash
gh variable set PROD_AUTO_PLAN --repo amsyrzmi/CSAV2 --body true
gh variable set PROD_AUTO_PLAN --repo amsyrzmi/CSAV2 --body false
```

The first line turns it on, the second off. The plain read is
`gh variable get PROD_AUTO_PLAN --repo amsyrzmi/CSAV2` (it fails when the variable is unset, which
means off). The script shows it among everything else:
`deploy/github/setup-environments.sh production --check --repo amsyrzmi/CSAV2` prints
`[n/total] variable PROD_AUTO_PLAN: ok (true)`, `ok (false)` or `ok (unset: off)`. That command
needs repository admin and exits 1 when any other item of the target is not ok, so a red exit is
not about the switch unless its own line says `differs`.

## Rollback

- **Staging never goes back through the workflow.** A plan that is a downgrade stops red, even for
  an explicit `sha`; the manual rollout is the way back (see "Break-glass").
- **Production, image only, automatic.** When `/readyz` or the smoke test fails after services were
  recreated, the run applied no migration and the previous images are registry digests, the rollout
  recreates the five services on the previous digests, waits for readiness and prints
  `Rolled back: the previous images are running; <sha7> is not.` It still ends red, and nothing
  is recorded as deployed (production never writes a staged record). The exact lines are in "What
  a smoke stop or a rollback means".
- **Production, when migrations ran,** or the previous images are unknown, nothing is rolled back by
  itself. Fix forward with a new commit; restoring the backup the deploy took first
  (`deploy/PRODUCTION.md`, "Restoring a backup") is an owner decision, not a step. The recovery
  lines the rollout prints are in "What a failure means".
- **A deliberate rollback** is an explicit `sha` on `deploy-production`: a staged commit on `main`
  that already includes the digest change. The plan shows the downgrade warning, and the apply
  does not check the tip of `main` for an explicit `sha`. An older revision than the digest change
  can only be restored by break-glass (`deploy/PRODUCTION.md`, "Break-glass: manual deploy
  (owner only)").

## One-time setup

Each target needs three sides prepared once: GitHub, AWS and the host, interleaved as in "Order
and checks" below (the host comes before the AWS `apply`). The two scripts below are the repeatable
path, and the manual steps in `deploy/PRODUCTION.md`, "One-time setup", and
`deploy/STAGING_PIPELINE.md`, "Host setup for automatic rollouts", are the reference they were
built from.

### The AWS side: deploy/aws/setup-target.sh

Run from AWS CloudShell with an admin login, in a `git clone --depth 1` of the repository:

```bash
deploy/aws/setup-target.sh <staging|production> activate --region <region>
deploy/aws/setup-target.sh <staging|production> check --region <region> --node <managed-instance-id> --subject <subject-prefix>
deploy/aws/setup-target.sh <staging|production> apply --region <region> --node <managed-instance-id> --subject <subject-prefix>
```

`activate` creates the node's service role and one single-use activation (the code is typed at the
hidden prompt on the host and never pasted anywhere else). `apply` is idempotent: it creates the
GitHub OIDC provider, each deploy role with its trust and inline policy, and each Systems Manager
document; a second run makes no write call. `check` is read-only and exits 0 only when everything
is right. Run `check` before `apply`, especially for production, where a mistyped `--subject`
rewrites a role's trust policy. `apply` ends by printing the role ARNs, which contain the account
id: use them in CloudShell and in GitHub environment secrets only.

### The GitHub side: deploy/github/setup-environments.sh

Run on a machine where `gh` is logged in as a repository admin:

```bash
deploy/github/setup-environments.sh <staging|production> [--check] --repo <owner/name> [--region <region>] [--image-namespace <ghcr.io/owner>] [--reviewer <login>]... [--replace-reviewers] [--auto-plan true|false] [--paused true|false]
```

It manages the environments, their reviewers and their `main`-only branch policy, the secrets and
the repository variables of one target. Without `--check` it applies.

| Flag | Meaning |
| --- | --- |
| `--check` | Read-only. Prints one `[n/total] <what>: <result>` line per item and ends `check: everything is ok` (exit 0) or `check: <n> item(s) not ok` (exit 1). The result is `ok`, `missing` or `differs`; the two switches add their value (`ok (true)`, `ok (unset: off)`, `differs (<value>)`, `differs (not true or false)`); `note:` lines report what is only noted (reviewers on environments that need none, `can_admins_bypass`, `prevent_self_review` and `wait_timer`). |
| `--repo <owner/name>` | The repository (here `amsyrzmi/CSAV2`). |
| `--region <region>` | The AWS region variable of the target (`STAGING_DEPLOY_REGION` or `PROD_DEPLOY_REGION`): `apply` sets it when it is absent or different, `--check` compares it. |
| `--image-namespace <ghcr.io/owner>` | The repository variable `IMAGE_NAMESPACE`, set and compared the same way. |
| `--reviewer <login>` | Production only, repeatable (at most six): the required reviewers, for example `<login-1>` and `<login-2>`. |
| `--replace-reviewers` | Production only, with apply only: allows the reviewer set of an existing `production` to change. |
| `--auto-plan true\|false` | Production only: `apply` sets `PROD_AUTO_PLAN`, `--check` compares it. Not given means the variable is not touched. |
| `--paused true\|false` | Staging only: `apply` sets `STAGING_AUTO_PAUSED`, `--check` compares it. Not given means the variable is not touched. |

Environments, secrets and variables, by target:

| Environment | Reviewers | Secrets |
| --- | --- | --- |
| `staging` | none | `STAGING_DEPLOY_ROLE_ARN`, `STAGING_INSTANCE_ID` |
| `staging-images` | none | none |
| `production-plan` | none | `PROD_PLAN_ROLE_ARN`, `PROD_INSTANCE_ID` |
| `production` | the given set (required) | `PROD_APPLY_ROLE_ARN`, `PROD_INSTANCE_ID`, `PROD_CONSOLE_ORIGIN` |

The repository variables are `IMAGE_NAMESPACE` (both targets), `STAGING_DEPLOY_REGION` (staging),
`PROD_DEPLOY_REGION` (production), and the two switches `STAGING_AUTO_PAUSED` (staging, set only
with `--paused`) and `PROD_AUTO_PLAN` (production, set only with `--auto-plan`). A required
variable that is absent and was not given as a flag is reported `missing` by `apply` (and counted
below); a present one is never changed unless its flag is given with a different value.

**Secrets never appear on a command line or in output.** A secret value comes from the environment
variable `SETUP_<NAME>` (for example `SETUP_PROD_INSTANCE_ID`), or from a hidden prompt on a
terminal (`Value for <NAME> (hidden; Enter skips it; will be set on: <environments>)`; Enter leaves
the secret missing), and reaches `gh secret set` on stdin only. Without a terminal and without
`SETUP_<NAME>` a missing secret is not prompted for and not waited for: it is counted as missing. A
secret that already exists is not prompted for and not overwritten unless its `SETUP_<NAME>` is set
(an explicit rotation), so a routine run prompts for nothing. Values are validated (role ARN,
managed-node id, origin with no path, port or trailing slash) before any write, and no message ever
contains a value. A `SETUP_<NAME>` that is exported but empty is an error (exit 2), not a skip, and
a role ARN with a path (`role/path/name`) is refused. The script switches tracing off before it
reads any value (a caller's `bash -x` or `SHELLOPTS=xtrace` prints no secret) and moves every
`SETUP_<NAME>` out of the environment before it starts `gh`, `jq` or any other program, so no child
process inherits one. The only values printed are the two switches, which are `true`, `false` or
`unset: off` (for example `variable PROD_AUTO_PLAN: set (true)` or `unchanged (unset: off)` from
`apply`, and `ok (true)` from `--check`).

**What `apply` prints.** One `[n/total] <what>: <result>` line per item: first the environments
(`created`, `updated` or `unchanged`), then the secrets (`secret <NAME> on <env>: set`,
`unchanged` or `missing`, with a `note: give SETUP_<NAME> or run on a terminal` line under each
missing one), then the variables (`set`, `unchanged` or `missing`). A secret or a required variable
that has no value does not stop the run: everything else is applied first, and the run ends
non-zero with `apply: <n> item(s) still missing`. When everything is in place it ends
`apply: everything is in place` (exit 0).

**Read, build the whole desired state, write only the difference, then re-read.** `apply` reads
each environment, builds the complete `PUT` body from the current values plus the desired changes
(reviewers, `wait_timer`, `prevent_self_review` and `can_admins_bypass` always included), sends the
`PUT` only when something differs, and re-reads the environment after every write. When reviewers,
the wait timer, self-review, admin bypass or the branch-policy flags differ from what was intended it
stops with `[n/total] environment <name>: failed: <field> is <actual> after the write, expected
<intended>` (names and counts only). The reason is that GitHub's behaviour here is not documented:
what a `PUT` does with a field it does not mention is not documented (a probe on a throwaway
environment on 2026-11-18 showed that a partial `PUT` kept reviewers, self-review and admin bypass
that an earlier full `PUT` had set, while an earlier hand run on 2026-11-17 had suggested the
opposite), and the `can_admins_bypass` field is not in the published description of the request but
is accepted and read back. The script's tests run against a fake `gh` under both readings, and the
re-read after every write is the permanent guard against GitHub behaving differently.

**Production's reviewers are preserved** by carrying the current set over; `production` is refused
(before the first write) when it has no reviewer and none was given with `--reviewer`. On an
existing `production`, when the wanted reviewer set differs from the live one, `apply` prints
`reviewers on production: <old logins> -> <new logins>` and stops unless `--replace-reviewers` is
also given; `--check` shows the difference first. Reviewers on `staging`, `staging-images` and
`production-plan` are preserved and reported, never added or removed by the script: a reviewer on
`staging-images` would hold every merge, so `--check` prints it as a note, not a failure.
`can_admins_bypass` is wanted `false` on `production` and `production-plan` (the owner's decision;
`--check` reports `true` as `differs` and `apply` announces `admin bypass: true -> false (changed)`);
on `staging` and `staging-images` it is carried over and never judged. `prevent_self_review` is
never changed: one approval, self-review allowed.

**The branch policy is a separate request.** The `PUT` turns custom branch policies on (`{"protected_branches":
false, "custom_branch_policies": true}`); the `main` policy is a separate `POST` to
`.../deployment-branch-policies` of `{"name":"main","type":"branch"}`, made after the `PUT` and
only when `main` is missing, and any other policy is deleted once `main` exists. An environment
with custom policies on and none listed admits nothing, so this order is fail-safe. An
environment with no custom policies (its `deployment_branch_policy` is `null`, which is the state
of `staging-images` before the script runs) answers 404 `Not Found` to both the list and the
`POST`, so the script never lists the policies of such an environment before the `PUT` that turns
them on. `setup-environments.sh staging` therefore gives `staging-images` the `main`-only policy:
a workflow on any other branch could otherwise use it and its `packages: write`.

**The script's tests ran against a fake `gh`,** not against GitHub. The first real run is
`--check`, read the output, then apply, watched.

### The host side (per target)

The host is prepared by hand, on the host, by its operator. The steps are in
`deploy/PRODUCTION.md`, "One-time setup" (production), and in `deploy/STAGING_PIPELINE.md`, "Host
setup for automatic rollouts" (staging, together with the other developer who owns the home
server): the Systems Manager agent, the dedicated checkout, the shared entry script and its
root-owned config, the secret files, the lock and log directories.

### Order and checks

1. **GitHub environments:** `setup-environments.sh <target> --check`, then without `--check` (secrets
   cannot be set before the environment exists). With no `SETUP_<NAME>` values given (press Enter at
   each prompt on a terminal), this first apply creates the environments and, with `--region` and
   `--image-namespace`, the variables, then ends non-zero with
   `apply: <n> item(s) still missing`: that is expected until step 5.
2. **AWS activation:** `setup-target.sh <target> activate`, for the host's Systems Manager node.
3. **The host:** the steps of the target's own document, including the registration of the node.
4. **AWS apply:** `setup-target.sh <target> check`, then `apply`, then `check` again.
5. **Secrets:** the role ARNs from the AWS step, the node id and, for production, the console origin,
   given to `setup-environments.sh` through `SETUP_<NAME>` or its hidden prompt.
6. **Check both scripts:** `setup-target.sh <target> check` and `setup-environments.sh <target>
   --check` both end green.
7. **The first run, watched live:** for staging, "First run (acceptance), watched live"
   in `deploy/STAGING_PIPELINE.md`; for production, a plan with an empty `sha`, read, then
   rejected or approved with the owner present (`deploy/PRODUCTION.md`, "First production deploy:
   what to check").

## Break-glass

Break-glass is for the owner, when the pipeline is unavailable and a fix cannot wait. Pause
automatic runs first so that nothing starts while it is done: `STAGING_AUTO_PAUSED` set to `true`
(and `PROD_AUTO_PLAN` to `false`; see above), then resume afterwards. It is outside the staged
rule on production: nothing checks that staging ran the commit. Running a target's entry script on the
host by hand with no image digests keeps the staged gate through its own `gh` lookup; the manual Compose procedure
has no gate. Use the workflow whenever it works. Where each procedure lives:

- production: `deploy/PRODUCTION.md`, "Break-glass: manual deploy (owner only)";
- staging: `deploy/STAGING_PIPELINE.md`, "Manual rollout (break-glass)".

## Where each fact lives

- **Here, in `deploy/DEPLOY.md`:** the pipeline and who clicks; the routine for one release; the
  automatic staging rollout, its outcomes, the staged record and its limits; production's
  deploy from GitHub, its failures, refusals, smoke stops and rollback; the auto-planned runs;
  rollback in general; the one-time setup order and the two scripts; break-glass in outline.
- **`deploy/PRODUCTION.md`:** how the production stack differs from staging; the facts of the
  production host (entry script, checkout, log directory, lock, registry login); the manual
  one-time setup steps the scripts mirror; restoring a backup; the digest change; the shared entry
  script; the manual break-glass procedure; pruning; the secret files; the production feature
  rollouts.
- **`deploy/STAGING_PIPELINE.md`:** the facts of the staging host (entry script and config, user,
  log directory, lock, dedicated checkout); the manual rollout; recovery, retest and switches; the
  host setup for automatic rollouts and the first run; the numbered activation steps; resetting
  staging.
- **`deploy/RELEASE.md`:** how the images are built and published, with the dated history of the
  first publication.
- **The `deploy` skill (added later in this series):** what an agent may and may not do for either
  flow (plan before apply, never approve, never bypass the staged gate).

The test `tests/test_deploy_docs.py` keeps the moved headings at their destination and the stubbed
ones in place, and nothing else here is frozen: later edits are expected.
