# Mito rename, organisation move and CI shards: design

Status: REVIVED by the owner on 2026-11-14 (payments P1, the training video and credit monitoring are merged; only #125 was open). Project 1 (shards) is built first, projects 2 to 4 each get a design round that re-measures the inventory (the CD pipeline, production services and training worker added names, digests and deploy trust). It was first CANCELLED on 2026-11-13, before any of it was built. The owner chose to start payments
(P1, P2) and then the training library instead, because this work would take about 3 to 5 working days before
payments could start. The document is kept as reference: the facts in section 3 (public images, the AWS deploy
trust naming the repository, CI cost, the shard imbalance) and the approaches stay true if the work is revived.
Nothing below is scheduled. Revive it by asking for the Mito rename, the organisation move or the shard
rebalance; an unfinished plan for the shards is on the local branch `chore/ci-shards`, not merged.

Original status: brainstormed with the owner on 2026-11-13; project 1 (shards) was designed here in full,
projects 2 to 4 were scoped and each was to get its own design round before it is built.

## 1. Intent

The product is called Mito (production runs at `mito.fourstacks.my`), but the repository is still the public
`amsyrzmi/CSAV2`, and "csa" is in the package, the environment variables, the images, the containers and the
database roles. The owner wants, before payments are built:

- the repository moved into the `FourstacksPLT` organisation (Team plan), made private, and named `mito`;
- every "csa" name changed to "mito";
- CI split into better balanced, more parallel shards, because a private repository bills Actions minutes.

Why before payments: every pull request adds more "csa" names and any open branch has to be redone around a
rename, so the cheapest moment is now, with no other feature pull request open.

## 2. Goals, non-goals, anti-goals

Goals: one name (Mito) across repo, code, runtime and database; a private repository whose images are also
private; CI that finishes in about half the time at about the same cost; production touched as little as
possible and only after a rehearsal on staging.

Non-goals: changing the domain, the product's behaviour or any customer-facing text; renaming applied
migration files (their history stays as written).

Anti-goals (failure even if it "works"): the code goes private but the images stay public; production loses
its deploy login or its image pull; a rename that leaves staging and production on different names; a
big-bang change that cannot be backed out in pieces.

## 3. Facts found (2026-11-13, from the repo and GitHub)

- "csa" appears in 780 files and about 6,500 places: the `csa/` package, `CSA_*` variables, container and
  compose names, the images `csa-python`, `csa-frontend`, `csa-pg16-postgis-vector`, the database roles
  `csa_app`, `csa_ro`, `csa_identity`, the staging paths and host names, and docs.
- Images: `csa-python` and `csa-frontend` are PUBLIC packages under the owner's user; the combined database
  image is private. Both compose files and CI pin images by digest (28 references in `.github` and `deploy`).
- The AWS deploy role trusts the repository by its OIDC subject (`repo:amsyrzmi/CSAV2`, see
  `deploy/production/iam/apply-trust-policy.json` and `deploy/PRODUCTION.md`); a transfer or rename changes
  that subject.
- A full CI run is about 148 job minutes (shards 23, 33, 33 and 54 minutes, gate 3.5, frontend 2). The split
  weighs a file by its number of tests (a slow test counts 20), so shard 3 is the long pole at 54 minutes.
  CI records no per-file durations.
- The Team plan includes 3,000 Actions minutes a month; overage is billed per minute.

## 4. Approaches considered

- **A. Three steps with a rehearsal (chosen by the owner).** Repo, org, private and images first with no code
  change; then the code rename; then the database roles in a maintenance window after a full rehearsal on
  staging. Everything ends as Mito and production is touched once, in the last step.
- B. Same, but the database roles and the applied migration history keep the `csa_*` names. Least risky, but
  leaves a visible leftover and breaks "every csa name". Fallback if the role rename looks too risky in the
  rehearsal.
- C. One big-bang pull request and rollout. Fastest on paper, but one failure puts repo, images, CI trust and
  production in doubt together.

## 5. The four projects, in order

1. **CI shard rebalance** (small; designed in section 6). First, so CI is cheaper before the repo goes private.
2. **Organisation move, private repository, repository rename, private images.** Transfer `amsyrzmi/CSAV2` to
   `FourstacksPLT`, rename it `mito`, make it private; move or republish the images privately under the org and
   give the staging server and production a read-only pull login; update the AWS trust subject, the pinned
   digests, environments, secrets, remotes and docs. No application code changes. To be designed: exact
   transfer sequence, how the images move (package transfer or republish; digests must not change), the
   rollback of each step, who holds org owner rights.
3. **Code and runtime rename.** The `csa/` package, `CSA_*` variables, containers, images, compose projects,
   paths and docs. To be designed: one pull request or several, an alias period for the variables, the staging
   rollout, how the old image names are retired.
4. **Database roles and remaining production names, in a maintenance window** after a full rehearsal on
   staging: renaming `csa_app`, `csa_ro`, `csa_identity` (and the baseline and bootstrap that create them),
   plus server-side names. To be designed: the rename migration and its rollback, secret rotation, the window.

Each of 2 to 4 is brainstormed and planned when it starts. Payments P1 and P2 follow all four.

## 6. Project 1: CI shard rebalance (the design)

Today: pull requests run only the changed test files (shard 0); a push to main runs four shards, each file
whole (module-scoped Postgres fixtures run once per file) and assigned by a weight of test count.

Change:

1. **Record durations.** Every full run writes per-file seconds (from pytest's own timings or a junit file) and
   uploads them as an artifact.
2. **Balance by seconds.** Commit `tests/shard_durations.json` (file path to seconds). `tests/shard.py` weighs
   a file by its recorded seconds, longest first onto the lightest shard (the existing method); a file with no
   entry falls back to the old test-count weight scaled to seconds, so a new file still lands somewhere sane.
   A small script refreshes the file from the artifact of the latest successful main run; refreshing is a
   manual, reviewed change (a pull request), never an automatic commit.
3. **More shards.** Go from 4 to 6 by changing the matrix and `SHARD_TOTAL`; shard 0 for pull requests stays.
   Choose 6 unless the durations show a single file longer than the ideal share (a file is never split), in
   which case that file is reported and the shard count follows the data. Total work stays about the same;
   each shard adds about 4 minutes of setup.
4. **Expectation:** the slowest shard drops from about 54 to about 28 minutes; total minutes rise by about
   8. The post-merge gate stays the same.

Constraints: the split must stay deterministic so every job agrees on who runs what; `make test` and a local
run without `CSA_TEST_SHARD` keep running everything; no test is skipped or duplicated (a test of
`shard_modules` over the real file list proves every file lands on exactly one shard).

Non-goals for project 1: making individual tests faster, changing the Postgres image handling (fixed in
#112), changing what pull requests run.

## 7. Decisions made by the owner

- Organisation `FourstacksPLT` (Team plan; confirmed by the owner 2026-11-14, who owns it), repository `mito`, private.
- Approach A, in the order above, before payments.
- Training library with video (after payments P1 and P2), HR refinement after the Overview assistant: see the
  programme document.

## 8. Calls made by the assistant (the owner can overrule)

- Shards: 6 as a starting point, measured before it is final; durations kept in a committed file, refreshed
  by hand.
- Private images are required, not optional: a private repository with public images still exposes the code.
- The rename keeps applied migration files untouched; role renames happen in a new migration.
