# A CD pipeline from merge to production

Status: draft for owner approval, 2026-11-13. Builds on `2026-11-10-production-deploy-design.md` (the
`deploy-production` workflow, merged in PR #91) and `deploy/PRODUCTION.md`. No real hostnames,
addresses, account ids, node ids or ARNs appear here; the repository is public.

## Intent

`deploy-production` works: its first real run on 2026-11-11 deployed a commit with three migrations
through GitHub, OIDC and Systems Manager. But the route to it still has manual links the owner has to
remember, and the first run showed three weak spots:

- Staging is rolled out by hand from the home server. Until someone does that, no commit has a
  "staged" record and production refuses it.
- An empty `sha` meant "the newest staged commit", which was not the commit the owner had just merged.
- Both hosts need a GitHub token only to look up image digests and staged records. That token expires
  and has to come from another person.

The owner's target routine: **open a PR, merge it, then run one workflow to deploy production.**
Everything between the merge and that run is automatic, with tests at each stage (the owner stressed
that the tests before a deploy matter most), and the one human decision left is reading the production
plan and approving it.

## Goals

1. Every merge to `main` rolls out to staging (the home server) automatically, migrations included,
   with no "go" from anyone. The commit is recorded as staged only after staging has passed its own
   smoke test.
2. `deploy-production` with no `sha` means "the commit I just merged" (the tip of `main`), and refuses
   it with a clear message when staging has not finished it yet.
3. Tests run before and after each deploy, as part of the pipeline, not by hand (see Tests).
4. An image-only deploy that comes up unhealthy rolls back to the previous images by itself. Anything
   involving migrations stops and tells the owner to fix forward or restore the backup.
5. The workflow, not the hosts, talks to GitHub: no GitHub token on the home server or on production.
6. The AWS and GitHub setup for a deploy target is a repeatable script, not a list of commands typed
   into CloudShell.
7. Optional, off until the owner turns it on: the production plan starts by itself when staging
   succeeds, so the owner's only action is the approval.
8. One runbook and one skill describe both environments.

## Non-goals and anti-goals

- No automatic production apply. Approval of the `production` environment stays a person's click, and
  neither the plan role nor any new automation can apply.
- No inbound port, no SSH, no self-hosted GitHub runner, no poller on either host, no monitoring
  software outside AWS.
- No secrets, hostnames or ids in the repository. Values come from repository or environment
  variables and secrets, or from files on the hosts.
- Not a staging redesign: the staging stack, its compose files and its secret files stay as they are,
  except where a section below says otherwise.
- Not moving staging off the home server. The design keeps the target replaceable (one instance id
  and one host config), so moving later is not a rewrite.
- Not replacing the break-glass procedure. The owner can still run the script by hand on either host.

## Constraints

- The home server belongs to the other developer. Nothing in this project changes it until they
  agree; building is done in the repository, and the host-side steps are done by the owner or that
  developer with the setup scripts.
- The host has memory pressure (a shared machine). The Systems Manager agent measured about 55 MB
  resident on production and is acceptable here too, but must be measured on the home server.
- The repository is public: a workflow can run from `main` only, both new environments must be
  restricted to `main`, and every action is pinned to a commit.
- The rollout core (`scripts/staging_rollout.py`) is shared by both targets; a change to it is a
  change to both, and the existing tests for both targets must keep passing.
- OIDC: the repository issues GitHub's immutable subject, `repo:<owner>@<owner id>/<repo>@<repo id>`.
  Role trust policies carry a `<repo-subject>` placeholder that is filled from the repository's
  `actions/oidc/customization/sub` setting (PR #102).

## Approaches considered

How GitHub reaches the home server:

- **A. Systems Manager hybrid node on the home server, the production mechanism (chosen).** A
  `deploy-staging` workflow sends the rollout to the node with a short-lived OIDC role. Reuses
  everything built for production, one security model, the whole pipeline is visible in Actions. Cost:
  an agent running as root on the other developer's machine, and staging depends on AWS being
  reachable.
- **B. Self-hosted GitHub runner on the home server.** Simplest workflow, no AWS. Rejected: the
  repository is public, GitHub advises against self-hosted runners on public repositories, and the
  runner would need docker access on the machine that holds the staging secrets.
- **C. A timer on the home server that polls for new images.** No AWS, nothing inbound. Rejected:
  GitHub no longer shows what staging did, failures stay in a local log, and it adds a poll delay.

Where staging lives: the owner chose to keep it on the home server. A staging instance on AWS would
remove the "home server down, so no commit is staged and production is blocked" dependency. The
break-glass path covers that case for now, and the design keeps the staging target replaceable.

## The pipeline

```
PR -> CI -> merge -> CI on main -> publish-staging-images        (exists, automatic)
  -> deploy-staging   (new: pre-checks, rollout over SSM, smoke, record staged)   automatic
  -> deploy-production (manual run, or auto-started plan; plan -> approval -> apply -> smoke)
```

### The image publish needs no approval today

Verified on 2026-11-13: the environment `staging-images` has no required reviewer, and recent
`publish-staging-images` runs finished in under a minute with nothing waiting. The workflow's own
header and `deploy/RELEASE.md`, `deploy/STAGING_PIPELINE.md` and `deploy/PRODUCTION.md` still say the
owner approves the publish; those words are stale and PR 3 corrects them. "No go from anyone" depends on
the environment staying unreviewed. If the owner later adds a reviewer back, goal 1 becomes "automatic
after one click", and a merge waits on that click.

### deploy-staging (new workflow)

- Starts on `workflow_run` of `publish-staging-images` succeeding on `main`, and on
  `workflow_dispatch` with an optional `sha` (a re-run, or a commit the owner wants again).
- Two jobs, `plan` then `apply`, in the existing environment `staging` (made `main`-only; it has no
  reviewer). One role, `csa-staging-deploy`, trusts only that environment and may send only the two
  staging documents, `csa-staging-plan` and `csa-staging-apply`, to the one staging node. Two documents
  keep each parameter validated by `allowedPattern`, as in production.
- Concurrency group `deploy-staging`, no cancel-in-progress. **Newest wins**: before sending anything,
  a job checks that its commit is still the tip of `main` and exits successfully with "superseded"
  when it is not, so a burst of merges produces one rollout of the latest commit, not a queue.
- The workflow resolves the image digests for the commit and passes them to the instance (see "The
  workflow talks to GitHub"). The instance verifies each pulled digest and revision label itself.
- After `apply` succeeds, a final job writes the staged record (a GitHub Deployment with a `success`
  status in environment `staging`, description `staging rollout`) with the workflow's own token
  (`deployments: write`). GitHub also creates a Deployment by itself for any job that names an
  environment, and marks it `success` when the job ends, which would make a commit look staged after
  the plan job, before the rollout or the smoke test. Two guards: the plan and apply jobs set
  `deployment: false` on their environment (the builder verifies that the setting works as documented),
  and the staged gate counts only a Deployment whose description is `staging rollout`, which is what
  every existing record carries, so the records already written stay valid. The staged
  record therefore exists only after the instance reported success, smoke test included. A failed
  rollout leaves staging's previous commit as the newest staged one and fails the run (GitHub emails
  the owner).
- **When the plan is not clean.** The plan job parses the plan's `Pending migrations:` line and the
  `apply` job passes it on, exactly as production's workflow does (`parse_plan.sh`). A plan that exits
  non-zero or prints `Stopped at` fails the run before anything changes. A `WARNING:` line is shown in the
  run summary and does not stop the run, except a downgrade warning, which does (staging must never go
  back by itself). A missing secret file already stops the plan with exit 2 and the line `Nothing on
  staging was changed`; that red run is the accepted way a change that needs host preparation (a new
  secret file or a new switch) announces itself. A prerequisite that no check can see is the PR author's
  job: the pull request template gets a line, "needs host preparation before merge", so it is said
  before the merge.
- The instance side is the existing rollout, run by a generalised entry script (see Entry script).

### deploy-production (changes)

- **Default `sha`.** Empty means the tip of `main`. If that commit has no staged record the run fails
  in the plan job with `main's tip <sha7> is not staged yet; wait for deploy-staging`. An explicit
  `sha` still works, for a rollback to an older staged commit.
- **Pre-checks, in the plan job, before anything is sent to the instance** (all read by the workflow
  with its token): the commit's `ci` run on `main` concluded `success`; the commit has a staged
  record; both images are published and tagged with the full sha; the commit is on `main`. A failing
  check fails the run with its own message, and nothing reaches the instance.
- **On the instance, in the plan:** unchanged (migrations, host settings digest, downgrade warning,
  installed `backup.sh` equal to the repo copy).
- **Apply.** First step after approval, before anything is sent: the staleness check. The commit must
  still be the tip of `main` (unless it was given explicitly), and production must not already run a
  newer commit. This makes an approval of an out-of-date waiting run harmless.
- **After apply:** the instance runs its smoke test; then the workflow runs a public smoke test
  against the production console origin (kept as an environment secret, masked). A failed smoke test
  fails the run (see Rollback).
- **Auto-start the plan (goal 7).** A second trigger, `workflow_run` of `deploy-staging` succeeding,
  starts the plan for that commit when the repository variable `PROD_AUTO_PLAN` is `true` (default
  `false`). The apply still waits on the `production` environment. A new auto-started run cancels
  earlier runs of the workflow whose apply job is still `waiting`; the apply-time staleness check
  makes an approval that races the cancel harmless. A run whose apply job is `in_progress` is never
  cancelled.

### The workflow talks to GitHub, the hosts do not

Today the instance runs `gh` to find the published digests and the staged record, so each host holds
a classic `read:packages` token that expires. The workflow's own token can read both. So:

- The workflow resolves the digests (`csa-python`, `csa-frontend`) for the sha and passes them to the
  plan and apply documents as two new parameters, each `^sha256:[0-9a-f]{64}$`.
- The instance no longer calls `gh`. It still pulls by digest with an empty Docker config, checks the
  pulled image's digest and its `org.opencontainers.image.revision` label against the sha, and refuses
  on any mismatch. Wrong images are therefore still caught on the host.
- The staged-record check and the staged-record write move to the workflow.
- **Trade-off, accepted by the owner on 2026-11-13:** the instance no longer re-checks the staged record on its own. The gate is
  enforced by the workflow, which runs from `main`, assumes a role that only the protected environment
  can assume, and (for apply) waits for approval. The hosts' break-glass path (the owner running the
  script by hand) skips the gate unless the owner passes the digests and runs `record-staged` by hand;
  the runbook says so. Defence in depth is lower; the token and its expiry disappear.

### Tests

Both environments run read-only smoke checks. They never touch customer data, never call a model, and
finish in under a minute. The list lives in the repository and runs on the instance as a rollout step
after readiness and the existing `0485.sql` check:

- the gateway reports ready; the frontend answers on its port;
- the webhook route answers with an unauthenticated status (a 401, 403 or 404, not a 5xx);
- the database's recorded migration versions equal the versions on the commit;
- the console origin and webhook origin from the host file answer from the instance (production only;
  staging has no host file today, so these are skipped there);
- the workflow's public check: the production console origin answers with a 2xx or 3xx (the login
  redirect).

