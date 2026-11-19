---
name: deploy
description: Use when the owner asks to roll out, deploy or update staging (csa-staging) or production (csa-prod) - staging rolls out by itself through the deploy-staging workflow, production through the deploy-production workflow (plan, approval, apply), including images with or without database migrations.
---

# Deploy

One skill for both deploy targets. Staging (`csa-staging`) rolls out by itself on every green merge
to `main`, through the `deploy-staging` workflow. Production (`csa-prod`) takes one commit that
staging has already run, through the `deploy-production` workflow: a plan, one human approval, then
the apply. The single entry runbook is `deploy/DEPLOY.md`: the pipeline, the routine for one
release, every outcome and refusal, the auto-planned production runs, rollback, and the one-time
setup with its two scripts. This skill is what an agent may and may not do; it points at that
runbook for the details.

## Which flow

- "Roll out", "update" or "deploy" staging, or "is staging on the latest main": the staging flow
  (`## Staging: automatic rollouts`, and the manual break-glass only when the owner asks).
- "Deploy" or "roll out" production, or "put it live": the production flow (`## Production: hold
  before step 1`, then `## Production: steps`).
- A commit that staging has not run goes through the staging flow first; production never takes it.
- If it is not clear which target the owner means, ask. Do not guess, and do not start anything
  until the target is clear.

## Where to run

Everything runs from the main checkout `/home/amsyrzmi/CSAV2`, never from a worktree. The staging
and production flows use only `gh` commands, against the repository `amsyrzmi/CSAV2`: nothing runs
on the production instance from here, because Systems Manager runs the rollout there. The one
exception is the staging manual break-glass below, which runs `scripts/staging_rollout.py` (and
the git and docker commands listed there) in the main checkout, on the host that runs staging (it
refuses when HEAD is not on `origin/main` or the tree has tracked changes).

## Staging: automatic rollouts

Rollouts of `csa-staging` are automatic: every green merge to `main` is rolled out by the
`deploy-staging` workflow (plan, apply, then a record job that writes the staged record) once its
image publish has finished. The image publish needs no approval today (the `staging-images`
environment has no reviewer). If a reviewer is added to `staging-images`, every merge waits on that
click. The automatic rollout starts when the publish finishes. This skill checks that workflow,
dispatches it when the owner says so, and keeps the manual `scripts/staging_rollout.py` steps for
when the owner asks for them. Details: `deploy/DEPLOY.md`, "Staging: automatic rollouts
(deploy-staging)", and `deploy/STAGING_PIPELINE.md`, "Routine rollouts".

1. Check the latest runs: `gh run list --workflow deploy-staging.yml --limit 5`, and for a red one
   `gh run view <id> --log-failed`. Report the outcome verbatim: `nothing to roll out`,
   `already staged`, `superseded` and `paused` are green and mean nothing was sent (the runbook
   says why for each); `Stopped at lock`, `Missing secret files`, a downgrade refusal or any
   `Stopped at <step>: ...` is red. Show the lines as printed, do not interpret a failure and do
   not retry on your own.
2. A green run ends with `Recorded <sha7> as staged`. If only the `record` job is red, staging is
   updated but not recorded (production refuses the commit): tell the owner. After the owner's go,
   re-run that job (`gh run rerun <id> --failed`).
3. Dispatch only after the owner's go: `gh workflow run deploy-staging.yml --ref main`, or with a
   named commit `gh workflow run deploy-staging.yml --ref main -f sha=<sha>`. An empty `sha` is the
   tip of `main`. A dispatch ignores the pause. Staging never goes back through the workflow: a
   downgrade is refused. A dispatch with a named `sha` shares the `deploy-staging` concurrency
   group, so it can replace the pending automatic run for the tip; if that `sha` is older than what
   runs now it ends red at the downgrade stop and the tip waits for the next merge or a dispatch
   with an empty `sha`.
4. Pausing is the repository variable `STAGING_AUTO_PAUSED` (`gh variable set STAGING_AUTO_PAUSED
   --body true|false`); change it only on the owner's say-so.
5. Use the manual steps below only when the owner asks (or when the workflow cannot do it, such as
   a downgrade), and pause first, unpause after.

