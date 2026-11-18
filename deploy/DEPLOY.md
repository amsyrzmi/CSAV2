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
| `ci` | a pull request, or a push to `main` | none | nobody |
| `publish-staging-images` | a green push-to-main `ci` run completes, or a dispatch with a `ci_run_id` | `staging-images` (no reviewer, `main` only) | nobody |
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
   change stops all four services on staging during the automatic rollout, so say so in the pull
   request when it carries one.
2. **`ci` runs on `main`.** The push to `main` runs the full suite again and builds the two images.
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

This section is completed by the next change in this series.

### What a smoke stop or a rollback means

This section is completed by the next change in this series.

### What a failure means

This section is completed by the next change in this series.

### What each refusal means

This section is completed by the next change in this series.

## Auto-planned production runs (PROD_AUTO_PLAN)

The repository variable `PROD_AUTO_PLAN` switches the automatic start of the production **plan**.
Unset or any value but `true` means off, and it is off by default: with it on, both reviewers get a
GitHub review request for every waiting apply, one per merge. The production apply is never
approved by an agent and never skips the approval: the approval is a property of the `production`
environment (its required reviewers and its `main`-only branch policy), not of any workflow file,
so no workflow change can remove it.

**What the workflow does.** `.github/workflows/deploy-production-auto.yml` starts when
`deploy-staging` completes on `main`. The chain is `ci`, `publish-staging-images`,
`deploy-staging` and this workflow: three `workflow_run` links, which is the most GitHub allows;
the `deploy-production` run it makes is a dispatch, and a dispatch made with the workflow's own
token always creates a run. It has one job, `plan`, which runs
`deploy/production/auto_plan.sh` three times:

1. `check` decides whether there is anything to plan, with the staging mode of
   `deploy/production/resolve_commit.sh`, and ends green with a notice when there is not. It looks
   at the tip of `main`, never at the sha GitHub attaches to the triggering run, which is not the
   commit that run handled.
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
  queued, in progress, waiting or already succeeded. A run that failed or was cancelled does not
  count, so an early manual dispatch that failed with "not staged yet" does not stop the auto plan.