Pre-deploy tests are the pre-checks above (CI green, staged, published, plan clean, backup taken). The
unit tests for all of it follow the existing pattern: a fake runner for the rollout core, harness
tests for the shell scripts, and structural tests that read the workflow and policy files.

### Rollback

- The rollout, when `/readyz` or the smoke test fails after services were recreated, **and this run
  applied no migration, and the previous images are registry digests** (the plan's "Rollback
  references" are known), recreates the four services on the previous digests, waits for readiness
  and reports `rolled back to <sha7>`. It then still exits non-zero, so the run is red and the staged
  record is not written.
- If the rollback itself does not become ready, it stops and prints the recovery lines unchanged.
- If migrations were applied, or the previous images are unknown (the first deploy ran local tags), it
  does not roll back; it stops with the existing recovery lines (fix forward, or restore the backup
  the deploy took first).
- This replaces the "no automatic rollback" rule of the staging tool, narrowly: only after recreate,
  only image-only, only to a verified digest. Apply and the restart stay never-retried.

### Entry script and hosts

- `deploy/production/deploy_entry.sh` becomes a shared entry script, installed once per host under a
  per-target name (`csa-prod-deploy` on production, `csa-staging-deploy` on the home server). The
  target, checkout path, deploy user, lock file and log directory come from a root-owned config file
  next to it, not from arguments, so the Systems Manager document cannot change them.
- The staging host needs its own dedicated deploy checkout, owned by the deploy user, not the owner's
  working checkout: an automatic rollout must never check out or touch the directory the owner and
  their tools work in.
- The staging target keeps its flags file (`deploy/staging-rollout.env`) and its secret directory. It
  gets no host overlay unless the builder finds one is needed.

### Setup scripts (goal 6)

- `deploy/aws/setup-target.sh <staging|production>` (CloudShell): creates or updates, idempotently,
  the OIDC provider, the roles and their policies from the templates (filling region, account, node
  id and subject prefix, with the existing guard against empty values), and the two documents, and
  prints the role ARNs. A `--check` mode reads everything back and says what is missing or wrong,
  without changing anything.
- `deploy/github/setup-environments.sh <staging|production>` (a machine with `gh` logged in as a
  repository admin): creates or updates the environments, reviewers, `main`-only branch rules,
  secrets and the repository variables, from environment variables or hidden prompts. It never prints
  a secret, and a `--check` mode lists names, reviewers and rules.
- Both scripts hold no real ids; inputs are parameters. Tests run them against fake `aws` and `gh`
  executables on `PATH` and assert the calls.

### One runbook and one skill (goal 8)

- `deploy/DEPLOY.md` becomes the single entry: the pipeline, the routine per release, one-time setup
  for either target with the scripts, what each failure means, rollback, and break-glass.
  `deploy/PRODUCTION.md` and `deploy/STAGING_PIPELINE.md` keep only target-specific facts and point to
  it; nothing they hold today is deleted without a replacement.
- One skill, `deploy`, replaces `staging-rollout` and `production-deploy`. It covers both flows and
  keeps their rules (plan before apply, verbatim recovery lines, never bypass the gate, never approve
  or cancel without checking state).

## Decisions taken with the owner

- Staging stays on the home server and rolls out automatically on every merge, migrations included.
- Option A (Systems Manager on the home server).
- All eight goals are in scope.
- The owner accepts that the workflow is the only staged-record gate and the hosts hold no GitHub
  token (2026-11-13).
- The other developer has agreed to the Systems Manager agent on the home server (2026-11-13).

## Calls I made (to check)

- The smoke checks above, and keeping them read-only and under a minute.
- `PROD_AUTO_PLAN` defaults to off. Every merge would otherwise send both reviewers a review request.
- Image-only automatic rollback, and only that.
- The workflow enforces the staged gate; the instance no longer double-checks it.
- A dedicated checkout on the home server for automatic rollouts.
- The staged record stays a Deployment in environment `staging`, now identified by its description.
- A non-clean staging plan stops the run; a missing secret file is the accepted red run for host prep.
- The AWS setup script moves into PR 2.
- One `staging` environment and one staging role, not separate plan and apply ones, because staging
  has no human approval to separate from.

## Host preparation before PR 2 can run

Agreement is given, so PR 2 is built to be run, not only tested against fakes. Still to do on the
home server, once, before the first automatic rollout: register it as a Systems Manager node in the
production account and region, measure the agent's memory against the machine's headroom, create the
dedicated deploy checkout, and install the entry script and its config. The setup script prints each
step; the owner and the other developer do them together, and the first run is watched live.

## Delivery in three pull requests

1. **Core and production workflow.** Digest pass-through (no `gh` on the hosts), tip-of-`main`
   default, the pre-checks, the apply-time staleness check, the smoke test (instance and public),
   image-only rollback, with the existing staging record still written by the script. Independent of
   the home server; ships and is exercised on production first.
2. **Staging automation.** The `deploy-staging` workflow, the staging documents and role template, the
   generalised entry script and host config, the workflow-written staged record, and the AWS setup script
   (`setup-target.sh`, both targets), because the staging role and documents must exist before the
   workflow can run. Needs the other developer's agreement, a Systems Manager node on the home server and
   a dedicated checkout there before it can be exercised; until then it is built and tested against fakes.
3. **GitHub setup script, auto-plan, the consolidated runbook and skill, and the stale approval wording.**
   Depends on 1 and 2.

## Left to the builder

- The exact smoke checks and their endpoints, within the constraints above.
- How the entry script reads its config, the config format, and the exact install paths.
- How the staging compose project moves to the dedicated checkout (the compose files' relative paths
  and bind mounts must be checked first; a recreate must not change any volume).
- The names of the new documents' parameters, as long as each is validated by `allowedPattern`.
- How the `ci` run of a commit is found, and what happens when a commit has no `ci` run (a docs-only
  merge may skip jobs): decide whether "skipped by path filter" counts as green.
- Migration of the existing tests for the staging skill and the production skill into the new one.

## Risks

- The first automatic staging run happens on the home server with real data: the first run is watched
  live, and the manual script remains the fallback.
- The dedicated checkout changes where the compose project's files come from; the builder verifies on
  a copy of the compose config before anything runs on the host.
- Staging restarts after every merge, including a short stop of all four services when migrations are
  pending. Acceptable for staging; it is the owner's choice.
