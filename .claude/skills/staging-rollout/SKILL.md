---
name: staging-rollout
description: Use when the owner asks to roll out, deploy or update staging (csa-staging) to the latest merged main, including images with or without database migrations.
---

# Staging rollout

Rollouts of `csa-staging` are automatic: every green merge to `main` is rolled out by the
`deploy-staging` workflow (plan, apply, then a record job that writes the staged record). This
skill checks that workflow, dispatches it when the owner says so, and keeps the manual
`scripts/staging_rollout.py` steps for when the owner asks for them. Details:
`deploy/STAGING_PIPELINE.md`, "Routine rollouts".

## Automatic rollouts (the default)

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
   downgrade is refused.
4. Pausing is the repository variable `STAGING_AUTO_PAUSED` (`gh variable set STAGING_AUTO_PAUSED
   --body true|false`); change it only on the owner's say-so.
5. Use the manual steps below only when the owner asks (or when the workflow cannot do it, such as
   a downgrade), and pause first, unpause after.

## Manual rollout (break-glass)

Pause first (`STAGING_AUTO_PAUSED` `true`), unpause after.

Everything runs in `/home/amsyrzmi/CSAV2`, the main checkout, never in a worktree. The script
refuses when HEAD is not on `origin/main` or the worktree has tracked changes. The owner approves
the image publish on GitHub and gives the go here.

### Steps

1. Pull: `git pull --ff-only origin main`. If it is not a fast-forward, stop and tell the owner.
2. Plan (read-only): `.venv/bin/python -m scripts.staging_rollout plan`. Summarise it in a few
   lines: the target SHA, whether the images changed, the pending migrations or "none", the
   downtime (a migration stops gateway, frontend, worker, delivery and the training worker: console
   unavailable, bot replies delayed, not lost), the check files, and any missing secret files (exit 2).
   - `images for <sha7> are not published yet (<state>)`: the publish run starts by itself after a
     green main CI run; the owner approves the `staging-images` environment on it. Tell the owner
     and stop; do not poll.
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

## If something goes wrong

- `apply` now ends with a read-only smoke test (`smoke <name>: ok` lines, `Stopped at smoke: ...`
  on failure). After an image-only failure at readiness or smoke it may roll back by itself:
  `Rolling back to ...` then `Rolled back: the previous images are running; <sha7> is not.` and
  `Fix forward, then re-run plan and apply.` The run still ends red (exit 1) and writes no staged
  record; report the lines verbatim. `Stopped at rollback:` means the rollback itself failed:
  show the lines and ask the owner.
- If `apply` fails, show the script's own recovery lines verbatim: the step, the state staging
  is in, then whichever applies (`Nothing on staging was changed.`, the `docker ... start` restart
  command, `Migrations were applied: ...` with fix forward, or `Rollback images:` with the
  previous SHA), and `Re-run plan, then apply, once the cause is fixed.` Do not retry a migrate,
  restart or roll back on your own; ask the owner.
- If a command is blocked by the permission system or a hook, give the owner numbered, copy-ready
  commands to run instead, and wait for the output.

## Never

- Read, print or copy anything under `/srv/csa-staging/secrets`. Compose reads those files; the
  script only checks that they exist.
- Run the compose, docker or migrate commands by hand when the script can do it, or start
  `apply` before the owner's go.
- Export the staging switches yourself. They live in `deploy/staging-rollout.env`; changing one is
  a pull request.
