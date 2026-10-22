# Progress

Staging image publication workflow (proposed, 2026-10-22): PR #12 merged as
`12c74dd` (push CI 39826917861 and PR CI 39826987627 passed). Branch
`ci/staging-image-publication` adds a manual, `staging-images`-environment-gated
workflow that republishes the exact archive of one successful push-to-main CI
run to GHCR by full SHA with preserved digests, then fails unless the registry
digests match; no rebuild, no `latest`, `ci.yml` unchanged. Ten static and
synthetic OCI-layout tests pass locally; no dispatch, registry or GitHub API
call was made. Next gates: workflow PR review/CI/merge, owner-created protected
`staging-images` environment, then a separately approved dispatch of CI run
39754225130 before its artifact expires 2026-10-29. See the
[workflow contract](../deploy/RELEASE.md#staging-image-publication-workflow-proposed-not-yet-run)
and [active handoff](./handoffs/2026-10-19-domain-release-next-session.md).

Staging connector configuration and exact-image mount probes: PR #11 merged as
`7cdc1562a33bf3a33c4561e53d26263a6a08f731`. Post-merge CI
39754225130 passed gate, frontend and package, including Gitleaks. The current
review artifact was downloaded and inspected offline with explicit approval:
GitHub artifact digest and every internal `SHA256SUMS` entry verified; both OCI
manifest/config digests, source revision, amd64 platform and runtime users were
verified. Python runs as `1000:1000`; frontend's configured `node` resolves to
`1000:1000`. Exact artifact identities and limits are in the
[release evidence](../deploy/RELEASE.md#pr-11-merged-artifacts-identity-verification-2026-10-22)
and [active handoff](./handoffs/2026-10-19-domain-release-next-session.md).

The exact reviewed Python/frontend OCI archives were imported and
tested with explicit approval using synthetic files and network-disabled
containers. Python `Settings` loaded its expected secret files as UID/GID 1000;
the five migration files were readable without invoking migrations. The
frontend passed UID, baked-origin/mismatch, read-only-root, writable-tmpfs and
no-secret-mount checks. One initial frontend probe used an incorrect baked-file
path and failed; it was corrected to the Dockerfile's `/app/console-api-origin`
location and passed. Containers, exact imported image tags, synthetic fixtures
and temporary artifacts were removed. See the
[artifact identity](../deploy/RELEASE.md#pr-11-merged-artifacts-identity-verification-2026-10-22)
and [runtime evidence](../deploy/RELEASE.md#exact-image-synthetic-mount-probes-2026-10-22).

These probes did not start the application, Compose, database, tunnel or any
staging service. They establish no Compose secret runtime, database readiness,
network/ingress or end-to-end agent acceptance. The staging proposal still lacks
worker, delivery and KB consumers and does not enable real provider delivery.
Next prerequisite: select immutable Python/frontend registry image digests for
the proposal. The checked artifacts are review archives, not registry images;
publication/import into a registry or deployment needs separate approval. No
real secret, staging database, tunnel, DNS, route, Caddy, service or deployment
action occurred.

Read-only registry metadata check (2026-10-22): the GitHub Packages list API
returned HTTP 403 for the current token; candidate GHCR manifest HEAD requests
returned HTTP 401, and anonymous token requests returned HTTP 403. These
responses do not prove that matching packages are absent. Credentials were not
changed; no image was downloaded or published. A metadata lookup using an
already-authorized read-only identity is needed before selecting an existing
registry digest; publishing remains a separate approval gate. The user then
ran the metadata lookup under their authorized `gh` identity: the container
package list returned no `csa-python`/`csa-frontend` entries without error, and
both package-version lookups returned HTTP 404 `Package not found`. No matching
registry image exists in the `amsyrzmi` namespace; the verified CI archives are
the only artifacts. Next gate: separately approved registry publication.

Staging connector probe (2026-10-22): PR #9 merged as `97c9172` (PR CI
39730060082 and branch CI 39730054197 passed). The user resumed the staging
lane. The dedicated connector candidate
`cloudflare/cloudflared:2026.10.3@sha256:faa8e0a1...ff39` runs as UID 65532
with no root entrypoint; offline `--network none` probes with a fake token file
showed `65532:65532` `0400` is readable while UID-1000-owned `0400` is denied.
No connector, real token, tunnel, DNS, dashboard, Caddy or service change. See
[RELEASE.md](../deploy/RELEASE.md#dedicated-connector-image-and-token-file-probe-2026-10-22)
and the [active handoff](./handoffs/2026-10-19-domain-release-next-session.md).

Customer-service routing/grounding/handoff goldens (2026-10-22): PR #8 merged
as `b2dc7a2`; PR CI 39719424850, branch CI 39719295898 and post-merge main
CI 39725222410 passed (freshly checked). Local main was fast-forwarded without
touching unrelated deletions. `test/customer-service-goldens` adds four synthetic
real-worker cases over existing KB/ticket/escalation agents, with test-only seed/
observation support. No runtime, API, frontend, migration or dependency changed.

Final focused command covering all golden cases, KB tests and staff-path tests:
**71 passed, 63 existing warnings in 94.12 s**, no skips/deselections.
Repository lint/types/import checks passed. The initial four-case run was
**2 passed / 2 failed** because core-only seeding did not register/enable KB;
the synthetic fixture now explicitly registers/enables it with the current schema.
See [LG08 evidence and limits](./langgraph-rebuild/LG08_testing_seed_evals.md#customer-service-evaluation-expansion-2026-10-22).
No live model/TEI/provider requests, delivery worker, persistent database action or
deployment. Scripted model verdicts prove orchestration/guard behavior, not
independent model accuracy or multilingual quality. Full hosted CI remains the
merge gate; full local/load/reset checks were not rerun.

Customer-service inbox browser acceptance (2026-10-22): PR #7 merged as
`650bc08`; its PR CI 39711968531, branch CI 39711862612 and post-merge main
CI 39713659882 all passed (freshly verified). Local main was fast-forwarded
without touching the two unrelated review-document deletions.
The isolated `test/customer-service-browser` branch adds one real-session
synthetic packaged inbox scenario, with no application/frontend/API/schema change.
It proves takeover/release, committed human-reply lost-acknowledgement recovery,
approve/edit/reject/expired reply decisions, settled-action denial of new delivery,
role/tenant/CSRF/private-sandbox boundaries and exact durable counts.
See [M5 acceptance evidence and exclusions](
./handoffs/2026-10-17-m5-telegram-inbox.md#packaged-customer-service-browser-acceptance-2026-10-22).

Focused inbox backend regression: **14 passed, 1 existing warning in 102.20 s**.
Final packaged browser: **1 passed, 1 existing warning in 215.34 s**.
Repository lint/types/import checks passed. Initial synthetic fixture/locator
failures are retained in the linked evidence. All five customer deliveries remain
pending: no delivery worker, Telegram request, model, persistent migration,
shared-service restart or deployment. Full local/load/reset checks were not rerun,
per the user's focused-local/full-hosted-CI policy. This is inbox acceptance,
not full customer-service routing/grounding/language or live-provider completion.

HR workspace guidance (2026-10-22): PR #6 merged as `148c03e`; post-merge main CI
39700139073 passed (freshly verified). The bounded follow-up adds an ephemeral
current-grant directory/training guide to the staff workspace and fixed HR deep
links. HR retains session/grant/resource authority; no employee records, new
mutations, model calls, workspace-history changes or migrations are added.
See [HR evidence](./hr/PROGRESS.md#workspace-hr-guidance) and the
[active handoff](./handoffs/2026-10-19-domain-release-next-session.md).

At the user's request, use focused local validation through customer-service,
subscription and interactive-voice completion, with broader checks for shared
runtime changes and full hosted CI retained as the merge gate. No CI checks or
acceptance thresholds were disabled. This slice: **198 affected tests passed in
189.28 s**, the packaged HR browser scenario **1 passed / 1 existing warning in
227.78 s**, repository lint and isolated frontend checks passed. Initial test
expectation/selector failures are recorded in HR evidence. Full local pytest,
load and disposable-reset checks were not rerun; earlier full-suite evidence is
historical, not final-patch coverage. No deployment or persistent action occurred.

HR directory/training browser acceptance (2026-10-21): PR #5 merged as
`cd1d085`, local main was fast-forwarded without altering unrelated deletions,
and post-merge CI 39666475261 passed. The user installed missing Chromium system
libraries; a synthetic browser launch passed. The bounded follow-up adds one
real-session packaged HR browser scenario: field grants, role/tenant/CSRF denial,
current-grant revocation, training submission/human confirmation, and an exact
same-key/body retry after a committed draft loses its acknowledgement. Durable
request/receipt/audit/confirmation counts are checked. Browser egress is blocked.
See [HR evidence and exclusions](./hr/PROGRESS.md#directorytraining-browser-acceptance).
Final `NO_LOCAL_ENV=1 ENV=ci make lint test`: all lint/type/import checks passed;
**2,903 passed, 136 warnings in 2,474.64 s**, no skips/deselections, including
both packaged browser cases, all load tests and disposable reset. Only tests/docs
changed; no persistent migration, deployment, live model or microphone use.

HR integration review (2026-10-21): PR #4 merged as `bb197ae`; its main CI
39638334024 passed and local main was fast-forwarded with the two pre-existing
review-document deletions preserved. Developer B's uncommitted HR implementation
was copied into `integration/hr-foundation`, reconciled with workspace A1 and
reviewed without modifying B's worktree. Source now expects schema 0410 on that
branch, not in any persistent database. Both workspace and HR routes remain.
See [HR scope, validation and remaining gates](./hr/PROGRESS.md) and
[setup boundaries](./hr/SETUP.md).

Integration fixed identity-test cleanup for HR foreign keys without changing
runtime protection. Full `NO_LOCAL_ENV=1 ENV=ci make lint test`: lint passed;
**2,901 passed / 1 failed, 136 warnings in 2,745.14 s**. The only failure was
packaged M2 browser startup because host Chromium lacks `libatk-1.0.so.0`.
All four load cases and the disposable-only reset test passed. The isolated
frontend check passed. Local full acceptance is not green. Hosted PR CI
39660171469 and push CI 39659967703 passed on `5ac7387`: **2,902 passed,
136 warnings in 785.08 s**, policy coverage 100%, all gate/frontend/package jobs
green. Only evidence documentation changed afterward. PR #5 is ready for review
subject to final-head checks and the user's merge; no deployment,
persistent migration, service activation or live HR/provider request occurred.

Workspace A0/A1 merged (2026-10-21): PR #3 merged to `main` as `b3b84c7`
(feature commit `c860bcd`). It adds the owner-private `/console/v1/workspace`
API, `/workspace` and `/portal/workspace` pages, deterministic page guidance,
one tenant-scoped order read and migration 0370 (RLS, no FORCE); gateway
readiness now expects schema 0370. No HR, voice, mutation or provider call.
Details: [A1 handoff](./handoffs/2026-10-21-workspace-a1-implementation.md).
Evidence: local lint passed; local full suite had 1 known load-latency failure
plus 4 Node-PATH errors; local browser acceptance could not run (host Chromium
missing `libatk-1.0.so.0`). PR CI run 39607346826 first failed only
`test_real_ingress_50rps_and_durable_queue[asgi]` (`scheduled_ack_p95_ms`
243.7 vs `<200`, 2644 passed); the rerun of the failed job passed. Push runs
39607088205 (`c860bcd`) and 39631416960 (`b3b84c7`) passed. The load threshold
was not changed. Open: replace the unsalted SHA-256 workspace `_fingerprint`
with a keyed HMAC (key source/rotation undecided); stabilize or quarantine
the flaky load assertion with approval. Persistent dev was not migrated.

Product planning priority change (2026-10-21): the user requested business web
chat, HR, refined customer service and interactive voice as the first four
priorities, with Teach-a-task last. The
[two-developer roadmap](./handoffs/2026-10-21-workspace-agents-roadmap.md)
records proposed domain grouping, permission/workflow gaps, staged acceptance
and independent
[workspace](./handoffs/2026-10-21-developer-a-workspace-prompt.md) /
[HR](./handoffs/2026-10-21-developer-b-hr-prompt.md) continuation prompts.
This is planning, not newly implemented features or authority expansion.
The user confirmed internal staff workspace chat, directory/training as the
first HR workflow, and staff-browser voice first. HR grants/field/approval policy
and live voice consent/languages/retention remain explicit decisions.
Next is the bounded authority/ownership contract decision, not staging rollout.
Existing sandbox restrictions remain. Release gates and the prior evidence
below remain applicable; no feature code, migration or service changed here.

Operational follow-up (2026-10-21): filtered Docker metadata identified six
containers with Testcontainers labels and the test-fixture image. With explicit
user confirmation/approval they were stopped; all six report exited and retain
their database volume mounts. No container/volume was deleted or pruned. The
shared Cloudflare connector and persistent production/dev databases remained
running without restart. The cause of missing automatic cleanup is unproven.

Local staging Compose design validation (2026-10-21): added
[compose.staging-local-proposal.yml](../deploy/compose.staging-local-proposal.yml)
with external file-backed secret paths, explicit profiles, and isolated `data`,
`app` and frontend-only `edge` networks. Added
[synthetic Compose checks](../tests/test_staging_compose.py); the focused command
passed **5 tests in 0.88 s**. `NO_LOCAL_ENV=1 ENV=ci make lint` passed Ruff,
formatting, Pyright and import-linter. `docker compose config` ran with temporary
synthetic files only; no service/container started. It verifies grants, profiles,
network memberships, no host ports or connector, absolute source paths and
synthetic `0400` file modes. POSIX UID checks are modeled; actual database-image
UID was subsequently checked in an approved disposable container: the pinned
image's PostgreSQL process runs as UID 999. A synthetic `1000:1000`, mode-`0400`
bind-mounted password file was readable by the root entrypoint and not by the
PostgreSQL UID; startup succeeded through `POSTGRES_PASSWORD_FILE`. The
container used no network or persistent storage and was removed. Gateway and
migration image mounts remain untested. Official Cloudflare source at release
2026.10.2 supports `--token-file` and `TUNNEL_TOKEN_FILE` (path, not token
value), providing a file-only design for a future connector. Pin and validate
the exact connector image/version and file permissions before use. No connector
or Cloudflare configuration is included.

The main artifact `12315215226` also passed its recorded ZIP digest, seven file
checksums, all 22 OCI blob hashes, source/platform/promotion metadata, revision
labels and baked frontend-origin checks. The archive manifests are not registry
digests. No image was imported, executed, rebuilt or published. The user
selected protected staging-only secret files outside the repository and
dashboard-managed Cloudflare routes. No real secret, environment file, network,
tunnel, service or persistent database was changed. Documentation and proposal
changes remain local, uncommitted and unpushed.

Staging-only remaining work (paused behind the product contract task): verify
gateway/migration and a separately selected connector image's synthetic mounted
file access; the database UID probe and source-level token-file support are
recorded above. Obtain separate execution approval before image pulls/probes,
real secret/database creation, image publication or dashboard routing.

Post-merge reconciliation/staging review (2026-10-21): PR
[#2](https://github.com/amsyrzmi/CSAV2/pull/2) merged at 01:26:24 UTC as
`0264c0ade3739f1285ab1a06e577573b71984d44`; PR run 39525314161 and push run
39524681672 passed. Post-merge main run
[39527322898](https://github.com/amsyrzmi/CSAV2/actions/runs/39527322898) passed
gate, frontend and package at the final refresh. Its current-head review
artifact is available until 2026-10-28 01:41:52 UTC; integrity/runtime checks
for that artifact have not been run. After explicit
user approval, the three already-published task files were reconciled and local
`main` fast-forwarded to the merged head. The two pre-existing review-report
deletions remain unstaged and untouched. No credential, database, service or
tunnel was changed. The next slice is planning/provisioning design, not rollout:
the [staging prerequisite checklist](../deploy/RELEASE.md#post-merge-planning-checkpoint-2026-10-21)
records pending artifact/digest approval, supported secret delivery, separate
synthetic staging credentials/database, HTTPS origin and frontend-only ingress.
No staging containers or volumes were found. These status/checklist edits remain
local pending review; no additional commit, push or deployment was performed.
The user selected `https://staging-console.fourstacks.my` for planning only;
no DNS, tunnel, TLS or service configuration change was authorized by that choice.
The private console ports 13000/18081 had no listeners at the final read-only
check; this task did not stop or restart them, and no cause is established.

TCP harness correction (validated 2026-10-20; publication approved 2026-10-21):
the load subprocess now
reconstructs the inherited IPv4 socket with family autodetection and passes it
directly to Uvicorn, with `TCP_NODELAY=1`. A separate disposable loopback probe
verified accepted sockets report `AF_INET` and `TCP_NODELAY=1`. No application
behavior, tracing, pool size or acceptance threshold changed. One exact
`NO_LOCAL_ENV=1 ENV=ci make load-test` run passed **4 tests, 4 warnings,
264.13 s**. ASGI acknowledgement/scheduled p95 was **66.181/69.213 ms**,
TCP **149.415/150.448 ms**, and graph-only p95 **92.894 ms**, within unchanged
limits. Simultaneous TCP remained diagnostic at **329.062/338.653 ms**, not
50 completed turns/s capacity acceptance. Required full validation passed:
Ruff, formatting, Pyright (zero errors/warnings), import-linter and **2,550
tests, 136 warnings in 2574.63 s**. All four load and four packaged-console
tests passed. All seven migration tests passed, including the actual
disposable-only `make db-reset` test; none were skipped or deselected. This is
one green full run, not stable capacity evidence or a waiver of earlier
failures. No persistent database, environment file, provider credential or
running dev service was changed. The approved three-file follow-up is prepared
on `fix/tcp-load-listener` from merged `main` at `6e51354`. Require fresh hosted
checks and user review before merge; publication is not deployment approval.

TCP diagnosis (2026-10-20): session-only instrumented probes found a concrete
inherited-listener artifact in the load harness: Uvicorn's `--fd` path
reconstructs the IPv4 listener with `AF_UNIX` metadata; accepted TCP sockets
were observed with `TCP_NODELAY=0`. A fixed off/on/on/off experiment reduced
no-database health-route p95 from **48.181/45.179 ms** (off) to
**4.789/4.158 ms** (on). This explains a buffering component, not the whole
305.228 ms acceptance failure. SQL/transaction timings and pool waiting were
also higher in the slowest instrumented ingress batch. Tracing-off and
standard-asyncio probes did not establish either as the cause: the unchanged
default tracing/uvloop configuration was faster in a later control.
The measured host had full swap allocated but no swap-in/out during batches,
and aggregate CPU was not saturated. Laptop/shared-host variability remains
plausible, not a proven sole cause. No application/test source or thresholds
changed. These diagnostics do not supersede the failed load run. Exact scope,
metrics and next action are in the
[TCP diagnosis](./handoffs/2026-10-19-domain-release-next-session.md#tcp-diagnosis-2026-10-20).

Focused performance check (2026-10-20, source `39de725`): the single
`NO_LOCAL_ENV=1 ENV=ci make load-test` run finished **3 passed, 1 failed,
4 warnings in 268.79 s**. ASGI acknowledgement/scheduled p95 passed at
**69.981/70.605 ms**; TCP failed at **305.228/307.746 ms**, against unchanged
**<200 ms** limits. Graph-only p95 passed at **108.943 ms** against **<150 ms**.
The diagnostic simultaneous TCP case completed, reporting **516.237/554.800 ms**
acknowledgement/scheduled p95; that case has no latency acceptance assertion.
Each database-backed scenario completed 150 synthetic turns with 150 unique
replies and pending deliveries, not actual external deliveries. All databases
were disposable Testcontainers; no provider, persistent migration or service
restart was used. No timing retry, threshold change or full-suite rerun occurred.
LG15 performance remains open. Next: diagnose TCP latency without treating
diagnostic reruns as acceptance or assuming host contention. See the
[latest performance handoff](./handoffs/2026-10-19-domain-release-next-session.md#latest-slice-focused-performance-check-2026-10-20).

Documentation audit/release review (2026-10-20): the approved 54-path audit is
committed as `f46dba0fe006577dcc56d150bd62a576adf0f15e`; the follow-on synthetic
M2 browser slice is committed as `3979da55b72f11f44ac678ae6130b1020c79a1ec`.
Both are pushed to `review/domain-release-2026-10-20`. At **2026-10-20 16:34
UTC**, the local and remote branch heads matched `3979da5`; the only worktree
changes were two pre-existing deletions under `docs/reviews/`, excluded from
both commits. Draft PR [#1](https://github.com/amsyrzmi/CSAV2/pull/1) targets
`main` at `848d8efa864c12d2168b4ccd79e32b97e0bde790`. Its details endpoint
remains inaccessible to the current GitHub CLI identity (HTTP 403); the PR was
last confirmed open and draft earlier on 2026-10-20. The user plans to merge
after reviewing the updated PR. No merge, publication or deployment has occurred.

Both hosted CI runs on `3979da55b72f11f44ac678ae6130b1020c79a1ec` passed:
[pull-request run 39455808313](https://github.com/amsyrzmi/CSAV2/actions/runs/39455808313)
and [push run 39455797430](https://github.com/amsyrzmi/CSAV2/actions/runs/39455797430).
Frontend, gate (including pytest, Gitleaks and 90% policy branch coverage) and
package jobs succeeded. The earlier PR run
[39417032600](https://github.com/amsyrzmi/CSAV2/actions/runs/39417032600)
failed in Gitleaks on a GitHub integration HTTP 403 and skipped coverage/package;
it is superseded by the passing PR run for the newer head. The job-scoped
`pull-requests: read` permission in `.github/workflows/ci.yml` allowed Gitleaks
to pass. This documentation-only status reconciliation is included in a
follow-up commit on the branch; hosted checks on the updated head must complete
before merge. No credentials were inspected or changed.

The audit corrected LG09's overstated rollback/all-runbooks acceptance, stale
snapshot-removal chronology and the frontend's historical-session heading.
Documentation target/fence checks passed for **45 Markdown files** and **407
local targets**, with zero errors; `git diff --check` passed before the
publication. The documentation-only status-note follow-up for that audit also
passed the same checker for **45 files**, **407 local targets**, zero errors,
and `git diff --check`; it was included in the published audit commit. No
runtime tests, database probes or services were used for those documentation
changes.

Packaged JavaScript-enabled **synthetic M2 Agents/Runtime browser acceptance**
now passes: `tests/test_packaged_console.py::test_packaged_m2_browser_acceptance`
first passed in isolation (**1 passed**, one existing Testcontainers warning,
**170.34 s**)
against disposable packaged services and synthetic tenant/run/event data. It
covers authenticated UI/session behavior, admin switches and persistence,
read-only controls, tenant-scoped activity/polling, visible refresh failure and
recovery, same-origin requests, denied browser egress and no page errors. The
exact command and scope are in the
[active handoff](./handoffs/2026-10-19-domain-release-next-session.md#latest-slice-synthetic-m2-browser-acceptance-2026-10-20)
and [frontend README](../CSA_WebConsole/csa-console/web/README.md). The current
`NO_LOCAL_ENV=1 ENV=ci make lint test` first attempt passed lint but ended with
**2,546 passed, 136 warnings, 4 setup errors in 2,101.43 s**. Each error was a
packaged-console fixture setup assertion that Node was missing from `PATH`;
the disposable-reset setup was among them, so reset acceptance is not yet
established. No assertion failed. A second run with the reviewed temporary Node
and Chromium paths reached **2,549 passed, 136 warnings, 1 setup error in
2,188.20 s**: the new browser scenario encountered the first packaged test's
platform admin in the shared disposable module database. The fixture now
truncates `console_users` with `CASCADE` and clears login-attempt buckets in
that disposable database before each identity scenario. The focused sequential
identity/browser rerun passed (**2 passed**, one existing warning, **212.60 s**).
The final full run used the reviewed temporary Node wrapper and Chromium
libraries:

```sh
PATH=/tmp/fourstacks-toolbin:$PATH \
LD_LIBRARY_PATH=/tmp/csa-playwright-runtime-20261020/rootfs/usr/lib/x86_64-linux-gnu \
NO_LOCAL_ENV=1 ENV=ci make lint test
```

It passed: Ruff, formatting, Pyright (**0 errors/warnings**), import-linter and
**2,550 tests passed, 136 warnings, 2,562.35 s**. All four packaged-console
tests passed. `tests/test_migrate.py` reported seven passes, including
`test_make_reset_only_disposable_container`; it was not skipped or deselected.
The run used only Testcontainers/disposable fixtures; no persistent database,
service, or provider was used. The earlier setup failures above remain recorded.
`uv lock --check` passed. Final documentation checking covers 45 Markdown files
and 409 local targets with zero errors; `git diff --check` passed. This single
full-suite result does not erase historical latency failures or establish stable
performance acceptance.

The focused browser pass does not close public-ingress, persistent staging,
LG09 live cutover/rollback, credential rotation or real-backup recovery gates.
The earlier committed-range secret scan does not cover the audit commit or the
browser-slice changes. Hosted Gitleaks in PR run 39417032600 failed before
scanning because its token could not read PR commits (HTTP 403), not because it
reported a secret. The job-scoped `pull-requests: read` fix passed in hosted CI
run 39455808313 on browser-slice head 3979da5. No credentials were inspected or
changed. The current documentation-only status reconciliation has its own
hosted checks pending on the new PR head.

Latest exact hosted-artifact runtime acceptance (2026-10-20): **three passed**,
one existing Testcontainers warning, **233.60 s**. The existing identity/readiness,
bootstrap/storage and synthetic backup/restore cases ran with imported hosted
da1b50d OCI manifests, not rebuilt application images. ZIP digest, seven checksums,
22 blob hashes, source/platform/origin/promotion metadata and imported manifest
identity were verified. `NO_LOCAL_ENV=1 ENV=ci make lint` passes.
The first session-adapter attempt stopped at fixture setup (**three errors**,
**26.88 s**) because Docker's containerd image store reports manifest IDs rather
than the expected config IDs; correcting that assertion required no application
or repository-test changes. Details and exact identities are in the handoff.
No full-suite rerun was needed for this docs/session-only acceptance slice.
Evidence committed as `0e148f85a2e4b63fb11541909befc7a2fdbd58b5` and pushed to
`review/domain-release-2026-10-20`. Hosted CI run
[39381920580](https://github.com/amsyrzmi/CSAV2/actions/runs/39381920580)
and follow-up run
[39382774620](https://github.com/amsyrzmi/CSAV2/actions/runs/39382774620)
completed successfully. At the earlier 09:26 UTC observation, draft PR
creation/listing was blocked by HTTP 403 for pull-request resources. This was
superseded later the same day when the user created draft PR #1; see the current
status at the top of this file. No merge, publication, ingress or persistent DB
change.

Documentation audit/cleanup (2026-10-20): replaced the duplicated README history
with an entrypoint and rewrote `docs/PROJECT_OVERVIEW.md` as the architecture,
workflow, directory and reading-authority map. Removed the obsolete
`CSA_WebConsole/Docs/` tree (28 tracked files plus ignored old-console/workflow
snapshots) and the superseded ecommerce agent draft; kept dated handoffs, evaluation
reports, maintained runbooks and detailed architecture/API references. Updated
links/status warnings. `SERVER_HANDOFF.md` is ignored/local-only and now contains
only safe pointers; its stale host-specific operational details were removed.
Source-accuracy review corrected the shadow-mode blocking description, clarified
that the pending-action expiry job does not expire staff approvals, removed
unsafe credential-print/rotation examples, and scoped backup/restore and operator
SQL claims to their synthetic/tested evidence. It also distinguishes `EcommerceAgent`
registration from configured marketplace adapters and records source schema 0360
separately from the last recorded persistent-dev schema 0300. Final documentation
validation: local links and fenced code blocks consistent in 44 maintained
Markdown files; `git diff --check` passes. No runtime tests or services used.

Latest candidate scan (2026-10-20): offline Gitleaks **v8.24.2** scanned the
complete review range `848d8efa864c12d2168b4ccd79e32b97e0bde790..da1b50da5b40479370507020ce7d17cd1317707f`:
**three commits**, approximately **962,310 bytes**, **1.47 s**, exit **0**,
**no leaks found**; structured JSON report is empty. Full redaction and
`--ignore-gitleaks-allow` were enabled, with default rules and no custom
allowlist/baseline. Only a bare committed-history copy was mounted; networking
was disabled. This closes the initial candidate-range scan gap, not a full-history
or dirty-worktree scan and not a guarantee that no secrets exist.
The exact hosted-artifact acceptance above now supplies the next runtime evidence.
The evidence is committed and pushed as `0e148f8` plus status correction `710725b`;
PR review, merge and staging remain separate gates.

Latest hosted evidence (2026-10-20):
[run 39367800618](https://github.com/amsyrzmi/CSAV2/actions/runs/39367800618)
on da1b50d passes gate/frontend/package. **2,549 tests passed**, 136 warnings,
**667.46 s**; policy selection **102 passed**, two deselected, **100%** coverage.
OCI export/upload now verified: downloaded artifact ZIP matches GitHub's digest,
all seven listed file checksums match, and 22 OCI blob hashes match across both
linux/amd64 archives. Exact source/origin/promotion-disabled metadata verified;
archives were not imported or executed. See the handoff for digests/expiry.
Important: hosted Gitleaks scanned **only one commit** in the latest push.
The initial large candidate scan had failed; the separate offline candidate-range
scan above now supplies that missing evidence. No merge, publication or deployment.

Latest continuation: [Fourstacks domain/release handoff and next-session prompt](./handoffs/2026-10-19-domain-release-next-session.md).
Current development: M6 incomplete; source expects **0360**, while **0300** is the last
approved recorded persistent-dev schema, not a fresh probe. Latest M6D focused evidence:
**1,457 inventory tests passed**, one existing Testcontainers warning, **114.59 s**;
lint passes. This is not a full-suite or deployment pass.
The user selected future `console.fourstacks.my` hosting with apex/`www` landing
preserved. Repository-only CI/release packaging is now implemented:
service-image pull credentials, isolated frontend install/check/build gate, separate
digest-pinned Python/Next images, clean allowlisted contexts, SHA/checksum/digest
artifact metadata and an origin-bound frontend launcher. Promotion/deployment remain
disabled. The isolated staging manifest is a gated proposal, not an executed stack;
it has no host bindings or legacy network/DB dependency and no automatic migrations.
Local candidate builds and network-disabled packaged-runtime smoke checks pass.
Latest slice adds a disposable-only packaged-console HTTPS acceptance harness:
real packaged frontend-to-gateway login/logout, Secure cookies, Origin/CSRF,
password/session rotation, four-role and cross-tenant denial, legacy marketplace
path denial, M6 local-only warnings, and schema/identity readiness refusal/recovery.
Focused run: **1 passed**, one existing Testcontainers warning, **154.43 s**.
Final `PATH=/tmp/fourstacks-toolbin:$PATH NO_LOCAL_ENV=1 ENV=ci make lint test`:
lint passes; **2,547 passed**, 136 existing warnings, **3,490.88 s**, no failures
or skips. Includes the final harness, unchanged latency checks and disposable-only
reset verification. Earlier interrupted run, internal-network port setup error and
incorrect expected forged-header status are recorded in the handoff; both completed
failures were test-harness issues, not runtime changes.
This verifies HTTP-client/server-rendered behavior behind synthetic loopback TLS,
not JavaScript browser, Cloudflare/public DNS, staging bootstrap/uploads/workers
or hosted CI/OCI acceptance. No application code changed in this slice.
Earlier packaging lint and 27 packaging/approval/synthetic-resource tests pass; the exact isolated frontend
install/check rerun passes all 20 tests, lint/types/build/traces. Its initial Next
build-typecheck failure without a diagnostic remains recorded; cause is unproven.
Earlier packaging full lint/test run: **2,545 passed**, 136 existing warnings, **2,938.01 s**,
including unchanged latency assertions and the disposable-only reset; no skips.
This run collected before the last Dockerfile-parent symlink regression and synthetic
JSON allowlist fix, which have the separate final 27-test/lint pass. Exact commands
are in the linked handoff.
Historical latency failures remain recorded below; one pass is not stable capacity.
At that earlier packaging slice, Hosted Actions,
OCI export/upload and deployment acceptance remained unverified; subsequent hosted
and exact-artifact evidence is recorded above, while deployment remains unverified.
No push/commit,
registry publish, DNS/tunnel/provider change, existing-service restart, production
connection, environment-file inspection or persistent migration occurred.
Latest bootstrap continuation adds a disposable-only packaged migration/first-admin
and non-root upload-storage rehearsal. Focused test passes (**1 passed**, one
existing warning, **294.38 s**); no runtime code changed. It covers approval and
both DSN-target guards, packaged baseline/migrations/checkpoint setup, ledger-preserving
reapplication, single first-admin/audit creation, duplicate rejection and explicit
CHOWN-only disposable storage initialization. Interactive password prompts and HTTP
uploads are not covered. The initial full check passed lint but was interrupted
without a final pytest report; captured output included a load-acceptance failure.
It is not a full-suite pass; no cause or exact latency is established from that
partial output. See the handoff for final validation evidence.
Final recorded rerun of `PATH=/tmp/fourstacks-toolbin:$PATH NO_LOCAL_ENV=1 ENV=ci make lint test`
passes lint and **2,548 tests**, 136 existing warnings, **2,148.51 s**, no failures
or skips. Both packaged tests, unchanged load/latency tests and disposable-only
reset verification pass. The interrupted failing run remains evidence, not a
waiver or a measured cause; one subsequent pass does not establish stable capacity.
Latest restore slice (2026-10-20) adds a synthetic logical backup/restore drill to a
second disposable Testcontainer. Custom-format database dump plus password-free
role definitions restore migration timestamps, tenant/user/membership/audit rows
and checkpoint tables. New synthetic role passwords are provisioned separately.
Packaged runtime readiness, membership tenant isolation, role/audit privilege
boundaries and refusal/recovery on missing migration metadata pass.
Focused restore: **1 passed**, one warning, **291.16 s** (before final type guards
and explicit checkpoint-presence assertion). Final
`PATH=/tmp/fourstacks-toolbin:$PATH NO_LOCAL_ENV=1 ENV=ci make lint test`:
lint passes; **2,549 passed**, 136 existing warnings, **3,351.07 s**, no failures
or skips, covering final source, all three packaged tests, unchanged load/latency
and disposable-only reset verification. Historical failures remain recorded.
No runtime/schema behavior changed; encrypted-key recovery, real login continuity,
checkpoint replay, upload backup and WAL/PITR/RPO/RTO are not established.
Historical read-only hosted review found local HEAD equal to `origin/main` at
848d8efa864c12d2168b4ccd79e32b97e0bde790, but **92 worktree paths are
modified/untracked**. Remote main still has pre-packaging CI only; its latest
existing run failed during container initialization before checkout and produced
zero artifacts. GHCR package access could not be confirmed: authenticated package/
contents metadata endpoints returned 403; that is not proof of package visibility.
No run validates the dirty frontend/package jobs or OCI artifact path. Next gate is
owner approval to select changes for a clean review branch and confirmation that
GHCR grants this repo Actions read access. Do not commit/push/trigger/publish/deploy
without approval. See the [release handoff](./handoffs/2026-10-19-domain-release-next-session.md).
That source-selection approval was subsequently granted: review candidate c2e5376
contains the approved snapshot (118 files; the earlier 92 status entries included
directories). The user published the DB image, updated its three active digest
references in de955f0 and pushed `review/domain-release-2026-10-20`.
Hosted [run 39366363372](https://github.com/amsyrzmi/CSAV2/actions/runs/39366363372)
confirms GHCR service pull, schema setup, lint/types/imports and isolated frontend
acceptance pass; **2,549 tests passed**, 136 warnings, **658.60 s**.
The run nevertheless failed: Gitleaks could not resolve its commit range in the
shallow checkout and scanned approximately zero bytes. No secret-scan pass is
claimed; coverage and package jobs were skipped. The gate checkout now requests
full history (`fetch-depth: 0`) without weakening scanning. Hosted validation of
that fix, coverage and OCI export/upload remain pending; no merge/deployment.
See the [release contract](../deploy/RELEASE.md) for operator gates.

## Historical acceptance context

M4 history starts at [the prior closeout](./handoffs/2026-10-16-m4-closeout-m5-start.md).
M4 is **functionally closed with an explicitly accepted laptop-dev performance exception**;
M5's Telegram-only staff slice was reviewed and rolled out on explicitly approved guarded
dev at **0300**; platform-admin UI and synthetic Telegram acceptance are verified.
Live reader denial and representative tenant-admin/staff browser replay pass. Per-role
fresh approval-decision coverage remains automated evidence, not a repeated live matrix.
M5 full validation was **692 passed**, including both unchanged ingress latency checks.
Latest M6B full suite: **1065 passed**, 136 warnings, **3001.57 s**. Standalone ASGI
ack/scheduled p95 **74.585/76.926 ms** and TCP **174.885/175.282 ms** both pass the
unchanged **<200 ms** ack threshold; each completed150 requests and150 durable deliveries.
Simultaneous TCP **506.924/537.490 ms** and graph-only **105.615 ms** are diagnostic.
This full run started before the final narrow refresh-expiry-after-shop check was added;
the final verifier/parser/reconnect selection **173 passed**, and final full lint passes.
The subsequent synthetic verifier-to-completion selection passed **139 tests**, 1
testcontainers deprecation warning, **35.18 s**; `NO_LOCAL_ENV=1 ENV=ci make lint`
also passes (289 Python files formatted, Pyright0 errors, import-linter kept). This was
test-only scope, so the full suite was not rerun.
The preceding **1052 passed / 1 TCP latency failure** remains historical evidence, not
the latest result; one passing run is not a stable-capacity claim. For next-session
M6B-specific scope, see the dated
[M6B verifier handoff](./handoffs/2026-10-18-m6b-verifier-next-session.md); the current
continuation prompt is in the domain/release handoff above.
The preceding **971 passed / 1 TCP latency failure**
(ack p95 **213.232 ms**, unchanged **<200 ms**) remains recorded.
The preceding **932 passed / 1 TCP latency failure**
(ack p95 **259.251 ms**, unchanged **<200 ms**) remains recorded.
On 2026-10-18 the user explicitly made that latency failure non-blocking for continued
development. The suggested home-server cause is unverified; this is not a passed
benchmark, threshold change or production performance acceptance.
Earlier readiness and latency failures remain recorded below;
one passing run does not establish stable capacity or resolve their root cause.
M4's historical **674 passed / 2 latency failures** remain recorded;
one current pass is not a laptop capacity guarantee. See the closeout decision below.
Documentation cleanup consolidates stale
status/navigation and replaces the obsolete kickoff prompt; plans, historical evidence,
user-owned review reports and UI-guide material remain preserved.

## M6 Business management foundation (2026-10-17; offline)

- M6D inventory design (2026-10-18): user selected a CSA ledger for explicitly managed
  variants, fixed non-overlapping per-store quotas, local orders first, one default
  warehouse and future tenant-admin count/adjustment records with required reasons.
  Documented the per-variant authority model, quantity/reservation vocabulary, idempotent
  order ownership, report-only provider reconciliation and write-freeze conflict handling in
  the [M6 roadmap](./handoffs/2026-10-15-console-source-migration-plan.md#m6d-inventory-authority-and-reconciliation-design-2026-10-18-user-selected-direction).
  This separates existing `products.reserved_qty` local order repair from physical or
  provider stock reconciliation. Design only: no stock mutation, schema, sync or runtime
  behavior was wired. A separate immutable in-memory prototype in
  [domain.py](../csa/inventory/domain.py) and report-only reconciliation are covered by
  **1,352 focused synthetic cases**, including receipt/inspection-gated return restocking,
  zero-vs-missing/stale observations and generated reservation/allocation sequences, in
  addition to tenant/authority scope, two-admin approval, stale fences and unknown-write
  freezing. Targeted Pyright passes and `NO_LOCAL_ENV=1 ENV=ci make lint` passes. The
  full synthetic suite, `uv run pytest`, also passes: **2,419 passed, 136 warnings,
  1,874.60 s**, using the suite's disposable Testcontainers Postgres fixture. No test
  failures, including latency checks, occurred in this run; prior latency failures remain
  historical evidence and are not waived or erased. `git diff --check` passes.
  The detailed proposal specifies immutable
  count/adjustment audit evidence, stale-version handling, provider quantity-contract
  evidence gates and an audited freeze/count/reconcile/CAS cutover. The user approved a
  second distinct tenant admin for every non-zero count variance/manual on-hand change,
  with no self-approval; the workflow is not implemented. The audit and cutover details
  remain proposals. TikTok Products API/Get Product pages and its public Postman “Update
  Stock” request were reachable only as shells without the operation schema; search
  snippets were inconsistent and not used as evidence. The user selected Malaysia, but
  the supplied [Update Inventory v202309 reference](https://partner.tiktokshop.com/docv2/page/update-inventory-202309)
  also rendered only the Partner Center shell. No inventory endpoint, quantity meaning,
  market/version applicability or event coverage was verified. TikTok inventory remains
  report-only pending a sanitized rendering/export of the official Malaysia v202309
  inventory read/write schemas. The user suggested
  `/product/202309/products/{product_id}/inventory/update` as a candidate path; it is
  unverified and must be confirmed against the rendered docs before implementation.
  The behavior-only persistence/concurrency acceptance contract is recorded in the M6
  roadmap; it selects no isolation level or lock strategy and is not database execution
  evidence. The user approved continuing the local-only conceptual schema/transaction
  proposal keyed by explicitly enrolled tenant-scoped `products.id`. It now defines
  candidate warehouse/account, local-order reservation, count/adjustment command,
  append-only event and return-case keys; atomic boundaries for draft, cancel, fulfil,
  approval and inspection; composite tenant-FK prerequisites; and staged RLS/privileges.
  Review tightened claim-to-order-item product matching, active-reservation source of
  truth, explicit single initial warehouse, and a fenced count/reconcile cutover rather
  than copying the legacy quantity projection. It now also requires verified holding
  statuses, order-line quantity binding, and coordinated release versions so old writers
  cannot resume after cutover. Before persistent rollout/cutover, verify existing composite parent
  integrity, actor principals for admin and order events, deployed holding statuses,
  writer drain/fence mechanism, and initial-count evidence/approval. Source confirms the
  current code default holds `draft` and `pending_confirmation`; approval commits at
  `confirmed`, but deployed settings/writers need confirmation. `AgentContext` carries
  contact/trace, not verified staff identity; approval currently carries a staff target,
  not authenticated approver proof. Baseline FKs do not enforce same-tenant order-item,
  order and product ownership; actual DB rows were not read, so existing mismatches are
  unknown. Compose/source shows multiple writer services/jobs but no inventory maintenance
  gate or all-writer fence. No physical initial count or approval was performed. A future
  console count workflow must propagate session-derived admin identity. These
  data/deployment/actor/count gates block rollout/cutover, not offline synthetic design.
  Existing `products.stock_qty`/`reserved_qty` must not become a competing authority:
  proposal recommends keeping new storage non-authoritative until a tested atomic cutover,
  then making the ledger authoritative with any legacy projection maintained transactionally.
  The current order flow uses a product row as its local stock unit, but has no typed
  selectable-variant contract; external variant equivalence is not established. Provider
  mapping, allocations and sync remain deferred. No code, DDL or migration was added.
  Added a provider-agnostic Stage 1 synthetic acceptance matrix covering authority
  enrollment, reservation conservation/idempotency, fulfillment/return atomicity, tenant
  isolation, two-admin count approval, fixed allocations, conflict states, ambiguous
  outbound outcomes and audited cutover. Provider-order lifecycle cases are explicitly
  deferred; no provider fields/endpoints are assumed. The prototype covers only a subset:
  no durable audit/outbox, refund/return integration, transaction rollback, true
  concurrency proof, provider parser or provider order lifecycle. No schema/runtime/
  provider wiring was added; the matrix remains the later acceptance bar and no stock
  mutation was authorized.
  Live eligibility/acceptance remain separate gates.
- M6D candidate-schema feasibility: added
  [disposable PostgreSQL tests](../tests/test_inventory_persistence_feasibility.py)
  for same-tenant composite-key rejection, concurrent oversubscription prevention,
  stale-count rejection preserving a newer claim, and rollback after an injected event
  constraint failure. Focused run: **4 passed** in **10.47 s**, one Testcontainers
  deprecation warning; `make lint` passes (Ruff, formatting, Pyright, import-linter).
  This is test-local candidate-schema evidence only, not the application's schema or
  transactions, actual-row integrity, RLS, staff identity, writer fencing, physical count,
  full Stage 1 acceptance, or deployment behavior. No application code, DDL, migration,
  runtime wiring, persistent database or stock-write authority was added.
  Expanded 2026-10-19 evidence: **21 passed in 21.74 s**, one Testcontainers deprecation
  warning; `make lint` passes. Combined domain/persistence regression:
  `uv run pytest tests/test_inventory_domain.py tests/test_inventory_persistence_feasibility.py -q`
  passes **1,373 tests in 32.92 s**, one warning; `git diff --check` passes.
  New cases cover eight cancel/fulfil races, both terminal
  winners, exact replay/opposite-transition rejection, concurrent reservation replay and
  changed-quantity rejection, plus failure after each account/claim/event write for both
  terminal actions with successful retry and exact event payloads. Test-local replay
  checks now precede remaining-capacity validation, and terminal claims cannot revive.
  Baseline review corrected fixture order IDs from UUID to text; composite tenant anchors
  and null-product exclusions remain necessary.
  Return/replay continuation: **36 candidate persistence cases** now cover bounded
  concurrent return requests, duplicate request/inspection races, conflicting inspection
  dispositions, receipt-gated restocking with zero/partial/all sellable quantities, and
  request/receipt/inspection rollback with safe retry. Changed request quantity/order-line,
  invalid quantities and changed inspection payloads fail without partial writes.
  Identical synthetic keys in two tenants have independent state/events and replay;
  missing tenant-scoped targets fail. Combined domain/persistence command above:
  **1,388 passed in 49.21 s**, one Testcontainers deprecation warning; `make lint` and
  `git diff --check` pass. Test-local inspection advances version once even for zero
  sellable quantity. No migration compatibility, RLS, outbox, trusted physical evidence,
  refund/payment or order-status integration, two-admin approval, or full replay proof
  for every command type is claimed.
  The full suite was not rerun for this test-only change; the earlier 2,419-test result
  and historical latency caveats remain prior evidence.
  The user confirmed repository schema is sufficient for continued offline work; no old
  database/n8n dump is needed. Existing-row audits, deployed holding statuses/writers and
  physical counts gate persistent rollout/cutover, not synthetic development or proposal
  review. Migration-proposal review completed in the
  [M6 roadmap](./handoffs/2026-10-15-console-source-migration-plan.md#narrow-migration-proposal-review-2026-10-19-documentation-only).
  Recommends an identity-only first stage: necessary tenant-composite unique anchors
  and empty warehouse registry with RLS, tenant reads and no runtime mutation grants.
  Preserve existing global PKs, text order IDs, UUID product/item IDs and `qty`; no seeds,
  stock triggers, ledger tables or actor-dependent writes in that first stage.
  Same-tenant parent FKs are a separate compatibility change: even NOT VALID enforces new
  writes. Default grants must be explicitly revoked. Future append-only event enforcement,
  fingerprints, trusted actors, pending-count freeze and atomic order integration remain
  design requirements, not established by fixture tests. The review defines disposable
  migration apply/reapply, rollback, RLS/privilege and legacy-behavior acceptance.
  The user subsequently approved bounded identity scaffolding. Added
  [0360 inventory identity](../db/migrations/0360_inventory_identity.sql): the three
  required composite unique anchors and an empty warehouse registry with tenant/code
  uniqueness, at most one default per tenant, nonblank codes and tenant FK. RLS is enabled
  without FORCE; inherited grants are revoked and only tenant-scoped SELECT is granted
  to `csa_app`/`csa_ro`. No seed, ledger, enrollment, parent-integrity FK, legacy trigger
  or runtime mutation permission is added. Gateway source and migration-test expected
  version are now **0360**; no persistent database was migrated or service restarted.
  [Identity migration tests](../tests/test_inventory_identity.py) verify exact catalog
  anchors/types, empty registry, tenant-role reads and mutation denial, warehouse
  constraints, synthetic upgrade compatibility (including unchanged legacy inconsistent
  parent links), null-product lines, existing stock SQL, global order PK preservation,
  runner reapply and injected transactional DDL/stamp rollback. Readiness tests cover
  missing 0360 as well as a historical migration.
  Validation: `NO_LOCAL_ENV=1 ENV=ci make lint` passes.
  `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_inventory_identity.py
  tests/test_inventory_domain.py tests/test_inventory_persistence_feasibility.py
  tests/test_migrate.py tests/test_health.py tests/test_order_agent.py -q
  -k 'not test_make_reset_only_disposable_container'`:
  **1,421 passed, 1 deselected, 1 warning in 119.07 s**. The warning is the existing
  Testcontainers deprecation; reset was deliberately deselected. All database evidence
  uses synthetic data in disposable Testcontainers. Full suite and latency checks were
  not rerun; historical failures remain recorded. No `make db-reset`, persistent database,
  provider, environment-file, service, infrastructure or UI-guide changes occurred.
  Next: resolve typed ledger command/actor/evidence and pending-count contracts before
  proposing writable ledger storage; parent-integrity enforcement is still a separately
  reviewed legacy-compatibility change. Inventory authority and cutover remain gated.
- Offline inventory count contracts (2026-10-19): added frozen Pydantic
  [account/admin/request/review contracts](../csa/inventory/contracts.py) with strict
  quantities, tenant binding, distinct-admin review and canonical versioned SHA-256
  business-payload replay fingerprints. Session/time evidence is retained but excluded
  from the business fingerprint, allowing renewal for the same verified user. Models
  do not authenticate users, enforce fresh membership or create durable command outcomes.
  A prototype regression confirms pending non-zero count freezes new reservations;
  existing fulfilment may stale the count, after which approval fails and explicit
  rejection releases the freeze without mutating stock. No domain/runtime transition
  was changed. `NO_LOCAL_ENV=1 ENV=ci make lint` passes; focused contracts/domain:
  **1,383 passed in 7.45 s**, no warnings or database use. Full suite/latency not rerun,
  no reset/migration/provider/service access. Next: persisted count outcomes, trusted
  actor/evidence-loading boundaries and replay-under-freeze semantics before writable
  ledger storage; service/contact and return evidence contracts remain unfinished.
- Count-outcome/replay continuation (2026-10-19): added validated persistable
  pending/recorded/approved/rejected shapes, matching retained reviewer/command evidence,
  result-version checks and exact replay returning the unchanged record. No persistence
  writer or stale transition enforcement is implied. Added an injected async admin-loader
  boundary checking returned tenant/user/session and non-regressing evidence time;
  errors propagate, with no default adapter or authentication implementation.
  Prototype reservation replay intentionally checks existing quantity before freeze/
  shortfall guards; exact replay is a no-op, changed quantities fail and new claims
  remain frozen. Tenant/authority checks still precede lookup. Full lint and **1,391
  contracts/domain tests in 7.60 s** pass; `git diff --check` passes. No DB/reset/migration
  or provider/service access; full suite/latency not rerun. Next: review count transition/
  audit storage and transaction design, with a real trusted actor adapter and freeze
  concurrency still required before writable integration.
- Count persistence review (2026-10-19): **not ready for writable persistence**.
  [Review findings and transaction sequence](./handoffs/2026-10-15-console-source-migration-plan.md#count-storagetransaction-review-2026-10-19-no-writable-ddl-approved)
  identify shape-versus-transition validation, pending-freeze serialization independent
  of ledger version, transaction-bound actor revalidation/revocation, missing immutable
  audit envelope/enforcement, and stale rejection recovery versus approval fencing.
  Exact decision replay must bind reviewer/outcome/reason and preserve original evidence.
  Next bounded recommendation: disposable test-local count transactions with synthetic
  actors, races, exact/conflicting replay and coupled event-write rollback; no new
  application migration or runtime grants. Documentation-only review; prior 1,391-test
  evidence is unchanged, not database count-transaction evidence. No DB/reset/service/
  provider access; source remains0360.
- Disposable count harness (2026-10-19): added
  [20 synthetic transaction cases](../tests/test_inventory_count_transactions.py)
  for pending freeze/request/reservation races, competing decisions and exact/conflicting
  replay, approval versus fulfilment, independent stale fences, rejection recovery,
  zero counts, self-review denial, synthetic actor revocation ordering and seven
  coupled-write/event-failure rollback cases. Final state is checked on fresh connections.
  Combined count/contracts/domain/persistence run: **1,447 passed, 1 warning in 63.76 s**;
  `NO_LOCAL_ENV=1 ENV=ci make lint` and `git diff --check` pass. Warning is existing
  Testcontainers deprecation. Database use is disposable/synthetic only; no reset,
  persistent/provider/service/environment-file access. Full suite/latency not rerun.
  At that stage, single-account synthetic actor locks were not real session
  authorization, and selected payload checks/caller-written events were not canonical
  fingerprints or mandatory audit. The following audit-envelope entry records the later
  typed fingerprint/event-payload composition.
  No runtime code, migration or privileges changed; source remains0360.
  Next at that stage: immutable count audit envelope and composition of Pydantic
  command/outcome/fingerprint contracts with disposable transactions before
  writable-schema review; see the subsequent entry for completion evidence.
- Immutable count audit envelope (2026-10-19): added frozen
  [StockChangeAuditEvent](../csa/inventory/contracts.py) with event ID, retained command,
  transition kind, actor evidence, occurrence time, canonical fingerprint, observed
  ledger/authority revisions, before/after quantities, resulting version and optional
  decision. Validators reject tenant/fingerprint/time mismatches, malformed request
  transitions, approval fence/quantity mismatches and stock-changing rejections.
  Contract tests round-trip pending, approval and stale-rejection envelopes and reject
  invalid tenant, fingerprint, transition and occurrence-time evidence. The disposable
  transaction harness now persists and reloads typed command, decision, record and audit
  JSON in its transaction; it checks stored fingerprints, record/event command identity,
  event IDs, and terminal event/decision agreement across request, approval, rejection
  and rollback/retry cases. The stale-replay test retains the original count baseline.
  Focused count/contracts: **59 passed, 1 Testcontainers deprecation warning in 38.44 s**.
  Combined count/contracts/domain/persistence: **1,448 passed, 1 warning in 92.68 s**.
  `NO_LOCAL_ENV=1 ENV=ci make lint` and `git diff --check` pass. The full suite and
  latency checks were not rerun. This is synthetic, single-account evidence only: caller-
  inserted test events do not prove mandatory database-owned audit, immutable event
  privileges, RLS/tenant isolation, real session/membership revalidation or multi-account
  lock ordering. No runtime writer, migration or grant changed; schema remains 0360.
  No persistent database, reset, environment-file, provider or service access occurred.
  Next: exercise a test-local database-enforced audit boundary and review actual console
  identity transaction/lock boundaries before any writable-schema proposal.
- Test-local count audit enforcement and identity-boundary review (2026-10-19): the
  disposable count schema now uses a deferred constraint trigger that rejects command
  inserts/transitions unless a matching event with the same transition, event ID,
  fingerprint, command payload and decision payload exists at commit. A guard restricts
  new commands to pending/recorded and pending commands to one approved/rejected terminal
  transition, with retained command/fingerprint fields immutable. Event UPDATE, DELETE
  and TRUNCATE are rejected. Disposable tests prove request/approval omission and
  mismatched fingerprint roll back at commit, event mutations fail, and terminal
  replacement/command fingerprint mutation fail. This is **test-local DDL only**; no
  application migration, runtime role grant, writer or authority was added. Tests
  connect as the container's superuser `csa`, so they do not prove trigger protection
  from an owner/superuser, the deployed `csa_app` privilege profile, RLS, or a deployable
  security boundary.
  Source review confirms `IdentityService.resolve` locks a session only during its
  separate `csa_identity` pool transaction, reads memberships separately, then returns
  the actor after commit; tenant writes use `csa_app`'s independent `tx`. Membership
  changes and user/session revocations touch those rows in differing orders. No
  transaction-bound tenant-admin loader or globally consistent session/user/membership
  lock order currently exists. A future stock command must revalidate current actor
  authority on the write transaction before account/command locks without granting
  `csa_app` broad identity-table access, and align revocation lock order.
  A synthetic non-owner `count_trial_app` role can commit only when it inserts the
  matching audit event; it cannot update/delete/truncate events, disable the trigger or
  set `session_replication_role`. This is not the deployed `csa_app` grant profile and
  still does not protect against the superuser/table owner, who owns the test schema.
  Focused count transactions: **29 passed, 1 existing Testcontainers deprecation warning
  in 43.73 s**; combined count/contracts/domain/persistence: **1,457 passed, 1 warning
  in 114.59 s**. `NO_LOCAL_ENV=1 ENV=ci make lint` and `git diff --check` pass. Full
  suite/latency checks not rerun; no persistent database/reset, service, provider or
  environment-file access. Next: settle an inventory ownership/grant model that
  addresses the legacy `csa` owner/n8n bypass constraint, then reconcile one identity
  session/user/membership revocation lock order and test both under disposable roles
  before drafting any migration. Writable inventory remains gated.
- Selected token-parser continuation (2026-10-18): added pure offline
  [token-response parser](../csa/agents/ecommerce/token_response.py), not exchange
  transport, a trusted verifier or a usable grant. Selected frozen output masks access/
  refresh tokens and exposes explicit aware UTC access/refresh expiry plus stored scopes.
  Provider diagnostic/user/name/region/arbitrary fields are discarded; no tenant/app/
  shop/cipher or authorization provenance is inferred. Both expiry fields require
  explicit bounded integer Unix seconds and future timestamps (equality expired);
  no duration addition, string/bool/float coercion or default lifetime.
  Standard seller user_type0 only; creator/partner/Global Selling values remain
  unsupported pending separate verification. Scopes require actual arrays of nonempty
  exact strings, no whitespace normalization or duplicates; required order/shop scope
  policy is shared with the existing metadata gate, preserving exact FS spelling.
  Malformed/rejected/expired/unsupported responses yield fixed safe authorization codes,
  with validation input/cause suppressed and no logging. Raw payload is never returned.
  Lint/format/Pyright/import boundaries pass. Parser/shared-authorization selection:
  **103 passed**, **0.81 s**, including missing/malformed fields, strict types, expiry
  boundaries/duration rejection, masking, frozen output, timezone equivalence and scopes.
  Full synthetic lint/test: **1038 passed / 2 failures**, 136 warnings, **2259.43 s**.
  TCP ack/scheduled p95 **218.063/219.480 ms** fails unchanged **<200 ms** ack limit;
  ASGI **94.966/98.602 ms** passes. Each transport completed150 requests with150 durable
  deliveries. Simultaneous **514.039/538.389 ms**, graph-only **90.942 ms** are diagnostic.
  The other failure was the broad hard-coded-ID guard matching the numeric timestamp
  maximum. Replaced the literal with a bound derived from maximum UTC datetime,
  preserving behavior without weakening the guard. Added maximum-second boundary test.
  Corrected parser/policy/identifier-guard selection: **105 passed**, **1.53 s**;
  full lint/types/import boundaries pass. Subsequent full suite after the fix:
  **1052 passed / 1 TCP latency failure**, 136 warnings, **2693.14 s**. TCP ack/
  scheduled p95 **553.594/554.196 ms** fails unchanged **<200 ms**; ASGI
  **96.235/96.988 ms** passes. Each transport completed150 requests/durable deliveries.
  This was development non-blocking by user decision, not production acceptance or a
  proven host cause. No latency-only retry or threshold change. The later full run after
  exchange implementation passed **1065 tests**, with standalone ASGI/TCP ack p95
  **74.585/174.885 ms**, respectively; it does not erase the earlier failure or prove
  stable capacity.
  No schema/dependency/frontend change, env snapshot/real secret/key access, HTTP,
  callback/runtime registration, persistent reset/migration, service/webhook/UI-guide edit.
  Source0350/persistent dev0300 unchanged. Exact app/shop verifier composition and
  eligibility remain gates.
- Isolated exchange transport continuation (2026-10-18): added
  [token_exchange.py](../csa/agents/ecommerce/token_exchange.py), separate from attempt
  completion and any live verifier. The fixed HTTPS endpoint is called via raw
  `AsyncHTTPTransport` rather than `AsyncClient`, so request hooks/client instrumentation
  cannot observe query credentials; environment proxies are disabled, TLS verification
  retained, retries set to zero, and redirects are not followed. One 15-second timeout,
  cooperative cancellation, identity encoding, bounded 64 KiB streaming response and
  strict parser output. Errors are fixed safe codes without request URL/provider text.
  Synthetic no-network tests cover exact query, success, provider/HTTP rejection,
  redirect count, malformed JSON, URL-bearing network exceptions, timeout, cancellation,
  size bounds and captured-log/error secret absence. Transport/parser/reconnect tests:
  **105 passed**, one testcontainers deprecation warning, **41.69 s**. Full lint/format/
  Pyright/import boundaries pass. Full suite **1052 passed / 1 TCP latency failure** as
  recorded above. No provider request, integration into
  completion, callback, env/secret access, persistent reset/migration, service, webhook
  or UI-guide change. Live Partner eligibility and acceptance remain external gates.
- Trusted verifier composition (2026-10-18): added
  [authorization_verifier.py](../csa/agents/ecommerce/authorization_verifier.py).
  When injected as `complete_authorization`'s verifier callback, it runs under the
  one-use attempt/app fence. It loads immutable trusted app material and requires exact
  app ID, active state and revision before exchanging the code. The
  selected token is then used for one uncached authorized-shops request; only the exact
  server-selected local `shop_id` row is accepted, and its provider cipher is returned
  as masked data. The completed `VerifiedAuthorizationResult` carries the same context,
  parsed token/scopes/expiries and a verification timestamp after the fresh shop match.
  Token expiry is checked again after shop verification.
  `TikTokShopAdapter.authorized_shop_cipher` supports first authorization where no
  prior cipher exists. Its dedicated verifier transport is raw, TLS-verified,
  environment-proxy-independent, zero-retry, non-redirecting and response-bounded; it
  avoids AsyncClient URL logging/hooks for signed requests. Existing stored-cipher
  `require_authorized_shop` behavior remains exact-match and fail-closed. No browser
  callback, app registry/key source, default verifier registration or live network access
  was added; this verifier is not currently wired into a callback or runtime.
  Synthetic verifier/adapter/parser/exchange/reader/resolver/connection tests:
  **173 passed**, 1 testcontainers deprecation warning, **76.18 s**. Full repository
  lint/format/Pyright/import boundaries pass (**288 files formatted**, Pyright0 errors).
  Full suite **1065 passed** as above, but launched before the final refresh-expiry
  boundary addition; final focused tests include that case. Source0350/persistent dev0300
  unchanged; no persistent reset/migration, environment/secret access, production or
  UI-guide change.
- Synthetic end-to-end composition (2026-10-18): added one disposable-testcontainers
  test passing `TikTokAuthorizationVerifier` directly to `complete_authorization`.
  Separate `httpx.MockTransport` handlers cover both token exchange and authorized shops.
  The test checks the attempt is durably consumed at token-request time, exact trusted
  app ID/revision material, exact intended shop selection, encrypted access/refresh
  token and shop-cipher persistence with canonical AAD, and the completed grant/binding.
  A second reconnect receives no intended shop: the prior connection remains byte-for-byte
  intact, the attempt remains consumed, replay makes no extra app load or HTTP request,
  and provider diagnostics/secrets are absent from the safe failure/logs. A third flow
  forces shop-cipher encryption to fail after token/grant writes begin, proving the
  credential snapshot, grant count and binding count roll back together while that
  attempt remains consumed. No production code change was needed. Focused
  authorization/transport/parser/connection selection: **139 passed**, 1 testcontainers
  deprecation warning, **35.18 s**. `NO_LOCAL_ENV=1 ENV=ci make lint` passes: Ruff,
  **289 files formatted**, Pyright0 errors and
  import-linter kept. No full-suite rerun (test-only change); earlier full-suite
  latency evidence above remains unchanged. No real provider/app registry/callback/
  runtime wiring, persistent dev access, migration/reset, environment/secret access or
  UI-guide change. Seller/app eligibility and separately approved live acceptance remain
  external gates; next concrete step is external confirmation of seller/app eligibility
  and authorization access before separately scoping any real app-material source.
- Public exchange-contract continuation (2026-10-18; documentation only): rendered
  official [Authorization overview](https://partner.tiktokshop.com/docv2/page/authorization-overview-202407)
  confirms GET token/get with app key/secret/code in query and literal
  `grant_type=authorized_code`. Both access/refresh `*_expire_in` fields are absolute
  Unix timestamps, not durations; `granted_scopes` is an array of scope keys.
  Auth code is single-use/30min; local attempt lifetime remains intentionally10min.
  `open_id` identifies user, not shop; exact fresh shop/app binding remains necessary.
  Standard seller0 is distinguished from creator/partner/Global Selling types;
  this verification does not claim support for all user types or this account's eligibility.
  Query-bearing secrets require explicit HTTP logging/telemetry/error isolation before
  transport work. Legacy OAuth duration defaults/shop inference/logs/webhook saver are
  not reused or changed. No actual exchange/parser/transport/verifier is added here.
  Next: strict selected-response parser with synthetic Unix-time/scope/type/error tests;
  then isolated single-attempt transport and separate trusted verifier composition.
  Documentation-only; latest975 full pass remains prior implementation evidence.
  Attached env snapshot unopened; no credentials, login, API Testing Tool, live request,
  persistent DB/service/webhook/frontend/UI-guide change. Source0350/persistent0300.
  See [verified contract and implementation prerequisites](./handoffs/2026-10-15-console-source-migration-plan.md#m6b-public-token-exchange-contract-verification-2026-10-18).
- Staff connection-status continuation (2026-10-18): existing staff-only store list,
  detail and exact-binding reads now expose a narrow `connection` metadata projection.
  A single tenant/record-bounded batch query checks latest exact-store grant lifecycle,
  generation, app revision/state, expiry, binding existence and stored-scope booleans.
  No tokens/ciphers/`meta`, scope values, codes, state hashes or credential presence are
  selected into response data. Advisory statuses include unsupported/missing shop,
  unverified, revoked/disconnected, expired, app unavailable, missing binding/permissions
  and `authorization_recorded` (explicitly not verified live). Constants remain
  authorization unverified, permissions unknown and provider reads/live integration
  unavailable; no metadata status authorizes dispatch.
  Business UI displays the statuses and explains eligible app/seller authorization is
  still required. Connect/Reconnect are disabled with no handlers; no new mutation,
  callback, credential resolution or synthetic/legacy exchange invocation is added.
  Stable record paging/filtering, tenant remount/abort and explicit refresh are retained.
  Initial target exposed a missing lateral-query tenant alias and duplicate synthetic
  shop IDs; corrected projection and fixture identities. Final targeted console/store/
  contract/readiness/migration selection: **30 passed**, 1 warning, **216.14 s**,
  including real disposable-only reset/idempotency and all three permitted staff roles.
  Tests assert exact lifecycle/list/detail shapes, retained history selection, tenant
  isolation, denied reader/machine/anonymous roles and mutation rejection; crypto/exchange/
  provider functions are guarded against invocation. Console OpenAPI snapshot regenerated.
  Backend lint/types/import boundaries pass. Isolated frontend **16 transport + 2 state
  tests**, lint, types, production build and boundary traces pass without env files.
  Full synthetic `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`:
  **975 passed**, 136 warnings, **1989.63 s**, including full lint and disposable-only
  reset/migration. Standalone ASGI ack/scheduled p95 **53.206/53.613 ms** and TCP
  **178.197/178.696 ms** pass unchanged **<200 ms** ack acceptance. Each transport
  completed 150 requests with 150 durable deliveries. Simultaneous TCP
  **809.617/839.308 ms**, graph-only **87.540 ms** remain diagnostic, not sub-200 ms
  simultaneous acceptance. Historical failures and development-only non-blocking
  decision remain; no latency-only retry or stable host-capacity/root-cause claim.
  No schema/dependency change; source still requires0350, persistent dev remains0300.
  Attached environment snapshots were not opened. No existing secrets/key access,
  provider HTTP, persistent migration/reset, service/webhook or UI-guide change.
  See [staff connection status](./handoffs/2026-10-15-console-source-migration-plan.md#m6b-staff-connection-status-2026-10-18).
- Authorization-attempt continuation (2026-10-18): user cannot proceed beyond Partner
  Center account registration. Partner review/publishing/seller authorization remain
  an external live gate, not a blocker for synthetic development. No custom-app/own-shop
  exemption or pre-review Sandbox access is assumed.
  User chose preserve-current-until-success reconnection and no replay after exchange
  starts. Added0350 hashed random one-use state, exact immutable tenant/app-revision/
  local-store/credential-generation binding, ten-minute expiry, cancellation and
  consume-before-callback commit. No raw state/code/token is persisted in attempt rows.
  Internal coordinator invokes one injected trusted verifier under app/credential
  fences and a15s overall deadline; no provider implementation, URL, callback route,
  public API, registry/key lookup or live connection control is added.
  Trusted verifier output must authenticate exact context/shop/scopes; typing or copying
  the requested context does not prove TikTok provenance. Error payloads must be safe;
  callbacks must honor cancellation and never retry/log codes or tokens.
  Success atomically encrypts/replaces access/optional refresh tokens with existing row/
  field AAD, increments DB credential generation, disconnects active old grants via the
  existing trigger, inserts a new grant/audit plus encrypted shop evidence and completes
  the attempt. Expiry/scope/time/context mismatches fail explicitly. Missing new refresh
  token clears the old one, not a fallback. No refresh operation is implemented.
  Failed/cancelled/expired attempts preserve prior credentials; uncertain exchange or
  persistence failure leaves a consumed attempt that cannot replay. Grant uniqueness
  now applies only to active tenant/credential grants, preserving terminal history and
  permitting new authorization without reviving old grants or old read operations.
  Shared grant/shop writers support caller-owned atomic transactions; grant creation
  now locks exact credential generation as well as app revision.
  Targeted connection/lifecycle/grant/storage/reader/dispatch/readiness/migration:
  **116 passed**, 1 warning, **228.66 s**, including disposable-only reset/idempotency.
  Expanded connection/grant selection: **39 passed**, 1 warning, **46.64 s**, including
  concurrent callback/reconnect winner, atomic crypto-failure rollback, app/generation
  commit-gap denial, cancellation ordering and revoked-connection reauthorization.
  Full `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`:
  **971 passed / 1 TCP latency failure**, 136 warnings, **1772.52 s**.
  All connection/grant/fence tests, disposable reset/idempotency and lint/types/import
  boundaries pass. ASGI ack/scheduled p95 **79.155/80.843 ms** passes; TCP
  **213.232/213.527 ms** fails unchanged **<200 ms** ack acceptance.
  Each transport completed 150 requests with 150 durable deliveries. Simultaneous TCP
  **2403.971/2513.879 ms**, graph-only **90.017 ms** remain diagnostic.
  Full validation is not green. Latency remains non-blocking for development by user
  decision, not production acceptance or proof of a home-server cause. No latency retry
  or threshold relaxation. The945 full pass below belongs to the preceding slice.
  Gateway source now requires0350; persistent dev remains0300. No existing credentials/
  keys, live HTTP, persistent migration/reset, services, frontend or UI-guide changed.
  See [offline authorization choreography](./handoffs/2026-10-15-console-source-migration-plan.md#m6b-offline-authorization-attempts-and-reconnection-2026-10-18).
- App-lifecycle continuation (2026-10-18): user selected a global non-secret app gate
  across all tenants, with external app secrets remaining external. Added empty0340
  app state/audit metadata and immutable grant `app_revision`. Registration is always
  disabled at reserved revision1; each activation/disable increments revision with
  state-change CAS and atomic audit. Legacy grants/operations default to reserved1,
  which can never be active; no registry, grant authorization or secret is backfilled.
  Runtime can only read global non-secret metadata and execute a narrow shared-lock
  function; owner/operator connection is required for state changes. Tenant grant
  history remains under staged RLS; app-level denial does not rewrite tenant audit.
  Fence order is global app (shared), credential, grant. Both verification and the
  post-consumption send transaction check the exact active app revision; grant/binding
  creation and stored loading use the same gate. External material must report the
  exact revision before any credential lookup/decryption. Existing read-winner ordering
  is retained; disable-winner rejects and commit-gap disable burns the consumed attempt
  without sending. Old grants never revive when the app is reactivated.
  Operator protocol: commit disable before external mutation, stage a new immutable
  external snapshot for the next revision, then activate. Crashes leave a durable deny.
  Arbitrary out-of-band overwrites of an active external snapshot cannot be prevented
  by this database; no external registry/key integration or lifecycle endpoint exists.
  Lint/format/Pyright/import boundaries pass. Targeted lifecycle/authorization/grant/
  dispatch/reader/resolver/storage/readiness/migration selection: **150 passed**,
  1 warning, **205.31 s**, including disposable-only reset and migration idempotency.
  Expanded lifecycle selection: **9 passed**, 1 warning, **16.56 s**, adding concurrent
  CAS and shared-read/cancellation-release coverage. Full synthetic
  `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`:
  **945 passed**, 136 warnings, **1601.30 s**, including disposable-only reset/migration
  checks and full lint. Standalone ASGI ack/scheduled p95 **53.556/56.647 ms** and
  TCP **136.871/137.385 ms** pass unchanged **<200 ms** ack acceptance. Each transport
  completed 150 requests with 150 durable deliveries. Simultaneous TCP
  **1242.929/1462.160 ms** and graph-only **84.332 ms** remain diagnostic, not sub-200 ms
  simultaneous acceptance. Historical failures, development-only non-blocking decision
  and unverified host-capacity/root-cause caveats remain; no latency-only retry occurred.
  Gateway source now requires0340; persistent dev remains0300. No real credential/key
  lookup, live HTTP, persistent migration/reset, service or UI-guide change.
  See [global app lifecycle](./handoffs/2026-10-15-console-source-migration-plan.md#m6b-global-app-lifecycle-fence-2026-10-18).
- Trusted-storage continuation (2026-10-18): user selected external app secrets plus
  explicit PostgreSQL app/shop binding evidence, not arbitrary credential metadata.
  Added empty0330 immutable tenant-scoped bindings linked to immutable grants.
  Shop cipher is encrypted with injected crypto and cell AAD
  `ecommerce_shop_bindings.shop_cipher:<grant UUID>`; app key/secret remain external.
  Trusted exchange writer locks credential then grant and requires an exact current
  active grant. It cannot authenticate provider provenance by itself; no exchange,
  backfill, reconnection, automatic refresh or console/agent endpoint is introduced.
  Dispatch supplies its existing fenced connection to the bound reader/resolver.
  Stored loading joins exact tenant/record/shop/app/generation, active unexpired grant,
  non-null access token and unexpired credential, selecting no refresh token or `meta`.
  App mapping is validated before stored material lookup. Crypto failures are safe,
  explicit rejections; private values are never public response contracts.
  External registry changes are not DB-fenced: coordinated grant invalidation before
  app rotation/disable remains an operational prerequisite, not an automatic guarantee.
  Lint/format/Pyright/import boundaries pass. Targeted selection: **103 passed /
  1 test-fixture failure**, 1 warning, **140.87 s**. The foreign-tenant FK probe
  reused an existing binding key, so uniqueness correctly fired before the expected FK
  check; using an unbound foreign grant fixes the probe. Corrected focused test:
  **1 passed**, 1 warning, **14.41 s**, including RLS, FK, immutability and conflicts.
  Full `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`:
  **932 passed / 1 TCP latency failure**, 136 warnings, **1656.08 s**.
  All storage/reader/dispatch and disposable reset/migration tests pass; lint passes.
  Standalone ASGI ack/scheduled p95 **78.420/81.942 ms** passes; TCP
  **259.251/260.143 ms** fails unchanged **<200 ms** ack acceptance.
  Start-lag p95 ASGI/TCP **3.974/4.150 ms** does not establish the cause of the
  TCP acknowledgement failure. Each transport completed 150 requests and produced
  150 durable deliveries. Simultaneous TCP **1587.302/1922.243 ms** and graph-only
  **90.947 ms** remain diagnostic. No latency retry, threshold relaxation, new waiver
  or host/DB/handler root-cause claim. Overall acceptance remains blocked by latency.
  Gateway source now requires0330; persistent dev remains0300, with no migration,
  reset, real credential/key read, provider call, service change or UI-guide edit.
  The **907 passed** full result below belongs to the preceding resolver-contract slice.
  See [trusted storage scope](./handoffs/2026-10-15-console-source-migration-plan.md#m6b-trusted-shop-binding-storage-2026-10-18).
- Offline resolver-contract continuation (2026-10-18): added explicit injected trusted
  app/credential loaders, decryptor and caller-owned HTTP client, with immutable
  secret-masked material contracts. Exact context/app/generation and token-expiry
  checks precede decryption; expiry is checked again afterward. Uses existing
  `v1:` encryption and canonical row/field AAD via `credential_aad`; rejects malformed
  binding/version, empty token and crypto failure explicitly, without refresh/fallback.
  No default DB loader, settings/key lookup or runtime registration is provided.
  Trusted app registry and verified app/shop cipher provenance are required, not inferred
  from arbitrary `meta`. Row AAD alone does not prove tenant/app binding.
  Synthetic fixtures use an explicit synthetic AES-GCM key, wrong-AAD/tampering tests
  and a mocked fenced read; no real credentials or environment key is read.
  Synthetic resolver/reader/dispatch/authorization/shop/readiness/migration selection:
  **122 passed**, 1 warning, **141.49 s**, including real disposable-only
  `make db-reset` and migration idempotency. Repository lint/format/Pyright/import
  boundaries pass. Full `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`:
  **907 passed**, 136 warnings, **1617.69 s**, including disposable-only reset.
  Standalone ASGI ack/scheduled p95 **64.075/64.510 ms**; TCP
  **128.870/130.786 ms**, both pass the unchanged `<200 ms` ack criterion.
  Each transport completed 150 requests with 150 durable deliveries.
  Simultaneous TCP ack/scheduled p95 **2583.780/2639.902 ms** is diagnostic only,
  not a latency acceptance pass; graph-only p95 **100.422 ms**.
  This does not erase historical latency failures or establish stable host capacity.
  No schema/SQL/dependency/frontend change, live request, persistent reset/migration,
  service replacement, console/agent wiring or UI-guide edit. Eligibility and actual
  trusted storage/loader/decryption deployment remain gated.
  See [offline resolver contract](./handoffs/2026-10-15-console-source-migration-plan.md#m6b-offline-credential-resolver-contract-2026-10-18).
- Bound-reader continuation (2026-10-18): added server-only composition of an injected
  trusted bound reader, fresh shop verification under credential/grant fences, revised
  evidence provenance `fresh_authorized_shop_with_stored_grant_scopes`, and one native
  detail/search read of the persisted operation target. Invalid/inactive grant metadata
  is checked before resolver/verification HTTP; context mismatch fails before   shop lookup, for both access and refresh tokens.
  Consumption remains committed before the order call, with lifecycle recheck after
  that commit and no order retry/fallback after provider rejection or response loss.
  Synthetic reader/dispatch/authorization/adapter/readiness/migration selection:
  **184 passed**, 1 warning, **123.40 s**. Full lint/format/Pyright/import boundaries
  pass. Final `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`:
  **887 passed**, 136 warnings, **1957.34 s**; full lint and disposable reset checks pass.
  Standalone ASGI ack/scheduled p95 **96.040/100.194 ms** and TCP
  **140.022/140.805 ms** pass unchanged **<200 ms** checks. Each completed
  150 requests with 150 durable deliveries. Simultaneous TCP ack/scheduled p95
  **535.413/564.530 ms**, worker turn p95 **1748.987 ms**, 150 unique replies
  and graph-only p95 **92.783 ms** remain diagnostic, not sub-200 ms simultaneous
  acceptance. Historical failures remain; no latency-only retry, threshold change,
  new waiver or stable capacity/root-cause claim.
  No real resolver/decryption is implemented. The server resolver must authenticate
  tenant/record/shop/app/generation and app-ID-to-app-key mapping; a typed wrapper
  cannot authenticate arbitrary caller-supplied credentials. Injected client ownership
  stays with the caller; it must not retry/log signed requests. Verification failure
  occurs before order consumption; no automatic retry is introduced. All observed
  HTTP used synthetic MockTransport. No console/agent wiring, real credential lookup,
  provider call, migration, persistent reset, service change or UI-guide edit.
  User reports no registered company and inability to publish the TikTok Shop app.
  Developer/app eligibility is an external unresolved live-acceptance gate. No own-shop/
  unpublished exception has been verified for this market/account; individual seller
  eligibility or general TikTok developer sandbox access does not prove Shop API access.
  See [server-bound composition and remaining gates](./handoffs/2026-10-15-console-source-migration-plan.md#m6b-bound-reader-composition-2026-10-18-offline-continuation).
- Authorized-shop continuation (2026-10-18): user revised per-operation policy to
  **fresh shop match + stored verified grant scopes**, with current order permission
  enforced by the actual order endpoint. Shop lookup is not fresh order-scope evidence;
  no introspection endpoint was verified or invented.
  Official rendered v202309 docs confirm signed GET `/authorization/202309/shops`,
  header token, no shop-cipher query and response `data.shops` IDs/ciphers.
  Added exact configured-shop/cipher matching with no cached success, strict malformed/
  duplicate rejection, secret-masked internal cipher and safe failures. Returns no
  provider payload and does not mint evidence or authorize tenant/app/generation.
  Synthetic shop/native/legacy/evidence selection **138 passed**, **3.49 s**;
  full lint/format/Pyright/import boundaries pass. Final
  `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`: **877 passed**,
  136 warnings, **1785.66 s**; full lint and disposable-only reset checks pass.
  Standalone ASGI ack/scheduled p95 **64.403/64.999 ms** and TCP
  **149.340/152.742 ms** pass unchanged **<200 ms** checks. Both transports
  completed 150 requests with 150 durable deliveries. Simultaneous TCP ack/scheduled
  p95 **450.294/470.276 ms**, worker turn p95 **1760.904 ms**, 150 unique replies
  and graph-only p95 **90.034 ms** remain diagnostic evidence, not sub-200 ms
  simultaneous acceptance. Historical failures remain; no latency-only retry,
  threshold change, new waiver or stable laptop capacity/root-cause claim.
  Trusted server adapter binding, revised evidence provenance and fenced order integration
  remain unimplemented. Existing evidence labels must not be treated as proof of fresh
  scopes from this primitive. No real seller request/account/credential inspection,
  schema/dependency/frontend change, persistent migration/reset, rollout or UI-guide edit.
  See [revised policy and verified shop primitive](./handoffs/2026-10-15-console-source-migration-plan.md#m6b-fresh-authorized-shop-primitive-2026-10-18-offline-continuation).
- Credential/read-fence continuation: user selected conservative fenced ordering and
  no automatic replay. Added0320 with DB-managed credential generations, atomic grant
  disconnection on material credential change/deletion, and tenant-scoped immutable
  read targets plus single-use consumption. Dispatch commits consumption before the
  send attempt, then rechecks credential/grant under locks held through a bounded
  trusted callback. Revocation in the commit gap prevents dispatch; uncertainty after
  consumption never permits replay. New operation/verification required per detail/page.
  This is internal synthetic-only orchestration, not a credential resolver/live verifier,
  adapter integration or provider-side exactly-once guarantee. New gateway expects0320;
  persistent dev remains0300, with no rollout/migration/reset or credential inspection.
  Repository lint/format/Pyright/import boundaries pass. Final targeted selection:
  **183 passed**, 1 warning, **135.22 s**, including migration idempotency, gateway0320
  readiness and real `make db-reset` on disposable containers only.
  Final `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`: **857 passed**,
  136 warnings, **2823.83 s**. Full lint and disposable-only reset checks pass.
  Standalone ASGI ack/scheduled p95 **156.515/165.059 ms**, TCP
  **146.065/147.137 ms** pass unchanged **<200 ms** checks. Both transports
  completed 150 requests with 150 durable deliveries. Simultaneous TCP ack/scheduled
  p95 **1418.025/1485.486 ms**, worker turn p95 **2495.281 ms**, 150 unique replies
  and graph-only p95 **104.453 ms** are diagnostic evidence, not sub-200 ms
  simultaneous acceptance. Prior **831 passed / 1 ASGI latency failure** remains
  historical evidence. No threshold change, latency-only retry or new waiver;
  this pass does not establish stable laptop capacity or fix earlier latency causes.
  Updated the prior deletion test for intentional atomic disconnection plus retained audit.
  UI-guide/frontend/dependencies remain untouched.
  Inspected existing latency evidence: failing ASGI start-lag p95 **474.326 ms** and
  earlier failing TCP **447.883 ms**, versus prior passing **5.250/3.660 ms**.
  Delay occurs before HTTP entry as well as inside measured ack; p95 values cannot be
  subtracted to isolate a stage. Standalone workers start only after ingress. Current
  logs do not establish host/CPU/DB/pool cause; no latency retry or threshold change.
  See [fencing design, tests and latency limits](./handoffs/2026-10-15-console-source-migration-plan.md#m6b-credentialread-fences-2026-10-17-offline-continuation).
- Durable-grant continuation: added forward-only **0310**, internal async repository
  and parameterized SQL for verified grant metadata, exact local-store creation
  matching, tenant-scoped reads and compare-and-swap terminal invalidation.
  Active grants may become revoked/disconnected once; revision increases atomically
  and evidence fields cannot be rewritten. Database-triggered append-only lifecycle
  events commit/roll back with the grant change. Explicit privileges override broad
  inherited defaults; RLS is enabled without FORCE. No existing credential/grant is
  imported or inferred. Local store metadata still reports unverified/unavailable.
  Synthetic grant/authorization/store/adapter/migration/reset selection **154 passed**,
  1 warning, **70.14 s**; full lint/format/Pyright/import boundaries pass.
  Migration idempotency and real `make db-reset` pass on disposable test containers
  only. Initial full lint/test: **828 passed / 4 failed**, 136 warnings,
  **1419.40 s**. Gateway expected0300 while the shipped migration set reached0310:
  two health checks failed and both TCP benchmarks stopped at readiness503 before
  measurement. These were not measured TCP latency failures. ASGI ack/scheduled
  p95 **108.097/109.660 ms** passed; graph-only p95 **92.040 ms**.
  Corrected the gateway expected version to0310. Follow-up readiness/migration/grant
  selection **23 passed**, 1 warning, **54.29 s**, including disposable reset.
  Corrected full lint/test: **831 passed / 1 failed**, 136 warnings, **2219.18 s**.
  Full lint and disposable-only reset checks pass; readiness failures are resolved.
  Only failure: `test_real_ingress_50rps_and_durable_queue[asgi]`, ack p95
  **681.146 ms** (unrounded 681.146350922063), against unchanged **<200 ms**;
  scheduled ack p95 **817.483 ms**, start lag p95 **474.326 ms**.
  TCP ack/scheduled p95 **145.722/150.077 ms** passes. Both transports completed
  150 requests with 150 durable deliveries. Simultaneous TCP ack/scheduled p95
  **514.618/545.924 ms**, worker turn p95 **2241.174 ms**, 150 unique replies
  and graph-only p95 **101.987 ms** are diagnostic evidence, not sub-200 ms
  simultaneous acceptance. Full validation is **not green**. No further latency retry,
  threshold change or new waiver; laptop causality remains unproven.
  **0310 is not applied to persistent dev**, whose last approved schema remains0300.
  No token columns were selected, no credentials inspected/decrypted and no services,
  infrastructure/webhooks or UI-guide changed. No frontend or dependency change.
  This repository is not an authorization service: loaded state is not a lock/lease
  through dispatch or proof that the credential's current generation still matches.
  Credential deletion preserves grant/audit history; missing/replaced credentials must
  fail a future current-credential check. Rotation/reconnection/reauthorization and
  operation consumption are deliberately unsupported, not silently reactivated.
  Trusted per-operation verification, current-credential generation fencing and
  atomic lifecycle-versus-read dispatch enforcement remain required before live reads.
- Authorization-evidence continuation: user explicitly selected **per-operation**
  verification, not a cached freshness interval. Added immutable internal context,
  grant-state, operation and verifier-evidence contracts plus a fail-closed order-read
  metadata gate. Exact tenant/record/platform/store/app/credential-generation binding,
  grant identity/revision, documented order and authorized-shop scopes, aware timestamps
  and expiry are checked. Unknown evidence, revoked/disconnected state, prior-operation
  evidence and mismatched revisions are rejected with fixed safe error codes.
  Every detail/page read will require a new server-created operation; no silent reuse
  between pages or cached evidence is permitted by the selected design.
  Synthetic authorization/store/native/legacy selection **121 passed**, **4.41 s**;
  full lint/format/Pyright/import boundaries pass. Final
  `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`: **819 passed / 1 failed**,
  136 warnings, **2740.30 s**. Only failure:
  `test_real_ingress_50rps_and_durable_queue[tcp]`, ack p95 **643.091 ms**
  (unrounded 643.0913070216775), against unchanged **<200 ms**; scheduled ack
  p95 **853.983 ms**, start lag p95 **447.883 ms**. ASGI ack/scheduled p95
  **90.558/91.601 ms** passes. Both transports completed 150 requests with
  150 durable deliveries. Simultaneous TCP ack/scheduled p95 **538.030/551.924 ms**,
  worker turn p95 **1847.232 ms**, 150 unique replies and graph-only p95
  **104.030 ms** are diagnostic evidence, not sub-200 ms simultaneous acceptance.
  Disposable-only reset checks pass; persistent dev was not reset. Full validation
  is **not green**. No latency retry, threshold change or new waiver; laptop
  causality remains unproven. The preceding snapshot run's **784 passed** remains
  historical evidence, not the current full result.
  This pure gate checks trusted metadata only: it does not prove evidence provenance,
  fetch current grant state, consume an operation, prevent concurrent revoke/dispatch
  races or authorize the existing adapter by itself. It remains unwired to any browser,
  agent, credential resolver or provider call. A durable state/verifier/atomic dispatch
  service is required before any live claim. No DB schema/dependency/frontend change,
  credential inspection, persistent reset, service replacement, rollout or UI-guide edit.
- Provider-native continuation: added immutable typed TikTok order/payment/line snapshots
  and explicit detail/page adapter methods, separate from normalized `EcommerceOrder`.
  They retain distinct line IDs, repeated SKU lines, `sale_price` and decimal precision
  without fabricating quantity, unit price, currency or price totals. Duplicate line IDs
  and malformed required fields reject the whole read; buyer/address/arbitrary fields
  are excluded. Shared request/page guards preserve signing, cardinality, identity,
  opaque tokens, bounded pages and safe failures for both native and legacy methods.
  Synthetic native/legacy selection **82 passed**, **3.48 s**; repository lint, formatting,
  Pyright and import boundaries pass. Final
  `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`: **784 passed**,
  136 warnings, **2757.17 s**; full lint and disposable-only reset checks pass.
  Standalone ASGI ack/scheduled p95 **102.295/103.366 ms**, TCP
  **189.661/190.897 ms** pass unchanged **<200 ms** checks. Both transports
  completed 150 requests with 150 durable deliveries. Simultaneous TCP ack/scheduled
  p95 **1256.362/1467.896 ms**, worker turn p95 **2621.009 ms** and 150 unique
  replies remain diagnostic evidence, not sub-200 ms simultaneous acceptance.
  Graph-only p95 **103.014 ms**. No latency retry, threshold change or new waiver;
  historical failures remain and this pass does not establish laptop capacity.
  No frontend/console route/schema/dependency change, live provider request, credential
  inspection/resolution, persistent reset, rollout or UI-guide edit.
  This is a selected internal provider projection, not browser wiring or full provider
  compatibility. Quantity/bundle/discount semantics and durable grant/revocation/
  credential-generation binding remain gates before normalized/live store reads.
- Official-spec continuation: inspected rendered TikTok API Reference documentation only.
  Verified v202309 detail uses `GET /order/202309/orders?ids=...` and `data.orders`;
  search `page_size`/`page_token` are signed query parameters, not body fields.
  Corrected requests/envelope handling with exact identity/cardinality checks and
  comma-ID rejection. Targeted adapter/ecommerce **47 passed**, **3.22 s**.
  Final `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`: Ruff/format/Pyright/
  import-linter pass; **749 passed**, 136 warnings, **2783.22 s**. Standalone ASGI
  ack/scheduled p95 **102.572/105.608 ms** and TCP **150.074/151.481 ms** pass unchanged
  **<200 ms** checks. Each transport completed150 requests with150 durable deliveries.
  Simultaneous TCP ack/scheduled p95 **1184.752/1213.678 ms**, graph-only p95 **97.809 ms**
  remain diagnostic evidence: the suite pass is not a claim that simultaneous ingress
  meets200 ms. Disposable-only `make db-reset` regression checks pass; no persistent reset.
  No latency retry/threshold change or new performance waiver. Historical failures remain;
  one passing run does not establish laptop capacity or a root cause for earlier failures.
  Docs also confirm order/authorized-shop required scopes, but no existing grant was
  checked. Documented durable scope/expiry/revocation/generation binding requirements;
  no guessed evidence freshness interval or credential resolver was added.
  Important remaining blocker: documented line price is `sale_price`, and no quantity
  field appears in the inspected line schema. Legacy normalized fixtures/parser still
  require quantity and `sku_sale_price`; documented-shape lines fail explicitly, not
  invented quantity1. Request correction is not full response compatibility or live wiring.
  No account/credential inspection, seller API call/testing tool, migration, restart,
  persistent reset, infrastructure/webhook or UI-guide change. See
  [official references, corrections and remaining gates](./handoffs/2026-10-15-console-source-migration-plan.md#m6b-official-tiktok-request-verification-2026-10-17-offline-correction).
- Store-identity continuation: added staff-only local store-record list/detail and UI,
  platform filters, bounded stable UUID pagination, and immutable exact tenant/record/
  platform/store binding. Queries select identity/expiry/update columns only, never
  tokens or `meta`; no credential read/decrypt or presence/permission inference.
  Authorization remains `unverified`, permissions `unknown`, provider reads `unavailable`.
  Expiry states are local timestamps only, not authorization. Missing store IDs stay
  visible but cannot satisfy binding. Unsupported non-marketplace records are excluded.
  Initial backend/contract selection **15 passed**, 1 warning, **229.13 s**; final pure
  contracts **3 passed**, **1.70 s** (overlap, one additional no-authority contract case).
  Frontend **15 transport + 2 state tests**, lint/types/build/trace boundary pass.
  Prior store-slice `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`: Ruff/format/Pyright/
  import-linter pass; **742 passed / 1 failed**, 136 warnings, **2766.25 s**.
  Only failure: `test_real_ingress_50rps_and_durable_queue[tcp]`, ack p95
  **200.161 ms** (unrounded200.16062981449068) against unchanged **<200 ms**,
  scheduled ack **204.904 ms**. This remains a failure, not rounded into a pass.
  ASGI ack/scheduled p95 **98.408/102.235 ms** pass. Both transports completed150
  requests with150 durable deliveries. Simultaneous TCP ack/scheduled p95
  **899.339/912.837 ms**, graph-only p95 **92.619 ms** are diagnostic evidence.
  Disposable-only `make db-reset` checks pass; persistent dev was not reset.
  Full validation is **not green**. No latency retry, threshold change or new waiver;
  laptop causality remains unproven.
  No live account/credential inspection, provider call, persistent-dev reset/restart,
  migration, infrastructure/webhook or UI-guide edit. No new browser acceptance claimed.
  This is metadata binding, not a provider credential resolver or a legacy adapter fix.
  See [local store-identity scope and remaining gates](./handoffs/2026-10-15-console-source-migration-plan.md#m6b-local-store-identities-and-binding-offline-continuation).
- Local source/store-filter continuation: order list/detail/cursors now enforce tenant
  plus exact persisted channel and optional platform-scoped store ID. Store-only
  queries fail400; invalid channel/store bounds422; mismatched resources/cursors404.
  All-source defaults preserve missing-store orders. UI explicit Apply remounts order
  paging on scope changes; local inventory remains clearly tenant-wide, not store stock.
  Backend/contract selection **10 passed**, 1 warning, **166.26 s**. Isolated frontend
  **14 transport + 2 state tests**, lint/types/production build/trace checks pass.
  Prior filter-slice `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`: Ruff/format/Pyright/
  import-linter pass; **736 passed / 1 failed**, 136 warnings, **2713.67 s**.
  Only failure: `test_real_ingress_50rps_and_durable_queue[tcp]`, ack p95
  **235.195 ms** against unchanged **<200 ms**, scheduled ack **240.766 ms**.
  ASGI ack/scheduled p95 **104.607/106.695 ms** pass. Each transport completed150
  requests with150 durable deliveries. Simultaneous TCP ack/scheduled p95
  **574.772/588.553 ms**, graph-only p95 **98.615 ms** are retained diagnostic metrics.
  Disposable-only `make db-reset` regression checks pass; persistent dev was not reset.
  No latency retry/threshold change/new waiver. Full validation remains **not green**.
  Browser replay is not verified: temporary synthetic API/frontend were HTTP-ready,
  but browser connections to both loopback hostnames were refused. Fixture processes
  were stopped and the one-shot helper removed; no forwarding/infrastructure changes.
  No shared TikTok account/credential inspection, provider call, persistent reset,
  service replacement, migration or UI-guide edit. Provider/store credential binding
  remains pending, not implied by local matching. See the [local filter evidence](./handoffs/2026-10-15-console-source-migration-plan.md#m6b-local-sourcestore-filters-offline-continuation).
- M6B continuation: offline TikTok order-read contract groundwork is implemented.
  Detail/search now reject provider errors, incomplete/malformed orders, mismatched
  detail IDs and invalid pages rather than fabricate successful default data.
  Typed one-page reads preserve continuation tokens, Decimal money and carrier references;
  legacy first-page list shape remains. Read errors expose safe codes, not raw provider/
  transport/validation contents; reads do not place access tokens in URLs or follow redirects.
  Targeted synthetic adapter/ecommerce checks: **41 passed**, 2.51 s.
  Prior adapter-groundwork `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`: Ruff/format/Pyright/
  import-linter pass; **732 passed / 1 failed**, 136 warnings, **2701.96 s**.
  Only failure: `test_real_ingress_50rps_and_durable_queue[tcp]`, ack p95
  **608.321 ms** against unchanged **<200 ms**; scheduled ack **627.508 ms**.
  ASGI ack/scheduled p95 **185.855/198.599 ms** pass. Each transport completed150
  requests with150 durable deliveries, not a latency waiver. Simultaneous TCP
  ack/scheduled p95 **1337.291/1362.941 ms**, graph-only p95 **96.771 ms** are
  diagnostic evidence, not a passing TCP latency claim. Disposable-only reset checks pass.
  One initial full run was stopped before completion while finalizing the redirect guard;
  no result is claimed for it. No latency retry/threshold change was made.
  Full validation remains **not green**; no new M6 performance waiver is implied.
  No console/provider wiring, live credentials, store claims or product/write changes.
  Current official API compatibility and tenant/store credential binding remain gates,
  not assumed complete. See the [M6B groundwork and verification gates](./handoffs/2026-10-15-console-source-migration-plan.md#m6b-order-read-contract-groundwork-offline-not-provider-verification).
- User requested a multi-platform seller workspace, TikTok-first, and approved **staff-only
  business reads** plus **read-only local stock with no synchronization authority**.
  Expanded [M6 roadmap](./handoffs/2026-10-15-console-source-migration-plan.md#m6--business-tabs-integrations-and-staff-preparation)
  stages local reads, verified seller reads/connections, separately authorized TikTok
  operations, multi-platform inventory decisions, and original invoice/staff preparation work.
- Implemented separate `/business` and `/portal/business` UI plus typed local
  `/console/v1/business/orders` and `/products` lists/scoped summary-detail reads.
  Bounded keyset pagination; decimal monetary fields; orders include persisted channel,
  store/order identifiers and tracking/carrier references. Products show local
  stock/reserved/available counts. No address/contact/credential/arbitrary attributes in
  these projections. Readers/machines/anonymous denied; staff tenant scoping preserved.
- Explicit UI labels distinguish local update from sync freshness, local catalogue from
  published listings, tracking references from live carrier tracking, and missing local
  rows from a marketplace with no data. Marketplace sync, connections, listing/stock/
  fulfilment/refund/payment writes and later tickets/invoices/preparation are unavailable,
  not fake working controls. Existing TikTok adapter is not wired by the empty
  `EcommerceAgent()` runtime constructors; no live connection was verified.
- Targeted backend/contract selection: **6 passed**, 1 warning, 77.60 s. Isolated
  frontend: **13 transport + 2 state tests**, ESLint, Next type generation, TypeScript,
  production webpack build and trace-boundary checks pass. Initial frontend lint caught
  synchronous effect state resets; moved resets to explicit navigation/refresh actions,
  then reran checks.
- M6A foundation full `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`: Ruff/format/Pyright/
  import-linter pass; **695 passed / 1 failed**, 136 warnings, **1795.08 s**. Only failure:
  `test_real_ingress_50rps_and_durable_queue[tcp]`, ack p95 **788.334 ms** against unchanged
  **<200 ms** (scheduled ack **1034.766 ms**). ASGI ack/scheduled p95
  **118.552/119.057 ms** pass. All150 TCP requests completed with150 durable deliveries;
  this does not satisfy latency acceptance. Simultaneous TCP ack/scheduled p95
  **514.905/540.197 ms**, graph-only p95 **100.350 ms** are retained diagnostic evidence,
  not a threshold waiver. Guarded disposable-only `make db-reset` regressions pass.
  No latency retry or threshold change was made. Full validation is **not green**;
  M4's laptop-dev exception is not an M6 performance waiver, and laptop causality
  remains unproven. M6A live acceptance/performance sign-off remain open.
- No persistent migration/reset, service replacement, provider calls, password/token
  inspection, production/infrastructure/webhook change, dependency change or UI-guide edit.
  Existing dirty M5 implementation preserved. Last approved dev build is M5, not M6.
- Current read-only environment probe (2026-10-17 about 07:04 UTC) found no listeners
  on18081/13000; recorded prior M5 PIDs are no longer present. This continuation did
  not stop or restart them. Prior live acceptance is historical evidence; do not claim
  the console is currently available. Service restoration/replacement and live M6
  acceptance remain separately approval-gated.

## M5 Telegram customer inbox (2026-10-17 implementation record; guarded dev rollout 2026-10-16 UTC)

- User explicitly selected Telegram customer conversations and outbound-reply approvals
  for platform admins (explicit tenant), tenant admins and tenant staff. Readers and
  machines are denied, including message reads. Customer-web and order decisions are
  deferred. Private sandbox histories, contacts and held turn fields remain excluded.
- Added separate queue/detail UI and typed `/console/v1/inbox` reads, takeover, release,
  human replies and structured shared approval transitions. Decisions use verified
  console user UUIDs, CSRF/RBAC/tenant checks, one shared domain implementation and
  atomic console/domain audit and outbox. Telegram edit/action binding remains intact;
  acknowledgement text now says queued, not delivered.
- Indefinite ownership and model bypass match LG04. Locked egress/holds/fallback and
  delivery checks prevent stale automatic sends across takeover/release. A matching
  escalation permits the intentional code-selected handoff acknowledgement; explicit
  staff replies retain their own authority. Delivery claiming orders each conversation
  through retries. A provider-accepted send still cannot be recalled or made exactly once.
- Additive 0300 introduces the suppression/version fields, message provenance, replay
  receipts and indexes with staged RLS, no FORCE. After disposable validation and separate
  explicit approval, persistent dev advanced **0290 → 0300** without reset and guarded
  services were replaced. No environment-file, webhook, upload or production change.
- The checkout was clean at `d8f7149` on entry. Existing user review files and UI-guide
  state are preserved. No commit was requested or made.
- Post-review offline validation passes. Final exact results belong in
  [the M5 evidence record](./handoffs/2026-10-17-m5-telegram-inbox.md).
  M4's accepted laptop-dev exception remains; no threshold or release waiver changed.
- Implementation review fixed three coupled gaps before rollout:
  - Takeover of an existing timed escalation must clear its expiry and establish
    indefinite ownership, not return already-handled. The detail UI now offers
    **Take over indefinitely** for a temporary pause.
  - Escalation now checks current automation authority under the conversation lock
    before tickets/notifications/mute effects; stale in-flight work cannot replace
    indefinite takeover with a 24-hour mute or re-mute an explicitly released turn.
  - Existing Telegram order callbacks now commit metadata-only completed ingress
    provenance with their customer message/outbox. The new strict delivery route
    binding otherwise rejected these replies after a successful order decision.
    Console order decisions stay deferred; no transport/binding guard was relaxed.
- Review targeted tests: **59 passed**, 23 warnings, 142.23 s (inbox, staff, coordinator),
  plus **2 passed**, 3 warnings, 22.56 s (actual order approve/reject callback-to-delivery
  path with mocked Telegram). Five new regression cases in total; selections overlap
  the full suite. Final isolated frontend **12 transport + 2 state tests**, lint/types/
  production build and safe traces pass. Post-review full result is recorded below.
- The prior completed readiness/load follow-up was **7 passed / 1 ASGI latency failure**,
  not an in-progress run. Its corrected expected schema 0300 fixes the earlier four
  readiness failures; it does not establish green performance acceptance.
- Review changes were offline. At that review stage no persistent migration/reset, service replacement,
  credentials inspection, live external send, production/infrastructure/webhook change
  or UI-guide modification. Keep rollout and live acceptance separately approval-gated.
- Final post-review `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`:
  **692 passed**, 136 warnings, 1645.86 s. Ruff/format/Pyright/import-linter pass;
  actual fingerprint-guarded disposable-only `make db-reset`/0300 and staged RLS pass.
  Unchanged ingress-only 50-rps acknowledgement/scheduled p95: ASGI
  **108.284 / 109.555 ms**, TCP **174.623 / 176.324 ms**, both `<200 ms`, each
  150 completed turns and 150 pending deliveries. Separate simultaneous-capacity test
  passes with 150 unique replies and worker-turn p95 **2690.479 ms** (ack **382.791 ms**,
  distinct criteria); graph-only p95 **88.396 ms** passes. No thresholds, pool sizes,
  tracing or durability were relaxed. Historical failures and the laptop-dev exception
  remain context, not an established cause or production-capacity guarantee.
- Subsequent explicit user approval authorized forward-only 0300, coordinated dev
  replacement and authenticated/synthetic Telegram acceptance. Exact dev target/role
  guards passed; only pending 0300 applied atomically, without reset or password
  provisioning. Gateway/worker/delivery and isolated frontend replaced; health/readiness/
  login 200 and dedicated heartbeats fresh. Initial frontend test-origin rewrite caused
  500; rebuilding isolated source with actual **18081 at build time** fixed it.
- Authenticated Synthetic Admin acceptance: scoped reads, indefinite takeover, three
  completed real Telegram turns without automatic output while muted; shared-service
  synthetic reply approvals; human reply commit/response loss/same-key retry with
  **one message/delivery/receipt/attempt**; approve/edit delivered once each; reject audited
  without fallback while paused. User confirmed the three labeled receipts exactly once.
  Stale version409, other-tenant404, private-sandbox404, distinct bounded message pages,
  and reload recovery of the same delivered message/settled cards pass.
- Deliberate release left the synthetic Telegram conversation AI-owned at version4.
  Fresh post-boundary trace `b9b49cba-5e4d-4fd6-9576-2c050fdad52b` produced the delivered
  seventeen-day KB answer, user-confirmed; it is the only new automatic delivery since
  takeover. No stale paused turn emitted after release.
- Live reader replay **blocked**: user forgot password and encountered login throttling.
  No reset/bypass/retries; reader/machine and tenant-staff/admin authority retain automated
  evidence only. See [exact rollout evidence and active dev handles](
  ./handoffs/2026-10-17-m5-telegram-inbox.md#approved-guarded-dev-rollout-and-live-acceptance-2026-10-16-utc).
  Dirty worktree/UI-guide preserved; no production, infrastructure, webhook or commit changes.
- Subsequently the user explicitly approved recovery of both synthetic dev console logins.
  User-operated hidden terminal prompts set new passwords privately; exact-dev guarded
  transaction reused existing identity hashing/SQL, revoked sessions/links, cleared
  targeted throttling and audited both recoveries at **2026-10-16 20:54:24 UTC**.
  Metadata verified both enabled with zero active sessions/links immediately afterward.
  No password/hash was printed; database-role passwords, bot/API/encryption credentials
  and production were untouched. Live reader replay remains pending, no longer treated
  as requiring the forgotten old password. New low-count login attempts after recovery
  are not evidence that the prior lockout was retained.
- Reader live replay subsequently **passes (2026-10-16 21:02 UTC)**: verified normal
  `read_only` identity200; no inbox navigation; direct `/portal/inbox` permission denial
  without transcript/approval content. Conversation list/detail/messages/approvals reads
  and CSRF-valid takeover/release/reply/approval-decision probes all return **403**.
  No accepted mutation, send, credential inspection or role elevation. Tenant-admin/staff
  browser replay remains pending; the reader-specific gap is closed.
- Subsequent approved separate role fixtures completed normal private invitations:
  `m5-tenant-admin@example.test` and `m5-tenant-staff@example.test`. Both verified
  roles/tenant, inbox reads200, foreign-tenant403 and private-sandbox404. Each exercised
  UI takeover/human reply/release; one delivered message/attempt/receipt each, user-confirmed
  exactly once in dev Telegram. Staff replay of settled approval1 returns already_handled,
  queued=false. Reader remains enabled/read_only; final conversation AI-owned/version8.
  Fresh approval decisions were live-tested as platform admin, with the full per-role
  decision matrix still automated; separate M3 KB role replay is not closed by this.

## Historical incident and recovery: unintended isolated-dev schema reset (2026-10-16)

During runtime-stabilization validation, the assistant intended to run `make db-reset`
against a newly created disposable Postgres container. It passed that container's
host/port/credentials but failed to override `PSQL`. Host `psql` was unavailable, and
the Makefile's fixed fallback used `docker exec csa_dev_db psql`, ignoring the selected
disposable target for destructive baseline commands. The persistent isolated-dev
`public` and `lg` schemas were dropped and the baseline recreated there; the subsequent
Python migration step connected to the intended disposable database and failed because
its baseline was absent. This was an assistant execution/safety error, not an authorized
reset of persistent dev.

Initial read-only verification confirmed zero clients and no console users, KB upload
metadata, migration ledger or checkpoint schema. Earlier live acceptance records below
are historical, not evidence for the rebuilt stack. The initial full validation run was
stopped. No production database connection or change was performed.

The user subsequently chose and explicitly approved a **clean dev rebuild**, not backup
restoration. Recovery progress:

- Removed the fixed-container `psql` fallback. Reset preflight rejects unsafe targets
  and compares PostgreSQL system identifier/database through the selected `psql` command
  and Python DSN before DDL. Makefile and Compose now both default to dev port 5434.
- **13 focused reset-guard tests pass**; a fingerprint-verified `make db-reset` passed
  on an explicitly named disposable container. A separate two-container mismatch check
  refused the target and preserved both sentinel rows.
- Verified persistent `csa_dev_db` belongs to Compose project `csa-dev`, service
  `postgres`, with only loopback port 5434. Applied migrations through **0280** to the
  already-recreated baseline, **without another schema drop**, then recreated checkpoints.
  App and identity roles authenticated successfully; the existing read-only role
  password verifier was preserved.
- Recreated the two synthetic acceptance/development tenants with their prior UUIDs and
  nine disabled capability entries each, using maintenance audits with no impersonated
  human actor. Verified `@CSAV2_bot` via read-only API and recreated the DB mapping for
  its existing webhook. No webhook, Caddy, Cloudflare or n8n setting was changed.
- Two files remain in the dedicated upload root; none was deleted or adopted. Their
  lost metadata/content integrity has **not** been recovered. Historical records,
  accounts, KB documents, subscriptions and enabled switches have **not** been restored.
- Gateway readiness and frontend login HTTP 200 pass after restart. Frontend restarted
  after the exact old dev
  PID retained its Next lock following TERM and required KILL. Fresh administrator
  bootstrap completed privately; the user reports login and required password change
  completed, and a read-only DB check confirms one enabled platform administrator with
  no pending password change. Fresh KB/Telegram acceptance subsequently passed below. Guarded
  conversation and delivery tasks were restarted; their heartbeats are fresh. The
  conversation task enables KB ingestion against dedicated storage/loopback TEI (no
  separate duplicate KB worker). Scheduled jobs, including mute expiry, have successful
  tenant-scoped `job_runs`; this alone is not evidence of a live expired-row transition.
- Recovery acceptance exposed two safe onboarding defaults that required deliberate
  rollout: `onboarding` tenants are not eligible for the normal KB poller, and
  `config.policy.deployment_mode=shadow` suppresses sends. Only the recreated Telegram
  tenant was made `pilot`; the user then explicitly approved its gate policy `auto`,
  with maintenance audits. M1 remains onboarding/disabled. No historical turns replayed.
- Fresh FAQ job `5f066252-1c96-45c0-af90-1952bb8096d5` completed on attempt 1 with
  two chunks. The user reported a transient upload 500; frontend logs contain proxy
  socket-hang-up errors, but the precise cause is not established. No duplicate upload
  was needed. Do not claim that incident fixed.
- Fresh Telegram trace `202ac6db-acf8-4a4a-a03f-105512bbe71d` completed on attempt 1:
  `kb_agent` succeeded, one fact, verification true, gate `auto/send`, one message,
  outbox delivered on attempt 1. User confirmed the seventeen-day answer.
- Verified only the two requested environment values after user authorization:
  blank upload root/ingestion false remain in the environment file; the approved running
  worker explicitly overrides these with dedicated storage/ingestion true. No secret
  values were displayed and no environment file was edited.
- Fresh `NO_LOCAL_ENV=1 ENV=ci make lint test`: lint passed, **644 passed / 3 failed**.
  ASGI ingress p95 **203.61 ms**, TCP p95 **530.46 ms** exceeded unchanged **200 ms**
  thresholds. The third failure was the existing reset test's separate database name
  being correctly refused by the new strict target guard. That test now uses its own
  disposable container/database `csa`, without weakening the guard; an actual make-level
  mismatch regression verifies both databases retain sentinel rows.
- Combined targeted rerun: **17 passed / 2 failed** (reset fixture credential mismatch,
  subsequently corrected; unchanged TCP p95 **363.48 ms** still exceeds **200 ms**).
  ASGI passed in that rerun. Final reset-only selection after correction:
  **15 passed**, covering the actual make-level mismatch/sentinel regression and
  successful reset/migrations/RLS check in a dedicated disposable container.
  Final `make lint` passed at that stage. Validation was still non-green then;
  the subsequent investigation and full-suite result are recorded below. Do not
  repeatedly rerun tests or relax thresholds merely to manufacture an acceptance result.

Before this incident, the new mute-expiry/runner failure-health fixes and concurrency/
worker-recovery selection passed **40 tests**. The changes remain uncommitted.
Subsequent offline validation is green below; live rollout/acceptance of the logging
change remains pending, and the historical hang/500 causes are not proven.

## Ingress latency and gateway/proxy investigation (2026-10-16)

- Safety: no environment snapshots or secrets read, persistent-dev DB connections,
  resets, webhook changes or production/Caddy/Cloudflare/n8n changes. Tests use
  synthetic fixtures/disposable containers. At inspection neither dev gateway 18081
  nor frontend 13000 was listening; the earlier acceptance was historical, not a
  claim that these services are still available. Existing services were not restarted.
- Baseline TCP benchmark reproduced failure at **277.25 ms p95** versus unchanged
  **<200 ms** at 50 requests/s. A diagnostic TCP run passed at **147.00 ms**;
  diagnostic ASGI showed **492.27 ms ack p95**, **517.92 ms scheduled ack p95** and
  **148.45 ms start-lag p95**. Temporary SQL timing instrumentation showed delay
  distributed across sequential statements (roughly 13–18 ms mean per ingress
  lookup), not one uniquely slow query. Profiling and host load affect these runs:
  this does not prove a PostgreSQL bottleneck or justify relaxing thresholds.
- Consolidated credential/runtime reads into one tenant-scoped SQL lookup (six
  fetches instead of seven for the normal first-contact path), with no cache or
  weakened authentication/RLS. Regression checks joined metadata, policy override
  precedence, foreign-tenant isolation and query count. Existing n8n rollback/shadow,
  callback and idempotency behavior remain covered using mocks, not live n8n.
  Combined ingress/cutover/ambiguous-commit/both unchanged latency checks: **19 passed**.
  A full-suite repeat is required before a stable performance claim.
- Reproduced liveness failure in an isolated subprocess using the **actual gateway**
  and deliberately undrained stdout: health 200 before pressure, then health timeout,
  with the event-loop stack blocked in `structlog/_output.py`. No database connection
  or external request was used. This proves a failure mode, **not** the cause of the
  historical hang, whose live process stack is unavailable.
- User explicitly approved bounded background logging with observable failure.
  Structured/stdlib logging now formats on the producer (preserving context), then
  writes on a daemon thread: at most 1,024 waiting events plus one active write.
  A sink older than five seconds makes readiness 503; overflow or writer error
  latches an explicit, secret-free reason and `dropped_events` count until logging
  is reconfigured/restarted. Overflow cannot block a request or grow an unbounded
  backlog. Liveness remains 200. Shutdown drains with a bounded deadline and reports
  failure rather than waiting forever. Output still requires a functioning collector;
  full-queue events cannot be retained and are counted, not silently hidden.
- New real-subprocess regression fills stdout and verifies the pressure request
  completes, health stays 200 and readiness reports 503/queue-full/nonzero drops.
  Logging/gateway/identity-lifecycle/poller selection **29 passed**, including exact
  4.999-second/5.001-second stall threshold and recovery without event loss;
  `make lint` passed.
  This change has **not** been loaded into or accepted against the live dev stack.
- The installed Next.js proxy maps an upstream socket error to HTTP 500. A new
  loopback test reproduces that after a simulated durable upload, then verifies an
  **explicit** same-key multipart retry returns the existing job, preserving CSRF
  and file contents without automatically replaying mutations. Frontend transport
  selection **9 passed**. The real database lost-COMMIT-acknowledgement regression
  also passes. These explain how a queued job can survive a 500, **not why the original
  upstream connection closed**. No timeout increase or success-shaped fallback added.
- Final full `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`: **655 passed**,
  134 warnings, **1,742.50 s**; Ruff/format/Pyright/import boundary all pass. This includes
  the existing disposable-only `make db-reset` test; persistent dev was not reset.
  The additional exact stall-threshold/recovery test was added after full-run collection
  and passed in the final 29-test focused selection above (counts overlap; do not add them).
- Full-run 50-rps results, with **150 completed turns and 150 pending deliveries each**:

  | Transport | Ack p95 | Scheduled ack p95 | Start-lag p95 | Required |
  |---|---:|---:|---:|---|
  | ASGI | 114.186 ms | 120.390 ms | 5.869 ms | Both ack measures <200 ms |
  | TCP | 129.436 ms | 130.810 ms | 3.206 ms | Both ack measures <200 ms |

  This is a clean offline run, not proof of stable latency under every shared-host load,
  nor live browser/Telegram acceptance of the new logging implementation.
- Frontend changed-test ESLint and all nine transport tests pass. No dependencies,
  environment files, Caddy/Cloudflare/webhooks or production resources were changed.
  No dev listener was running at that offline validation's final check; the live
  rollout and acceptance below subsequently received explicit approval.

### Guarded dev rollout and synthetic acceptance (2026-10-16)

- After explicit user approval, started the isolated loopback gateway (18081), frontend
  (13000), standard conversation/KB worker and delivery worker. Before launch, neither
  app port was listening and no repository worker/delivery process had a connection to
  dev PostgreSQL. `csa-dev/postgres` remained on loopback 5434. The VS Code task runner
  returned “Task not found” for the declared labels, so the exact guarded command bodies
  in `.vscode/tasks.json` were run as scoped background processes; their database/role
  startup checks passed. No task file or environment setting was changed.
- Gateway `/healthz` and `/readyz` returned 200. Frontend loopback login redirected to
  the sign-in page; the user signed in privately. Authenticated KB polling loaded the
  selected synthetic tenant's agents, files, documents, ingestion jobs and gaps, with
  the UI refreshing every five seconds.
- Fresh synthetic return-policy upload returned 202 and completed on attempt 1 as job
  `ac7c91cd-0e15-4b5d-8294-0923f94bd20c` (one chunk, `tei-384`). Authenticated original
  download returned 200/138 bytes; its SHA-256 matched the local synthetic fixture
  (`4264d2e1a9bd25323211782b1b2bf63bd74ac210525d687ff6527034db7adee6`).
- Explicit retry acceptance used a second synthetic CSV: a client-side connection reset
  was injected before the request reached the server, the UI retained the selected file,
  and the explicit retry reused the same `Idempotency-Key`. The retry returned 202 as
  job `9521c0e3-d9a6-441f-83af-880d7eb72cae`, then completed on attempt 1 with one chunk.
  This validates the UI's retained-key retry path; it is not a live committed-response
  loss test (the latter remains covered by offline proxy/database regressions).
- The user sent the synthetic question “What is the sample return window for runtime
  acceptance?” and confirmed receiving the expected seventeen-day answer. Tenant
  `76946f31-2b21-4e48-9383-65d1a78029c2` trace
  `7a4d0622-74f0-431a-9907-6d2bf323ffe1` has one completed ingress (attempt 1), one
  inbound and one outbound message, and a successful `kb_agent` run. Its persisted
  result contains four fact IDs, including three retrieved KB-chunk facts; fact text
  was not selected. UI trace events for verify, gate and emit all reported `ok`.
  Exactly one matching outbox row is `delivered` (attempt 1, Telegram HTTP 200).
- Read-only, exact-target dev queries corroborated three recent tenant
  `kb.upload_queued` console-audit records and fresh successful tenant scheduled-job
  heartbeats with no errors. No old upload files were reconciled or deleted. No database
  reset, production connection, webhook/infrastructure change or production setting
  change occurred.
- This is one synthetic isolated-dev acceptance, not a general provider/reliability
  claim or proof of the historical hang's cause. The guarded services remain running
  as detached loopback processes after the acceptance checks; the earlier offline check
  remains historical evidence.

### M3 gap, version and crash-window follow-up (2026-10-16)

- Through the authenticated KB UI, a deliberately unmatched synthetic KB-agent query
  appeared under Unanswered questions and remained visible after the five-second polling
  refresh. Selecting the M1 tenant showed zero gaps; returning to Local Telegram
  Development showed the synthetic gap. Mark resolved removed it from the UI, and a
  read-only exact-target dev query confirmed `resolved=true` and exactly one
  `kb.gap_resolved` audit. Read-only resolution denial is verified by the disposable
  console integration test, not by a separate live reader login.
- Uploaded a new synthetic CSV twice under the same logical document title, changing
  its answer. The UI showed version 1 superseded and version 2 active, each with one
  `tei-384` chunk. Both source versions are retained; no reconciliation or cleanup ran.
- Disposable-Postgres acceptance selection: **15 passed**, covering populated gap
  resolution/audit, read-only role denial, tenant scoping, version progression,
  publication rollback, commit-ack replay, deletion/embedding/read races, partial retry,
  and abandoned-job requeue/single claim. An added
  `test_worker_crash_after_document_commit_retries_without_duplicate_chunks` injects
  worker failure after document/chunk commit but before job completion; retry reuses the
  one document/chunk set, completes the same job on attempt 2, and retains one upload
  batch, source record and audit. The strengthened lost-COMMIT-ack test also checks one
  batch, file, job and audit after explicit same-key replay. Focused rerun: **2 passed**.
- `make lint` passes (Ruff, formatting, Pyright and import-linter). These are isolated
  synthetic tests; no persistent-dev reset or old-upload cleanup was performed. M3
  tenant-role browser replay remains unverified; in particular, the read-only boundary
  is test-proven, not browser-replayed with a live reader account.

### M4 private read-only sandbox (2026-10-16; guarded dev acceptance)

- Implemented the user's explicit **Sandbox assistant** scope: only KB query and product
  search/detail may execute. Shared runner enforcement denies business writes, customer
  history, invoice/payment/marketplace, booking/subscription/ticket and staff escalation
  even when a model requests them. Callbacks are rejected before pending-action
  consumption; confirmation controls intentionally remain unavailable.
- Additive migration 0290 supplies staged tenant RLS and per-account/tenant dedicated
  sandbox contacts. Human identity, tenant selection, CSRF/write role and exact owner
  checks protect conversation creation, bounded redacted text ingress, stable cursor
  history and audited archive. The queue's required routing metadata is internal-only,
  disabled and token-free, without changing any existing Telegram endpoint.
- Web worker/graph dispatch and local outbox delivery validate conversation/contact/
  endpoint/trace/message binding. Availability and delivery attempt commit together;
  no external transport or Telegram credential decryption occurs. Held drafts/reasons
  remain private and held, without staff notification or customer-approval expiry release.
  Fail-safe status remains failed and does not promise staff follow-up.
- `/chat` and `/portal/chat` expose private, typed history and 2.5-second polling with
  older-page cursors, queued/processing/awaiting-delivery/held/available/failed/no-reply
  labels, 15-second HTTP deadlines versus 90-second turn delay warnings, explicit
  retained-key retry and reconnect without resend. Archive is confirmed hide, not purge.
  In-memory draft/key retention does not survive leaving the tenant or reloading:
  inspect persisted history before submitting again. Voice/files are unavailable.
- Runtime/runner/Telegram/contract/migration selection: **60 passed**. It includes real
  synthetic KB retrieval through the graph to a private reply, post-outbox-commit worker
  crash/reclaim without duplicate messages, local-delivery rollback/retry with no partial
  visibility, role/tenant/user/contact denial, replay conflict, archive and the actual
  `make db-reset` command pinned to a disposable container with fingerprint checks.
  The reset reached 0290 and verified all three tables have RLS enabled without FORCE.
- Final sandbox-focused rerun: **10 passed**, including expanded-redaction text bounds,
  signed-bigint cursor rejection, stronger routing/trace bindings and proof that only the
  fixed system refusal, not arbitrary template replies, bypasses model grounding.
  This selection overlaps the 60-test run; do not add the counts.
- Final isolated frontend checks pass: **11 transport/routing/cancellation tests plus
  2 state/deadline tests**, ESLint, route type generation, TypeScript and production
  webpack build. Traces exclude environment files, backups, nested Git and the legacy API.
  Final `make lint` passed (Ruff, formatting, Pyright and import-linter).
- Initial full run: **671 passed / 4 failed**. The new migration exposed the gateway's
  separately pinned expected schema version still at 0280. Updating the readiness
  constant/docstring to 0290 fixes that contract without changing thresholds or SQL.
  The health/load rerun then had **7 passed / 1 TCP latency failure**: ASGI
  **109.200 ms**, TCP **243.150 ms** against the unchanged `<200 ms` threshold.
  Exact standalone TCP repeat passed at **110.980 ms** (scheduled **111.606 ms**),
  with 150 completed turns and pending deliveries.
- Pre-rollout post-readiness-fix `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`:
  **674 passed / 1 failed**, 134 warnings, 1282.80 seconds. The remaining failure is
  `test_real_ingress_50rps_and_durable_queue[tcp]`: ack p95 **202.643 ms** and
  scheduled ack p95 **203.795 ms**, exceeding the unchanged 200 ms acceptance bound.
  ASGI passes at **79.734 / 81.872 ms** ack/scheduled; each transport completes
  150 turns with 150 pending deliveries. The separate simultaneous worker-capacity
  test passes with 150 unique replies, and graph-only p95 is **103.657 ms**.
  Full acceptance is **not green**. The standalone pass does not replace the full-run
  failure; no cause or M4-induced/non-M4-induced performance claim is established.
  No threshold was waived by the subsequent explicit guarded-dev risk approval.
- The user explicitly chose `approve_dev_with_known_failure`. Forward-only 0290 was
  applied on exact-target-guarded `127.0.0.1:5434/csa` with the existing migration runner;
  only that missing migration was applied. No reset, role-password provisioning or
  secret inspection. Guarded gateway/worker/delivery/frontend replacements are running.
  Gateway health/readiness and frontend login return 200; anonymous chat API is 401 and
  anonymous `/chat` redirects 307.
- Browser integration returned no connected client, including after the user reconnected.
  Acceptance below is **user-operated**, not an agent-operated browser replay.
  The user confirmed the seventeen-day KB answer. Trace
  `ba22451a-06f2-40b6-851b-f0a990bf6529` completed with one successful KB run, retrieved
  fact IDs and one delivered web reply. The dedicated sandbox contact has zero orders.
  Conversation `f10ac68b-9050-4cc8-9eed-e0a18313cfc1` is archived, both turns retained,
  with one `chat.created`, two `chat.queued` and one `chat.archived` audit records.
  Disappearance from the active list is intended hide behavior, not deletion.
- The invoice request did **not** pass refusal acceptance. Trace
  `82e937d1-d108-4975-8460-e6e2aa6ff79c` classified `billing_invoice`, planned no tasks
  with `ask_clarifying`, ran no agent and delivered a misleading request for invoice/date
  details. The compose guard only caught denied executions/escalations. The follow-up
  fixes this fact-free account-intent path and refuses confirmation plans before model
  composition; general KB-grounded policy answers remain allowed. Sandbox regression
  selection **11 passed**, with a final strengthened provider-noninvocation check
  **1 passed** (overlap). Full rerun and approved worker replacement are recorded below.
- The user initially reported receiving the seventeen-day Telegram answer, but then-current
  stored
  Telegram traces (`62f8b7bd-08f3-44ee-9daa-733ab8e4858f`, KB success/HTTP 200, and
  `16c43902-56b4-4a0b-99bf-e9289a8dabde`, HTTP 200) predate the M4 rollout.
  Do not treat those or the user's receipt alone as fresh post-rollout corroboration.
  Fresh Telegram/fixed refusal/reconnect were therefore still unverified at that point.
- Post-scope-fix `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`:
  **674 passed / 2 failed**, 134 warnings, 2113.83 seconds. Ruff, formatting, Pyright,
  import boundaries and disposable-only `make db-reset`/0290 acceptance pass.
  Both unchanged ingress benchmarks fail: ASGI ack p95 **508.717 ms** (scheduled
  **763.499 ms**) and TCP **298.288 ms** (scheduled **299.260 ms**) against `<200 ms`.
  Both complete 150 turns with 150 pending deliveries. This adds an ASGI failure to
  the previously disclosed TCP failure. No cause or M4 causality is established.
  Full acceptance remains **not green**; no threshold was changed.
- The updated two-failure evidence was explicitly disclosed before replacement.
  The user chose **Approve guarded dev worker replacement despite both latency failures**.
  Verified old worker PID 1632176/UID/exact cwd/guarded command marker, stopped only its
  managed service, and started `m4-dev-worker-scope-fix` with clean environment and
  exact dev host/port/database/role checks, rejecting `hostaddr`/service overrides.
  Startup confirmed the old PID absent; the replacement's job loop started and
  completed scheduled jobs at 16:40:52 UTC. Other services remained running unchanged;
  gateway readiness and frontend login remain 200.
- Fresh **user-operated**, read-only metadata-corroborated acceptance after replacement:
  - Invoice trace `f7b89539-d90f-411d-b030-0bdbd5b7b627` at 16:44:23 UTC reproduces
    `billing_invoice` / `ask_clarifying`, zero agent runs, and delivers the **exact**
    fixed sandbox-scope refusal. No invoice details are requested; sandbox orders remain 0.
  - Web KB trace `77fc0fee-1710-4631-89b4-286db125e085` at 16:45:23 UTC completes
    one successful `kb_agent` run and one delivered private reply. The user confirms
    the seventeen-day answer remains available.
  - Fresh Telegram trace `de8b3b0c-27ed-4297-8c8d-2f5bdb14445f` at 16:46:17 UTC
    completes one successful KB run and one HTTP-200 delivered reply; the user confirms
    receipt of the seventeen-day answer. This supersedes the earlier stale receipt.
  - User refreshes/reopens the new conversation: both questions/replies remain exactly
    once. User then tests browser-tab Network Offline -> No throttling: polling recovers,
    with the same history and no duplicate replies. These are user-operated checks,
    not agent-operated browser evidence.
- At that point reader-role browser replay remained unverified because the user had no
  reader account. The separately approved follow-up below closes controls/ownership
  browser evidence; performance acceptance remains open.
- Existing upload files, user-owned dirty changes, `ui-guide/` and the approved
  UI-guide deletion remain preserved. No production, infrastructure or webhook changes.
  M5 has not started. See [the M4 API contract](./CONSOLE_API.md#private-sandbox-assistant-m4).

### M4 reader replay and bounded latency investigation (2026-10-16)

- The shared browser became available. Agent-operated authenticated platform view of
  conversation `1e2140a9-4274-4fc5-a0d7-f98a50e5d19c` directly shows both the exact
  sandbox invoice refusal and seventeen-day KB answer, each once.
- The user explicitly approved **one synthetic dev-only account**, registering privately
  through the normal invitation flow. The prepared fixture was Synthetic M4 Reader,
  `m4-reader-20261016@example.test`, `read_only`, Local Telegram Development. The user
  created/accepted the invitation and signed in without sharing the password or link;
  the assistant did not inspect invitation tokens, passwords or cookies. No existing
  account's role was changed.
- Agent-operated reader `/portal/chat` checks: read-only status visible; New sandbox
  conversation disabled; Send to sandbox and Archive conversation controls absent.
  Same-origin authenticated GETs: own list **200**, **0** conversations; admin-owned
  active history `1e2140a9-4274-4fc5-a0d7-f98a50e5d19c` and archived history
  `f10ac68b-9050-4cc8-9eed-e0a18313cfc1` both **404**; selecting M1 tenant is **403**.
  No write was attempted through the browser or CSRF bypass. Reader write denial is
  separately reconfirmed by the real disposable backend test. The new reader has no
  own existing history, so that specific read path remains test-proven, not browser-replayed.
  Browser left signed in as Synthetic M4 Reader; no account cleanup/revocation performed.
- Bounded metadata-only diagnostics used disposable containers, synthetic fixtures and
  temporary session-local instrumentation; no live-dev load, production connection,
  persistent reset, service replacement or infrastructure change. The original
  150-request/50-rps/<200-ms assertions and queue/delivery-count checks were unchanged.
  Results, recorded in execution order (overlapping selections, not additive coverage):
  1. Instrumented ASGI/TCP selection: **1 passed / 1 failed**. ASGI ack/scheduled p95
     **132.946 / 134.701 ms**; TCP **460.769 / 461.766 ms**. Each completes 150 turns
     with 150 pending deliveries. ASGI pool wait p95 **0.114 ms**, event-loop lag
     **8.096 ms**, max 9 active receives. This passing ASGI sample does not profile
     the earlier full-run failure.
  2. TCP-only wrapper run: **1 passed**, ack/scheduled **141.116 / 143.003 ms**.
     Its shutdown-time profiling report was not produced; do not infer internal
     stage timings from that run.
  3. TCP-only profile emitted before shutdown: **1 passed**, ack/scheduled
     **118.645 / 118.939 ms**. Server receive p95 **68.684 ms**, pool wait
     **0.113 ms**, event-loop lag **6.477 ms**, max 5 active receives. Five named ingress
     query families each run 150 times; contact-resolution p95 **15.729 ms**. Again,
     this captures a passing run, not the TCP failure.
  4. Final **uninstrumented** reader-backend + original ASGI/TCP selection:
     **2 passed / 1 failed**, 3 warnings, 189.08 seconds. Reader denial passes;
     ASGI ack/scheduled **93.234 / 102.600 ms**; TCP **261.451 / 262.471 ms** fails
     unchanged `<200 ms`. Both transports complete 150 turns with 150 pending deliveries.
- Full-run acceptance remains **674 passed / 2 latency failures** as recorded above.
  The isolated passes are not a replacement for it. Shared-host CPU pressure was
  observable during investigation, but neither that nor any specific SQL/transport
  bottleneck is established as the cause. No speculative pool, tracing, transaction,
  durability or threshold changes were made. Further investigation must capture a
  failing request path with correlated stages/resource evidence, not rerun until green.
- Temporary profiling source/output/cache files were removed after recording the
  metadata above. Existing dirty changes (including later unrelated review files),
  old uploads, the UI-guide deletion and `ui-guide/` remain untouched. Readiness is 200.
  At that stage latency acceptance remained the M4 blocker; the subsequent explicit
  functional closeout/deferral below is the current decision.

### M4 functional closeout decision and M5 handoff (2026-10-16)

- The user reports that this server is a **laptop** and explicitly selected
  **Close functional M4 with documented laptop-dev performance exception; carry latency
  work forward**. The narrowed read-only Sandbox assistant milestone is functionally
  closed, with the implementation and acceptance limits above retained.
- This is an accepted local-development performance exception, **not** a latency fix,
  green full-suite result, production-readiness claim or changed acceptance threshold.
  Laptop/resource constraints are plausible context, not a proven failure cause.
  Full **674 passed / 2 latency failures** and final focused **2 passed / 1 failed**
  remain the actual test evidence. Carry failing-path profiling and representative-host
  validation into release acceptance (M7/M8); do not silently waive it for production.
- User explicitly chose conservative Markdown consolidation: remove stale active status
  and superseded kickoff directions, preserve plans/history/reviews/UI-guide. No Markdown
  files were identified as safe redundant deletions. Retained the old recovery handoff
  as incident history and updated it to point forward; replaced the old M2/M3 kickoff
  prompt with a canonical M5 handoff link.
- [M4 closeout / M5 kickoff](./handoffs/2026-10-16-m4-closeout-m5-start.md) contains the
  acceptance matrix, exact remaining performance debt, environment/service/fixture
  state, M5 authority question, implementation order and ready-to-paste session prompt.
  M5 must remain separate from owner-private sandbox history/holds; its original
  customer-web approval requirement needs an explicit authority/scope decision.
- This closeout changes documentation only. No new code/test/dependency/schema/runtime
  change, restart, reset, upload cleanup or production/infrastructure/webhook change.
  Closeout readiness **200**, frontend login **200**, `.env.local` remains ignored.
  No new test run was needed for documentation-only changes; prior failures are preserved.
  Dirty worktree, untracked reviews and UI-guide state remain intact. No commit made.

| Phase | Status | Evidence |
|---|---|---|
| LG01 Foundation | Done | lint/tests green |
| LG02 Contracts, state, registry | Done | `tests/test_contracts.py`, `test_registry.py` |
| LG03 Ingress, worker, delivery | Done | ordered worker, outbox, reaper + DLQ, load test |
| LG04 Coordinator graph | Done | `test_coordinator*.py` |
| LG05 Spokes & tools | Done | order, approval, escalation, ticket; verified live on Telegram |
| LG06 Decision layer | Groq + Jev classifier modes implemented; wider threshold calibration pending | `test_llm_provider.py`, `test_decide_providers.py`, live synthetic comparison; dev cascade row verified |
| LG07 Egress / HITL gate | Policy gate + staff approval (approve/reject/expire), semantic verify, Gemini composer failover, delivery crash recovery, brand voice & verbatim callbacks | `test_gate_policy.py`, `test_staff_path.py`, `test_ingress.py` (Edit), `test_worker.py`, `test_llm_provider.py`, `test_coordinator.py`; staff Edit (button + text reply, audited) |
| Console visibility (agent timeline + read API) | Done (polling; see CONSOLE_API.md) | `test_console_timeline.py` |
| Agent contract + bounded agent loop | Done: risk classes per action, read-only `observe` loop | `test_coordinator.py` (loop), `test_llm_provider.py` (next_step, risk) |
| LG08 Evals/golden cases | Done: 60 decision evals, 33 whole-turn golden cases, 100% policy coverage | `test_decision_evals.py`, `test_golden.py`, `test_load_acceptance.py`, `docs/evals/lg08_validation_report.md`; edge-case matrix expansion (hard legal, sentiment, human handoff, duplicate SKUs, cancel multi-line, button confirmation requirement, clarify bounds, never_auto) |
| LG10 Scheduled jobs & expiry sweep | Done: Advisory-locked JobRunner, per-client RLS execution, aggregated per-product stock release, reservation reconcile & repair, reapers, property test (zero drift) | `tests/test_jobs.py` (6 passed), `csa/jobs/`, migration 0250 `job_runs` |
| LG09 Cutover & operations | Done: Strangler pattern gateway proxy, instant rollback, shadow mode, 8 operational runbooks, ops procedures | `tests/test_cutover_ops.py` (4 passed), `docs/runbooks/` (01-08), `csa/ops/runbooks.py`, `csa/channels/ingress.py` |
| LG13 Agents/Console & E-Commerce draft | Done: Wave 1 agents (KB semantic vector search, gaps logger, ingestion; Invoices contact-scoped lookup & signed expiring download receipts), E-Commerce agent (TikTok Shop Open API HMAC signer + Shopee/Lazada adapters), Console API admin subscription toggles (`is_enabled`/`client_enabled`), specialized tabs catalog (LHDN, E-Com, KB, Invoices, Bookings) | `tests/test_ecommerce_agent.py` (4 passed), `tests/test_kb_agent.py` (7 passed), `tests/test_invoice_agent.py` (5 passed), `tests/test_console_capabilities.py` (3 passed), `csa/agents/kb/`, `csa/agents/invoices/`, `csa/console/` |
| Marketplace Ingress (TikTok Shop & Multi-Marketplace) | Done: TikTok Shop webhook verification (HMAC-SHA256, deduplication, ingress_events persistence), seller OAuth flow (signed state, code exchange, AES-GCM credential persistence, auto endpoint provisioning), Shopee/Lazada draft endpoints | `tests/test_marketplace_ingress.py` (17 passed), `csa/channels/marketplace/`, `apps/gateway/main.py` |
| Langfuse Observability Integration | Done: Direct async batch ingestion (`LangfuseSink`), redaction & privacy preservation, integrated into worker `PostgresEventSink`, initial activation trace verified live on `jp.cloud.langfuse.com` | `tests/test_langfuse.py` (4 passed), `csa/core/langfuse.py`, `csa/graph/events.py`, `apps/worker/main.py` |
| KB console upload ingestion | Done (gated by `KB_INGEST_ENABLED`): console writes ≤3 PDF/DOCX/CSV files to `KB_UPLOAD_ROOT/<client_id>/` + `kb_ingest_jobs` row; worker validates, confines paths, extracts, chunks, embeds (strict TEI, no silent fallback), stores versioned/deduplicated docs, records per-file `meta.results`, and retries with backoff. Model-filtered search. Also fixed LG13 regressions: `EcommerceAgent.ACTION_RISK` class attribute, stale capabilities test, JEV answers fixture for new agents, and `job_runs` (owner-written ops log) excluded from the RLS set-equality test. Full suite: 442 passed; the 6 failures were rerun in isolation and pass (the 3 latency thresholds failed only under host load). | `tests/test_kb_ingest.py` (25 passed), `tests/test_kb_agent.py` (7 passed), live dev TEI e5-small semantic check; not tested: real console writing jobs, worker loop running live in dev |
| LG13 Waves 2/3 preparation | Implemented provider-neutral slice, **not live integrations**: disabled-by-default booking/subscription agents provide contact-scoped local reads and unexecuted requests; staff-only email/research/invoice-draft libraries, strict inputs and Decimal totals. Immutable preparation ledger with RLS, contact-owner checks, actor/trace and concurrent idempotency. No external calls, executable approvals or billing/event/invoice mutations. | `csa/agents/staged/`, migration `0260_staged_agent_requests.sql`, `tests/test_staged_agents.py` (51 passed); `docs/handoffs/2026-10-15-wave23-preparation.md`. |
| LG15 Console turnover (n8n → CSA) | M0–M3 implemented; M4 functionally closed with accepted laptop-dev performance exception. M5 Telegram slice approved/running on guarded dev0300 with platform-admin UI, synthetic Telegram acceptance, live reader denial and representative tenant-admin/staff replay verified. M3 tenant-role replay and broader M5–M8 pending. | Latest full lint/tests **692 passed**, ASGI/TCP ack p95 **108.284 / 174.623 ms**, unchanged <200 ms. Frontend 12+2/lint/types/build pass; controlled live response-loss retry, ownership, approve/edit/reject, scoped denials and fresh post-release delivery verified. No production-capacity waiver or public deployment. |
| LG14 Teach-a-Task Virtual Desktop | Planned: Dedicated desktop container sandbox, user action recording, playbook compiler & scheduled replay | `docs/langgraph-rebuild/LG14_teach_task_desktop_agent.md` |

Console M0–M1 review/verification (2026-10-15):

### M3 authenticated KB upload and lifecycle (2026-10-15)

- Implemented CSRF/session-scoped multipart upload (1–3 PDF/DOCX/CSV, 10 MiB/file,
  31 MiB aggregate, 8 KiB per-part headers), server-only UUID storage names, signature/
  extraction validation, tenant-key idempotency and durable atomic publication before
  queue/file/batch/audit commit. Machine and reader writes are denied.
- Added typed source metadata and authorized original downloads, safe public job DTOs
  without storage paths/requesters/arbitrary metadata, document/source deletion and
  explicit audited orphan/partial/deleted-file reconciliation. Already-open downloads
  survive unlink and close on disconnect. Gap resolution and audit are atomic.
- Tenant locks, managed file checks and queued-at/hash tombstones prevent in-flight
  embedding, retry and legacy-path jobs from resurrecting deleted content. Deletion
  between path resolution/read is a per-file error, not a whole-job abort. Deleting
  linked content also removes its duplicate source aliases; deliberate new uploads
  after deletion are allowed. No legacy source import or automatic tombstone purge.
- Adopted platform `/knowledge-base` and tenant `/portal/faq` with stable retry keys,
  5-second job/source/version/model/gap polling, original downloads, confirmed deletion,
  role-derived controls, explicit failures and truthful queued/worker setup labels.
- Synthetic disposable-DB follow-up: **57 passed** across upload/worker/streaming/
  agent/contract checks. Includes all three types through HTTP → queue → hash worker →
  tenant search; concurrent idempotency/conflict; roles/CSRF/machine/tenant scoping;
  invalid/empty/type/path/four-file rejection; exact 10 MiB and 31 MiB bounds; partial/
  retry/delete/legacy embedding races; original downloads/disconnect; publication
  rollback/lost COMMIT acknowledgement and storage reconciliation/audits.
  Final three-format search/binary-contract/projection selection **3 passed**
  (overlaps the 57 selection); each format was queried and retrieved, not just stored.
  Earlier broader identity/migration/RLS selection **103 passed / 1 failed**;
  the sole immutable-context fixture error was fixed and its upload test passes.
- `make lint` passes; isolated frontend **8 transport checks**, ESLint, types,
  production webpack build and safe build traces pass. Disposable-only `make db-reset`
  succeeded at **0280**, verifying all three new tables have RLS; its container was
  removed. Persistent-dev activation is recorded separately below.
  Full `NO_LOCAL_ENV=1 ENV=ci make lint test`: **607 passed, 2 failed** in 25m37s.
  Both failures are the existing 200 ms ingress acknowledgement thresholds:
  ASGI p95 **266.718 ms**, TCP **887.834 ms**. Both completed 150 turns and
  recorded 150 pending deliveries. No threshold was relaxed or cause inferred.
  This full run began before the final source-read/download-resource follow-ups,
  which are covered by the 57-pass and final 3-pass focused selections plus final lint.
  Both unchanged benchmarks subsequently pass together in isolation: **ASGI 123.840 ms**,
  **TCP 137.444 ms**, 150 completed turns/pending deliveries each (**2 passed**).
  No cause for the variability or clean full-suite acceptance is claimed.
- **Authorized isolated-dev activation:** verified `csa-dev/postgres` on loopback 5434
  and forward-applied only **0280**, transactionally and without a reset. Restarted only
  console gateway 18081/frontend 13000; added the guarded `scripts.dev_kb_ingest` task.
  It requires `csa_app` on the exact dev database, a dedicated absolute storage root,
  and credential-free loopback HTTP TEI (or explicit dev hash embeddings). It claims
  only the explicit synthetic tenant, including its onboarding state; it does not start
  the full worker, delivery, bots or other agents.
- Gateway and KB tasks override `KB_UPLOAD_ROOT` to
  `$HOME/.local/share/csav2-dev/kb-uploads`; the configured value resolved to the
  repository root and was not used. No environment-file contents or credentials were
  displayed, and the file was not edited.
  This override is task-local, not a global configuration repair. See
  [startup instructions](./CONSOLE_API.md#isolated-dev-rollout-prerequisites).
- **Live platform-admin browser evidence:** user signed in privately; enabled only the
  synthetic M1 tenant's two KB switches. Four-file selection was rejected. A malformed
  DOCX fixture was rejected with 422; corrected PDF/DOCX/CSV upload returned 202 and
  reached `done`, one chunk each through the configured loopback TEI (`tei-384`).
  All three authenticated original downloads returned 200 with matching SHA-256,
  attachment headers and `no-store`. Reuploading the CSV reported duplicate/version 1
  without adding chunks. Confirmed source deletion removed both CSV aliases/shared
  document; confirmed PDF document deletion also removed its source. Both returned 204,
  deleted-source downloads returned 404 and compatible chunks fell from 3 to 1.
  Reconciliation returned 200/zero removals and preserved the remaining DOCX.
  Tenant-scoped identity reads corroborated upload, source/document deletion and
  reconciliation audit events; exactly one source file remains.
- Browser acceptance exposed a four-placeholder/two-argument `list_gaps.sql` regression.
  Fixed both console and KB-agent callers, typed nullable SQL booleans, and added real-DB
  HTTP coverage for unresolved listing, reader denial, resolve disappearance and audit.
  Rollout upload/agent/launcher/contract selection **34 passed** (includes **11** launcher
  guard tests); final `make lint` passes. Selections overlap; do not sum them.
- **Not tested live:** M3 tenant-admin/reader browser replay (roles/tenant isolation are
  covered by HTTP tests), populated/resolved gap UI, changed-content version progression,
  crash/retry races, customer conversation retrieval, Gemini/other providers or public
  deployment. Loopback TEI ingestion success is not a claim about Gemini failover.
  The KB switches remain enabled for synthetic tenant `m1-browser-20261015`; one
  `m3-smoke-shipping.docx` document/chunk remains as a clearly synthetic dashboard demo.

### M3 direct retrieval follow-up (2026-10-16)

- User reports successful additional PDF ingestion (10 chunks) and synthetic
  `demo-faq.csv` ingestion (2 chunks), each done in one attempt. Those job results
  alone do not establish download, changed-version or deletion acceptance.
- With permission, ran a read-only local check using existing `DefaultKbEmbedder`,
  `kb/search_chunks.sql` and `KbInput` defaults (threshold **0.65**, limit **3**).
  Guards verified dev mode, exact `csa_app` database target on loopback 5434 and
  credential-free loopback TEI; hash mode was explicitly refused. No attached
  environment-file snapshot or credentials were inspected.
- For the synthetic M1 tenant, **“What is the demo return window?”** retrieved the
  expected `demo-faq` return passage at cosine similarity **0.860727**. The paraphrase
  **“How many days do I have to return a demo item?”** retrieved the same passage at
  **0.898810**. Both returned “Synthetic returns are accepted within seventeen days.”
  Model label was **tei-384**; no threshold adjustment or fixture re-ingestion.
- Each query repeated with a random foreign tenant RLS context while still requesting
  M1 returned **zero rows**. All database transactions were read-only; no gap records,
  document changes or full agent/coordinator invocations. Non-demo passage contents
  were not printed.
- This verifies the existing embedding/search/threshold retrieval path, not complete
  customer answering. Telegram tenant mapping, conversation orchestration, policy/
  approval, composition and delivery remain untested in this follow-up. No services,
  bot/webhook settings or production resources were changed.

### Telegram acceptance preflight (2026-10-16)

- Metadata-only isolated-dev inspection found one enabled Telegram endpoint on
  `local-dev-telegram` (pilot), and none on the M1 browser tenant (onboarding).
  Existing M1 uploads are therefore not available through that bot's tenant scope.
- User selected synthetic-only acceptance on the existing dev Telegram tenant,
  confirmed the identified bot is dedicated to development, and requested that its
  webhook remain unchanged. Read-only Telegram `getWebhookInfo` reported a registered
  URL matching the configured dev base and existing endpoint, zero pending updates
  and no reported delivery error. Read-only `getMe` identified the public bot for
  the user; no tokens, webhook URLs or credential contents were displayed.
- User confirmed sending the exact synthetic routing probe. A subsequent read-only
  query found no matching ingress event in the isolated dev database within the last
  15 minutes. **Routing is not verified; Telegram end-to-end acceptance is blocked.**
  A registered webhook alone does not establish that this gateway received the probe.
- No polling, webhook changes, full conversation/delivery workers, fixture copying,
  tenant remapping or external message sends were performed. The existing poller
  removes the webhook on startup; the standard worker/delivery claims are not
  tenant-scoped. Neither was started as a shortcut. Diagnose the dev routing before
  enabling any conversation processing or delivery.
- Subsequent user-operated Caddy correction restricted the dev site to webhook POSTs
  forwarded to gateway 18081; user reports non-webhook `/readyz` now returns 404.
  A fresh exact synthetic probe was corroborated in isolated-dev ingress as
  **completed, one attempt** (2026-10-16 02:19:09 UTC); user also reports a bot reply.
  This resolves the earlier missing-ingress blocker, not KB-answer acceptance.
- Read-only host process inspection then found existing `apps.worker.main` and
  `apps.delivery.main` processes (including wrapper/child pairs). They were not started
  or stopped during this acceptance check, and their effective configuration/launch
  provenance is unverified. VS Code forwarded ports do not identify background workers.
  Do not start duplicate runtime processes or assume the current reply used KB;
  verify the existing runtime's dev-only scope before further live processing.
- Further preflight: existing runtime processes report `ENV=dev` and this repository
  as their working directory. At socket inspection time the worker had five and
  delivery two established connections to loopback 5434, with none to other standard
  PostgreSQL ports. Their DSNs were absent from process environment, so this is
  connection evidence, not complete effective-configuration attestation; a later
  socket-port/DB-session correlation returned no matching rows.
- The Telegram tenant has only two capability registry rows, no KB/knowledge entry,
  and zero active KB documents. Its generic clarification reply therefore does not
  demonstrate KB use. Tenant KB provisioning is a prerequisite to uploading the
  synthetic FAQ and testing a knowledge question. Browser automation was unavailable
  (connection timeout); no fallback direct DB writes, capability bypass, process
  restart, shadow-mode change or synthetic document copy was performed.
- User authorized an isolated-dev catalogue backfill for `local-dev-telegram`.
  Reused the console onboarding `_AGENTS` definitions, Pydantic input schemas and
  `console/provision_capabilities.sql` under application-role tenant transactions.
  Catalogue increased **2 -> 9**: added ecommerce, escalation, invoice, KB, order,
  order approval and ticket entries, all **unsubscribed/off**. Existing booking and
  subscription rows were compared and preserved unchanged. Repeated provisioning
  produced identical rows; a fresh post-commit read confirmed persisted state.
  Recorded `capability.catalogue_backfilled` with null actor and explicit authorized
  maintenance metadata rather than impersonating a console user. No schema/code change,
  reset, enablement, source copy, service restart or routing change. This repairs this
  older dev tenant only; no general existing-tenant backfill endpoint was added.
- Subsequent user enabled the Telegram tenant's KB subscription/switch and uploaded
  synthetic `demo-faq.csv`. Job `f0412a56-82a7-4232-88ff-844e40b93d7f`
  was durably queued (zero attempts), but the frontend reported a runtime timeout.
  Both gateway `/healthz` (no DB I/O) and `/readyz` timed out. Process inspection
  showed a blocked main thread, a terminal-I/O waiter and open app/identity transactions
  with DB lock waiters; this is not evidence of a TEI or file extraction failure.
- User tried Ctrl+Q without recovery and explicitly authorized gateway-only restart.
  Graceful termination did not exit; the exact verified gateway PID was force-stopped
  and the guarded VS Code gateway task relaunched. No frontend, worker, delivery,
  proxy or database reset/change. Replacement `/healthz` returned 200 in ~6 ms,
  `/readyz` 200 in ~32 ms and frontend login 200 in ~192 ms; DB lock waiters fell to
  zero. User then confirmed the KB page loads and shows the original queued CSV job.
  No duplicate upload was needed. Exact blocking cause remains undetermined; terminal
  output backpressure is only a lead, not a proven root cause or permanent code fix.
  Telegram-tenant ingestion remains pending; zero attempts means it has not been
  claimed, and the dedicated KB-only task still targets M1.
- With further authorization, claimed exactly the known queued Telegram-tenant FAQ
  job in a tenant-scoped transaction (unexpected job identity would roll back) and
  processed it through existing `KbIngestProcessor`, exact isolated `csa_app` guards,
  dedicated upload root and loopback TEI. Job finished **done**, one file, zero errors.
  This was a bounded run, not a new persistent worker. Existing M1 task unchanged.
- Telegram-tenant direct retrieval now passes at unchanged threshold 0.65/limit 3:
  exact return-window question **0.860727**, paraphrase **0.898810**, both retrieving
  the synthetic seventeen-day passage. No cross-tenant copy or threshold adjustment.
- User then asked the exact question through Telegram and received handoff text.
  Trace `59a3d8ac-0adf-4d9d-acfd-c1dd145a5056` corroborates completed ingress:
  JEV classified `refund_returns`; planner produced **zero tasks**, strategy
  `ask_clarifying`, confidence 0.6; validation changed strategy to `escalate`.
  Only escalation agent execution is recorded, composition had zero facts and the
  gate sent the system reply. **KB was not invoked**. The policy has an existing
  clarification-count escalation bound; do not remove it or infer an embedding failure.
  Telegram KB-answer acceptance remains blocked at planning/routing, despite successful
  ingestion and direct retrieval. No live policy/provider/runtime change was made.
- Planner follow-up: clarified the shared LLM planning prompt to route general policy/
  return-window/warranty/shipping/documentation questions to read-only `kb_agent.query`,
  even when classification says `refund_returns`. Explicitly distinguishes these from
  customer-specific refund/cancellation/eligibility/mutation requests and retains
  required inputs, button/human safeguards and existing clarification bounds.
  No deterministic keyword override or gate/validation relaxation was added.
- Provider/plan regression selection **35 passed**, including exact/paraphrased KB task
  validation after prior clarification, missing-task refund escalation bounds and existing
  invalid-agent/button-action rejection. `NO_LOCAL_ENV=1 ENV=ci make lint` passed.
  Synthetic-only live configured Groq planner checks selected KB query for both questions
  in two runs. Actual refund request selected `ticket_agent.create`, not KB lookup or
  button-only order mutation; no tasks were executed. Initial live harness incorrectly
  required an empty escalation plan for refunds, and was corrected to accept legitimate
  support-ticket planning; that initial assertion failure is not a clean pass.
  Mock tests verify prompt/contracts, not model reliability guarantees.
- Existing conversation worker has not been restarted to load this prompt change.
  Telegram KB-answer/composition/gate acceptance remains pending. No schema change,
  database reset, Telegram message send or full-suite rerun in this prompt-only follow-up.
- User subsequently authorized replacement of the existing dev conversation worker.
  Added a guarded VS Code conversation-worker task (all dev tenants/scheduled jobs,
  not tenant-scoped). Initial `runpy` launcher caused a Pydantic forward-reference
  error after claiming the synthetic turn; corrected it to normal module import and
  `asyncio.run(main())`. Standard stale-lease recovery completed that turn on attempt 2,
  but it intentionally followed the muted route from the prior handoff, with no delivery.
  User then authorized releasing only this synthetic conversation; existing tenant-scoped
  release SQL and an explicit maintenance audit restored AI ownership. No gate or mute
  safeguard bypass and no other conversation changes.
- **Telegram KB answer acceptance passed:** user resent the question and received
  “Synthetic returns are accepted within seventeen days.” Trace
  `973cf19a-e31e-4c18-978d-342d5e03f300` corroborates completed ingress, JEV
  `refund_returns`, planner/validation **kb_agent, auto_reply, confidence 0.88**,
  successful KB execution, composition with one fact, verification true, policy gate
  **send/auto**, one emitted message and delivery **delivered, one attempt**.
  No Telegram API call was made by the assistant to send the customer test message.
- Restart also surfaced a pre-existing scheduled `mute_expiry` SQL error referencing
  missing `conversations.updated_at`; not changed as part of this planner fix.
  Final acceptance does not establish general planner reliability, real refund
  execution, Gemini failover or a clean full-suite result.

### M2 agent controls and runtime visibility (2026-10-15)

- User subsequently confirmed the guided tenant-admin/read-only checks and cleanup.
  Those actions are user-confirmed, not independently replayed; populated-run and
  live-provider browser acceptance remain unverified.
- Follow-up dev smoke (15:49 UTC): user reported `Not Found` on all M2 views.
  Served OpenAPI confirmed the old gateway lacked `/agents`, `/overview` and `/runs`.
  With explicit user approval, restarted only the verified loopback gateway task on
  18081; readiness and all three routes then passed. The shared browser now has the
  user's authenticated platform-admin session: selected synthetic M1 tenant and
  verified tenant subscriptions, Agents catalogue and Runtime activity load without
  `Not Found`. Activity correctly shows zero recorded runs. No switches/data changed;
  tenant-admin/read-only mutation checks and populated-run browser acceptance remain open.
  This supersedes the no-restart/backend-not-loaded observation below.
- Implemented `/console/v1/agents` and specialised metadata with independent subscription,
  tenant switch, implementation stage, setup/circuit and role permissions. Unknown agents
  and MyInvois are not implemented; booking/subscription writes are prepared-only; invoice
  reads do not imply PDFs/issuance. Default marketplace runtime has no adapters. TEI is
  configured-but-unverified, with actual matching/incompatible active KB chunk counts;
  hash embeddings are explicitly lexical. Removed hard-coded healthy/compliance metrics.
- Added tenant-scoped `/overview`, ID-keyset `/runs` and `/runs/{id}` execution metadata.
  Raw inputs/outputs/customer text are excluded; query errors propagate. Platform and
  tenant agent/activity screens share session transport and reuse incremental trace polling.
  Platform users select a tenant, tenant admins cannot alter subscriptions, and staff/
  readers have read-only controls. Required safety switches cannot be turned off.
- Closed the runtime enforcement gap left by static planner descriptions: each production
  attempt rechecks current switches without using registry discovery caches. Both disabled
  switches block drafts and existing callbacks in separate dependency instances. Retry
  disable stops the next attempt. Console KB execution is gated; queued uploads are not
  claimed while disabled and spend no attempts. Already-started/claimed work may complete;
  this is not cancellation or distributed breaker management.
- Focused catalogue/API/identity/registry/runner/contract selection: **74 passed**.
  Follow-up order-callback/runner/timeline/KB-queue/worker selection: **77 passed**.
  These overlap and are not additive. `NO_LOCAL_ENV=1 ENV=ci make lint` passed.
  Final isolated frontend check passed **6 transport/routing checks**, ESLint, Next
  type generation, TypeScript, production webpack build and safe build-trace checks.
- Full `NO_LOCAL_ENV=1 ENV=ci make lint test`: **559 passed, 32 failed** (23m17s).
  It exposed lifecycle mocks missing the newly used TEI settings and golden fixtures
  missing registry provisioning. Corrected those fixtures without weakening enforcement,
  added PostgreSQL-bigint cursor bounds and equal-timestamp pagination evidence, and
  reran gateway lifecycle + all golden cases + runtime pagination + snapshot:
  **45 passed**. Final Python lint passes. These selections overlap earlier evidence.
- The full run's remaining load failure was ASGI ack p95 **853.178 ms** versus the
  unchanged **200 ms** limit. Subsequently reran both transport benchmarks alone:
  **2 passed, 2 deselected**; ASGI p95 **92.637 ms**, TCP p95 **126.684 ms**,
  each with **150 completed turns and 150 pending deliveries**. No threshold changed.
  This is actual passing isolated performance evidence, but does not establish the
  cause of earlier failures or a clean full-suite pass. The complete suite was not
  rerun after fixture corrections; release-level full-suite repeatability remains open.
- No schema/dependency change, reset, persistent-dev data mutation, provider call or
  service restart in this M2 implementation. Tests use synthetic disposable Postgres.
  Existing loopback login/health respond, and anonymous `/agents` redirects to login.
  The existing gateway must be deliberately restarted to load new backend routes;
  authenticated M2 browser acceptance, live provider checks and deployment remain pending.

### Isolated dev setup and browser smoke check (later the same day)

- Verified Compose project/service `csa-dev/postgres`, container `csa_dev_db`,
  dedicated volume and host port **5434** before connecting. Applied missing
  migrations **0250, 0260, 0270** transactionally with ledger stamps, without
  resetting data. No production database or existing service was changed.
- Enabled `csa_identity` login; the user set its password with interactive psql,
  configured the ignored local env file and bootstrapped `admin@example.test`.
  A malformed identity URI was diagnosed without printing secret values; the user
  repaired it with a local password prompt and percent-encoding. Both database
  roles now connect to the approved dev endpoint; identity privilege readiness passes.
- Started only the loopback gateway **18081** and Next frontend **13000** using
  VS Code tasks. Frontend API origin is `http://127.0.0.1:18081`; browser Origin
  is `http://localhost:13000`, privately forwarded through VS Code. Cookies are
  non-Secure for this loopback HTTP test only. No worker/delivery/bot/provider
  processes, public proxy changes or runtime cutover.
- Gateway `/healthz` and `/readyz` return 200. Live same-origin checks pass:
  login 200; protected anonymous pages redirect; legacy API/deferred projects
  return 404; anonymous identity returns 401; CSRF cookie/no-store and rejected
  unauthenticated mutation verified without displaying tokens.
- Browser login page inspected directly. User confirms login, forced password change,
  administration home, tenant list and member screen all work. DB audit confirms
  login and password-change events. The agent-shared browser did not inherit the
  user's signed-in session, so authenticated screen acceptance is user-reported,
  not an agent-operated end-to-end test. Subsequent guided lifecycle results are
  recorded below; disposable integration evidence is separate.
- Isolated frontend build/typecheck/lint and five transport checks passed again
  before launch. No additional full test suite or reset was needed for this setup.
  Existing TCP performance acceptance failure remains unresolved.

### Guided M1 browser lifecycle checks (2026-10-15)

- User created synthetic tenant `m1-browser-20261015` and accepted invitations for
  `m1-admin@example.test` (tenant admin) and `m1-reader@example.test` (read only).
  Database checks corroborate tenant creation, both scoped memberships and consumed
  invitation tokens; all nine capability switches remain disabled.
- User reports tenant-admin access to platform administration is blocked, reader
  management controls are unavailable, direct team access is blocked and used
  invitation acceptance is rejected. These are user-operated browser results;
  the agent-shared browser still has a separate unauthenticated session.
- Logout acceptance subsequently passed: user inspected the reader's incognito-window
  Network request and reported `POST /console/v1/auth/logout` returned **204**.
  A follow-up DB check corroborated two reader logout audit events and two revoked
  reader sessions (latest logout 14:19:35 UTC). Earlier redirect-only checks had
  no matching audit; the explicit reader-window request resolves that evidence gap.
  The user also previously confirmed direct portal access after logout redirects to login.
- Login troubleshooting: the user privately recovered the dev platform-admin password
  with an audited local operation. An attempted targeted throttle cleanup deleted zero
  rows; the remaining buckets did not match the intended admin email. Explicitly entering
  the intended email and recovered password restored login. No credential values logged.
- Services were restarted using the existing loopback tasks after both were found
  stopped. Gateway readiness and frontend login returned 200 afterward. No worker,
  delivery process, production connection or reset was involved.

### Earlier implementation review

- Fixed Unicode password changes raising `TypeError`: constant-time equality now compares
  UTF-8 bytes. Fixed resets bypassing an unaccepted invitation's membership creation:
  reset issuance returns 409 without invalidating the invitation; accept/renew it instead.
- Session integration selection **33 passed**. Follow-up review regressions plus
  config/contract/lifecycle/password selection **30 passed** (overlapping selections,
  not additive). This includes the pending-invitation regression added after the first
  selection started. Final `ENV=ci NO_LOCAL_ENV=1 make lint` passed.
  The existing accepted-account invite/reset single-use/session-rotation test was also
  rerun after the reset guard change: **1 passed**.
- Frontend transport/routing checks **5 passed again during review**. The implementation
  run also passed isolated ESLint, Next type generation, `tsc --noEmit` and Next 16.3.8
  production webpack build; no frontend source was changed during this review.
- The already-running full `ENV=ci NO_LOCAL_ENV=1 make lint test` completed before the
  review fixes: **564 passed, 1 failed** (19m44s). TCP ingress ack p95 **1021.433 ms**
  exceeded **200 ms**. The exact standalone rerun also failed: p95 **451.844 ms**,
  150 completed turns and 150 pending deliveries, 2.084 turns/s. Cause is not established;
  do not dismiss this as full-suite contention or claim performance acceptance is green.
  No threshold relaxation or unrelated performance workaround was made.
- Previously recorded `make db-reset` plus migration reapply passed in a new disposable
  Postgres container (0270, LangGraph checkpoint setup and synthetic seeds); it was removed.
  This review changed no schema and did not reset/migrate any existing database.
- `NO_LOCAL_ENV=1` prevents Make and development settings from reading `.env.local`;
  Make uses its declared webhook default rather than exporting empty `CHANNEL_MODE`.

Next bounded slice: complete authenticated **M2 browser acceptance** using the new agent/
activity screens, then **M3** authenticated three-file upload,
download/delete safety and job-progress UI can proceed independently after M1. No additional
provider keys are needed for these provider-neutral packages. Both load transports now
pass in isolation, but obtain a clean full-suite rerun and investigate repeatability
before treating the release as verified.

Safety correction: the first use of the pre-existing test-image fixture sent the
repository build context to the local Docker daemon, potentially including ignored
env/reference files. No secret values were inspected/displayed, and the Dockerfile
copies no application files. The fixture now uses `deploy/` only. Adopted frontend
verification copies an explicit source allowlist to `/tmp`; build traces are checked
for env files, nested Git, backups and the copied API. No deployment, live browser or
provider validation, existing-process/port/webhook/proxy change, or commit was performed.

Required human-console settings: `IDENTITY_DATABASE_URL` (same database, narrow
`csa_identity` role), `CONSOLE_ALLOWED_ORIGIN`, and frontend `CSA_CONSOLE_API_ORIGIN`.
Provision the role using `CSA_IDENTITY_PASSWORD` only in the migration/provisioning
process, then run the deliberate interactive first-admin bootstrap. Cookie/expiry
and optional machine allowlist settings are documented in `CONSOLE_API.md` and
`.env.example`; neither real env file was edited. Invitations/resets use manual
delivery. Membership switching/account attachment, browser visual checks, M3+ business
UI, web chat, KB upload, approvals, provider execution and runtime turnover remain open.
The unsupported invoice download-link endpoint now returns 501 without signing fallbacks.

Wave 2/3 verification (2026-10-15): `make lint` passed (Ruff, format, Pyright and
import-linter). Final preparation/identifier selection: **52 passed** (51 preparation
cases). Earlier planner/coordinator/worker selection: 117 passed. Actual `make db-reset`
passed in a separate disposable `wave_reset` database; no existing dev/production database
was reset or migrated. Gateway readiness and migration expectations now require `0260`.
Full `ENV=ci make lint test`: **499 passed, 1 failed** (16m19s): TCP ingress p95
440.25 ms exceeded the unchanged 200 ms acceptance limit. This is not a clean full-suite
pass. The exact failing TCP benchmark passed when rerun alone
(`test_real_ingress_50rps_and_durable_queue[tcp]`, 1 passed in 135s).

Earlier verification (2026-10-14, after the KB ingest slice): `make lint` passed.
`ENV=ci make test` ran 448 tests: 442 passed and 6 failed. Three failures were test drift
and were fixed (2 in the JEV fixture, 1 in the RLS set test). Three were latency thresholds
missed under host load. All 6 passed when rerun in isolation.
Earlier verification (2026-10-14): `ENV=ci make test` **363 passed** (14m19s).
`make coverage`: 88 passed, 100% policy statement/branch coverage; CI enforces at least 90%.
`make db-reset` passed on a disposable isolated Postgres (0220, two seed tenants); the live dev
database was not reset. Gitleaks 8.24.2 source scan passed locally, network disabled, excluding
local secrets/dependencies/caches; no Git history scan was possible in this non-Git workspace.
Live: orders, Confirm, staff
alert, Approve, customer message, buttons cleared after tap, Resume AI.
Agent loop verified live (2026-10-12): a product
question ran `observe` step 1 and stopped with `done`; a draft with Confirm buttons skipped the loop;
the Confirm tap replied with the order facts verbatim (no greeting).

Dev schema 0220 and cascade policy are active. A live classification persisted
`cascade / jev / jev:jev-1.13.0` (1267 ms), then passed verify/gate and finalized.
Gateway `/readyz` returned ready after restarting gateway, worker and delivery.
The golden tests also exposed and fixed missing active-order context and the invalid multi-order
`list` action; status now uses the real agent's `status` action and excludes unplaced drafts.

The next LG08 slice added persisted approval checks (auto/suggest/first-contact/shadow), injection
followed by three muted turns and classifier timeout. Load evidence uses real ingress SQL and
the durable graph, with synthetic traffic and no external provider/delivery calls: 150 arrivals
at 50/s, pre-opened eight-connection ack p95 109.642 ms, schedule-to-ack p95 112.704 ms,
150 completed events and unique replies. Backlog drain was 2.051 turns/s; cold pool expansion
failed the 200 ms target. See [validation report](./evals/lg08_validation_report.md) for scope.

Latest slice adds wrong-contact/expired order confirmation, expired ticket cancellation,
cross-contact order lookup and multi-agent partial-result review. Two synthetic tenant scenarios
inserted in 0.238 s after migrations and proved product/order RLS isolation.

Real TCP now runs a separate Uvicorn gateway with runtime tracing and disposable database URLs.
Tracing batches compact server spans; contact resolution reduces SQL round trips; gateway-only
autocommit avoids implicit transactions for unscoped endpoint reads. Explicit tenant transactions
and eight-connection capacity remain intact. Tests cover concurrency, legacy repair, identity
conflict rollback and tenant GUC reset in both pool modes.

`make load-test` passed all three checks on unchanged repetition: TCP ack/scheduled p95
**108.808/110.330 ms**, ASGI **187.828/190.964 ms**, graph-only **97.839 ms**; both ingress paths
completed 150 turns with unique replies. Earlier failed experiments, including a post-fix ASGI
scheduling failure, are retained in the report. Only the isolated dev gateway was restarted;
`/readyz` is ready on 0220. Worker/delivery, live dev data, webhook and production were preserved.

Additional simultaneous diagnostic: four independent test-only worker runtimes completed
150 events, but only about 2.34 turns/s; latest ack p95 was 782.528 ms under database
contention. Checkpoint operations dominate worker time. This is not the production worker
topology and does not establish 50 turns/s. User decision: preserve synchronous per-node
checkpoints and plan cloud scaling against measured DB/worker capacity; no checkpoint semantics
or runtime worker concurrency were changed. Capacity notes and queue monitoring guidance are in
[RUNBOOK.md](./RUNBOOK.md).
Two spawned-worker integration tests pass on disposable Postgres: two processes process one
conversation in order exactly once, and a killed claimant releases its session lock for reaper
recovery. These are echo-path correctness checks, not graph-worker capacity evidence.

After adding the process tests: full lint/test **328 passed**, policy coverage **88 passed / 100%**,
disposable reset and isolated Gitleaks passed. The dev gateway remained ready; no runtime worker
configuration was changed.

LG07 compose failover slice: Gemini is now an optional, compose-only secondary provider; all
provider failures produce a localized no-claim template. Mocked primary→fallback→template tests
and deterministic template grounding pass. Latest full suite: **333 passed**; after the final
pending-confirmation template safeguard, the focused composer/settings/contracts run passed
**56 tests**. `make lint` is clean. No live Gemini request was made.

LG07 staff Edit slice: approval alerts gain an **Edit** button (private staff chats); the next
private message from that chat becomes the customer reply, validated (non-empty, ≤2000 chars, no
control characters or `/commands`), delivered once through the outbox and audited as `edited`
(`approval_audit.old_values` draft / `new_values` final_text). Migration **0230** (additive:
`edit_reply` action, `awaiting` status); gateway `EXPECTED_SCHEMA_VERSION` is now 0230, so a
restarted dev gateway reports not-ready until the dev DB is migrated. Evidence: `make lint` clean;
full `make test` **347 tests** (342 passed + 5 failures that were solely the old schema-version
pin, fixed; those three files rerun: 14 passed). Edit coverage: 8 new tests in
`tests/test_staff_path.py` plus 1 webhook-level test in `tests/test_ingress.py`, all on throwaway
testcontainers Postgres with mocked Telegram transport. **Not done:** `make db-reset` on the
persistent dev DB (migration was applied from scratch by the test containers instead); no live
Telegram Edit round trip.

LG07 closing slice: Edit now binds to the alert message (migration **0240**, `staff_actions.source_message_id`
recorded on the tap) and works in staff groups by replying to the alert (non-matching group text is
rejected 422, never a customer message); Edit button shown for group targets too. Also: outbound
`redacted_body` is redacted at all four insert sites; finalize persists `contacts.locale`
(en|ms|zh|ta); `has_attachment` is an `always_review_if` flag; delivery 429 `retry_after` test added
(dead-letter/alert already tested); DB-backed shadow-policy test; default `GOOGLE_AI_MODEL` is
`gemini-3.1-flash-lite`. Migrations 0230 and 0240 were applied (additive, no reset) to dev DB
`csa_dev_db`. Evidence: `make lint` clean; full `make test` **358 passed**. **Not tested live:**
Telegram Edit round trip (private or group) on the dev bot, real Groq outage, and a crash-kill of the
delivery worker mid-send; live Telegram Edit round trip (group alert reply) succeeded on the dev bot.

## Known gaps

- LG07 private-chat Edit binds to the chat's newest armed Edit (group Edit binds to the replied alert). Gemini failover: key and `GOOGLE_AI_MODEL=gemini-3.1-flash-lite` work for a direct client call, and live compose failover with a mocked-failing Groq primary and real Gemini succeeded 3/3 (~1 s) after the single-attempt budget fix (fallback timeout 5.0 s); a real Groq outage was not exercised; the old default `gemini-2.5-flash` returned 404 for new keys, so the default is now `gemini-3.1-flash-lite`. Migrations 0230/0240 were applied to the dev DB `csa_dev_db` (not a reset).
- Circuit-breaker and dead-letter alert producers already enqueue staff notifications and are covered by circuit and ingress/delivery reaper tests; delivery remains Telegram-only.
- No staff-target admin APIs; staff authorised by chat id, not per person.
- Ticket SLA not read from `clients.config.sla`.
- Jev cascade currently gates on intent confidence only; broader per-question calibration, vendor
  retention/SLA review and real-client rollout remain pending. Synthetic results are not production guarantees.
- WhatsApp, Gmail, Calendar channels and extra agents are planned only (see LG13).
- LG08 full golden matrix, broad empty-database seed timing and longer simultaneous socket-level
  ingress/worker load remain open. The separate four-consumer diagnostic completed every event,
  but ack p95 was 782.528 ms and drain throughput about 2.34 turns/s; this is far below 50
  turns/s. Four independent graph savers are only a benchmark topology, not production runtime.
  Do not scale concurrency or weaken checkpoint durability until a workload and connection
  budget is selected. ASGI timing on the shared generator/server loop remains variable.
  Invalid/expired callbacks currently escalate rather than returning a dedicated rejection acknowledgement.
