---
name: production-deploy
description: Use when the owner asks to deploy, roll out or update production (csa-prod) to a commit staging has already run, through the deploy-production GitHub workflow (plan, approval, apply).
---

# Production deploy

Deploys one commit on `main` that staging has already run to production with the GitHub workflow
`deploy-production`. The workflow checks the commit and reads the image digests from the registry
(job `plan`, first step), plans on the instance (environment
`production-plan`, read-only, no reviewer), waits for one approval in the environment
`production` (the owner `amsyrzmi` or the other developer `DanSan0408`), then applies exactly what
the plan printed. Details, every refusal and the recovery steps: `deploy/PRODUCTION.md`,
"Deploying from GitHub".

## Where to run

Only `gh` commands from the main checkout `/home/amsyrzmi/CSAV2`, against the repository
`amsyrzmi/CSAV2`. Nothing runs on the instance from here: Systems Manager runs the rollout there.

## Steps

1. Pick the commit. Leave `sha` empty when no commit is named: an empty `sha` means the tip of
   `main`, the commit just merged, and only that one. If the tip is not staged yet the run fails
   with `main's tip <sha7> is not staged yet; wait for deploy-staging`; say so, and offer the
   `staging-rollout` skill or a wait; never pick an older commit instead. A named commit must be
   the full 40-character SHA of a commit on `main` that staging has run (its staging rollout
   printed `Recorded <sha7> as staged`); naming an older one is a deliberate rollback. A commit
   staging has not run goes through the `staging-rollout` skill first.
2. Start the run, then find it:

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
   "none", the downtime (a migration stops gateway, frontend, both workers and delivery: console
   unavailable, bot replies delayed, not lost), `Host overrides:`, and any missing secret files.
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
   run page; that approval is the go, and it covers this run only. You never approve, reject or
   review the deployment yourself, by `gh` or any other way, and never ask someone else to approve
   on the owner's behalf.
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

## If something goes wrong

- Show the rollout's own recovery lines verbatim and point to the matching part of
  `deploy/PRODUCTION.md`. Do not start the workflow again, retry, restart or roll back on your
  own; ask the owner.
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
- If a command is blocked by the permission system or a hook, give the owner
  numbered, copy-ready commands to run instead, and wait for the output.

## Never

- Approve the `production` deployment, or change the environments, their reviewers or branch
  rules, the secrets or the AWS roles and documents.
- Bypass the staged gate or suggest a way around it. The break-glass procedure in
  `deploy/PRODUCTION.md` is the owner's own decision, outside the staged-first rule; do not run
  it or offer it as a fix for a refusal.
- Run compose, docker, migrate or the rollout script on the instance by hand.
- Read, print or copy secrets, `/srv/csa-prod/host.env` values, role ARNs, the instance id or the
  bucket name. The repository is public.
- Change a production switch except by a pull request to `deploy/production-rollout.env` (the
  optional Jev switches, none set today, are added the same way).
