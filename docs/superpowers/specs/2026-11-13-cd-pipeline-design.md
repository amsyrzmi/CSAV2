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
- **Apply.** First step after approval, before anything is sent: the staleness check. In a default
  run: main's tip must not have moved since the plan; the run then fails with `main moved to <sha7>
  since this run's plan; start the workflow again`. An explicit `sha` is a deliberate rollback to an
  older staged commit, is not checked, and shows the downgrade warning in its plan. This makes an
  approval of an out-of-date waiting run harmless.
- **After apply:** the instance runs its smoke test; then the workflow runs a public smoke test
  against the production console origin (kept as an environment secret, masked). A failed smoke test
  fails the run (see Rollback).
- **Auto-start the plan (goal 7).** A separate workflow, `deploy-production-auto`, started by
  `workflow_run` of `deploy-staging`, dispatches the plan for the tip of `main` when the repository
  variable `PROD_AUTO_PLAN` is `true` (default `false`). The apply still waits on the `production`
  environment. The auto workflow cancels earlier auto-started runs whose apply job is still
  `waiting`, by an explicit API call; the apply-time staleness check makes an approval that races the
  cancel harmless. A run whose apply job is `in_progress` is never knowingly cancelled (see "Changes
  made while building PR 3" for the one-round-trip window).

### The workflow talks to GitHub, the hosts do not

Today the instance runs `gh` to find the published digests and the staged record, so each host holds
a classic `read:packages` token that expires. The workflow's own token can read both. So:

- The workflow resolves the digests (`csa-python`, `csa-frontend`) for the sha and passes them to the
  plan and apply documents as two new parameters, each `^sha256:[0-9a-f]{64}$`.
- The method is the registry manifest API, not the packages API. The images are public, so the
  workflow asks `ghcr.io` for an anonymous pull token, then reads the `Docker-Content-Digest` of
  the manifest tagged with the full sha. It needs no `packages: read` permission, and the
  `read:packages` token is needed only on a host that is run by hand (break-glass).
- Digests are optional on the host CLI. Given both, the instance makes no GitHub call. Given
  neither (a host run by hand, break-glass), it does today's `gh` lookup and still enforces the
  staged gate itself, so that path keeps its gate. Only one of the two is refused.
- With digests, the instance no longer calls `gh`. It still pulls by digest with an empty Docker config, checks the
  pulled image's digest and its `org.opencontainers.image.revision` label against the sha, and refuses
  on any mismatch. Wrong images are therefore still caught on the host.
- The staged-record check and the staged-record write move to the workflow.
- **Trade-off, accepted by the owner on 2026-11-13:** the instance no longer re-checks the staged record on its own. The gate is
  enforced by the workflow, which runs from `main`, assumes a role that only the protected environment
  can assume, and (for apply) waits for approval. The hosts' break-glass path (the owner running the
  script by hand, with no digests) keeps its own staged gate, using the `gh` lookup and the
  `read:packages` token; the manual Compose procedure has no gate, as before. Defence in depth is
  lower for the workflow path; the token and its expiry disappear from it.

### Tests

Both environments run read-only smoke checks. They never touch customer data, never call a model, and
finish in about 110 seconds in the worst case (the two origin checks retry up to 3 times, 5 s apart). The list lives in the repository and runs on the instance as a rollout step
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
  references" are known; every replica of every previous app container was running, on refs that
  agree per app), recreates the four services on the previous digests, waits for readiness
  and prints `Rolled back: the previous images are running; <sha7> is not.` It then still exits non-zero, so the run is red and the staged
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

- The smoke checks above, and keeping them read-only and short (about 110 seconds worst case).
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

## Changes made while building PR 2

The plan for PR 2 (`docs/superpowers/plans/2026-11-17-cd-pipeline-pr2.md`) changed or fixed these
points of the design above. They are what the code and the runbooks now do.