## Staging: manual rollout (break-glass)

The procedure is in `deploy/STAGING_PIPELINE.md`, "Manual rollout (break-glass)". Pause first
(`STAGING_AUTO_PAUSED` `true`), unpause after.

Everything runs in `/home/amsyrzmi/CSAV2`, the main checkout, never in a worktree. The script
refuses when HEAD is not on `origin/main` or the worktree has tracked changes. The owner's go here
is what authorises a manual rollout; nothing reaches staging by hand without it.

### Steps

1. Pull: `git pull --ff-only origin main`. If it is not a fast-forward, stop and tell the owner.
2. Plan (read-only): `.venv/bin/python -m scripts.staging_rollout plan`. Summarise it in a few
   lines: the target SHA, whether the images changed, the pending migrations or "none", the
   downtime (a migration stops gateway, frontend, worker, delivery and the training worker: console
   unavailable, bot replies delayed, not lost), the check files, and any missing secret files (exit 2).
   - `images for <sha7> are not published yet (<state>)`: the publish run starts by itself after a
     green main CI run and needs no approval today (`<state>` reads `waiting for approval` only if a
     reviewer was added to `staging-images`). Tell the owner and stop; do not poll.
   - Any other `Stopped at <step>: ...`: report it as it is and stop.
3. Wait for the owner's go. A go covers this one plan only. If the plan output changes before
   `apply` (different SHA, digests or migrations), show the new plan and ask again.