- `cancelled run <id> (planned <sha7>)`: a stale auto-started run was cancelled.
- `left alone: run <id> (<reason>)`: a waiting run was not cancelled. The reason is
  `started by <login>` (a person's run, including a rollback), `same commit`, `apply is not
  waiting`, `not a dispatch`, `not on main` or `unreadable fields`.
- `run <id> moved on; left alone`: a cancel was refused because the run had just changed state.
- `warning: could not cancel run <id>; it stays waiting`: the cancel was refused and the run is
  still waiting. The new run queues behind it, as for a manual run: reject the stale one.
- `started deploy-production run <id>: <url>`: the plan was started. The apply waits for approval on
  the `production` environment.
- `the dispatch was accepted but no deploy-production run started (check that workflow_run chaining
  is not limited)` (red): GitHub accepted the dispatch and made no run. This is the one failure that
  needs looking at: start `deploy-production` by hand and say so.

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
  trip remains in which an approval can land first. That is harmless: the apply's first steps
  re-check that `main` has not moved (a stale run then fails with `main moved to <sha7> since this
  run's plan`), and a late cancel does not stop an SSM command that was already sent to the
  instance.
- The pass runs once, before `start`: an earlier auto run with its `plan` job still running is not
  listed as waiting and is missed. The new run queues behind it and the owner rejects the older one.
- The workflow shares the `deploy-production` concurrency group with manual runs, and GitHub keeps at
  most one pending run in a group. An auto dispatch made while one manual run waits and another is
  pending replaces the pending manual run. `supersede` never cancels a manual run, but this rule can
  replace a person's pending one; dispatch again.
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
token can read the repository, read Actions and deployments, and write Actions (cancel and
dispatch). Approving a pending deployment needs a user who is a required reviewer, and the token is
not one; the script and the workflow contain no call that approves. It never checks out or runs
anything from the triggering run: it checks out `main`, takes no artifact, and uses no field of the
triggering event in a command.

**Turning it on and off** (an owner action; the value is `true` or `false`):

```bash
gh variable set PROD_AUTO_PLAN --repo amsyrzmi/CSAV2 --body true
gh variable set PROD_AUTO_PLAN --repo amsyrzmi/CSAV2 --body false
```

The first line turns it on, the second off. Read the state of the switch with
`deploy/github/setup-environments.sh production --check`, which prints `true`, `false` or
`unset: off`.

## Rollback

- **Staging never goes back through the workflow.** A plan that is a downgrade stops red, even for
  an explicit `sha`; the manual rollout is the way back (see "Break-glass").
- **Production, image only, automatic.** When `/readyz` or the smoke test fails after services were
  recreated, the run applied no migration and the previous images are registry digests, the rollout
  recreates the four services on the previous digests, waits for readiness and prints
  `Rolled back: the previous images are running; <sha7> is not.` It still ends red, and the
  staged record is not written. The exact lines are in "What a smoke stop or a rollback means".
- **Production, when migrations ran,** or the previous images are unknown, nothing is rolled back
  by itself. Fix forward with a new commit, or restore the backup the deploy took first
  (`deploy/PRODUCTION.md`, "Restoring a backup"). The recovery lines the rollout prints are in
  "What a failure means".
- **A deliberate rollback** is an explicit `sha` on `deploy-production`: a staged commit on `main`
  that already includes the digest change. The plan shows the downgrade warning, and the apply
  does not check the tip of `main` for an explicit `sha`. An older revision than the digest change
  can only be restored by break-glass (`deploy/PRODUCTION.md`, "Break-glass: manual deploy
  (owner only)").

## One-time setup

Each target needs three sides prepared once, in this order: GitHub, AWS, and the host. The two
scripts below are the repeatable path and the manual steps in `deploy/PRODUCTION.md`, "One-time
setup", and `deploy/STAGING_PIPELINE.md`, "Host setup for automatic rollouts", are the reference
they were built from.

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
| `--check` | Read-only. Prints one `[n/total] <what>: ok|missing|differs` line per item and ends `check: everything is ok` (exit 0) or `check: <n> item(s) not ok` (exit 1). |
| `--repo <owner/name>` | The repository (here `amsyrzmi/CSAV2`). |
| `--region <region>` | The AWS region variable of the target (`STAGING_DEPLOY_REGION` or `PROD_DEPLOY_REGION`). |
| `--image-namespace <ghcr.io/owner>` | The repository variable `IMAGE_NAMESPACE`. |
| `--reviewer <login>` | Production only, repeatable (at most six): the required reviewers, for example `<login-1>` and `<login-2>`. |
| `--replace-reviewers` | Production only, with apply only: allows the reviewer set of an existing `production` to change. |
| `--auto-plan true\|false` | Production only: sets `PROD_AUTO_PLAN`. Not given means the variable is not touched. |
| `--paused true\|false` | Staging only: sets `STAGING_AUTO_PAUSED`. Not given means the variable is not touched. |

Environments, secrets and variables, by target:

| Environment | Reviewers | Secrets |
| --- | --- | --- |
| `staging` | none | `STAGING_DEPLOY_ROLE_ARN`, `STAGING_INSTANCE_ID` |
| `staging-images` | none | none |
| `production-plan` | none | `PROD_PLAN_ROLE_ARN`, `PROD_INSTANCE_ID` |
| `production` | the given set (required) | `PROD_APPLY_ROLE_ARN`, `PROD_INSTANCE_ID`, `PROD_CONSOLE_ORIGIN` |

The repository variables are `IMAGE_NAMESPACE` (both targets), `STAGING_DEPLOY_REGION` (staging),
`PROD_DEPLOY_REGION` (production), and the two switches `STAGING_AUTO_PAUSED` (staging, set only
with `--paused`) and `PROD_AUTO_PLAN` (production, set only with `--auto-plan`).

**Secrets never appear on a command line or in output.** A secret value comes from the environment
variable `SETUP_<NAME>` (for example `SETUP_PROD_INSTANCE_ID`), or from a hidden prompt on a
terminal, and reaches `gh secret set` on stdin only. A secret that already exists is not prompted
for and not overwritten unless its `SETUP_<NAME>` is set (an explicit rotation), so a routine run
prompts for nothing. Values are validated (role ARN, managed-node id, origin with no path, port or
trailing slash) before any write, and no message ever contains a value. The only values printed
are the two switches, which are `true` or `false`.

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
   cannot be set before the environment exists).
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
- **The `deploy` skill:** what an agent may and may not do for either flow (plan before apply, never
  approve, never bypass the staged gate).

The test `tests/test_deploy_docs.py` keeps the moved headings at their destination and the stubbed
ones in place, and nothing else here is frozen: later edits are expected.
