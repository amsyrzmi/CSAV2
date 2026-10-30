---
name: staging-rollout
description: Use when the owner asks to roll out, deploy or update staging (csa-staging) to the latest merged main, including images with or without database migrations.
---

# Staging rollout

Rolls the published images of the latest `main` commit out to `csa-staging` with
`scripts/staging_rollout.py`. The owner approves the image publish on GitHub and gives the go here;
nothing reaches staging without both. Details: `deploy/STAGING_PIPELINE.md`, "Routine rollouts".

## Where to run

Everything runs in `/home/amsyrzmi/CSAV2`, the main checkout, never in a worktree. The script
refuses when HEAD is not on `origin/main` or the worktree has tracked changes.

## Steps

1. Pull: `git pull --ff-only origin main`. If it is not a fast-forward, stop and tell the owner.
2. Plan (read-only): `.venv/bin/python -m scripts.staging_rollout plan`. Summarise it in a few
   lines: the target SHA, whether the images changed, the pending migrations or "none", the
   downtime (a migration stops gateway, frontend, worker and delivery: console unavailable, bot
   replies delayed, not lost), the check files, and any missing secret files (exit 2).
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

## If something goes wrong

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