4. Apply with exactly the command line `plan` printed last:
   `.venv/bin/python -m scripts.staging_rollout apply --sha <full sha>`, plus
   `--migrations <versions>` only when `plan` printed it. `apply` refuses if HEAD moved since
   `plan`; then run `plan` again and ask again. Report the result: the final status lines, and
   any check output. If it stops, show its `Stopped at ...` line, the state line and the recovery
   lines verbatim, exactly as printed.
   A successful manual `apply` (the owner's `gh` login, no digests) then records the commit for
   production as a successful GitHub Deployment in the environment `staging` and prints
   `Recorded <sha7> as staged`; production deploys only recorded commits. If it prints `WARNING: staging is fine but the staged record was not
   written: ...`, staging is fine: report it and, after the owner's go, run the printed `Retry:`
   line, `.venv/bin/python -m scripts.staging_rollout record-staged --sha <full sha>`.
5. After a successful `apply`, offer the owner the assistant retest: the checklist
   `docs/ASSISTANT_RETEST.md`, and optionally first the model smoke run (about 40 to 50 model
   calls, synthetic facts only): `docker exec csa-staging-gateway-1 python -m scripts.assistant_smoke`.
   Run the smoke run only after the owner says yes, and report its `FAIL` lines and the final
   `N passed, M failed` line as printed. The checklist rows marked "manual" are for the owner.

## Production: hold before step 1

If the target contains the pictures PR 2b commits (the QR picture with payment answers and Telegram
reminders) and production does not yet run `c770ad77` (`c770ad770eb426ebc93ad923e55fd3f588afc05a`,
PR 2a) or a later commit, stop and tell the owner: production must first deploy `c770ad77` with
an explicit `sha` (`deploy/PRODUCTION.md`, "Pictures (PR 2b): before the first production deploy").

## Production: steps

Deploys one commit on `main` that staging has already run to production with the GitHub workflow
`deploy-production`. The workflow checks the commit and reads the image digests from the registry
(job `plan`, first step), plans on the instance (environment
`production-plan`, read-only, no reviewer), waits for one approval in the environment
`production` (the owner `amsyrzmi` or the other developer `DanSan0408`), then applies exactly what
the plan printed. Details, every refusal and the recovery steps: `deploy/DEPLOY.md`, "Production:
deploying from GitHub" (the host facts and break-glass stay in `deploy/PRODUCTION.md`).

1. Pick the commit. Leave `sha` empty when no commit is named: an empty `sha` means the tip of
   `main`, the commit just merged, and only that one. If the tip is not staged yet the run fails
   with `main's tip <sha7> is not staged yet; wait for deploy-staging`: it means staging has not been
   rolled out and recorded for the tip yet, so check the latest `deploy-staging` run
   (`gh run list --repo amsyrzmi/CSAV2 --workflow deploy-staging.yml --limit 5`, then
   `gh run view --repo amsyrzmi/CSAV2 <id> --log-failed` for a red one). Say what it shows (still
   running, `nothing to roll out`, a failed record job) and offer the staging flow above or a
   wait; never pick an older commit instead. A named commit must be
   the full 40-character SHA of a commit on `main` that staging has run (its `deploy-staging` run, or a manual
   staging rollout, printed `Recorded <sha7> as staged`); naming an older one is a deliberate rollback. A commit
   staging has not run goes through the staging flow above first.
2. Look for a run that already exists, then start one only if the owner has asked for it and none
   covers that commit (the tip, or the commit the owner named): with `PROD_AUTO_PLAN` on, `deploy-production-auto` may have started the plan
   already (see `## Auto-planned production runs`). Dispatch only after the owner's go: the owner's
   request to deploy production is the go to start the plan, and it is not the approval in step 4.
   Start the run, then find it:

   ```bash
   gh workflow run deploy-production --repo amsyrzmi/CSAV2 --ref main
   gh workflow run deploy-production --repo amsyrzmi/CSAV2 --ref main -f sha=<full sha>
   gh run list --repo amsyrzmi/CSAV2 --workflow deploy-production --limit 1 --json databaseId,status,url
   ```

3. Read the plan once the `plan` job has finished (check with
   `gh run view <run id> --repo amsyrzmi/CSAV2 --json jobs --jq '.jobs[] | [.name, .status, .conclusion] | @tsv'`,
   then `gh run view --repo amsyrzmi/CSAV2 --job <plan job id> --log`). Summarise it in a few
   lines: the target SHA, the image digests, whether the images changed, any `WARNING:` line (a
   downgrade warning means the target is older than what runs now), the pending migrations or
   "none", the downtime (a migration stops gateway, frontend, both workers, delivery and the
   training worker: console unavailable, bot replies delayed, not lost), `Host overrides:`, and any missing secret files.
   Do not poll in a loop; check again only when the owner asks or after a short wait.
   - The step `Resolve and check the commit` refuses before anything is sent to the instance,
     with one line: `sha must be the full 40 character lowercase commit SHA`,
     `commit <sha7> is not on main`, `the ci run for <sha7> has not succeeded on main`,
     `main's tip <sha7> is not staged yet; wait for deploy-staging` or
     `commit <sha7> has not been rolled out to staging` (the staged gate: say so and offer the
     staging rollout; there is no bypass), or `images for <sha7> are not published (csa-<app>)`.
     Report the line as printed.
   - Any other `Stopped at <step>: ...` or a failed `plan` job: report it as printed and stop.
4. Wait for the approval. The owner or `DanSan0408` approves the `production` deployment on the
   run page; that approval is the go, and it covers this run only. You never approve, never reject
   and never review the deployment yourself, by `gh` or any other way, and never ask someone else
   to approve on the owner's behalf.
5. Report the result from the `apply` job's log
   (`gh run view --repo amsyrzmi/CSAV2 --job <apply job id> --log`): `Backup taken: ...`, the
   final status lines, the `smoke ...: ok` lines and `Rollout of <sha7> finished.`; then the
   `Public smoke test` step. If the apply job fails with
   `main moved to <sha7> since this run's plan; start the workflow again`, nothing was sent: tell
   the owner and let them decide to start again. If it stops, show its `Stopped at ...`
   line, the state line and the recovery lines verbatim, exactly as printed. The run is red
   after a rollback too:
   - `Rolled back: the previous images are running; <sha7> is not.` and `Fix forward, then re-run
     plan and apply.`: the rollout put the previous images back after a failure at readiness
     or smoke (the `Stopped at smoke:` line above says why). Production is on the previous
     images; the fix is a new commit, not a re-run.
   - `Stopped at smoke:` without a rollback line: the new images are running and the rollback was
     not allowed (a migration, or unknown previous images). `Stopped at rollback:`: the
     rollback itself failed; production may be unhealthy. Report the lines verbatim and ask the
     owner; do not start another run yourself. If the lines include
     `To roll back: git checkout <sha> ...`, that is the owner's decision: relay it. A rollback
     is then a new run with `sha` set, only after the owner's go, and only to a commit that
     includes the digest change.

## Auto-planned production runs

The repository variable `PROD_AUTO_PLAN` (default off) lets the workflow `deploy-production-auto`
start the production plan by itself after `deploy-staging` has staged the tip of `main`. It only
starts the plan: the apply always waits for a human approval on the `production` environment, from
the owner or the other developer, and the human approval is never given by an agent. Details:
`deploy/DEPLOY.md`, "Auto-planned production runs (PROD_AUTO_PLAN)".

- When a production run is waiting (or before starting any run), read its state first: list the
  latest runs with `gh run list --repo amsyrzmi/CSAV2 --workflow deploy-production.yml --limit 5`,
  then the run and its jobs
  (`gh run view <run id> --repo amsyrzmi/CSAV2 --json status,event,headSha,jobs`, and
  `gh api repos/amsyrzmi/CSAV2/actions/runs/<run id> --jq .triggering_actor.login` for who started
  it). Say whether it was auto-started (`github-actions[bot]`) or a person's run, which commit it
  plans, and that its apply is waiting for the owner or the other developer.
- Never approve it and never reject it, and never ask someone else to approve for the owner. The
  owner approves or rejects on the run page ("Review deployments").
- Never cancel a production run yourself, with or without the owner's go: cancelling a waiting
  apply is a reject in effect. Read its state and give the owner the exact command,
  `gh run cancel <run id> --repo amsyrzmi/CSAV2`, for the owner to run. What the owner should
  check first: cancel only a run whose apply job is still `waiting` (read in the same turn);
  never one whose apply is approved, queued, `in_progress` or finished (an approved run may
  already have sent the deploy to the instance; tell the owner to check the host log and the
  public smoke test instead). The only automatic cancel is the workflow's own `supersede`, which
  cancels stale auto-started runs whose apply is still waiting (seven conditions, in
  `deploy/DEPLOY.md`); it never cancels a person's run.
- `PROD_AUTO_PLAN` is changed only on the owner's say-so:
  `gh variable set PROD_AUTO_PLAN --repo amsyrzmi/CSAV2 --body true|false`. The plain read is
  `gh variable get PROD_AUTO_PLAN --repo amsyrzmi/CSAV2` (it fails when unset, which means off).
- Report the workflow's lines verbatim and do not interpret them: `nothing to plan: ...` (green:
  the tip is not ready or not staged yet), `a deploy-production run for <sha7> already exists:
  <run id>`, `cancelled run <id> (planned <sha7>)`, `left alone: run <id> (<reason>)`,
  `started deploy-production run <id>: <url>`, and the red `the dispatch was accepted but no
  deploy-production run started`. After a red `deploy-production-auto` run, look for a
  `deploy-production` run before starting another, and start one only after the owner's go.
