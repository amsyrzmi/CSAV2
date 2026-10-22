# Fourstacks domain and release handoff (2026-10-19)

## Current continuation: staging image publication workflow proposed (2026-10-22)

PR #12 (artifact identity, mount probes and registry lookup evidence) merged as
`12c74dd23dde8d18800258c56cc22e4f190bae8d`; its push CI 39826917861 and PR CI
39826987627 passed at `ca19a3c`. The user chose a protected CI publication path
(option b) over a manual push of the verified archive.

Branch `ci/staging-image-publication` (worktree
`/home/amsyrzmi/CSAV2-staging-publish`) adds
[publish-staging-images.yml](../../.github/workflows/publish-staging-images.yml),
[test_publish_staging_images.py](../../tests/test_publish_staging_images.py) and
the [workflow contract](../../deploy/RELEASE.md#staging-image-publication-workflow-proposed-not-yet-run).
It is manual `workflow_dispatch` with one `ci_run_id`, gated by environment
`staging-images`. It verifies a successful push-to-main `ci.yml` run on an
ancestor of `main`, downloads that run's exact archive, verifies checksums,
revision, single amd64 manifest and blob hashes, copies with
`skopeo copy --preserve-digests` to `ghcr.io/<owner>/csa-{python,frontend}:<full-sha>`,
and fails unless the re-read registry digests match. Nothing is rebuilt;
`latest` is never used; `ci.yml` stays build-only.

Local evidence: 10 new tests passed (static permission/trigger/identity
assertions plus the archive-verification block executed against synthetic OCI
layouts, with wrong-revision, multiple-manifest and tampered-manifest
rejections); the 7 staging Compose tests still pass; repository Ruff check and
format check, Pyright on the new test, YAML parse and `bash -n` on every run
block, `git diff --check` and a credential-pattern scan passed. Not run locally:
Gitleaks (hosted CI runs it), actionlint/shellcheck (not installed), and any
real dispatch, GitHub API or registry call. Static and synthetic checks do not
prove GHCR authentication, `skopeo` behaviour on the runner or package creation.

**Gates, in order (each separately approved):**

1. Review and merge the workflow PR after full hosted CI.
2. The repository owner creates environment `staging-images` with a required
   reviewer and deployment branches limited to `main` **before** any dispatch;
   GitHub otherwise auto-creates it unprotected.
3. Approve one dispatch. Run 39754225130 (`7cdc156`, artifact `12414924895`,
   expires 2026-10-29 09:36:53 UTC) would publish exactly the identity-verified
   and mount-probed images; success requires registry digests
   `sha256:f42239697940243492cfbc8fabcd691e7479328ff013c4675b52634ffbbfc764`
   (Python) and
   `sha256:af62b0cab9074c9d177c6b74413d44c1fc0af3fbeeb9162d22eefd9c8c803e4f`
   (frontend). Any other run is a new build needing its own verification.
4. Then: staging-host pull credentials, staging-only secrets/test identities,
   isolated DB bootstrap, application startup, dedicated tunnel/hostname,
   authenticated browser/tenant-isolation/ingress/recovery acceptance, and
   protected repeatable promotion.

The staging proposal still has no worker, delivery or KB consumers; do not
claim end-to-end agent conversations or enable real provider delivery. No
registry write, deployment, real secret, Compose, database, tunnel or DNS
action occurred.

```text
Continue CSA staging/CD in amsyrzmi/CSAV2. Verify, do not assume: PR #12 merged
as 12c74dd; the ci/staging-image-publication PR (manual, environment-gated GHCR
publication of exact CI archives with preserved digests) state and its CI.
If merged, confirm the user created environment `staging-images` with a
required reviewer and main-only deployment branches before asking separate
approval to dispatch it with ci_run_id 39754225130 (artifact expires
2026-10-29 09:36:53 UTC). Expected registry digests: Python
sha256:f42239697940243492cfbc8fabcd691e7479328ff013c4675b52634ffbbfc764,
frontend sha256:af62b0cab9074c9d177c6b74413d44c1fc0af3fbeeb9162d22eefd9c8c803e4f.
Record the job-summary @sha256 references and any failure verbatim. Later gates
remain separate approvals: pull credentials, staging-only secrets/identities,
isolated DB bootstrap, app startup, tunnel/hostname, browser/tenant/ingress/
recovery acceptance, protected promotion. No worker/delivery/KB consumer is
configured. Roadmap order: Staging/CD -> subscription -> WhatsApp test-number
demo -> interactive voice -> Google mailbox/calendar -> reporting/wallboards.
Use isolated worktrees; preserve the two pre-existing deletions in
/home/amsyrzmi/CSAV2. Ask before commit, push or PR; the user merges.
```

## Prior continuation: staging artifact mount probes verified (2026-10-22)

PR #11 merged as `7cdc1562a33bf3a33c4561e53d26263a6a08f731`. Fresh checks:
PR #11 CI 39748495863 and branch CI 39748381545 passed at source head
`97d98e199c28604c1ae938371d5d83e98983862d`; post-merge main CI 39754225130
passed gate, frontend and package at merge commit `7cdc1562a33bf3a33c4561e53d26263a6a08f731`,
including Gitleaks. No merge was performed by this session.

The post-merge review artifact (ID `12414924895`, GitHub digest
`sha256:920ac9637341ef6ed38e7763a867a6d2cf6e5f3329c6cfb274c5f168d800cf91`,
expires 2026-10-29 09:36:53 UTC) was downloaded for offline verification with
explicit approval and removed afterward. It was then downloaded a second time
under separate approval for exact-image synthetic mount probes and removed
afterward. GitHub artifact metadata remained available for the same run and
artifact ID; all downloaded artifact `SHA256SUMS` entries passed, as did OCI
manifest/config hashes. Both images identify source revision
`7cdc1562a33bf3a33c4561e53d26263a6a08f731`,
`linux/amd64`; frontend origin is `http://gateway:8080`, and promotion is
disabled.

| Image | Manifest digest | Config digest | Runtime user |
| --- | --- | --- | --- |
| Python | `sha256:f42239697940243492cfbc8fabcd691e7479328ff013c4675b52634ffbbfc764` | `sha256:c7c681096ddd73b17177ec642157a9ee887a80cab4cf88b2519784230c2ac0b1` | `1000:1000` |
| Frontend | `sha256:af62b0cab9074c9d177c6b74413d44c1fc0af3fbeeb9162d22eefd9c8c803e4f` | `sha256:af86f20a3d23e942d91e68f2fd9962f8273ae56ebf9a3708e3d1bd61638ba210` | `node` = `1000:1000` from image `/etc/passwd` |

The exact images were imported locally with separate approval after checking
their identities and the absence of the exact commit-specific tags. Three
short-lived `--pull=never --network none --read-only --rm` probes passed:

1. Python `Settings` read synthetic `DATABASE_URL`, `LG_DATABASE_URL`,
   `IDENTITY_DATABASE_URL`, `CREDENTIALS_KEY` and disabled `GROQ_API_KEY`
   files as UID/GID 1000. The synthetic credentials key passed model validation;
   no DB connection was attempted.
2. All five migration-target files were readable as UID/GID 1000. The
   migration runner and database were not invoked.
3. Frontend assertions passed for UID/GID 1000, baked
   `http://gateway:8080` origin and rejection of a mismatch, root read-only
   mount, writable `/tmp` and `/app/.next/cache` tmpfs, and absence of a
   `/run/secrets` mount. The Node assertion process did not start the server.

The first frontend probe failed on an incorrect baked-origin path assumption;
the Dockerfile shows the standalone tree is copied to `/app`, so the corrected
`/app/console-api-origin` path passed. This initial failure is retained as
evidence, not hidden. All containers auto-removed; the two exact imported image
tags, task-owned synthetic files and private temporary artifact directory were
then removed. No database, Compose service, app server, real secret or network
connection was used.

The review archives are not published registry images, and matching immutable
Python/frontend registry references have not been identified, so staging
Compose has no verified image references. **Next bounded prerequisite:**
identify eligible existing registry image digests matching these reviewed
source artifacts, or obtain separate approval to publish/import them into the
chosen registry. No registry write or deployment is authorized by the completed
runtime probes. A read-only lookup attempted with approval was blocked: the
GitHub Packages list API returned 403 for the current token; unauthenticated
HEAD requests for candidate GHCR manifests returned 401, and anonymous token
requests returned 403. This does not prove the packages are absent. No
credential was changed, and no registry image was downloaded or published.
The user then ran the lookup under their authorized `gh` identity: the package
list returned no `csa-python`/`csa-frontend` entries without error, and both
version lookups returned HTTP 404 `Package not found`. (An earlier attempt's
exit code 1 is consistent with `set -eu` stopping on that 404.) No matching
registry image exists in the `amsyrzmi` namespace. A local `docker inspect` of
`:latest` tags also found nothing, which is local-only and irrelevant to
digest selection; staging must never use `latest`.
Staging-only secrets, isolated database bootstrap, application
startup, tunnel/hostname routing and public acceptance remain separate gates.

The inert proposal includes database, gateway, frontend, migration and the
dedicated connector only. It has **no worker, delivery or KB consumer**, so it
does not support accepted end-to-end agent conversations; no real provider
delivery is enabled or implied. Despite the image-mount probe passing,
staging-only secrets/test identities, DB bootstrap, app startup, tunnel/hostname
and public acceptance remain separate approvals. Protected repeatable staging
promotion is later still. Subscription acceptance is next in the agreed roadmap
only after these staging gates are met; do not bypass them.

Read this section, current [progress](../PROGRESS.md), the
[release contract](../../deploy/RELEASE.md#pr-11-merged-artifacts-identity-verification-2026-10-22),
and the [staging proposal](../../deploy/compose.staging-local-proposal.yml)
before continuing. Keep all work in an isolated worktree; the original main
worktree still has only the two pre-existing review-report deletions. Preserve
the six-phase order: Staging/CD, subscription, WhatsApp test demo, interactive
voice, Google mailbox/calendar, reporting/wallboards.

The original PR #11 publication and merge are already complete; these current
documentation changes have not been committed or published. Preserve the
unrelated deletions in `/home/amsyrzmi/CSAV2`. Keep the user's roadmap order:
Staging/CD -> subscription -> WhatsApp test-number demo -> interactive voice
-> Google mailbox/calendar -> reporting/wallboards. Ask before commit, push or
PR; the user merges.

```text
Continue in `/home/amsyrzmi/CSAV2-staging-artifacts`, branch
`docs/staging-artifact-verification`, based on PR #11 merge
`7cdc1562a33bf3a33c4561e53d26263a6a08f731`. First verify worktree, branch and
CI. PR #11 CI 39748495863 and branch CI 39748381545 passed at
97d98e199c28604c1ae938371d5d83e98983862d; post-merge main CI 39754225130
passed all jobs at 7cdc1562a33bf3a33c4561e53d26263a6a08f731.

Artifact 12414924895 was verified twice: all listed SHA256SUMS entries, OCI
manifest/config hashes, source revision, linux/amd64 platform, origin and
promotion-disabled metadata matched. Exact Python and frontend OCI images were
loaded locally under separate approval. Python Settings synthetic secret-file
resolution and all five migration secret-file readability checks passed as
UID/GID 1000 without DB access or running migrations. Frontend passed UID,
baked-origin/mismatch, root-read-only, /tmp and /app/.next/cache tmpfs, and no
secret-mount assertions; server not started. The first frontend attempt used an
incorrect baked-origin path and failed; corrected to /app/console-api-origin
per Dockerfile and passed. Containers, exact imported tags, synthetic files
and temporary artifact files were removed.

No matching registry image refs have been identified; review archives are not
registry images. The approved read-only lookup was blocked: GitHub Packages
returned 403 for the current token; anonymous manifest HEADs returned 401 and
anonymous token requests returned 403. This does not prove package absence.
A user-run lookup under an authorized identity then found no `csa-python` or
`csa-frontend` package (list empty; versions HTTP 404). Next bounded gate is a
separately approved registry-publication design: how the verified archives
reach GHCR (CI job with `packages: write` vs. manual push of the verified
archive), package visibility, and post-push digest verification. Do not publish,
deploy, start Compose, provision real staging secrets, bootstrap DB, activate
tunnel/DNS, or merge without separate approval. No worker/delivery/KB consumer
is configured, so do not claim end-to-end agent conversations. Keep the order:
Staging/CD -> subscription -> WhatsApp test-number demo -> interactive voice
-> Google mailbox/calendar -> reporting/wallboards. Preserve the unrelated
deletions in /home/amsyrzmi/CSAV2. Ask before commit, push or PR; user merges.
```

## Prior continuation: customer-service goldens (2026-10-22)

PR #8 merged as `b2dc7a2695a349bd94ed6a75b76fa479473fe95e`. PR CI
39719424850, branch CI 39719295898 and post-merge main CI 39725222410 passed,
freshly verified. Main was fast-forwarded while preserving unrelated deletions.

The isolated `test/customer-service-goldens` branch in
`/home/amsyrzmi/CSAV2-cs-goldens` adds four real-worker synthetic scenarios:
tenant-scoped KB policy/review regeneration, foreign-only KB gap, wrong-customer
ticket denial, and durable human-handoff suppression of follow-up automation.
Only tests and documentation change. Read
[LG08 exact evidence/limitations](../langgraph-rebuild/LG08_testing_seed_evals.md#customer-service-evaluation-expansion-2026-10-22).
All golden, KB and staff-path regressions: **71 passed / 63 existing warnings in
94.12 s**; repository lint/types/import checks passed. Initial fixture and command
failures are retained. No live model/TEI/provider, delivery worker, persistent
database, microphone, frontend/runtime change or deployment.

Next: review/publish this bounded slice with approval, require full final-head
hosted CI and the user's merge. Then inspect subscription's existing plan/status/
unexecuted-request coverage and implement the smallest missing acceptance slice.
Do not silently enable billing execution or promise live provider integration.
Customer-service synthetic inbox/routing/grounding/handoff evidence is improved,
not live-model/multilingual quality or full product completion. Interactive voice
still requires a bounded session/ingress/consent/replay design before media activation.
Continue focused local checks, broaden for shared-runtime changes and retain full
hosted CI as the merge gate.

## Prior continuation: customer-service inbox acceptance (2026-10-22)

PR #7 merged as `650bc08a63df9db8153673ef5970466f5c3665b3`. Final PR CI
39711968531, branch CI 39711862612 and post-merge main CI 39713659882 passed,
freshly checked. Local main was safely fast-forwarded, retaining the two unrelated
review-document deletions. HR guidance is merged; no deployment occurred.

The next isolated branch is `test/customer-service-browser` in
`/home/amsyrzmi/CSAV2-customer-service`. Its test/documentation-only slice adds
real-session packaged customer inbox browser acceptance. Read the
[M5 evidence](./2026-10-17-m5-telegram-inbox.md#packaged-customer-service-browser-acceptance-2026-10-22)
for exact scope, commands, durable counts and initial failures.
Focused inbox regression: **14 passed / 1 warning in 102.20 s**; final browser:
**1 passed / 1 warning in 215.34 s**; lint/types/import checks passed.
No frontend/API/runtime/schema changes, provider sends or delivery worker.
Five deliveries deliberately stay pending, not delivered.

Next: review and publish this bounded acceptance slice with approval, require
final-head full hosted CI, then the user's merge. Continue customer-service
refinement with a bounded synthetic routing/grounding/handoff evaluation over
existing KB/order/ticket/escalation components before claiming that bundle finished.
Subscription already has gated local plan/status reads and unexecuted requests;
scope remaining subscription behavior separately. Interactive staff-browser voice
is still not implemented by local dictation and needs its own reviewed ingress/
session/consent/cancellation/replay contract before LiveKit/provider activation.

Retain focused local validation through customer-service/subscription/interactive
voice completion; broaden for shared-runtime changes and retain full hosted CI
before merge. Do not use historical full-suite evidence as final-patch coverage.
No production/persistent DB access, shared-service restart, deployment or live
provider/microphone request is authorized by this continuation.

## Prior continuation: workspace HR guidance (2026-10-22)

PR #6 merged as `148c03e2271164992191091442a8d8ca67dce408`; main CI
39700139073 passed, freshly checked. Local main was fast-forwarded while
preserving the two unrelated review-document deletions. Work continues on
`feature/workspace-hr-guidance` in `/home/amsyrzmi/CSAV2-workspace-hr`.

The bounded change adds a deterministic, non-persisted HR grant guide beside
workspace chat, using existing HR session/authority checks and fixed same-origin
directory/training links. Platform navigation preserves selected tenant; HR
services still decide record eligibility and mutations. No employee records,
new mutations, model calls, workspace action-union changes or migrations.
Read [HR evidence](../hr/PROGRESS.md#workspace-hr-guidance) and
[API contract](../CONSOLE_API.md#staff-hr-contract-source-implementation-2026-10-21).

Focused affected tests: **198 passed in 189.28 s**. Expanded synthetic packaged HR
browser: **1 passed / 1 existing warning in 227.78 s**. Repository lint and isolated
frontend checks passed. Initial fixture/assertion/selector failures are retained
in HR evidence. Full local pytest, load and disposable reset were not rerun.
No persistent database, provider, microphone, rollout or shared-service changes.

**User-selected validation policy:** use focused local checks through completion
of customer-service, subscription and interactive-voice slices. Include relevant
permissions/replay/public-contract and UI/browser checks; broaden locally when
shared runtime/contracts change. Full hosted CI remains the merge gate. Do not
disable tests or reinterpret old full-suite evidence as covering new changes.

Next: review this bounded diff and publish only with approval, then require
final-head hosted CI and the user's merge. After that, scope the next staff-chat/
customer-service refinement slice from the roadmap; do not enable all tools or
start LiveKit/provider work implicitly. Subscription already has gated local
plan/status reads and unexecuted requests, not billing execution. Interactive
voice, typed-assistant browser coverage, remaining HR-domain browser acceptance,
qualified payroll review, owner/grant provisioning, retention/HMAC-key policy and
persistent rollout remain separate gates.

## Prior continuation: HR browser acceptance (2026-10-21)

PR #5 merged as `cd1d085`; local main matches it with the two pre-existing
review-document deletions preserved. Post-merge CI 39666475261 passed.
The user installed Chromium system dependencies; launch now works. Do not
repeat the old missing-library diagnosis as a current blocker.

The `test/hr-browser-acceptance` branch in `/home/amsyrzmi/CSAV2-hr-browser`
adds a synthetic real-session HR directory/training browser scenario to the
existing packaged acceptance harness. Read [HR progress](../hr/PROGRESS.md)
and [setup/test command](../hr/SETUP.md#acceptance-before-rollout).
It proves scoped fields, tenant/role/CSRF denial, current-grant revocation,
training submission, bound human confirmation, committed-write lost-response
recovery and exact durable outcomes. It changes only tests/documentation.

Initial focused scenario: 1 passed, one warning in 440.51 s. Final expanded
scenario is covered by full `NO_LOCAL_ENV=1 ENV=ci make lint test`:
**2,903 passed, 136 warnings in 2,474.64 s**, all lint/type/import checks passed.
Both packaged browser cases, unchanged load tests and disposable-only reset
passed. No failures/skips/deselections. Historical integration failures below
remain recorded and are not reclassified.

Next: review/merge this bounded acceptance PR after final-head hosted checks.
Then scope staff workspace web-chat refinement, starting with permission-aware
HR page guidance/navigation without enabling new mutations. Actual assistant
chat browser coverage, microphones/interactive LiveKit voice, remaining HR
domain browser coverage, qualified payroll review, retention/HMAC-key policy,
real owner/grant provisioning and persistent rollout remain separate gates.
No persistent migrations, providers, deployment or shared-service restart occurred.

## Prior continuation: HR integration PR #5 (2026-10-21)

PR #4 merged to main as `bb197ae`; main CI 39638334024 passed. Local main
matches it and retains only the two pre-existing review-document deletions.
HR integration is on `integration/hr-foundation` in
`/home/amsyrzmi/CSAV2-hr-integration`; B's original dirty HR worktree is unchanged.
Read [HR progress](../hr/PROGRESS.md) and [setup](../hr/SETUP.md) first for
implemented scope, explicit authority and exact integration evidence.

Both workspace A1 (0370) and HR (0380-0410) are present, with schema readiness
0410 and regenerated OpenAPI. Review fixed shared disposable identity-fixture
cleanup for the new HR foreign keys and added protection-restoration coverage.
Full lint passed; full pytest is **2,901 passed / 1 failed** (host Chromium
missing `libatk-1.0.so.0` before browser assertions). Load and disposable-reset
tests passed; isolated frontend checks passed. Do not call that local full run
green. Hosted PR CI 39660171469 and push CI 39659967703 passed on `5ac7387`,
with **2,902 passed / 136 warnings in 785.08 s**, 100% policy coverage and
gate/frontend/package green. CI installed its browser dependencies; the shared
host was not modified. Only evidence documentation changed afterward. PR #5
is the integration merge boundary; verify its final-head checks before the
user merges it. Main is not yet HR-enabled.

After review/CI and the user's merge, the next bounded acceptance slice is a
synthetic authenticated HR directory/training browser replay covering explicit
grants, cross-tenant denial, human confirmation and uncertain-response recovery.
It is not implemented by this integration. Actual local microphone support,
interactive LiveKit voice, external sends, payroll-compliance review, real
owner/grant provisioning, retention/HMAC key decisions and persistent rollout
remain separate gates. No deployment or persistent database action occurred.

## Prior continuation: workspace A1 merged; HR integration next (2026-10-21)

PR #3 (workspace A0/A1) merged to `main` as `b3b84c7`; local `main` was
fast-forwarded with the dirty documentation/staging files preserved. Read the
[A1 handoff](./2026-10-21-workspace-a1-implementation.md) and the
[PROGRESS entry](../PROGRESS.md) for scope and evidence, including the flaky
first CI run. Main now expects schema 0370. Developer B's HR work in the
separate `feat/hr-foundation` worktree is uncommitted and based on `0264c0a`
with migrations 0380–0410; B must rebase onto `b3b84c7`, set gateway/test/
schema-notes expectations to 0410, merge both sides' `proxy.ts`, `AppShell.tsx`
and `check-isolated.mjs` changes, regenerate OpenAPI, then hand off for
integration review. HR grants/field policy, workspace retention, the HMAC
fingerprint key and voice consent/retention remain open decisions.

## Prior continuation: workspace and HR planning (2026-10-21)

The user changed the immediate priority before further staging preparation:
business web chat, HR, customer-service refinement and interactive voice are
the first four priorities; Teach-a-task is last. Start with the
[two-developer roadmap](./2026-10-21-workspace-agents-roadmap.md) and its separate
workspace/HR prompts. It records recommendations and unresolved authority
decisions, not implementation or deployment approval. Preserve the existing
owner-private, read-only sandbox. No broad workspace tool enablement is authorized
by this planning document.
The user confirmed staff workspace chat, directory/training HR first and
staff-browser voice first; sensitive-data grants and voice consent/retention
still need review.

The staging continuation below is retained but no longer the immediate next
task. Do not provision/attach a connector or alter the existing shared
Cloudflare service, which the user confirmed serves their other sites/apps.
Six labeled test-fixture containers were stopped with explicit approval after
the user confirmed no tests depended on them; they and their volumes remain.
No deletion/pruning or persistent database restart occurred. Do not infer the
cleanup failure cause or turn this into production troubleshooting.

## Prior continuation: local Compose staging design validated (2026-10-21)

Start with the [local-Compose staging section](../../deploy/RELEASE.md#local-compose-staging-variant-configuration-only-validation-2026-10-21)
and the [selected staging design](../../deploy/RELEASE.md#verified-artifact-and-selected-staging-design-2026-10-21).
Local `main` is at `0264c0a`, matching `origin/main` at the last check. The user
selected protected staging-only secret files outside the repo, dashboard-managed
Cloudflare routes, and `https://staging-console.fourstacks.my` for planning only.
Do not ask for or inspect secrets, change `.env.local`, or use production services.

The inert [local-Compose proposal](../../deploy/compose.staging-local-proposal.yml)
uses `file:` sources, explicit approval profiles, no host ports, and internal
`data`, `app`, and frontend-only `edge` networks. It contains no connector. Its
synthetic validation passed:
`NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_staging_compose.py -q` —
**5 passed in 0.88 s**. `NO_LOCAL_ENV=1 ENV=ci make lint` passed Ruff,
formatting, Pyright and import-linter. The initial test attempts failed on test assumptions
(profiled services are omitted unless profiles are enabled; rendered networks
are maps); both were corrected before the passing run. The test ran
`docker compose config` only, using a sanitized environment and temporary
sentinel-only files. It did not start containers. The permission cases model
owner-only `0400` access for UID 1000 and a distinct synthetic UID.

The user approved one disposable probe using the locally available exact
database image digest. With `--pull=never`, `--network none`, tmpfs database and
socket paths, and one synthetic file bind mount, the PostgreSQL process and
`postgres` account both reported UID **999**. The source file was host-owned
`1000:1000`, mode `0400`; the root entrypoint could read it and initialize via
`POSTGRES_PASSWORD_FILE`, while UID 999 could not read it after startup. This
matches the bootstrap flow; it does not imply the running server needs the
source file. The container and fixture were removed. No real credentials,
network or persistent database were used. Gateway/migration mounts remain
untested because their image references are not yet supplied, though both
services are configured to run as UID 1000.

The official Cloudflare `cloudflared` source at release 2026.10.2 defines
`--token-file` and `TUNNEL_TOKEN_FILE`; it reads the token from that path. This
provides a file-only design option: mount the token file only into a dedicated
connector and set the path, never the token value, in `TUNNEL_TOKEN_FILE`.
However, no connector image is selected or pinned, and its runtime UID and
secret-file readability have not been verified. No connector was added or
started.

Next bounded task: select and review an exact Cloudflare connector image/version
and digest, then verify its file-backed synthetic secret access without using a
real token. Keep it out of the Compose proposal until its dedicated edge plus
outbound-network design and access policy are reviewed. Obtain explicit
approval before image pulls or container probes, real staging secret/database
creation, image publication, network attachment or dashboard routing. No full
suite or database reset was run for this infrastructure proposal.

Preserve the two unrelated `docs/reviews/` deletions. Current task changes are
uncommitted/unpushed and limited to this handoff, `docs/PROGRESS.md`,
`deploy/RELEASE.md`, the new Compose proposal and its test. No user action is
needed now; do not create real secret files or change Cloudflare settings.

```text
Continue from the current local-Compose staging section and deploy/RELEASE.md.
Local main is 0264c0a and matched origin/main at the last check. The synthetic
Compose config test passed 5 tests; an explicitly approved, no-network,
tmpfs-only probe of the local pinned DB image confirmed PostgreSQL UID 999 and
entrypoint access to a synthetic bind-mounted password file. The postgres UID
could not read that 1000:1000 mode-0400 file, as expected after bootstrap.
Gateway/migration mounts remain untested because their image refs are not
supplied. The official `cloudflared` 2026.10.2 source supports `--token-file` and
`TUNNEL_TOKEN_FILE`, allowing a path-only handoff. Next review and pin a
connector image, then verify synthetic file permissions before any connector
configuration. Do not add/start it or expose a token. No real secrets,
.env.local, persistent database, app service, network, DNS/tunnel, registry,
merge or deployment changes. Preserve two unrelated review deletions and
current unstaged proposal/test/docs changes.
```

## Prior continuation: post-merge staging preparation (2026-10-21)

PR [#2](https://github.com/amsyrzmi/CSAV2/pull/2) merged at 01:26:24 UTC as
`0264c0ade3739f1285ab1a06e577573b71984d44`. Both source-head CI runs passed:
PR 39525314161 and push 39524681672. Post-merge
[main run 39527322898](https://github.com/amsyrzmi/CSAV2/actions/runs/39527322898)
passed gate, frontend and package at the final refresh. Review artifact
`csa-review-only-0264c0ade3739f1285ab1a06e577573b71984d44`
(ID `12315215226`, 195119606 bytes) expires 2026-10-28 01:41:52 UTC.
It was listed, not downloaded, integrity-verified or runtime-tested here.

The user explicitly approved reconciling only the three published task files
and moving to main. Local main now matches the merged head. Only the two
unrelated review-report deletions remained before this documentation follow-up;
they are still unstaged and must not be included in any future task commit.
No environment file, credential, persistent database or running service changed.
The final read-only listener check found no listeners on private console ports
13000/18081. This task did not stop or restart them; the cause is not established.
Earlier pre-merge publication prompts below are historical.

Read the [staging preparation checklist](../../deploy/RELEASE.md#post-merge-planning-checkpoint-2026-10-21)
and the inert Compose proposal before designing any executable staging step.
No staging-named containers or volumes were found. Docker Compose 2.40.3 is
installed; the proposal assumes external secrets whose provisioning mechanism
has not been validated for this host. The existing cloudflared container uses
`network_default`; no ingress contents, token, network attachment or DNS was
changed or inspected. Do not attach it to the gateway or assume connectivity
from the proposal's isolated network.

The user selected `https://staging-console.fourstacks.my` as the staging origin
for planning only. No DNS/tunnel/TLS configuration or rollout is authorized.
Next bounded task: prepare a supported secret-delivery/ingress design for that
origin, without provisioning or starting it. Main CI
passed; verify exact artifact identity before requesting image publication
or approving registry digests. New synthetic staging secrets must be distinct;
the user's existing development credentials remain unchanged. No commit/push
of this documentation follow-up has been requested.

```text
Continue from the current post-merge section and deploy/RELEASE.md's staging
checklist. Local main is at 0264c0a, containing merged PR #2. Its source PR/push
CI passed, as did post-merge main CI 39527322898 (gate/frontend/package).
Preserve the two unrelated docs/reviews deletions. Current follow-up changes
only progress, this handoff and the release checklist; validate docs/diff only.
Use https://staging-console.fourstacks.my as the user-selected planning origin;
prepare the supported secret-delivery method and frontend-only ingress design
for separate execution approval. No env-file/credential
changes, production access, persistent migration, service start/restart,
registry publication, tunnel/network/DNS mutation, merge or deployment.
Do not copy dev credentials into staging or treat synthetic tests as public
ingress, sustained capacity or production recovery acceptance.
```

## Latest slice: corrected inherited TCP listener (2026-10-20)

The local change in `tests/test_load_acceptance.py::load_client` replaces the
Uvicorn `--fd` bootstrap with `socket.socket(fileno=...)`, which detects the
actual family, verifies IPv4 and sets `TCP_NODELAY=1` before passing the socket
to `uvicorn.Server.run(sockets=[listener])`. The parent still reserves the
ephemeral loopback port and passes only that descriptor; subprocess readiness,
real tracing and process cleanup remain unchanged. No application code, pool
sizes, event-loop selection or timing thresholds changed.

A separate disposable loopback subprocess using the corrected bootstrap and
the installed Uvicorn HTTP protocol verified the accepted socket had family
`AF_INET` (2) and `TCP_NODELAY=1`; it was terminated afterward.

The exact focused command ran once:

```sh
env -i HOME="$HOME" PATH="$PATH" NO_LOCAL_ENV=1 ENV=ci make load-test
```

Result: **4 passed, 4 warnings in 264.13 s**, no skips.

| Measurement | Result | Existing limit |
| --- | --- | --- |
| ASGI acknowledgement / scheduled p95 | 66.181 / 69.213 ms | Both <200 ms; passed |
| TCP acknowledgement / scheduled p95 | 149.415 / 150.448 ms | Both <200 ms; passed |
| Graph-only p95 | 92.894 ms | <150 ms; passed |
| Simultaneous TCP acknowledgement / scheduled p95 | 329.062 / 338.653 ms | Diagnostic only; no latency assertion |

Every database-backed scenario completed 150 synthetic turns with 150 unique
replies and 150 pending deliveries. Sequential queue throughput was
2.159/2.207 turns/s for ASGI/TCP. Simultaneous worker-turn p95 was 2064.861 ms.
None of this establishes external delivery or sustained 50 completed turns/s.
The earlier 305.228 ms failure and diagnostic matrices below remain evidence.

Required full validation:

```sh
env -i HOME="$HOME" PATH="/tmp/fourstacks-toolbin:$PATH" \
  LD_LIBRARY_PATH=/tmp/csa-playwright-runtime-20261020/rootfs/usr/lib/x86_64-linux-gnu \
  NO_LOCAL_ENV=1 ENV=ci make lint test
```

Ruff, formatting, Pyright (zero errors/warnings) and import-linter passed.
Full pytest: **2,550 passed, 136 warnings in 2574.63 s**, no skips or
deselections. All four load tests and four packaged-console tests passed.
`tests/test_migrate.py` passed all seven tests, including
`test_make_reset_only_disposable_container`, which invokes the real reset
target against only its disposable database. No bare persistent reset was run.
Successful full-run load metrics are captured by pytest, not printed in this
log; use the focused metrics above only for that focused run.

This is one green full run with the correction, not demonstrated stable
capacity or a production performance waiver. Historical failures are retained.
No provider request, credentials/environment-file change, dev-service restart
or deployment was performed. Gateway readiness and frontend login returned
HTTP 200 after validation. The unrelated two `docs/reviews/` deletions remain
excluded.

Publication approved on 2026-10-21: only `tests/test_load_acceptance.py`,
`docs/PROGRESS.md` and this handoff are prepared on `fix/tcp-load-listener`,
based on merged `main` at `6e51354`. A separate worktree preserves the original
dirty review branch. Main did not include the later `39de725` status-note
reconciliation; this follow-up carries those corrections as well. Current next
task: require fresh hosted checks on the published fix head and user review
before merge. If the GitHub CLI still cannot create PRs (HTTP 403), provide the
existing fix branch's compare URL for manual PR creation; do not change
credentials. No deployment is authorized.

```text
Read this corrected-listener section first; older next-task prompts below are
historical. The local harness change preserves IPv4 socket metadata and enables
TCP_NODELAY. Exact focused load tests passed 4/4 and full lint/tests passed
2550/2550 (136 warnings, 2574.63 s), including disposable reset and packaged
browser tests. Docs link/fence and diff checks must pass after final edits.
The user approved publication on 2026-10-21. The three-file follow-up is on
fix/tcp-load-listener based on merged main at 6e51354; unrelated docs/reviews
deletions remain excluded. Check fresh hosted CI and PR status before the user
merges. If PR creation remains permission-blocked, give a manual compare link.
No persistent DB changes, provider requests, environment-file changes, running
dev-service restarts, tunnels or deployment. Preserve thresholds and historical
failures; do not equate a green run with stable production capacity.
```

## TCP diagnosis (2026-10-20)

This diagnostic follows the failed 305.228 ms TCP acceptance run below; it
does not replace it. No repository application or test code changed.

The inspected ingress path uses an eight-connection preopened autocommit pool,
one unscoped endpoint lookup and an explicit tenant transaction. Six query-loader
calls plus transaction begin, tenant `set_config` and commit execute on the
synthetic fresh-contact path. The console response-header middleware bypasses
channel ingress. The TCP subprocess uses real tracing, unlike the ASGI benchmark
which stubs it.

Session-only instrumentation measured server time, pool acquisition, query
paths, transaction entry/exit, event-loop lag and host pressure. Each controlled
batch sent 150 requests at 50/s; no worker or provider ran. Two diagnostic
matrices each verified 600 durably queued synthetic events before Testcontainers
teardown. These are ingress probes, not completed-turn/delivery acceptance.

First fixed-order matrix (instrumentation affects timing):

| TCP configuration | Health p95 | Ingress acknowledgement p95 |
| --- | --- | --- |
| Default uvloop, tracing on, first control | 61.386 ms | 271.712 ms |
| Standard asyncio, tracing on | 52.391 ms | 167.001 ms |
| Default uvloop, tracing off | 50.159 ms | 149.759 ms |
| Default uvloop, tracing on, final control | 51.242 ms | 134.930 ms |

The final unchanged control was faster than both alternate configurations, so
this sequence does not isolate tracing or uvloop as the cause. In the slow first
ingress control, pool-acquisition p95 was 63.222 ms, transaction begin/end
21.659/20.826 ms, and individual query-path p95s approximately 23.7–29.4 ms.
The final control had pool-acquisition p95 0.212 ms and transaction begin/end
4.484/7.060 ms. These percentiles cannot be added to reconstruct request p95.

A concrete harness artifact was then verified in the installed Uvicorn:
`--fd` reconstructs the inherited IPv4 listener with
`socket.fromfd(fd, AF_UNIX, SOCK_STREAM)`. Accepted TCP sockets were observed
with `TCP_NODELAY=0`, unlike the normal TCP transport expectation. A second,
fixed off/on/on/off matrix changed only the inherited listener's `TCP_NODELAY`
setting, holding default uvloop/tracing on:

| Listener and accepted socket NODELAY | Health p95 | Ingress acknowledgement p95 |
| --- | --- | --- |
| 0, first control | 48.181 ms | 127.630 ms |
| 1, first treatment | 4.789 ms | 113.150 ms |
| 1, second treatment | 4.158 ms | 168.261 ms |
| 0, final control | 45.179 ms | 141.168 ms |

This establishes a TCP buffering component in the harness. It does not prove
that eliminating it fixes the entire original failure: ingress timings remained
variable, and these probes had diagnostic headers, health warm-up and no queue
drain. Neither matrix is a new acceptance result.

Host: Intel i5-1135G7, four cores/eight threads, approximately 15 GiB RAM.
Swap was fully allocated, but all measured batches had zero swap-in/out.
Aggregate CPU idle ranged 35.21–79.68% in the first matrix and 47.15–92.36% in
the second. The slowest first-matrix ingress batch had CPU pressure 7.611%,
I/O full pressure 3.002% and memory full pressure 0.865%. This supports
variable scheduling/storage pressure, not aggregate CPU exhaustion or a proven
laptop-only cause. No thermal/power/frequency-causality measurements were made.

Both matrices used the session file `files/test_tcp_diagnostic.py` with:

```sh
env -i HOME="$HOME" PATH="$PATH" PYTHONPATH="$PWD" NO_LOCAL_ENV=1 ENV=ci \
  uv run pytest -c pyproject.toml -p tests.conftest \
  /path/to/session/files/test_tcp_diagnostic.py -q -s --tb=short
```

The second additionally set `DIAG_NODELAY_MATRIX=1`. Results were **1 passed,
2 warnings in 74.49 s** and **1 passed, 2 warnings in 60.72 s**. Raw metrics
are retained in the session's `files/tcp-diagnostic-20261020.log` and
`files/tcp-nodelay-diagnostic-20261020.log`; the disposable app processes,
listeners and databases were cleaned up. The existing private console was not
restarted or modified.

Next bounded change: correct the test harness's inherited TCP socket handling,
verify accepted sockets have the intended TCP settings, then rerun the exact
unchanged acceptance selection once. Do not disable tracing, switch event loops,
increase pool sizes or relax limits on this evidence. A corrected-harness pass
would still not prove stable full-suite performance. No commit/push or live
rollout was performed.

## Latest slice: focused performance check (2026-10-20)

At source `39de72512dcee66e2e74c786654122a1698010cf`, ran once without coverage:

```sh
env -i HOME="$HOME" PATH="$PATH" NO_LOCAL_ENV=1 ENV=ci make load-test
```

Result: **3 passed, 1 failed, 4 warnings in 268.79 s**. No skipped tests.

| Measurement | Result | Existing limit |
| --- | --- | --- |
| ASGI acknowledgement / scheduled p95 | 69.981 / 70.605 ms | Both <200 ms; passed |
| TCP acknowledgement / scheduled p95 | 305.228 / 307.746 ms | Both <200 ms; acknowledgement assertion failed first |
| Graph-only p95 (200 turns) | 108.943 ms | <150 ms; passed |
| Simultaneous TCP acknowledgement / scheduled p95 | 516.237 / 554.800 ms | Diagnostic only; no latency assertion |

Both sequential transports and the simultaneous case each completed 150
synthetic turns with 150 unique replies and 150 pending deliveries. Sequential
queue throughput was 2.195 turns/s (ASGI) and 2.146 turns/s (TCP), not 50
completed turns/s. Simultaneous worker-turn p95 was 1954.827 ms. No external
delivery or real provider was exercised. Warnings were the Testcontainers
deprecation and missing optional `/run/secrets` directory.

Only disposable Testcontainers were provisioned. The existing private dev
gateway/frontend remained untouched and returned readiness/login HTTP 200
afterward. No environment-file access, persistent migration/reset, provider
request, timing retry, threshold relaxation or full-suite rerun occurred.
The user's earlier private-console smoke check was reported successful; it is
user-operated evidence, not an independently observed authenticated test.

LG15 full-suite performance acceptance remains open. The next bounded task is
TCP latency diagnosis, not another acceptance retry: inspect the ingress,
connection-pool and tracing paths and collect attributable diagnostic evidence
with disposable fixtures. Do not attribute the failure to host contention
without measurements. Keep the existing thresholds and this failed run.

```text
Start with the latest performance section of this handoff and LG08/LG15.
The single focused load run at source 39de725 failed TCP p95 at 305.228 ms
(scheduled 307.746 ms); ASGI and graph-only limits passed. Diagnose TCP latency
using disposable synthetic fixtures only with NO_LOCAL_ENV=1 ENV=ci.
Do not retry until green, relax thresholds, or claim full-suite acceptance.
Do not change persistent dev data, environment files, provider credentials,
running services, tunnels or deployment. Preserve unrelated docs/reviews
deletions. No commit/push is authorized for this diagnostic continuation.
```

## Latest slice: synthetic M2 browser acceptance (2026-10-20)

The documentation audit is committed as `f46dba0fe006577dcc56d150bd62a576adf0f15e`;
the browser slice is committed and pushed as
`3979da55b72f11f44ac678ae6130b1020c79a1ec` on
`review/domain-release-2026-10-20`. The only local worktree changes before this
status-note update were two pre-existing deletions under `docs/reviews/`; they
remain excluded from both commits.

The new JavaScript-enabled test
`tests/test_packaged_console.py::test_packaged_m2_browser_acceptance` passed
once against packaged candidate services, synthetic tenant/run/event records,
and disposable Testcontainers Postgres: **1 passed**, one existing
Testcontainers deprecation warning, **170.34 s**. It exercises UI login,
password change, logout and stale-session revocation; platform/tenant-admin
switch permissions and persisted capability/audit results; read-only UI;
two-tenant Runtime activity filtering/detail/polling; visible refresh failure
and recovery; same-origin requests, denied browser egress and no page errors.
No agent, model, worker or provider is invoked.

The executed focused command was:

```sh
PATH=/tmp/fourstacks-toolbin:$PATH \
LD_LIBRARY_PATH=/tmp/csa-playwright-runtime-20261020/rootfs/usr/lib/x86_64-linux-gnu \
NO_LOCAL_ENV=1 ENV=ci uv run pytest -c pyproject.toml \
  tests/test_packaged_console.py::test_packaged_m2_browser_acceptance -v --tb=short
```

This host needed the temporary Node wrapper and extracted Chromium libraries;
they are validation workarounds, not project requirements. CI installs Chromium
with system dependencies. The package/dependency and CI changes are local.
First `NO_LOCAL_ENV=1 ENV=ci make lint test` attempt: lint passed (Ruff, format,
Pyright with zero diagnostics, and import-linter); pytest completed with
**2,546 passed, 136 warnings, 4 setup errors in 2,101.43 s**. All four errors
were packaged-console setup assertions that Node was absent from `PATH`; no
test assertion failed. The packaged-console disposable-reset check was among
the four that could not set up, so reset acceptance was not established by
that attempt. A second run with the reviewed temporary Node wrapper and Chromium
libraries completed with **2,549 passed, 136 warnings, 1 setup error in
2,188.20 s**: the new browser test tried to bootstrap an admin already present
in the module-scoped disposable database. The fixture now truncates
`console_users` with `CASCADE` and clears login-attempt buckets in that
disposable database before each identity scenario. A focused sequential rerun
of the packaged identity and M2 browser tests passed (**2 passed**, one existing
warning, **212.60 s**). Final full run:

```sh
PATH=/tmp/fourstacks-toolbin:$PATH \
LD_LIBRARY_PATH=/tmp/csa-playwright-runtime-20261020/rootfs/usr/lib/x86_64-linux-gnu \
NO_LOCAL_ENV=1 ENV=ci make lint test
```

It passed lint (Ruff, formatting, Pyright **0 errors/warnings**, import-linter)
and **2,550 tests, 136 warnings, 2,562.35 s**. All four packaged-console tests
passed. `tests/test_migrate.py` reported seven passes, including
`test_make_reset_only_disposable_container`; it was not skipped or deselected.
`uv lock --check` passed. Final documentation validation covered 45 Markdown
files and 409 local targets with zero errors; `git diff --check` passed. The run
used Testcontainers/disposable fixtures only. This one full-suite pass does not
erase historical latency failures or establish stable performance acceptance.

Hosted CI on the browser-slice commit passed: pull-request run
[39455808313](https://github.com/amsyrzmi/CSAV2/actions/runs/39455808313) and push
run [39455797430](https://github.com/amsyrzmi/CSAV2/actions/runs/39455797430)
both succeeded, including frontend, gate (pytest, Gitleaks and policy branch
coverage) and package. This confirms the job-scoped `pull-requests: read`
permission fixed the earlier Gitleaks integration 403. The earlier failed run
[39417032600](https://github.com/amsyrzmi/CSAV2/actions/runs/39417032600) is
retained as historical evidence, not the current result. The current GitHub CLI identity could not read PR details (HTTP 403); PR #1 was
last confirmed open and draft before the browser-slice push. The user plans to
merge after reviewing the updated PR. No merge, registry publication, promotion
or deployment has occurred.

## Prior slice: documentation audit closeout and release review (2026-10-20)

Read this section and the [current next task](#next-bounded-task-and-copy-ready-prompt)
first; older slices below preserve historical evidence.

After the approved documentation publication, local branch
`review/domain-release-2026-10-20` and its remote match HEAD
`f46dba0fe006577dcc56d150bd62a576adf0f15e`. At **2026-10-20 11:46 UTC**, the
index is empty and the only worktree changes are the two pre-existing,
unstaged deletions under `docs/reviews/`; they remain excluded. Do not restore
or stage them. `ui-guide/` is untouched; `SERVER_HANDOFF.md` remains
ignored/local-only, not a commit input. The 54-path audit commit is published.

The user created draft PR [#1](https://github.com/amsyrzmi/CSAV2/pull/1), titled
“Review packaged release and synthetic acceptance evidence”, from
`review/domain-release-2026-10-20` to `main` at
`848d8efa864c12d2168b4ccd79e32b97e0bde790`. Fresh GitHub MCP observations at
**11:46 UTC** confirm it is open and draft. No merge, registry publication,
promotion or deployment is authorized or performed.

The newest pull-request CI run
[39417032600](https://github.com/amsyrzmi/CSAV2/actions/runs/39417032600) is
still **in progress**: frontend passed; gate is running pytest, while Gitleaks
and policy branch coverage are pending. Ruff, format, Pyright, import-boundary
and schema-migration steps passed. Earlier same-head run
[39402852826](https://github.com/amsyrzmi/CSAV2/actions/runs/39402852826)
completed successfully, including gate/frontend/package; it does not replace
the newer PR run. `gh pr list` still returns HTTP 403, but GitHub MCP confirmed
the PR and check status. Credentials were not inspected or changed.

Reviewed the entrypoint/overview, architecture/API/schema/progress/frontend docs,
handoffs, LG13 and runbooks 01–08 plus their new status index. The obsolete
`CSA_WebConsole/Docs/` tree and ecommerce draft remain removed. No replacement
feature or runtime code was added. Final bounded corrections:

- LG09 no longer claims operational rollback or all-eight-runbooks execution.
  Its rollback test checks mocked n8n forwarding then LangGraph enqueue, not the
  reverse switch or loss-free delivery. Its runbook test covers helpers for
  01–04, 06 and 08, not rotation or restore.
- The migration roadmap no longer says the removed drop/workflow snapshots
  remain on disk, or places Git adoption after their later removal.
- The frontend's dated browser-session record is explicitly historical.
- Progress qualifies an earlier packaging-era "hosted unverified" statement
  rather than contradicting the later hosted evidence.

Documentation validation from the repository root:

```sh
python3 /home/amsyrzmi/.copilot/session-state/b1accf21-1a93-42cf-ac8d-80b76e7173bd/files/check_docs.py
git diff --check
```

The session-only checker checks local file/directory link targets outside fenced
code and balanced backtick/tilde fences. Scope: all 41 current Markdown files
under `docs/`, root README, release contract, frontend README and the changed
historical console state file: **45 files**, **407 local targets**, **zero errors**;
`git diff --check` exits **0**. It does not validate remote URLs or heading
fragments. The checker and `git diff --check` were rerun after this PR/status
refresh and pass with the same counts. No runtime tests, lint/build, database
probe or services were used for this prose-only slice. Earlier runtime
passes/failures below remain unchanged.
The earlier committed-range secret scan excludes the audit commit. Hosted
Gitleaks is pending in PR run 39417032600.

## Earlier slice: exact hosted OCI runtime acceptance (2026-10-20)

Re-downloaded artifact **12241365102** from successful hosted run
[39367800618](https://github.com/amsyrzmi/CSAV2/actions/runs/39367800618),
source `da1b50da5b40479370507020ce7d17cd1317707f`. Before import, checked the
GitHub ZIP digest, all seven listed file checksums, all **22** content-addressed
OCI blobs and the source/timestamp/platform/origin/promotion-disabled metadata
recorded below. Each OCI descriptor matches its BuildKit metadata and records
linux/amd64. Both runtime configs carry the exact source-revision label.

The session-local `test_hosted_oci.py` adapter imports the three unchanged tests
and identity fixtures from `tests/test_packaged_console.py`, replacing only
`packaged_images`: it verifies artifact metadata/checksums, refuses existing
candidate tags, loads the two OCI archives into Docker's containerd image store,
checks imported IDs/platform/revision and yields their exact manifest IDs.
It does not invoke the application image builder. The database fixture still builds
its separate combined extension image and uses only disposable Testcontainers.

Imported/executed manifest IDs (not rebuilt config hashes or published images):

- Python: `sha256:d07ee12b7e23b36c7ca4d44b79cd1aab7de8e485253137b05602e9b8e6f464ea`.
- Frontend: `sha256:09f8e8e4191b6881ebd96ab454fb6c4dad7508da3ba2db113d61f220ed395934`.

Session-local command, from repository root (`SESSION_FILES` denotes this
session's files directory, not an environment/secrets file):

```sh
NO_LOCAL_ENV=1 ENV=ci uv run pytest -p tests.conftest \
  -c /home/amsyrzmi/CSAV2/pyproject.toml \
  "$SESSION_FILES/test_hosted_oci.py" -v --tb=short
NO_LOCAL_ENV=1 ENV=ci make lint
```

Final pytest: **3 passed**, **one existing Testcontainers deprecation warning**,
**233.60 s**. All three exact-artifact cases pass:

1. HTTPS login/logout, secure cookies, password/session rotation, CSRF/origin,
   four roles and tenant isolation, legacy marketplace path denial, M6 rendered
   local-only warnings, and readiness fault/refusal/recovery.
2. Packaged migration approval/target guards, baseline/migrations/checkpoints,
   reapplication ledger preservation, first-admin/audit and duplicate refusal;
   explicit CHOWN-only disposable volume initialization and UID/GID1000 writes.
3. Synthetic logical backup/restore into a second disposable database, exact
   ledger/identity/membership/audit preservation, restored role/RLS boundaries,
   packaged readiness and incomplete-ledger refusal/recovery.

Initial attempt: **three fixture setup errors**, **26.88 s**, before application
startup. The adapter compared Docker's image ID with the OCI config digest.
This Docker 29 containerd-backed store reports the OCI manifest digest instead.
The correction compares with the already verified manifest digest, retaining
independent revision/platform checks; no acceptance assertion or runtime code was
weakened. Both logs and the small adapter remain in session storage.

Lint passes: Ruff/check-format, Pyright (**0 errors/warnings**) and import boundary.
Repository changes in this slice are documentation only; the full suite was not
rerun. Earlier complete-suite/latency failures and passes remain dated evidence.
The imported task-owned application tags and containers were removed, and the
temporary download was deleted. No production connection, persistent migration,
existing-service restart, registry publication, public route or deployment.

This establishes exact hosted-artifact HTTP/server-rendered runtime acceptance,
not JavaScript browser/public ingress acceptance, live provider eligibility,
worker/delivery behavior, HTTP uploads or a persistent staging rollout.
The four documentation updates were committed as
`0e148f85a2e4b63fb11541909befc7a2fdbd58b5` and pushed to
`review/domain-release-2026-10-20`. Hosted CI run
[39381920580](https://github.com/amsyrzmi/CSAV2/actions/runs/39381920580)
and follow-up run
[39382774620](https://github.com/amsyrzmi/CSAV2/actions/runs/39382774620)
completed successfully. Listing PRs and creating a draft PR failed with
HTTP 403 (`Resource not accessible by personal access token`) through both GitHub
GraphQL and REST. At that observation, no PR was confirmed or created. The next
step was to use an authorized GitHub account/token to check the run conclusion
and open/review the PR. This state was superseded later the same day when the
user created draft PR #1; see the latest slice at the top. Merge and staging
execution require separate approval.

## Earlier slice: complete review-candidate secret scan (2026-10-20)

Offline Gitleaks **v8.24.2** completed successfully over the exact review range:

```text
848d8efa864c12d2168b4ccd79e32b97e0bde790..da1b50da5b40479370507020ce7d17cd1317707f
```

This includes c2e5376 (the initial 118-file snapshot), de955f0 and da1b50d.
Git confirms a non-shallow repository and exactly **three commits** in this range.
Scanner output: **3 commits scanned**, approximately **962,310 bytes**,
**1.47 s**, **no leaks found**, exit **0**. The JSON report is exactly `[]`.
This supplies the missing candidate-range scan evidence; it does not replace the
record of the original failed hosted scan or expand the later hosted one-commit scan.

The existing local scanner image was used without a pull:
`zricethezav/gitleaks:v8.24.2`, resolved RepoDigest
`zricethezav/gitleaks@sha256:b5918eb91b8d2473cec722f066abb4352e4ffdc4ec9f4283ec143aba9ec9ebc4`.
A task-owned `git clone --bare --no-hardlinks` copy supplied only committed history;
no worktree/environment files were mounted. Docker ran with networking disabled,
read-only rootfs/history mount, invoking UID/GID, all capabilities dropped and
`no-new-privileges`. Only its task-owned results directory was writable.
Scanner invocation inside that isolated container:

```sh
gitleaks git /candidate \
  --log-opts=848d8efa864c12d2168b4ccd79e32b97e0bde790..da1b50da5b40479370507020ce7d17cd1317707f \
  --redact=100 --ignore-gitleaks-allow --no-banner --no-color \
  --report-format=json --report-path=/results/candidate-redacted.json
```

Default rules were used; no custom config, ignore file, baseline or suppression
was supplied. Inline `gitleaks:allow` comments were deliberately ignored.
This is a candidate commit-diff scan, not a full-history/base-tree or uncommitted
worktree scan, and cannot guarantee absence of secrets. No CI/runtime code changed.
The temporary bare copy was removed; the redacted log and empty report remain in
session storage. Documentation links/fences and `git diff --check` pass; no code
tests or databases were started for this documentation-only update.

Next at that point: disposable runtime acceptance of the exact hosted OCI artifacts,
not freshly rebuilt substitutes. Evidence/docs remain uncommitted. PR review and
merge/promotion/staging approvals remain separate; no remote write or deployment
occurred in that slice. The later runtime acceptance above now passes.

## Earlier slice: hosted OCI artifact verification (2026-10-20)

[Run 39367800618](https://github.com/amsyrzmi/CSAV2/actions/runs/39367800618)
for clean commit `da1b50da5b40479370507020ce7d17cd1317707f` succeeded:
gate, frontend and package jobs all passed. Hosted pytest: **2,549 passed**,
136 warnings, **667.46 s**. Policy coverage selection: **102 passed**, two
deselected, 35 warnings, **26.57 s**; reported branch-aware total **100%**.
Gitleaks completed successfully but explicitly scanned **one commit**, the latest
push range. This is not proof that the large initial c2e5376 candidate or full
repository history was scanned: its earlier run failed before completing scanning.
Full checkout history fixes revision resolution, not the scanner's range selection.

Downloaded only the build-only release artifact (not diagnostic SARIF):
`csa-review-only-da1b50da5b40479370507020ce7d17cd1317707f`,
artifact ID **12241365102**, **195,120,478 bytes**, expires
**2026-10-27T04:21:30Z**. Its downloaded ZIP SHA-256 matches GitHub metadata:
`b934c61a82485b542ebc4e40f0099b4e04bbf4af078291c22850718adec22a86`.
All **seven** SHA256SUMS entries match, and all content-addressed blobs inside
both OCI archives match their hashes (**10 Python**, **12 frontend**).
Each OCI index has the linux/amd64 descriptor matching its BuildKit metadata:

- Python manifest: `sha256:d07ee12b7e23b36c7ca4d44b79cd1aab7de8e485253137b05602e9b8e6f464ea`.
- Frontend manifest: `sha256:09f8e8e4191b6881ebd96ab454fb6c4dad7508da3ba2db113d61f220ed395934`.

Release metadata names the exact commit above, `linux/amd64`,
`http://gateway:8080` and `promotion=disabled`. OCI manifest digests are not a
claim that application images have been published in GHCR. Archives were inspected
without extraction/import/execution and the temporary downloaded ZIP was removed.
No registry publish, PR merge, deployment, provider/ingress change or service restart.

Next at that point: establish a successful secret scan of the **whole review
candidate**, not only the most recent fix commit, before merge or promotion review.
The later offline scan above now supplies that evidence.
Use redacted output and preserve scanning failures; no broad allowlist or bypass.
After that, exact hosted-artifact runtime acceptance and separately approved staging
remain gates. This run establishes export/upload/integrity, not execution of the
downloaded OCI images or public HTTPS/browser acceptance.

## Earlier slice: hosted CI checkout-history fix (2026-10-20)

The user approved the full source selection, committed locally as c2e5376 (118
files; the earlier 92 status entries included directories). The user subsequently
published the combined DB image, updated the three active CI/staging references
in de955f0 to registry index digest
`sha256:4e610cbe04be850136f42fffcc1ef546ceff5b13d8f25322738d0d74a9a1fefe`,
and pushed `review/domain-release-2026-10-20`.

[Hosted run 39366363372](https://github.com/amsyrzmi/CSAV2/actions/runs/39366363372)
completed with failure, but GHCR service initialization, baseline/migrations,
Ruff/format/Pyright/import boundary and isolated frontend acceptance passed.
Hosted pytest: **2,549 passed**, 136 warnings, **658.60 s**, no skips.
Gitleaks failed before a useful scan: default shallow checkout lacked the parent
required by its push commit range; Git reported an ambiguous/unknown revision,
and the scan processed approximately zero bytes. Its partial "no leaks" output
is not acceptance. Coverage and packaging were skipped, so there are no release
OCI artifacts (the scanner uploaded a diagnostic SARIF artifact only).

The gate checkout now uses `fetch-depth: 0`. Frontend/package checkout behavior,
scanner thresholds/configuration, read-only workflow permissions and deployment
gates are unchanged. This is a two-line workflow fix, not a scanner bypass.
The CLI watch helper also failed because the configured token cannot read check
annotations; direct run-status/job/log reads succeeded. No token permissions changed.

Next: validate the history fix on hosted CI, then coverage and package OCI
export/metadata/checksum/upload. Do not merge or deploy until those gates pass.

## Earlier slice: read-only hosted CI/artifact readiness review (2026-10-20)

No remote write, run dispatch, artifact download or credential inspection occurred.
`gh repo view`'s GraphQL default-branch field and REST contents/package endpoints
returned 403 with the configured token; this limits visibility, not proof of a
repository/package permission setting. `gh workflow list` identified `ci` as an
active workflow.

Read-only branch comparison:

- Local `HEAD` and `origin/main` both resolve to
  `848d8efa864c12d2168b4ccd79e32b97e0bde790`.
- The worktree has **92 modified/untracked paths**. Therefore neither HEAD nor
  origin/main contains the current packaging changes; no clean release candidate
  for those changes exists remotely.
- `git show origin/main:.github/workflows/ci.yml` confirms remote workflow source
  predates the local changes: it has only `gate`, no global `NO_LOCAL_ENV=1 ENV=ci`,
  no service-level GHCR credentials and no frontend/package jobs.
- Latest existing run is [39025588709](https://github.com/amsyrzmi/CSAV2/actions/runs/39025588709)
  for that same HEAD. It failed at **Initialize containers** before checkout and
  all workflow steps were skipped. Logs supplied no further cause. It produced
  **zero artifacts**. This does not prove the local credentials change fixed
  service-image pull access.
- GitHub's package metadata endpoint also returned 403 for the authenticated
  identity. GHCR package visibility and the explicit repository Actions read grant
  remain **unverified**; do not infer either public or private access from that 403.
- No remote run includes or validates the dirty frontend/package workflow, OCI
  exporter/metadata/checksum/upload steps or any candidate image.

**Next required decision before hosted validation:** the repository owner must
review/select the intended dirty-worktree changes for a clean candidate, approve
committing and pushing that exact selection (prefer a review branch/PR), and confirm
the GHCR package grants `amsyrzmi/CSAV2` Actions `read` access. Do not commit or push
the 92-path worktree wholesale, alter package visibility, dispatch CI, publish an
image or deploy without that explicit approval. Once a clean commit is approved and
on a review branch, observe its normal PR CI and verify gate/frontend/package jobs,
OCI metadata and checksummed artifacts. The package job is build/artifact-only;
promotion remains disabled.

## Earlier slice: synthetic database backup/restore rehearsal (2026-10-20)

Added `test_packaged_synthetic_backup_restore` to
[the packaged acceptance module](../../tests/test_packaged_console.py).
See [the restore contract and exclusions](../../deploy/RELEASE.md#disposable-synthetic-database-restore).
The source is the module's migrated synthetic Testcontainer; the destination is
a second fresh Testcontainer. No real dev, persistent staging or production data
is used, and no application/runtime/schema behavior changed.

The test takes a custom-format logical dump and a separate password-free roles dump.
Only the existing Testcontainer owner's duplicate CREATE statement is omitted;
role attributes, database owners, grants and RLS are restored. Both restore commands
fail on errors, with database restore in one transaction. App/identity passwords
are deliberately re-provisioned with fresh synthetic values, not copied role hashes.

Assertions compare exact migration versions/applied timestamps, two tenant IDs,
synthetic user email, membership role and audit actor/tenant/target fields. The
packaged runtime then checks domain/identity readiness and membership tenant
isolation, denied app reads of session secrets, denied identity reads of domain
data and audit mutation denial. Checkpoint table presence is explicitly checked.
Removing/restoring the exact 0360 ledger entry verifies readiness refusal/recovery
for simulated incomplete migration metadata; this is not a corrupted-archive drill.
Dump bytes stay in process memory and target-container temporary files, which
are removed by teardown. No backup file is added to the repository or uploaded.

Focused command:

```sh
PATH=/tmp/fourstacks-toolbin:$PATH NO_LOCAL_ENV=1 ENV=ci uv run pytest \
  tests/test_packaged_console.py::test_packaged_synthetic_backup_restore \
  -q -x --tb=short
```

**1 passed**, one existing Testcontainers warning, **291.16 s**.
The initial type check found two Docker output-union typing errors; added explicit
bytes guards and an explicit checkpoint-table assertion before the final full
check. No failed pytest restore attempt or runtime workaround occurred.

Final command:
`PATH=/tmp/fourstacks-toolbin:$PATH NO_LOCAL_ENV=1 ENV=ci make lint test`,
captured with `set -o pipefail` and a session-local log. Ruff/format (**310 files**),
Pyright (**0 errors**) and import boundary pass; **2,549 tests passed**,
136 existing warnings, **3,351.07 s** (55m51s), no failures/skips.
This covers the final type guards/checkpoint assertion, all three packaged tests,
unchanged load/latency tests and
`tests/test_migrate.py::test_make_reset_only_disposable_container` (passed).
No persistent reset occurred. Historical latency failures remain evidence;
this pass does not establish stable capacity.

Not established: actual browser login/session continuity after restore, checkpoint
replay, upload-file recovery, encryption-key/credential recovery, WAL/PITR,
RPO/RTO, persistent backup scheduling, public ingress or hosted CI/OCI acceptance.
All M6/live-provider gates remain. No environment file/secret inspection, persistent
database access/reset, service restart, publish/deploy, commit/push or DNS/tunnel
change occurred.

## Earlier slice: disposable staging bootstrap and storage rehearsal

Extended [the packaged acceptance module](../../tests/test_packaged_console.py)
with a second independent test. Fresh allowlisted image builds are shared within
the module, but bootstrap uses its own fresh Testcontainer/database, internal
network and upload volume. It does not reuse the identity test's migrated fixture.
See [the release contract](../../deploy/RELEASE.md#disposable-bootstrap-and-storage-rehearsal).

The test proves:

- Missing approval fails before reading nonexistent secret files; no domain
  relations are added to the fresh database.
- The baseline comes from the packaged Python image and is applied explicitly
  with the disposable database image's `psql`. No application-startup migration.
- Either incorrect owner or checkpoint DSN target is rejected before migrations:
  baseline relation state is unchanged and runtime/identity roles remain absent.
  Synthetic uppercase secret files exist only in container tmpfs.
- Explicitly approved packaged migration provisions all shipped SQL through 0360,
  identity login and `lg` checkpoint tables. Reapplication preserves exact
  migration versions and original applied timestamps.
- Packaged `bootstrap_console_admin.run` creates exactly one forced-change platform
  admin and bootstrap audit event; a second invocation is rejected. The synthetic
  password is passed through exec environment, never argv, and is absent from the
  bootstrap output. This tests the packaged async entry point, **not** interactive
  `getpass` prompting/confirmation.
- A task-owned root-owned upload volume rejects the non-root container's write.
  Even root exec in that capability-dropped application container cannot chown.
  A separate network-disabled initializer with only CHOWN changes the volume's
  directory ownership; the original UID/GID 1000 runtime then writes, reads,
  checks ownership and deletes a synthetic probe. All owned containers, volume,
  network attachment/network and database are cleaned up.

Focused validation:

```sh
PATH=/tmp/fourstacks-toolbin:$PATH NO_LOCAL_ENV=1 ENV=ci uv run pytest \
  tests/test_packaged_console.py::test_packaged_staging_bootstrap_and_storage \
  -q -x --tb=short
```

First attempt: **1 failed**, one Testcontainers warning, **233.10 s**. Docker's
archive API rejected writing into the read-only container, even at the tmpfs
target. The test now writes its synthetic files using an unprivileged process
inside explicitly UID-owned tmpfs; the runtime filesystem remains read-only.
Final focused attempt: **1 passed**, one existing Testcontainers deprecation warning,
**294.38 s**; focused Ruff/format/Pyright pass. No application, migration, frontend,
provider or persistent deployment behavior changed.

The first `PATH=/tmp/fourstacks-toolbin:$PATH NO_LOCAL_ENV=1 ENV=ci make lint test`
attempt passed lint and collected 2,548 tests, but was stopped before a final report.
Captured output reached `tests/test_load_acceptance.py .F`; exact failure detail,
timing and final totals are unavailable. Do not claim this run passed, attribute a
cause or erase it with a later passing run. Validation was restarted with retained
session-local output, without changing tests or latency thresholds.

Final recorded rerun used the same command with `set -o pipefail` and `tee` to a
session-local log. Ruff/format (**310 files**), Pyright (**0 errors**) and import
boundary pass; **2,548 tests passed**, 136 existing warnings, **2,148.51 s**
(35m48s), no failures/skips. Both packaged-console tests, all four load acceptance
tests and the disposable-only reset test passed. This covers final test source;
subsequent changes are documentation only. The interrupted load failure remains
recorded, with no established cause or changed acceptance threshold.

No persistent staging Compose execution, public ingress, real secrets or environment
files, owner credential mounting into gateway/frontend, service restart, publish,
push/commit, DNS/tunnel change, provider call or production connection occurred.
HTTP uploads/worker execution, backup/restore, hosted CI/OCI and live ingress remain
unverified. Existing M6 labels/gates and historical latency failures remain intact.

## Earlier slice: disposable packaged-console acceptance

Added [the packaged-console harness](../../tests/test_packaged_console.py).
It builds fresh Python/Next candidates from the existing allowlisted materializers
and uses the module's synthetic Testcontainers PostgreSQL, not persistent Compose.
The non-root, read-only application containers have no published ports and only a
new internal network. A test-local loopback HTTPS terminator forwards to the
frontend's private bridge address. The TLS client verifies the generated test
certificate; Secure cookies are not bypassed or downgraded. This requires local
Linux Docker bridge connectivity, not remote-daemon/Desktop acceptance.

Verified assertions include anonymous rejection; platform-admin login; forced
password change and session rotation/old-session rejection; exact Origin/CSRF
checks; tenant-admin, staff and reader boundaries; two synthetic tenants with
distinct orders and cross-tenant list/detail denial; logout; secret-free validation
errors; authenticated/anonymous legacy marketplace path 404s; and rendered M6
local-only/non-authoritative/unavailable warnings. Removing migration 0360, adding
an unsafe identity-domain grant, or removing an identity SELECT grant makes the
running gateway refuse readiness. Grants/ledger entries are restored in `finally`
blocks, and final readiness recovers. No runtime/source/API/migration behavior changed.

Validation chronology:

- The earlier acceptance attempt was interrupted without a completed result.
  Its one known candidate tag and explicitly inspected generated context directory
  were removed; unrelated temporary files, caches, images and services were left alone.
- First completed attempt:
  `PATH=/tmp/fourstacks-toolbin:$PATH NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_packaged_console.py -q -x --tb=short`
  ended with **1 setup error**, one existing Testcontainers deprecation warning,
  **375.98 s**. Both builds completed, but Docker did not publish a port on its
  internal-only network. The harness now uses private bridge addresses with no
  published application ports instead of weakening network isolation.
- Second attempt with the same command: **1 failed**, one warning, **144.45 s**.
  The harness expected 400 for a forged browser identity header; current source
  explicitly returns 403. Corrected that assertion to the exact implemented denial.
- Final focused attempt with the same command: **1 passed**, one existing
  Testcontainers deprecation warning, **154.43 s**, no skips.
  All role/readiness/route assertions above completed. Candidate tags, contexts,
  application containers, network attachment/network, TLS files and the database
  are torn down by the test's cleanup/Testcontainers.
- Final required check:
  `PATH=/tmp/fourstacks-toolbin:$PATH NO_LOCAL_ENV=1 ENV=ci make lint test`
  passed Ruff, formatting (**310 files**), Pyright (**0 errors**) and the import
  boundary; **2,547 tests passed**, 136 existing Testcontainers/missing-secret-directory
  warnings, **3,490.88 s**, no failures/skips. This covers the final harness,
  unchanged latency assertions and
  `tests/test_migrate.py::test_make_reset_only_disposable_container` (passed,
  not skipped). No bare persistent reset was executed. Historical latency failures
  remain below/in progress; one passing full run establishes no stable capacity.
- Documentation validation: **80 local targets** and balanced fences across the
  release contract, progress, LG15 and this handoff; `git diff --check` passed.

See [the release contract](../../deploy/RELEASE.md#disposable-packaged-console-acceptance)
for prerequisites and limitations. This is HTTP-client/server-rendered acceptance,
not authenticated JavaScript browser, Cloudflare/cookie-forwarding, DNS,
hosted Actions/OCI, persistent staging, uploads/worker or provider acceptance.
The existing frontend source and its allowlist were not changed. The attached
environment snapshot was not inspected. No push, commit, publish, deployment,
DNS/tunnel/provider change, existing-service restart or persistent migration/reset
was performed. M6 and live seller eligibility remain incomplete/gated.

## Earlier slice: repository-only packaging implemented

The CI/release-packaging continuation below has now been implemented, **not
deployed**. See [release packaging and staging proposal](../../deploy/RELEASE.md)
for the build contract and operator gates. The earlier host observations and
domain/provider planning below remain dated context, not fresh runtime evidence.

- [CI](../../.github/workflows/ci.yml) preserves the Python checks, ephemeral
  schema setup, Testcontainers, secret scan and coverage. Service-level GHCR
  credentials fix the pull-before-login bootstrap ordering. Global
  `NO_LOCAL_ENV=1 ENV=ci` prevents local development configuration loading.
- A separate frontend gate installs the exact reviewed lock into a fresh
  allowlisted directory with lifecycle scripts disabled, then tests transport,
  state and release origin rules, ESLint, typegen/TypeScript, standalone build
  and trace exclusions. The materializer and gate share one source allowlist.
- Build-only packaging waits on both gates, requires clean source and produces
  separate Python/frontend OCI archives, full-SHA revision labels/tags, commit
  timestamp/platform/origin, input-file checksums, BuildKit digest metadata and
  archive checksums. Permissions remain read-only; there is no push, publish,
  promotion or deployment job. Hosted workflow execution has not been tested.
- Digest-pinned Python/uv/Node bases, frozen production Python dependencies and
  `npm ci --ignore-scripts` are used. Explicit generated contexts exclude local
  configuration, copied API/backups, Git, uploads and installed modules. Root,
  frontend-checkout and historical Dockerfile contexts deny the whole checkout;
  disposable DB builds send only their Dockerfile. Symlinks fail closed.
  A final runtime-asset audit found the existing synthetic decision-case JSON;
  it is explicitly allowlisted and its packaged pure loader is verified offline.
- Python runs source from `/app` without an unlocked Hatch build dependency.
  The non-root runtime includes all shipped SQL and gateway/worker/delivery/KB
  commands. Gateway readiness uses Python `/readyz`, not unavailable `curl`.
  Next standalone includes its runtime dependencies, static assets and brand.
- The frontend artifact is origin-bound. Build and runtime must agree on the
  private `CSA_CONSOLE_API_ORIGIN`; its launcher refuses missing, invalid or
  mismatched origins before starting Next. Changing it requires a new artifact.
- The [staging proposal](../../deploy/compose.staging-proposal.yml) has an
  isolated database/volume/internal network, separate uppercase domain,
  checkpoint and identity secret targets, HTTPS origin/secure cookies, readiness
  dependencies and **no host bindings/external network**. Every service needs
  an explicit approval profile. Migration has a different profile plus an exact
  approval/target guard, never an application startup dependency. Baseline,
  identity bootstrap, volume ownership and release acceptance remain operator
  gates; no staging service or persistent migration was run.
- The gateway still mounts the old marketplace routes. Those routes are not
  the M6B callback; the frontend rejects their authorize/callback/webhook paths
  with 404 even with a session. Packaged-runtime checks confirm that boundary.
  Direct private access still reaches mounted backend routes: do not expose a
  gateway catch-all. No legacy OAuth authorization repair is claimed.
- Existing M6 local-only/unverified/setup-required and synthetic-demo labels
  remain intact. No live TikTok/Gmail/Calendar or authoritative inventory is
  enabled. Apex/`www`, DNS, tunnel, providers and old services remain untouched.

### Commands and outcomes for this slice

Commands run from the repository root; `PATH=/tmp/fourstacks-toolbin:$PATH`
selects the existing host-local Node 24.21.0/npm tooling, not a portable dependency
install. CI/container Node is pinned to 24.14.0. No environment-file contents were
read. The requested parent `../AGENTS.md` does not exist; root/frontend guidance
was read.

| Command | Outcome |
|---|---|
| `PATH=/tmp/fourstacks-toolbin:$PATH npm --prefix CSA_WebConsole/csa-console/web run check:isolated` | First attempt failed because local `node_modules/next` was missing. After the reviewed install below, passed 17 transport/routing tests + 2 state tests + 1 origin test, ESLint, typegen, TypeScript, standalone build and safe traces. |
| `PATH=/tmp/fourstacks-toolbin:$PATH npm --prefix CSA_WebConsole/csa-console/web ci --ignore-scripts --no-audit --no-fund` | Passed; 500 locked packages, lifecycle scripts disabled. Existing ESLint support-deprecation warning retained; no upgrade. |
| `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_release_context.py tests/test_health.py tests/test_migrate.py tests/test_console_contract_snapshot.py -q` | 18 passed, 1 Testcontainers deprecation warning, 79.09 s. Includes the real reset target bound only to a disposable fixture database. This preceded expanded pure packaging guards. |
| `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_release_context.py -q` | Final pure packaging/approval selection: 16 passed, 1.53 s; no DB, secret access or provider calls. |
| `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_release_context.py tests/test_decision_evals.py -q` | Final allowlist/resource regression: **27 passed, 2.36 s**, no warnings. An initial invocation used nonexistent `tests/test_decision_eval.py` and ran no tests (exit 4); corrected to the existing plural filename. |
| `NO_LOCAL_ENV=1 ENV=ci make lint` | Initial run found a new-test import-order issue; corrected with targeted formatting. Final pass: 309 formatted files, Pyright 0 errors, import contract kept. |
| `PATH=/tmp/fourstacks-toolbin:$PATH node CSA_WebConsole/csa-console/web/scripts/check-isolated.mjs --install` | First exact CI-mode run installed successfully and passed tests/lint/typecheck, then Next's build printed `Failed to type check` without a diagnostic. A later exact rerun passed all 20 tests, lint/types/build/traces after candidate image builds completed. Failure remains recorded; cause is unproven and no check was disabled. Both temporary trees were cleaned. |
| `NO_LOCAL_ENV=1 ENV=ci make lint test` | Passed lint and **2,545 tests**, 136 existing Testcontainers/missing-secret-directory warnings, **2,938.01 s**. No failures/skips, including unchanged latency checks and disposable-only reset. The run collected before the final Dockerfile-parent symlink regression and synthetic JSON allowlist fix; these have the final separate 27-test/lint pass above. No second full suite was run. |

Materialization and local build commands:

```sh
NO_LOCAL_ENV=1 ENV=ci python3 -m scripts.release_context /tmp/csav2-release-109dcb30-python
PATH=/tmp/fourstacks-toolbin:$PATH node CSA_WebConsole/csa-console/web/scripts/check-isolated.mjs \
  --context /tmp/csav2-release-109dcb30-frontend
DOCKER_BUILDKIT=0 docker build \
  --build-arg SOURCE_REVISION=$(git rev-parse HEAD) \
  --build-arg SOURCE_DATE_EPOCH=$(git log -1 --format=%ct) \
  -t csa-release-check-python:109dcb30 /tmp/csav2-release-109dcb30-python
DOCKER_BUILDKIT=0 docker build \
  --build-arg SOURCE_REVISION=$(git rev-parse HEAD) \
  --build-arg SOURCE_DATE_EPOCH=$(git log -1 --format=%ct) \
  --build-arg CSA_CONSOLE_API_ORIGIN=http://gateway:8080 \
  -t csa-release-check-frontend:109dcb30 /tmp/csav2-release-109dcb30-frontend
# Refresh the Python image after the final context-parent symlink guard:
NO_LOCAL_ENV=1 ENV=ci python3 -m scripts.release_context /tmp/csav2-release-109dcb30-python-final
DOCKER_BUILDKIT=0 docker build \
  --build-arg SOURCE_REVISION=$(git rev-parse HEAD) \
  --build-arg SOURCE_DATE_EPOCH=$(git log -1 --format=%ct) \
  -t csa-release-check-python:109dcb30 /tmp/csav2-release-109dcb30-python-final
# Refresh after auditing the existing synthetic JSON runtime asset:
NO_LOCAL_ENV=1 ENV=ci python3 -m scripts.release_context /tmp/csav2-release-109dcb30-python-assets
DOCKER_BUILDKIT=0 docker build \
  --build-arg SOURCE_REVISION=$(git rev-parse HEAD) \
  --build-arg SOURCE_DATE_EPOCH=$(git log -1 --format=%ct) \
  -t csa-release-check-python:109dcb30 /tmp/csav2-release-109dcb30-python-assets
```

All four image builds passed. Public base images were pulled by their recorded
versions to resolve the digests now pinned in the Dockerfiles. `docker buildx
version` / `imagetools inspect` initially failed because Buildx is unavailable on
this host; the existing legacy builder was used instead, with its deprecation
warning. The OCI exporter/metadata/upload path is **not locally verified**.
Final contexts were 1.730 MB Python and 563.2 kB frontend, not full checkouts.
These are **dirty-worktree candidates**, not releases of the HEAD label
`848d8efa864c12d2168b4ccd79e32b97e0bde790`.
Final local image IDs (not registry manifest digests):

- Python: `sha256:b1f9c65c527f61c354ae93cd4844eee58c299d8bebccf01e54cc65e81185fdd2`.
- Frontend: `sha256:325f8e29f07c612a560334798de5a07e3f83217ebd3c290380eb44d8d95eaeab`.

Transient `docker run --rm --network none --read-only` smoke checks passed.
Python imported gateway/worker/delivery/KB modules, checked UID 1000, all shipped
SQL through 0360 and absent environment/reference trees, without a DB connection.
The final refreshed image also successfully loaded the existing synthetic decision
cases through the pure loader; no evaluator/provider execution was invoked.
Frontend used container-only loopback (no host port), tmpfs cache and UID 1000:
launcher startup, baked rewrite destination, login/static/brand 200, protected
navigation redirect and all four legacy marketplace path denials passed.
The default launcher with `CSA_CONSOLE_API_ORIGIN=http://different:8080` exited
1 before startup, as required. The helper server/container was terminated;
no existing service was restarted.

Compose structure validation passed, using no environment file or actual secrets:

```sh
CSA_STAGING_PYTHON_IMAGE=csa-python@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
CSA_STAGING_FRONTEND_IMAGE=csa-frontend@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
CSA_STAGING_CONSOLE_ORIGIN=https://staging.example.test \
docker compose --env-file /dev/null -f deploy/compose.staging-proposal.yml config --quiet
```

Workflow YAML/read-only permissions/gate dependencies, final script syntax,
focused frontend-script lint and **299 local documentation links across nine
maintained files** passed structural checks; `git diff --check` passed. An initial
link-check script missed whitespace around an existing multiline Markdown target;
the corrected whitespace-aware check passed without changing that link. A focused
ESLint invocation from the wrong directory found no matching files; rerunning from
the adopted package directory passed. This is not hosted Actions acceptance, registry
pull permission proof, byte-identical double-build evidence or staging deployment
acceptance. No registry publishing/import/promotion, HTTPS identity/browser matrix,
proxy cookie forwarding, backup restore, migrations on a persistent database,
real ingress connectivity, live provider or landing-page HTTP smoke check was run.
No push, commit, SSH, DNS/tunnel/provider change or service replacement occurred.
Source still expects 0360; persistent dev0300 is only the last recorded approval.
Earlier latency failures remain in [progress](../PROGRESS.md); packaging does not
change thresholds or establish capacity.
The four explicitly task-owned build-context directories were removed after
validation; candidate images remain local, unpublished. Existing temporary
directories, Docker services and unrelated worktree changes were not cleaned up.

## Original domain-planning scope (historical)

This is the current continuation entry point. Read [AGENTS](../../AGENTS.md),
[progress](../PROGRESS.md), [LG15](../langgraph-rebuild/LG15_console_turnover.md),
the [M6 roadmap](./2026-10-15-console-source-migration-plan.md), and
[LG09](../langgraph-rebuild/LG09_cutover_ops_cloud.md). The
[M6B verifier handoff](./2026-10-18-m6b-verifier-next-session.md) is retained as dated
subsystem evidence, not the current schema/deployment instruction.

The user selected:

- Preserve the existing landing page on `fourstacks.my` and `www.fourstacks.my`.
- Plan the CSA console at `https://console.fourstacks.my`.
- Document a gated CI/CD pipeline and next implementation prompt, not implement or
  deploy a pipeline in this documentation slice.
- Google Gmail and Calendar are intended future CSA integrations, not an instruction
  to alter existing n8n integrations. Calendar should eventually support relevant dated
  business workflows; this does not authorize automatically exporting every date.

Preserve the dirty worktree, including broad console, ecommerce, inventory and migration
changes. Do not reset, stash, restore, remove unrelated files or edit `ui-guide/`.
No live infrastructure, DNS, provider settings, services, environment files or persistent
database were changed or inspected in this slice.

## Pre-packaging source versus reported host state (historical)

| Area | Evidence and implication |
|---|---|
| Source schema | [Gateway](../../apps/gateway/main.py) expects every shipped SQL migration and maximum `0360`; [0360](../../db/migrations/0360_inventory_identity.sql) is identity-only inventory scaffolding. |
| Persistent dev | Last approved recorded state is `0300`. Not probed in this task; do not call it the current observed version or start the latest build against it without reviewed forward migrations. |
| Existing host | User-supplied `docker ps` shows landing service `fourstacks-web` on loopback 8088, `cloudflared`, legacy `csa-platform` DB/MCP/KB/embeddings, n8n and LiveKit. Running containers alone do not prove traffic, dependency usage or safe retirement. |
| Port conflict | The supplied snapshot binds another application to host 8080, while [production Compose](../../deploy/compose.yml) tries loopback 8080. Select a verified free port or private network route; do not run this manifest unchanged. |
| Runtime uncertainty | No new CSAV2 gateway/frontend containers appear in that snapshot. Host processes, reverse-proxy rules, tunnel ingress, Docker networks and image digests were not supplied or inspected; deployment status remains unknown. |
| Temporary databases | Two random-name Postgres containers in the snapshot could be test infrastructure, but ownership/lifecycle is unverified. Do not delete them by name or assume they are abandoned. |
| Production Compose | Gateway/worker/delivery plus automatic migration dependency; no adopted Next.js frontend, console identity DSN/origin wiring, or complete upload/KB-worker packaging. It omits required `PUBLIC_BASE_URL`, mounts lowercase secrets while settings are case-sensitive, and leaves `TURN_ENGINE` at its `echo` default. It targets the external legacy `csa-net`/`postgres` stack, not a new isolated staging database. |
| Image | [Dockerfile](../../deploy/Dockerfile) builds only the Python app, not the Next.js console. Its runtime does not install `curl`, although Compose's gateway health check invokes `curl`; `/healthz` also proves only liveness, not schema/identity readiness. |
| CI | [Existing CI](../../.github/workflows/ci.yml) already runs Python lint/types/import checks, schema setup, pytest, secret scan and coverage. No frontend gate, application image publishing or CD is implemented there. |

The original proxy attachment was unavailable; the user subsequently supplied port and
Compose status summaries, not proxy configuration. Obtain only sanitized hostname/path
routes, network mode and service-to-network membership before choosing the origin address.
Never request a raw `docker inspect`, rendered secret-bearing Compose output, tunnel
token, process environment or environment-file dump.

## Domain and proxy plan (not applied)

1. Keep apex, `www`, landing service and all unrelated hostnames unchanged.
2. Add an explicit `console.fourstacks.my` Tunnel published-application route and
   matching proxied DNS record to the existing tunnel, after verifying its management
   mode and ingress configuration. A specific hostname avoids relying on the supplied
   wildcard A record; DNS alone does not configure an ingress route.
3. The wildcard presently points to an origin IP, while apex/`www` use the tunnel.
   Do not replace/delete that wildcard: other applications may depend on it.
4. Route the console hostname to the adopted frontend; browser `/console/v1/*` stays
   same-origin and is forwarded to the private gateway. Deny unsupported legacy routes;
   do not tunnel the entire gateway as a public catch-all.
5. `localhost` from a bridge-networked `cloudflared` container means that container,
   not the host. Choose a reviewed shared-network service address or existing
   host-proxy route after confirming topology. Do not expose database/admin ports.
6. Preserve HTTPS, Host/Origin, cookies and CSRF headers through the proxy. Trust
   forwarded headers only from the actual proxy/network, not arbitrary Internet peers.
   Do not cache authenticated pages or API responses. Validate multipart upload limits,
   timeouts and redirects. An edge login challenge must not block machine callbacks.
7. Keep external webhook/callback ingress separate from human console access rules.
   A future `hooks.fourstacks.my` is an option, not a selected/registered endpoint.
   Only expose explicitly implemented and reviewed callback paths.
8. Smoke-test landing apex/`www` before and after any separately approved rollout.
   An unavailable CSA release must not take down the landing page.

## URL and configuration checklist

Do not globally search-and-replace an environment file or regenerate credential keys.
The app, Compose and frontend do not all load configuration the same way:
[Settings](../../csa/core/settings.py) reads `.env.local` only in development unless
`NO_LOCAL_ENV=1`; production requires process configuration/secrets. Compose interpolation
and Next.js automatic environment loading are separate mechanisms.

| Setting/surface | Required planning change |
|---|---|
| `CONSOLE_ALLOWED_ORIGIN` | Exact `https://console.fourstacks.my`, no path; match the actual browser origin. |
| `CONSOLE_COOKIE_SECURE` | Keep `true` for public HTTPS. Recheck forwarded cookie headers, login/logout, CSRF, invitation/reset flows and session expiry. |
| `CSA_CONSOLE_API_ORIGIN` | Server-only private gateway origin, not the public landing URL and never `NEXT_PUBLIC_*`. Next rewrites evaluate it during build; server-side calls also need it at runtime. Build/release strategy must account for both. |
| `PUBLIC_BASE_URL` | Public origin for supported backend-generated links; choose after path routing is decided. Current uses include invoice links and the legacy TikTok OAuth builder. Changing it neither publishes a route nor enables the supported M6B flow. |
| Database URLs/key | Do not change DB host/role/schema or encryption keys merely because the domain changes. Domain, checkpoint and identity DSNs retain their distinct roles and isolated staging target. |
| OAuth registration | Register only the exact implemented HTTPS callback, including path and trailing-slash semantics, with each provider. Keep development/production clients separated; authorize scopes and consent per environment. No final CSA Google callback path exists yet. |
| Google app branding | Review owned-domain verification, authorized domains, homepage/privacy/terms URLs, audience/test users, scopes and any verification/security-assessment requirements before public OAuth. Google OAuth is not implemented console SSO. |
| Existing webhooks | Inventory Telegram/provider webhook URLs, Pub/Sub push endpoint and audience, allowed hosts, callback allowlists and voice callback consumers. Change only migrated consumers after approval; preserve n8n/LiveKit routes. |
| Operational references | Review non-secret deployment configs, monitoring URLs, health probes, frontend build config, email/link templates and external allowlists. Remove old URLs only after cutover and recovery validation. |

### TikTok caveat: a legacy callback exists

[Marketplace router](../../csa/channels/marketplace/router.py) is mounted by the gateway.
It includes `/api/v1/channels/tiktok_shop/oauth/authorize` and
`/api/v1/channels/tiktok_shop/oauth/callback`, using the older
[OAuth helper](../../csa/channels/marketplace/oauth.py).
Those are **not** the supported M6B verifier/completion flow.
The newer [verifier](../../csa/agents/ecommerce/authorization_verifier.py) and
[completion](../../csa/agents/ecommerce/connections.py) compose only in synthetic tests;
no runtime/default app-material/key source or supported console callback is registered.

Do not register/expose that legacy callback merely by changing `PUBLIC_BASE_URL` or a
TikTok redirect URL. Review/contain legacy marketplace routes before public exposure.
Seller/app eligibility, approved scopes, exact shop binding, immutable app revisions,
safe callback design and separately approved live acceptance remain gates.

## Gmail Pub/Sub versus Calendar

The existing Gmail topic Publisher principal should be
`gmail-api-push@system.gserviceaccount.com`, scoped to the required topic. That grant
permits Gmail to publish notifications; it does not authorize mailbox reads or grant
CSA's consumer permission to pull/receive them.

Gmail still needs user OAuth/consent, encrypted token lifecycle, topic/subscription
configuration, a consumer and reliable history synchronization. For authenticated Pub/Sub
push, validate the push JWT issuer/signature/audience and intended service identity;
configure required service-agent token-creation permissions separately from Publisher.
For pull, grant the consumer only the needed subscriber permission. Persist/deduplicate
history progress, handle expired history with resync, acknowledge only after durable
acceptance, and renew mailbox `watch` before expiration (at least every seven days;
Google recommends daily). No Gmail consumer/watch runtime was found in active CSA source.

**Do not add a guessed Calendar Publisher principal to that Gmail topic.** Calendar
API's standard `events.watch` uses HTTPS `web_hook` notification channels, not Gmail's
Pub/Sub delivery. Calendar requires its API enabled, per-user/calendar authorization,
a callback, persisted channel/resource IDs, channel-token checks, incremental
synchronization and renewal/replacement before channel expiry. Notifications signal a
change; fetch authoritative event data. A service account is useful only with explicit
calendar sharing or approved Workspace delegation, not as a universal user-calendar grant.

Begin future Calendar implementation with one opt-in booking/calendar workflow,
read-only availability first. Then separately authorize creates/updates/cancellations,
attendee/email effects, idempotency and conflict handling. Define tenant/calendar mapping,
date-only versus instant semantics, IANA time zone, daylight-saving handling, ownership,
privacy and source-of-truth. Orders, reminders, subscriptions and staff tasks do not become
Calendar events automatically simply because they have a date.

## Incomplete-feature presentation and release boundaries

Deployment is independent of feature completion. Publish only an explicitly accepted
release; do not equate `main`, image-build success or latest schema with production approval.

| Area | Honest public console state |
|---|---|
| M0-M5 | Previously validated slices, subject to deployment-specific role/session/readiness smoke checks. Last recorded acceptance is guarded dev, not a public-production sign-off. |
| M6A | Staff-only local records; label "Local data - no marketplace synchronization". |
| M6B | Stored metadata is not a live connection check; Connect/Reconnect remain disabled with eligibility/setup explanation. |
| M6C | Seller write/fulfil/refund operations unavailable; no clickable controls implying execution. |
| M6D | Prototype and disposable persistence tests, not an authoritative ledger or stock sync. No writable inventory UI. |
| M6E / Google | Preparation/local-read support only where implemented; Gmail/Calendar execution explicitly "Not available yet", not "Connected". |
| Synthetic demo | Persistent visible "Synthetic demo" label; page-local state only, no provider effects or saved business records. |

These M6 local/demo/unavailable distinctions already exist in
[BusinessWorkspace](../../CSA_WebConsole/csa-console/web/components/console/BusinessWorkspace.tsx).
This slice changes documentation only, not UI. A release must test that unfinished
features fail closed in the backend as well as hiding/disabling controls.

M6D's next development step remains inventory ownership/grants and real identity
revocation lock ordering, followed by applicable Stage 1 acceptance and separately
reviewed writable-schema work. Domain hosting does not waive those gates.

## Original gated CI/CD plan (packaging now implemented above; deployment disabled)

1. **PR CI, GitHub-hosted:** preserve Python checks, disposable PostgreSQL and exact pins;
   add adopted frontend's reviewed install and isolated transport/state/lint/type/build
   checks. No production environment files, keys, provider traffic or host Docker socket.
   Keep latency failures visible; do not relax thresholds or omit them to call CI green.
2. **CI bootstrap:** service containers start before workflow steps. The current private
   GHCR database image cannot rely on a later `docker/login-action` to authorize its
   initial pull. Confirm image visibility or configure service image pull credentials.
3. **Build:** after gates pass, build separate Python and frontend images from a clean
   reviewed commit, tag with commit SHA and record immutable digests. Add reviewed
   `.dockerignore`/build contexts before building: none currently exists at repo root.
   Do not upload environment files, legacy dumps, uploads or credentials as build context.
   Publish only trusted-branch builds with narrowly scoped package-write permissions;
   never deploy or publish privileged artifacts from untrusted PR code.
4. **Staging:** use a separate database, secrets, upload storage, hostname and synthetic
   fixtures. Add portable frontend/identity/KB packaging and readiness-aware health checks.
   Do not reuse the legacy `csa-platform` database just because Compose points to it.
5. **Acceptance:** test login/CSRF/roles/tenant isolation, safe unfinished-feature labels,
   disabled provider mutations, upload boundaries and landing-page preservation. Publish
   the exact build/schema/readiness manifest and checks, not just a green login page.
6. **Promotion:** protected environment plus explicit approval of a named digest/release;
   verify GitHub plan/repository visibility supports required approval protections.
   If unavailable, use an explicit operator release gate, not pretend protection exists.
   Serialize deployments; do not cancel a running migration.
7. **Migrations:** separate deliberate step with backup/restore evidence, compatibility
   preflight and scoped credentials. Do not attach automatic production migrations to
   routine app startup. A source-0360 artifact must not be promoted against recorded
   dev0300. No reset or automatic DB downgrade.
8. **Release/rollback:** retain previous image digests, switch only new CSA routes after
   readiness and smoke tests, monitor, and roll back application routing only when
   schema-compatible. Inventory authority rollback requires its own audited cutover.
   Leave apex/`www`, n8n, LiveKit, old databases and unrelated services untouched.

No SSH/cloudflare/deployment credentials are requested now. Do not run untrusted CI
on a self-hosted runner attached to this shared production host. An eventual deployment
agent must be constrained to the new CSA stack, not broad host administration.

## Original documentation-slice evidence and Markdown reconciliation (historical)

This slice is documentation-only: no lint/test suite, database, service or deployment
command is needed to validate prose. A read-only `python3` check validated **307 local
file/folder links across 13 maintained Markdown files**; `git diff --check` passed.
The first link-check attempt used unavailable `python` (exit 127); the same check
succeeded with the existing `python3`, without installing dependencies. Fragment
anchors and external-site behavior are not implied by the local file-link count.
Prior M6D code evidence remains:

- `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_inventory_count_transactions.py -q`:
  **29 passed, 1 existing Testcontainers deprecation warning, 43.73 s**.
- Combined count/contracts/domain/persistence selection: **1,457 passed, 1 warning,
  114.59 s**; `NO_LOCAL_ENV=1 ENV=ci make lint` passed.
- That is focused evidence, not a new full-suite, live-provider or deployment pass.
  Historical full-suite latency failures and earlier passing runs remain in progress.

Updated current pointers, stale schema claims and old "latest" statements in maintained
entry/reference documents. Corrected the architecture diagram's ingress ordering.
Retained dated M4/M5/M6B handoffs, reviews, phase designs and acceptance results because
they contain distinct decisions/evidence; no verified redundant Markdown required
deletion. Historical plan commands are not authorization to deploy.

Official references consulted for the hosting/integration checklist:

- [Cloudflare Tunnel DNS](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/dns/)
- [Google web-server OAuth](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Gmail push notifications](https://developers.google.com/workspace/gmail/api/guides/push)
- [Calendar push notifications](https://developers.google.com/workspace/calendar/api/guides/push)
- [GitHub deployment environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)

## Next bounded task and copy-ready prompt

Historical pre-merge continuation: use the current next task and prompt in
[the corrected-listener section](#latest-slice-corrected-inherited-tcp-listener-2026-10-20)
instead. The following preserves the earlier PR #1 publication instructions.

Draft PR [#1](https://github.com/amsyrzmi/CSAV2/pull/1) targets `main` from
`review/domain-release-2026-10-20`; no duplicate PR is needed. On browser-slice
head `3979da55b72f11f44ac678ae6130b1020c79a1ec`, both the PR and push workflows
passed all jobs, including Gitleaks, policy branch coverage and package. The
earlier Gitleaks 403 is resolved by the job-scoped `pull-requests: read`
permission. This handoff and `docs/PROGRESS.md` now record that result in a
documentation-only follow-up; wait for hosted checks on its updated PR head
before merging. The user, not this task, will perform the merge. No publish,
promotion or deployment.

The final local `make lint test` run passed: **2,550 tests**, including all four
packaged-console tests and the disposable-reset acceptance test; exact command,
warnings and duration are recorded above. `uv lock --check`, the documentation
link/fence checker (**45 Markdown files, 409 local targets, zero errors**) and
`git diff --check` pass. The browser acceptance, pinned Playwright dependency
and lock, CI browser installation and minimum Gitleaks permission fix are
committed in `3979da5`. This follow-up reconciles the stale pre-push status in
this handoff and `docs/PROGRESS.md`; the documentation links/fences and
whitespace are validated. It is documentation-only and excludes the two
pre-existing deletions under `docs/reviews/`. Hosted CI must rerun for the
updated PR head. The user will review and merge PR #1 after those checks pass;
do not merge it here. No registry publication, promotion or deployment.

This synthetic browser pass closes only the JavaScript-enabled packaged M2
interaction gap described above. It does not establish exact hosted-browser
identity, public proxy/cookie forwarding, worker/provider behavior, persistent
staging, LG15 performance, or LG09 pilot/SLO, loss-free rollback, rotation,
real-backup recovery and n8n-retirement gates. No live or persistent operation
is authorized.

```text
Continue from docs/handoffs/2026-10-19-domain-release-next-session.md.
The documentation audit (`f46dba0`) and synthetic M2 browser acceptance
(`3979da55b72f11f44ac678ae6130b1020c79a1ec`) are pushed to
review/domain-release-2026-10-20. The browser-slice PR and push runs
39455808313 and 39455797430 both passed frontend, pytest, Gitleaks, policy
coverage and package. This handoff and docs/PROGRESS.md contain a docs-only
status reconciliation; it excludes the pre-existing docs/reviews deletions.
Check hosted CI on the updated docs head before merge; the user will merge the
PR after reviewing it. Do not merge, publish, promote or deploy.
After merge, the smallest bounded acceptance task is to define and execute the
LG15 full-suite performance acceptance with its existing thresholds and
historical failures preserved; first read the current progress and LG08/LG15
criteria. Do not relax thresholds or imply one passing run establishes stable
capacity.
Use synthetic disposable Testcontainers only with NO_LOCAL_ENV=1 ENV=ci; never
run bare make db-reset. No production/env-file access, persistent DB change,
service restart, real provider request, infrastructure/DNS/tunnel/webhook/upload,
registry publication or deployment. Preserve fourstacks.my/www and promotion
disabled. Source expects 0360; persistent 0300 is dated evidence, not a fresh
probe. Keep prior failures and secret-scan scope limitations explicit.
```
