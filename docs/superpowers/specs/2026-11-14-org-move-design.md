# Project 2: move the repository to the `FourstacksPLT` organisation (design)

Status: APPROVED by the owner (Amsyar) in chat on 2026-11-14: the two stages of section 4 (transfer and rename first,
"go private" as a separate gated stage), with the instruction to lower the private-repository cost as much as
possible, including deleting very old images where needed (section 6.1). Nothing in this document has been done yet.
Parent: `2026-11-13-mito-rename-and-org-move-design.md` (the four projects; this is project 2). Projects 3 (code and
runtime rename) and 4 (database roles) get their own designs and do not depend on the repository being private.

## 1. Intent

The repository is `amsyrzmi/CSAV2`, public, under the owner's personal account. The owner owns the organisation
`FourstacksPLT` (Team plan; members `amsyrzmi` and the other developer) and wants the code and images to live there
as `FourstacksPLT/mito`, and eventually private. The move must not interrupt production (the live interim instance)
or staging, and must not leave any deploy path depending on the old account.

## 2. Goals, non-goals, anti-goals

Goals
- Repository at `FourstacksPLT/mito`, CI, image publishing, staging rollout and production deploy all working
  from the new place, each proven by a real run.
- Images published to and pulled from the organisation's registry namespace.
- Every step reversible until the last one; each step verified before the next.

Non-goals
- Renaming code, services, database names or paths (project 3), database roles (project 4).
- Completing production (WhatsApp go-live, uptime alerts, off-box secrets copy, EC2/RDS move).
- Replacing the personal-account classic token used for break-glass with a new auth scheme beyond what private
  needs (section 6).

Anti-goals
- A move that breaks the anonymous paths before their credentialed replacements are proven.
- A "private" repository that silently loses the production approval gate or runs up an unplanned CI bill.

## 3. Facts that shape the design

(Full table with file and line references: scratchpad research, 2026-11-14; the load-bearing ones follow.)

1. Every image pull in staging and production is anonymous today (the rollout scripts give docker an empty
   config; `deploy/production/resolve_commit.sh` asks for an anonymous registry token; the instance runs an
   anonymous HTTPS `git fetch`). Those all break when the images or the repository become private.
2. The registry namespace must be lowercase. `publish-staging-images.yml` builds its namespace from
   `github.repository_owner` and `resolve_commit.sh` from the repository owner, so after the transfer both become
   `FourstacksPLT` and publishing fails.
3. Container packages belong to the account, not the repository: they stay at `ghcr.io/amsyrzmi/*` after a
   transfer and the registry has no redirect. Images must be copied to the organisation with their digests kept
   (the production pins and the staging rollout depend on digests).
4. The GitHub API path for packages changes kind (`/users/amsyrzmi/packages` becomes `/orgs/FourstacksPLT/packages`),
   not only name (`scripts/rollout_targets.py`).
5. The AWS deploy roles trust the repository's OIDC subject, which contains owner login, owner id, repository name
   and repository id. Transfer and rename change all but the repository id, so the trust policies must accept the
   new subject before the transfer.
6. Private repository costs and limits (all UNVERIFIED against current GitHub pricing; the owner checks Billing):
   about 23,000 Actions minutes a month against 3,000 included on Team (roughly $120 to $160 a month overage at the
   Linux rate), 10 GB of artifacts against 2 GB included, and, per GitHub's documentation, required reviewers on
   environments are not available for private repositories below Enterprise, which would remove the approval
   between production `plan` and `apply`.

## 4. Approaches

- **A. One move: transfer, rename, images and repository private together.** Fewest steps; but it blocks on the
  approval-gate and billing answers, puts credentials into three pull paths at the same moment as an account move,
  and makes any failure hard to attribute. Rejected.
- **B. Two stages (chosen by the owner).** Stage A transfers and renames while the repository and the two app
  images stay public, so nothing needs a credential yet; Stage B makes images and then the repository private
  once the cost, approval-gate and credential questions are answered. Each stage is reversible; projects 3 and 4
  can start after Stage A.
- **C. Stay on the personal account, make only the images private.** Does not meet the owner's goal (organisation
  ownership) and still needs credentials on every pull. Rejected.

## 5. Stage A: transfer and rename, repository and app images stay public

### 5.1 Prep pull requests (merged before the transfer; no behaviour change while the values stay as they are)

- **P1, one namespace setting.** A repository variable `IMAGE_NAMESPACE` (lowercase registry prefix, set to
  `ghcr.io/amsyrzmi` at first). `ci.yml` (service image and `CSA_DB_IMAGE`), `publish-staging-images.yml` and the
  production plan step use it instead of deriving the owner; `resolve_commit.sh` receives the namespace instead of
  taking the owner from `REPOSITORY`, and lowercases whatever it is given. Flipping the namespace later is a
  settings change, not a code change. A variable (not an expression) because GitHub expressions have no
  lowercase function.