- A `deploy-production-auto` run that concluded `skipped` means the switch is off or
  `deploy-staging` did not succeed; that is normal.

## If something goes wrong

- Staging: `apply` now ends with a read-only smoke test (`smoke <name>: ok` lines,
  `Stopped at smoke: ...` on failure). After an image-only failure at readiness or smoke it may
  roll back by itself: `Rolling back to ...` then `Rolled back: the previous images are running;
  <sha7> is not.` and `Fix forward, then re-run plan and apply.` The run still ends red (exit 1)
  and writes no staged record; report the lines verbatim. `Stopped at rollback:` means the
  rollback itself failed: show the lines and ask the owner.
- Staging: if an apply is slow, hangs (for example on a docker pull) or is rolled back by the
  smoke check, report the lines verbatim and ask the owner. Do not kill any process and do not
  re-run a failed job on your own: a re-run is a rollout action and needs the owner's go. It
  may be host load (unproven).
- Staging: if `apply` fails, show the script's own recovery lines verbatim: the step, the state
  staging is in, then whichever applies (`Nothing on staging was changed.`, the `docker ... start`
  restart command, `Migrations were applied: ...` with fix forward, or `Rollback images:` with the
  previous SHA), and `Re-run plan, then apply, once the cause is fixed.` Do not retry a migrate,
  restart or roll back on your own; ask the owner.
