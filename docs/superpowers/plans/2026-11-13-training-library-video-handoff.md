# Handoff: build video in the training library (separate session, parallel with Payments 1)

You are a fresh Claude Code session. The owner (Amsyar) asked for the training library with video to be
built in its own session while another session builds Payments 1. Everything below is what you need;
do not ask the owner for anything this file already answers.

## What you build

Video upload (500 MB, 8 MB pieces through the gateway), background transcription and a whole-video
summary, playback with a clickable transcript, and a `training_ask` action in the staff assistant.
The design is APPROVED by the owner (every item under "Calls made by the designer" stands; do not
reopen them): `docs/superpowers/specs/2026-11-13-training-library-video-design.md`.
The plan (15 tasks) is written and was self-reviewed by its author; nobody else has read it yet, so
your pre-flight scan (the SDD skill's first step) is its first independent check:
`docs/superpowers/plans/2026-11-13-training-library-video.md`.

## Where things are

- Repo `/home/amsyrzmi/CSAV2` (GitHub `amsyrzmi/CSAV2`, PUBLIC: synthetic data only, never real media,
  addresses or secrets). The main checkout stays detached on `origin/main` and is used only for
  staging rollouts. NEVER build in it.
- The plan and design live on the local and pushed branch `docs/plan-training-video` (it contains the
  design commit 78ef355 and the plan commit, on top of origin/main 76a4c71). The design is also in
  PR #114.
- Create your build worktree from that branch:
  `cd /home/amsyrzmi/CSAV2 && git worktree add .claude/worktrees/feat-training-video -b feat/training-video docs/plan-training-video`
  and work only inside `/home/amsyrzmi/CSAV2/.claude/worktrees/feat-training-video`.
- Read first: `AGENTS.md` (the "Migration and contract rules" section is binding), the plan's Global
  Constraints and Review Focus, the design sections the tasks cite, and the owner-facing memory index
  at `/home/amsyrzmi/.claude/projects/-home-amsyrzmi-CSAV2/memory/MEMORY.md` (read the files it points
  to for staging, production deploy, host memory pressure, worktree guard, blocked-actions).

## How to run it (the owner chose subagent-driven development)

Invoke the skill `superpowers:subagent-driven-development` and follow it exactly: a ledger at the
workspace printed by `bash <skill>/scripts/sdd-workspace docs/superpowers/plans/2026-11-13-training-library-video.md`
(git-ignored, your recovery map after a context reset), the pre-flight scan, a fresh implementer per
task, a scoped task review after each (spec + quality) and fix rounds, one whole-branch review on the
most capable model at the end, then ONE fix wave and one scoped re-review. Skill scripts live under
`/home/amsyrzmi/.claude/plugins/cache/superpowers-marketplace/superpowers/7.0.1/skills/subagent-driven-development`.
Always set the model explicitly when dispatching (sonnet for mechanical tasks, opus for the multipart
store, the job lifecycle, ffmpeg and `training_ask`, and for the final review the most capable model
you have). Rulings, not stalls: decide, record `Ruling: ...` with the cost if wrong in the ledger, keep
going. Only four things stop you: an irreversible or destructive operation, a security-sensitive
action, a side effect outside your worktree that needs the owner (a merge, a push to a shared branch,
a publish, a staging rollout, a production change), or a plan so broken that every path is a guess.

**Task 1 is a stop gate.** It measures whether a 320 MB training worker running ffmpeg is workable on
the 2 GB production host. If the measurement says it is not, STOP and bring the owner the finding and
the three options (separate worker with a different limit, sharing the existing worker, a larger host).

## Rules learned the hard way (this repo, this host)

- Tests: ONE file at a time, always through the wrapper
  `bash /tmp/claude-1000/-home-amsyrzmi-CSAV2/7d09c8ff-ee80-4525-b21a-195503f98d9c/scratchpad/ci.sh /home/amsyrzmi/CSAV2/.venv/bin/python -m pytest <file> -q`
  (a worktree session refuses `ENV=` prefixes and compound commands; if that scratchpad is gone,
  recreate an equivalent wrapper). The host is shared and 14 GB: browser files need more than 3000 MB
  free (`free -m`), otherwise leave them to CI. `make lint` as one command, in the foreground with
  timeout 600000; it can take 10+ minutes under load.
- Run every long command in the FOREGROUND and wait. Never start something in the background and then
  end your turn; never write `while pgrep -f ...` loops that match themselves (they spin forever).
  Subagents sometimes stop while their own test run is still going: before accepting a hand-back,
  check `git log` and `git status` for the commit and for processes still running.
- A PostToolUse formatter hook mass-reformats files after the Edit tool (line length 120; the project
  uses 100) and has dropped imports before. Use Write for new files and small python replace scripts
  for edits, then check `git diff --stat` and run `.venv/bin/ruff format` on touched Python.
- CI note: tests build the Postgres image from `deploy/db.Dockerfile` unless `CSA_DB_IMAGE` is set
  (CI pins a working image by digest; PR #112 made the fixtures honour it). A fresh local uncached
  build pulls PostGIS 3.7.0 which fails on Postgres 16.15: do not "fix" that here.
- `gh pr edit` fails on a GraphQL deprecation; edit a PR with
  `gh api -X PATCH repos/amsyrzmi/CSAV2/pulls/<n> --input -` (JSON with title/body).
- Node is at `/tmp/fourstacks-toolbin/node` (not on PATH); the web folder needs a `node_modules`
  symlink to the main checkout's; run node test files one at a time from `CSA_WebConsole/csa-console/web`.
- Never read, print or copy anything under `/srv/csa-staging/secrets`; never put an App Key/Secret in
  chat or repo.
- Commit trailers on every commit: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and
  `Claude-Session: <your session url if you have one>`; PR bodies end with
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. A hook blocks rebase and force-push:
  use merge commits.

## The parallel session: Payments 1 (do not collide)

It builds in `/home/amsyrzmi/CSAV2/.claude/worktrees/feat-payments-1` (branch `feat/payments-1`,
migrations 0590-0591; 0592-0599 are reserved for Payments 2). You own migrations 0600-0609 only. The rules,
which the plan also states and Task 15 carries out:
1. Each session stays in its own migration range; neither references the other's tables nor rebuilds a
   shared CHECK list.
2. `EXPECTED_SCHEMA_VERSION` (`apps/gateway/main.py:71`, currently "0581") is always the highest version
   on main. Whoever merges second merges `origin/main` in (a merge commit, never a rebase) and keeps the
   higher value.
3. `docs/console_openapi.json` is regenerated after merging main in
   (`python -m scripts.export_console_openapi`), never merged by hand.
4. Tests that pin a migration number compare with `EXPECTED_SCHEMA_VERSION` instead (the plan's Task 2
   fixes the one at `tests/test_contact_linking_schema.py:374`); `_company(dsn)` in
   `tests/test_training_schema.py` stays unchanged (three other schema tests import it).
5. Shared files both sessions touch: `docs/CONSOLE_API.md`, `docs/console_openapi.json`,
   `docs/PROGRESS.md`, `db/migrations/README.md`, `docs/RUNBOOK.md`, `apps/gateway/main.py`. Keep your
   edits to these small and append-style; resolve conflicts by keeping both sides.
6. Staging is shared: a rollout runs from the MAIN checkout (`git checkout --detach origin/main`, then
   `.venv/bin/python -m scripts.staging_rollout --target staging plan`, then `apply --sha <sha>` only after
   the owner says go) and migrations stop all four services. Never run a rollout while the other session
   might be rolling out; ask the owner. The owner approves the image publish on GitHub and gives the
   go in chat. Do not touch production.

## Done means

All 15 tasks complete and reviewed, whole-branch review clean after one fix wave, `make lint` and every
test file named in the tasks green (full golden / smoke where the plan says), merge-main-in done, a
pull request open against `main` with the owner's decisions and the "owner actions before production"
list (training-worker S3 key and multipart permissions, S3 lifecycle rule, `VOICE_STT_ENDPOINT`, provider
prices to confirm, the real-bucket browser playback retest). Do NOT merge, do NOT roll out, do NOT
change production. Your final message lists every `Ruling:` from the ledger with what it costs if wrong,
the PR link, CI state, and what the owner must do next.