- **The trigger is a wake-up; the commit is the tip of `main`.** On 2026-11-17, real publish runs
  showed that a `workflow_run`-started publish carries the tip of `main` at creation as its
  `head_sha`, not the commit it published (a publish skipped for a cancelled `ci` run carried the
  next commit's sha). The workflow never reads `github.event.workflow_run.head_sha`; every automatic
  run resolves the tip of `main`. Newest wins in three places: GitHub's concurrency drops older
  pending runs; an automatic run whose tip is not ready yet (ci not green, or images not published)
  ends green with `nothing to roll out`; the apply job ends green with `superseded` when `main`
  moved since its plan. An automatic run whose tip already has a staged record ends green with
  `already staged`.
- **An explicit `sha` (dispatch) skips the tip checks.** The plan's downgrade warning still stops
  it, so staging never goes back through the workflow, even on purpose; taking staging back is the
  manual script (break-glass).
- **Pause switch: the repository variable `STAGING_AUTO_PAUSED`.** `true` turns automatic runs into
  a green `paused` notice; unset or anything else means automatic. Dispatch ignores the pause. A
  required reviewer on `staging` was rejected: it would hold every run, dispatch included, and
  leave a queue of waiting runs. The owner sets the variable to `true` before PR 2 merges.
- **A dedicated deploy user on the home server, `csa-deploy`** (system user, no login shell, member
  of `docker`), owning its own checkout `/srv/csa-staging-deploy/CSAV2`. The owner's account was not
  reused: its `gh` login has the `repo` scope and its home holds SSH keys and tool sessions.
  `csa-deploy` passes through `/srv/csa-staging` and the secret directories with search-only ACLs
  (`x` on directories, nothing on files).
- **Install paths on the home server:** entry script `/usr/local/sbin/csa-staging-deploy` and its
  config `/usr/local/sbin/csa-staging-deploy.conf` next to it; Python environment
  `/opt/csa-staging-deploy/venv` (a uv-built 3.12 environment, because the host's `python3` is
  3.14); log directory `/var/log/csa-staging-deploy`; entry lock `/run/lock/csa-staging-deploy.lock`.
  Production keeps its paths.
- **Config format:** `KEY=value` lines, parsed and never sourced, exactly six keys, all required
  (`TARGET`, `DEPLOY_USER`, `CHECKOUT`, `PYTHON`, `LOCK`, `LOG_DIR`). The file must be a regular
  file, owned by the uid that owns the script, and not writable by group or others. Environment
  variables override nothing; a refused config exits 78.
- **Production is unaffected on the host.** Its documents do not change and the installed
  `csa-prod-deploy` keeps working; reinstalling the shared script there needs a config file
  (`deploy/PRODUCTION.md`, "Reinstalling the shared entry script"), not a prerequisite.
- **A rollout lock in the core**, `/run/lock/<project>-rollout.lock`, taken by `plan` and `apply`
  and not by `record-staged`, so the owner's manual staging runs and the workflow's runs exclude
  each other. The file is opened read-only and `O_CREAT` is passed only when it does not exist yet:
  `/run/lock` is mode 1777 and the host has `fs.protected_regular = 2`, so the kernel refuses
  `open(O_CREAT)` on an existing file there for a non-owner, even read-only.
- **The staged record.** The record job writes it with the workflow token; the core writes it on
  staging only when it was not given digests (the owner's manual run). The production gate
  (`resolve_commit.sh`) and the core's break-glass gate count a deployment only when its
  description is `staging rollout`, so GitHub's auto-created deployments never count, even if
  `deployment: false` does not behave as documented.
- **Shared scripts stay in `deploy/production/`** (no file moves); staging-only files go in
  `deploy/staging/` and the AWS script in `deploy/aws/`.
- **`resolve_commit.sh` exits 3** for "not ready yet" (ci not succeeded, images not published) and 1
  for every other refusal. Production treats both as a failure; staging's automatic runs treat 3
  as `nothing to roll out`. It needs `DEPLOY_TARGET` (`staging` or `production`).
- **The staging region is its own repository variable, `STAGING_DEPLOY_REGION`**, so the target
  stays replaceable; its value equals `PROD_DEPLOY_REGION` today.
- **One role serves the plan and apply sessions** of staging (the session names differ for the
  audit log). The OIDC subject carries the environment but no ref, so the `staging` environment's
  main-only deployment-branch policy is the only ref restriction. Whether the subject still carries
  `environment:staging` under `deployment: false` is not documented: the first dispatch proves it,
  and it fails closed with an AssumeRole error.
- **For PR 3, not changed here:** GitHub's rule is that `workflow_run` cannot chain more than three
  levels of workflows. ci, publish and deploy-staging are three levels, so a `PROD_AUTO_PLAN`
  workflow started by `workflow_run` of deploy-staging would be a fourth level and the last allowed
  (a fifth does not run); prefer a `workflow_dispatch` from deploy-staging's token (allowed) so the
  chain stays short. `publish-staging-images.yml` still uses unpinned `@v4` actions.

## Changes made while building PR 3

The plan for PR 3 (`docs/superpowers/plans/2026-11-18-cd-pipeline-pr3.md`) changed or fixed these
points of the design above. They are what the code and the runbooks now do. The three statements of
"deploy-production (changes)" that no longer held (a second trigger in `deploy-production`, a new
run that cancels earlier runs, and an `in_progress` apply that is "never cancelled") are corrected
there in place.

- **The auto-plan is a separate workflow, `deploy-production-auto`.** A run that waits for approval
  holds the concurrency group `deploy-production`, so a gate job inside the new run would never start
  while the run it must cancel is waiting; concurrency also cannot cancel only a run whose apply is
  `waiting` and spare one whose apply is `in_progress`. A separate workflow also leaves manual
  dispatch exactly as it was: `deploy-production.yml` is untouched (its `Manual dispatch only`
  header kept), and so are `resolve_commit.sh`, `ssm_run.sh`, `parse_plan.sh`, `deploy_entry.sh` and
  `public_smoke.sh`. A failing auto-plan is a red `deploy-production-auto` run, not a red staging or
  production run. The workflow runs `auto_plan.sh` three times: `check`, `supersede` and `start`.
- **The dispatch and the `GITHUB_TOKEN` exception.** `start` runs `gh workflow run deploy-production`
  with no `sha`, so the run plans the tip of `main` and can never request an explicit rollback. GitHub
  documents that events made with `GITHUB_TOKEN` create no new run, except `workflow_dispatch` and
  `repository_dispatch`; the job holds `actions: write` for that. It then verifies that a run
  appeared and fails red when none did.
- **The chain count.** ci, `publish-staging-images`, `deploy-staging` and `deploy-production-auto`
  are three `workflow_run` links, the last allowed by GitHub's limit of three levels; the dispatch
  comes last and is a `workflow_dispatch` run, not a fourth link. Whether the dispatched run counts
  is not documented, so the live acceptance (Part C of the plan) proves it, and `start` fails red,
  not silently, when no run appears.
- **`check` decides; the conclusion of `deploy-staging` does not.** `deploy-staging` ends green for
  `paused`, `nothing to roll out`, `already staged` and `superseded` as well as for a real rollout,
  so `auto_plan.sh check` asks the facts with `resolve_commit.sh` for the staging target. It ends
  green with `nothing to plan: ...` when ci or the images are not ready, when the tip has no staged
  record, or when a `deploy-production` run for the tip is already queued, in progress, `waiting` or
  successful. A run that failed or was cancelled does not block a new plan. Any other resolver
  failure is red.
- **The cancel rule and its race.** The only mechanism is `POST .../actions/runs/{id}/cancel`.
  `supersede` cancels a run only when, read just before the call, its status is `waiting`, its
  event is `workflow_dispatch`, its triggering actor is `github-actions[bot]`, its branch is `main`,
  its sha differs from the tip, and its jobs are exactly `plan` (success) and `apply` (`waiting`).
  A person's manual run is never touched, and a run whose apply is `in_progress` is never knowingly
  cancelled. Every field fails closed: an unreadable field leaves the run alone. The rule is a read
  followed by a call, so a window of one API round trip remains in which an approval can land first;
  the apply's first steps (the variables and the staleness check) come before the AWS credentials, so
  the outcome is benign. Two limits are accepted: an earlier auto run still in its `plan` job is
  missed, and GitHub's own rule can replace a person's pending manual run with an auto dispatch.
- **Security: the auto path cannot skip the approval.** The approval is a property of the
  `production` environment, not of a workflow file. The auto workflow has no environment, no
  `id-token` permission and no secret, never calls the pending-deployments endpoint (a test asserts
  it) and never checks out or runs anything from the triggering run. It and its scripts never approve:
  the approver is always a person.
- **The setup script: read, build the whole desired state, write only on a difference, re-read.**
  `deploy/github/setup-environments.sh` reads each environment and writes only what differs. Two
  observations about `PUT .../environments/<env>`, neither documented: a hand run on 2026-11-17 was
  read as replace-on-PUT, and a controlled probe on a throwaway environment on 2026-11-18 showed that
  a partial PUT (only `deployment_branch_policy`) did not reset reviewers, `prevent_self_review` or
  `can_admins_bypass`, and that `can_admins_bypass` is accepted by the PUT body though it is not
  documented. The plan relies on neither: the script builds the complete body from the current values
  plus the change and re-reads the environment after every write, failing loudly when anything
  differs from what was intended. The tests run against a stateful fake `gh` under both PUT
  semantics (merge and replace). The script never prints a secret (values reach `gh secret set` on
  standard input only), and `--check` reads everything and reports without writing.
- **The owner's decision of 2026-11-18 on admin bypass.** `can_admins_bypass` was set to `false` on
  `production` and `production-plan` on 2026-11-18 at the owner's request, so a repository admin can
  no longer skip the approval; the script enforces and reports it. `prevent_self_review` stays
  `false` by the owner's earlier decision (one approval, self-review allowed). `staging` and
  `staging-images` keep `true`, carried over and reported.
- **`staging-images` had no branch policy.** On 2026-11-18 it had no reviewer, as the design says,
  and no branch restriction either, although `deploy/RELEASE.md` described `main` only. A real
  environment with no custom policies answers 404 to `.../deployment-branch-policies`, so the script
  derives "missing" from the environment itself and lists or POSTs a policy only after the PUT that
  turns custom policies on. Until `setup-environments.sh staging` runs for real, `staging-images`
  still has no branch policy.
- **Secrets and variables, by environment.** `staging`: `STAGING_DEPLOY_ROLE_ARN`,
  `STAGING_INSTANCE_ID`; `staging-images`: none; `production-plan`: `PROD_PLAN_ROLE_ARN`,
  `PROD_INSTANCE_ID`; `production`: `PROD_APPLY_ROLE_ARN`, `PROD_INSTANCE_ID`,
  `PROD_CONSOLE_ORIGIN`. Repository variables: `IMAGE_NAMESPACE`, `STAGING_DEPLOY_REGION`,
  `PROD_DEPLOY_REGION`, and the switches `STAGING_AUTO_PAUSED` and `PROD_AUTO_PLAN`. A secret that
  exists is overwritten only when its `SETUP_<NAME>` is set.
- **One runbook and one `deploy` skill.** `deploy/DEPLOY.md` holds the cross-cutting text; the
  generic sections moved there whole and a permanent moved-heading test keeps the moved and the
  stubbed headings in place. Completeness of the move was proved once by a one-off line check, not by
  a frozen list of every heading. One `deploy` skill replaces `staging-rollout` and
  `production-deploy` and keeps their rules, including: never approve, never reject, and read a run's
  state before any cancel.
- **The stale publish-approval wording is fixed in every place** with one sentence: "The image
  publish needs no approval today (the `staging-images` environment has no reviewer). If a reviewer
  is added to `staging-images`, every merge waits on that click." The places were the publish
  workflow header, `deploy/RELEASE.md`, `deploy/STAGING_PIPELINE.md` and the staging skill; the
  design's statement that `deploy/PRODUCTION.md` said it too was wrong: PRODUCTION.md had no such
  sentence, so nothing changed there. The dated history in `deploy/RELEASE.md` is kept as it is.
- **Pins.** `publish-staging-images.yml` now pins `actions/checkout` and `actions/download-artifact`
  to the commit each `v4` tag points at, with a version comment, and stays on major 4 (the workflow
  publishes the images, and a major upgrade is a different change). The unpinned actions of `ci.yml`
  are not part of PR 3.

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