- Production: show the rollout's own recovery lines verbatim and point to the matching part of
  `deploy/DEPLOY.md` ("What a failure means", "What a smoke stop or a rollback means", "What each
  refusal means") and `deploy/PRODUCTION.md`. Do not start the workflow again, retry, restart or
  roll back on your own; ask the owner.
- Runner exit 124: the command may still be running on the instance. Tell the owner to check the
  host as the runbook says, and do not start another run. Instance exit 75: another deploy holds
  the lock.
- A rollback to an older staged commit is a new run with `sha` set to it, after the owner's go,
  but only if that commit includes the digest change: the entry script runs the target commit's
  own rollout CLI with digests, and an older one fails at argument parsing (exit 2 with
  `unrecognized arguments`). A revision older than the digest change (what production runs
  before its first deploy after it) can only be restored with break-glass, which is the owner's
  procedure (`deploy/PRODUCTION.md`, "Rolling back to a revision older than the digest change");
  tell the owner, do not run it.
- A run waiting for approval holds the workflow's concurrency group: a new dispatch queues
  behind it and a third cancels the second. Before starting a new run, say if one is still
  waiting; the owner rejects a stale one, and any run still waiting from before the digest
  change merged. You never reject it yourself.
- The run needs the secret `PROD_CONSOLE_ORIGIN` on `production`; if the apply job's
  `Require the deploy variables` step names it, tell the owner (`deploy/PRODUCTION.md`, "After the
  digest change merges"). Do not set it yourself.
- The GitHub and AWS setup of a target (the environments, reviewers, secrets and variables, the
  roles and documents) is the owner's, done once with `deploy/github/setup-environments.sh` and
  `deploy/aws/setup-target.sh` (`deploy/DEPLOY.md`, "One-time setup"). Do not run either script,
  and do not set a secret value; when the owner needs one of them, give numbered, copy-ready
  commands from that section.
- If a command is blocked by the permission system or a hook, give the owner
  numbered, copy-ready commands to run instead, and wait for the output.

## Resetting staging

The reset tool (`deploy/STAGING_PIPELINE.md`, "Resetting staging") is the owner's, at a terminal;
an agent may run only `plan`. Before the owner runs it, remind them to set `STAGING_AUTO_PAUSED`
to `true` first (and back to `false` after `accounts`). The `staging_reset` `wipe` and `accounts`
take the same rollout lock as a rollout: when one holds it they change nothing and stop with
`Stopped at lock: ...`; report the line as printed and wait for that rollout to end.

## Never

- Read, print or copy anything under `/srv/csa-staging/secrets`. Compose reads those files; the
  script only checks that they exist.
- Run the compose, docker or migrate commands by hand when the script can do it, or start
  `apply` before the owner's go.
- Export the staging switches yourself. They live in `deploy/staging-rollout.env`; changing one is
  a pull request.
- Approve or reject a production deployment, or ask anyone to approve for the owner. Never, with
  or without the owner's go.
- Cancel a production run yourself, with or without the owner's go: give the owner the command
  to run, and say that the owner should cancel only a run whose apply job is still `waiting`
  (read in the same turn); never one whose apply is approved, queued, `in_progress` or finished.
- Run `gh run rerun` on a production run: a rerun re-uses the old plan. A new run, after the
  owner's go, is the way.
- Change the environments, their reviewers or branch rules, the secrets, or the AWS roles and
  documents. Never.
- Change `PROD_AUTO_PLAN` on your own: only on the owner's say-so.
- Bypass the staged gate or suggest a way around it. The break-glass procedure in
  `deploy/PRODUCTION.md` is the owner's own decision, outside the staged-first rule; do not run
  it or offer it as a fix for a refusal.
- Run compose, docker, migrate or the rollout script on the instance by hand.
- Read, print or copy secrets, `/srv/csa-prod/host.env` values, role ARNs, the instance id or the
  bucket name. The repository is public.
- Change a production switch except by a pull request to `deploy/production-rollout.env` (the
  optional Jev switches, none set today, are added the same way).