- **P2, authenticated registry token with anonymous fallback.** `resolve_commit.sh` requests the registry token
  with the workflow's `GITHUB_TOKEN` when present and falls back to anonymous; the `plan` job gets `packages: read`.
  `tests/test_production_resolve.py` covers both paths.
- **P3, optional registry login for rollouts.** `staging_rollout.py` and `staging_reset.py` copy an optional
  read-only docker auth file into the temporary docker config they already create (file absent: exactly today's
  anonymous behaviour). The owner, image-owner and repository values move into single named constants so the
  flip in 5.3 is one place. Its first test is a real staging rollout (the other developer's rule for changing
  `staging_rollout.py`).
- **P4, gitleaks licence variable.** `ci.yml` passes `GITLEAKS_LICENSE: ${{ secrets.GITLEAKS_LICENSE }}` to the
  gitleaks step. Harmless while unset; organisation-owned repositories may need the free key.

### 5.2 Order (who does what, how it is verified, how it is undone)

1. **Owner, AWS CloudShell.** Save both current trust documents. Make the `sub` condition of both roles a list
   (old value plus the immutable organisation forms for the old and new repository name, each with its own
   `:environment:production-plan` / `:environment:production` suffix). Verify with
   `aws iam get-role --query Role.AssumeRolePolicyDocument`. Undo: reapply the saved files.
2. **Owner, GitHub.** Obtain a gitleaks licence; I build and the owner merges P1 to P4 (CI green on the old
   repository); roll staging once with P3 to prove nothing changed. Undo: revert the pull requests.
3. **Owner and other developer.** Agree a window, no rollout running anywhere, announce a merge freeze on `main`
   (no new main commit means nothing new to publish, stage or deploy during the move).
4. **Owner, GitHub.** Transfer `amsyrzmi/CSAV2` to `FourstacksPLT`, then rename to `mito`; keep it public. Verify:
   repository id unchanged, environments, reviewers, environment secrets, the `PROD_DEPLOY_REGION` variable,
   `IMAGE_NAMESPACE`, the staged Deployment records and the OIDC subject prefix all present. Undo: transfer back.
5. **Owner.** Add `GITLEAKS_LICENSE` as a repository secret on `mito`; install any GitHub apps still needed on
   the organisation. Verify: a `workflow_dispatch` run of CI on `main` passes the gate job (the database image
   pull from the still user-owned private package is the open question; steps 6 and 7 fix it if it fails).
6. **Owner, own machine.** Copy the images to `ghcr.io/fourstacksplt/` with
   `skopeo copy --all --preserve-digests` (the database image's pinned digest is an index, so `--all`); at least
   the digests staging and production run now, the rollback candidates and the recent SHA-tagged versions.
   Verify each digest with `skopeo inspect --raw` piped to `sha256sum`. Undo: delete the organisation copies.
7. **Owner, organisation package settings.** Connect each package to `mito`; Actions access: write for the two
   app images, read for the database image; visibility: app images public, database image private. Verify with
   `gh api /orgs/FourstacksPLT/packages?package_type=container`.
8. **Owner and me.** Set `IMAGE_NAMESPACE` to `ghcr.io/fourstacksplt`; merge the post-transfer pull request (the
   constants of 5.1 P3 flipped to the organisation and `mito`, database image pins in CI and compose files, tests,
   the production-deploy skill, PRODUCTION.md and the other operator docs). On the staging host: point the
   checkout's remote at the new repository, then roll staging once and confirm the staged record lands in `mito`.
   Verify: main CI green, the publish run pushes to the organisation namespace with matching digests. Undo:
   variable back, revert; the old user packages are untouched.
9. **Owner, AWS and GitHub.** Run production `plan` (proves the plan role and the resolve path); the apply role
   is proven by the next real approved `apply`. Then trim each trust policy to the one real subject. Do not
   leave a classic `repo:amsyrzmi/CSAV2` subject behind: whoever controls that name could recreate it.

Stage A ends after step 9. Old user packages stay until Stage B (nothing references them after step 8, but they are
the rollback target).

### 5.3 What Stage A does not touch

The instance's `git fetch` stays anonymous HTTPS against a public repository (the old URL keeps redirecting while the
old name is unused; the instance remote is updated in Stage B). The home staging server is changed only by the
remote update and the registry file in step 8, with the other developer's agreement.

## 6. Stage B: private images, then private repository (separate, gated)

Gate: the owner records three answers before any Stage B step:
1. Billing: current price per minute, included minutes, artifact and package storage on the organisation's plan.
2. Approval gate: on a private repository on Team, can environment `production` still require reviewers? If not,
   decide the replacement (for example two workflow dispatches so apply is a separate approved run, or accept).
3. Pull credential: who owns the read-only registry credential and how it is rotated (a classic token with
   `read:packages` belongs to a person and expires; the instance's existing token already does).

Also before Stage B: cut CI cost if the billing answer needs it (skip or shorten the review-image artifact on
pull requests; the shard rebalance of project 1 shortens wall time, not minutes).

Steps (each reversible, verified by a real run): instance deploy key or token and remote switch to `mito`; the
registry credential placed where P3 reads it; break-glass token reissued with the scopes private needs
(`read:packages` and `repo`); app images private (verify the anonymous token is now refused, workflow plan resolves
through P2, staging and production plans pull with the credential); repository private (verify CI, publish,
staging rollout, production plan and apply, instance fetch; check Billing after a few days). Rollback: make
public again. Last, much later: delete the old user packages once no rollback reference or retention need
remains.

## 6.1 Lowering the cost (owner instruction, 2026-11-14)

The owner asked for the lowest possible cost and allowed deleting very old images. Read first: the CI workflow
already runs only the changed test files on pull requests (shard 0); the 20,000 or so main-run minutes a month
come from the full suite that every push to `main` runs on four shards (about 160 job-minutes per run, 127 runs
in 30 days). A repository that stays public costs nothing, so no lever below beats "do not go private"; the gate
in section 6 is still where that is decided.

| # | Lever | Saves | Risk and decision |
|---|---|---|---|
| L1 | Pull requests still build the images but stop uploading the roughly 300 MB review bundle (upload only on push to `main`, where the publish workflow needs it) | nearly all artifact storage (the 10.4 GB) | none for the pipeline; a reviewer can no longer download a PR-built image. Built as P5 |
| L2 | Keep the main-run bundle 3 days instead of 7 | a little storage | none. Built as P5 |
| L3 | Cancel a main run that a newer merge supersedes (concurrency group) | the minutes of every run that a later push overtakes; measure from run history before choosing | an overtaken commit never gets images or a staged record, which is fine because rollouts target the latest main; owner calls it after the measurement. Not in P5 until decided |
| L4 | Cut test minutes using the per-file timings from the shard work (project 1, part B): find files whose database or fixture setup dominates | the largest sustainable saving, since minutes are the sum of test time | needs the timings first; its own small project after project 1 |
| L5 | Dedicated self-hosted runner (private repositories only; never on the shared staging host, which is memory-bound) | all hosted minutes | needs a machine and its price; GitHub's own self-hosted-runner pricing changed recently, UNVERIFIED. Priced only if the Billing answer still looks too high after L1 to L4 |
| L6 | Package storage: copy only the needed versions to the organisation, prune automatically, delete very old versions of the old user packages | private-package storage, if Billing counts it (UNVERIFIED; public packages cost nothing) | see the retention rule below |

Image retention rule (applies to the copy in Stage A step 6, to a pruning step added to the publish workflow, and to
any deletion):
- Keep: the digests production and staging run now, the previous one of each (the rollback targets), and the newest
  20 SHA-tagged versions of `csa-python` and `csa-frontend`. The new organisation namespace receives only these.
- Never delete the database image (`csa-pg16-postgis-vector`): it was built and pushed by hand, and a fresh build
  today fails (the PostGIS release it pulls does not run on the pinned PostgreSQL), so it cannot be rebuilt.
- Delete only after Stage A step 9 and only the SHA-tagged versions outside the keep set, in the old user packages.
  The current token has no `delete:packages` scope, so the owner runs the deletion from a list the assistant
  writes (versions, digests, why each is outside the keep set); the assistant deletes nothing itself.
- Pruning in the publish workflow (keep the newest 20) is part of P5, so storage stops growing.

## 7. Calls made by the assistant (the owner can overrule)

- Registry namespace is lowercase `ghcr.io/fourstacksplt`, set by a variable rather than computed.
- P5 (L1, L2, and the publish-workflow pruning of L6) is a fifth prep pull request, built after P1 to P4; L3, L4, L5
  wait for the measurements named in the table.
- The new design file is separate from the parent design so it can merge independently of the open shard pull
  request, which carries the parent's update.
- Prep pull requests are built one at a time through the normal pull request flow, in the order P1, P2, P3, P4
  (P1 first because everything else reads the namespace).
- The transfer and rename are done by the owner; I never run organisation, AWS or instance steps.

## 8. Unverified items the owner checks

Package settings pages (Actions access after transfer; any transfer option), gitleaks licence requirement,
required reviewers on a private Team repository, Billing prices, the instance's token owner and remote, outbound
SSH from the instance, whether the immutable OIDC subject setting carries over (the new subject is read from the
API after the transfer and must match the trust policy exactly), `gh api` POST behaviour across the old-name
redirect (avoided by flipping the repository constant in step 8 before relying on it), GitHub apps installed on
the personal account.

## 9. After approval

A plan for P1 to P5 (and the post-transfer pull request), built subagent-driven. No transfer step starts until the
owner runs step 1 and agrees the window in step 3.
