# Repository-only release packaging

Status: build/review only. No registry publish, automated promotion, deployment,
service replacement, DNS/tunnel modification or persistent migration is enabled.
[CI](../.github/workflows/ci.yml) uses GitHub-hosted runners, read-only permissions,
the existing Python/schema/Testcontainers/coverage/secret gates and a separate
reviewed frontend install/check/build gate. Service pull credentials are supplied
before PostgreSQL starts, not merely by a later login step. The package must grant
this repository read access; fork-token/package visibility still needs hosted-CI
acceptance. No fallback to a different extension set is allowed.

## Inputs and artifacts

Never build from the repository root or copied web checkout. Their Docker ignore
files deny the entire context. Use new directories outside the checkout:

```sh
python3 -m scripts.release_context /tmp/REVIEW-python-context
node CSA_WebConsole/csa-console/web/scripts/check-isolated.mjs \
  --context /tmp/REVIEW-frontend-context
```

These commands refuse an existing destination and copy only reviewed application
Python/SQL plus the existing synthetic decision-case JSON, or the adopted frontend
allowlist. No environment file, local SQL dump,
Git directory, cache, upload, legacy Python API, copied archive or installed
dependency tree is sent to Docker. Symlink inputs fail closed. The frontend
materializer and isolated check use the same allowlist. Newly required source
must be deliberately added to that list. Generated contexts are disposable;
delete only your own specifically named directories after checking their paths.

Python installs the exact frozen production closure from `uv.lock`, without
installing the project/build backend; source runs from `/app`. Node uses lockfile
v3 and `npm ci --ignore-scripts --no-audit --no-fund`. Python, uv and Node bases
are pinned by digest. Next standalone output includes traced dependencies,
static assets and the reviewed public brand asset, not the build toolchain.
Both runtime users are non-root. One Python image supports gateway, worker,
delivery and KB CLI commands; packaging them does not authorize running them.

CI builds only after both gates pass, from its clean checkout. It records the
full source SHA, commit timestamp (`SOURCE_DATE_EPOCH`), platform (`linux/amd64`),
origin, exact input-file checksums, BuildKit image digest metadata and SHA-256 checksums with two separate
OCI archives. Tags and OCI revision labels use the SHA, never `latest`/`:1`.
PR merge SHAs identify the tested merge, not an approved production release.
Archive checksums, OCI manifest digests, local Docker image IDs and future
registry manifest digests are distinct; retain the correct kind at each step.
The pinned inputs and timestamp support repeatable builds; byte-for-byte
reproducibility across builders has **not** been established by a double build.
Artifacts expire in seven days and are review evidence, not durable releases.

Example build-only invocation (requires Buildx; **not deployment approval**):

```sh
docker buildx build --platform linux/amd64 \
  --build-arg SOURCE_REVISION=FULL_REVIEWED_40_HEX_SHA \
  --build-arg SOURCE_DATE_EPOCH=COMMIT_UNIX_TIMESTAMP \
  --output type=oci,dest=/tmp/REVIEW-python.oci.tar \
  --metadata-file /tmp/REVIEW-python.metadata.json /tmp/REVIEW-python-context
docker buildx build --platform linux/amd64 \
  --build-arg SOURCE_REVISION=FULL_REVIEWED_40_HEX_SHA \
  --build-arg SOURCE_DATE_EPOCH=COMMIT_UNIX_TIMESTAMP \
  --build-arg CSA_CONSOLE_API_ORIGIN=http://gateway:8080 \
  --output type=oci,dest=/tmp/REVIEW-frontend.oci.tar \
  --metadata-file /tmp/REVIEW-frontend.metadata.json /tmp/REVIEW-frontend-context
```

Dirty-worktree local builds are **unreleased candidates**, even when labelled
with HEAD for tooling validation. They must not be claimed as that clean commit.
CI's clean checkout, tests and recorded digests supply release identity.

## Origin and route boundary

`CSA_CONSOLE_API_ORIGIN` is a non-secret **server-only** bare HTTP(S) origin.
Next rewrites are baked during build while server identity requests read the
runtime variable. The artifact is therefore origin-bound: build with
`http://gateway:8080` and supply the same origin at runtime. The release launcher
rejects missing, invalid or different origins before starting the server.
Changing that origin requires a new build/digest, not a runtime-only override.
Never use `NEXT_PUBLIC_*`, credentials or a public landing-page URL for this.

Only `/console/v1/*` is forwarded to the private gateway. The mounted legacy
`/api/v1/channels/tiktok_shop/oauth/authorize`, `/oauth/callback`, and webhook
routes remain in the backend for existing compatibility; they are **not** the
M6B verifier callback. The adopted frontend returns 404 for them, even with a
session. Do not expose the gateway as a public catch-all or add a legacy callback
rewrite. Direct private-network access to the gateway still reaches its mounted
routes; the boundary is network isolation plus frontend path restrictions,
not removal of those routes or a claim that their authorization is repaired.
No new callback registration or live provider flow is implemented here.

## Isolated staging proposal (not run)

[compose.staging-proposal.yml](./compose.staging-proposal.yml) is an inert proposal:
all services are behind explicit approval profiles. It has **no published ports**,
no external network, no reference to the old `csa-platform` database and no
cloudflared attachment. `gateway:8080` is a container-only port, not host 8080.
A separate `staging-db` owns database `csa_staging`, its own volume and synthetic
fixtures. Internal networking deliberately prevents provider access. There are
no worker/delivery/KB consumers in this proposal, so queued jobs/chat do not
constitute working end-to-end acceptance. No provider keys/tokens are provisioned.

Before any separately approved execution:

1. Approve named Python/frontend **registry `@sha256:` digests**, verify the
   recorded origin, platform and checksums, and approve distinct staging secrets.
   Required Compose image variables have no default, but Compose does not itself
   enforce digest syntax; the operator must verify it. Nothing publishes these
   archives to a registry in this slice.
2. Provision `csa_app` domain/checkpoint DSNs and a separate `csa_identity` DSN,
   all targeting `staging-db:5432/csa_staging`. Owner DSNs must never be mounted
   into gateway/frontend. External secret targets use **uppercase Settings names**.
   The model key is a disabled synthetic placeholder, not a live API key.
3. Choose a separately approved staging HTTPS origin. Set exact
   `CONSOLE_ALLOWED_ORIGIN`, `PUBLIC_BASE_URL` and secure cookies. The future
   `console.fourstacks.my` choice is not DNS/origin evidence or automatic staging
   approval. Do not use apex/`www` as staging.
4. Explicitly provision the clean baseline in the new isolated database; the
   forward migration runner does not create it. Review extensions, ordered
   migration checksums, compatibility, identity-role grants and initial admin
   setup. For existing staging data require backup/restore evidence first.
   Current source expects every shipped migration through 0360; persistent dev
   0300 is only last recorded approved state and must remain untouched.
5. Migration is **not** a gateway startup dependency. The separate
   `migration-approved` profile and exact `approved-isolated-staging` approval
   are both required; the gate reads only staging secrets, validates the exact
   owner/database/host, then runs forward migrations and checkpoint setup.
   No automatic baseline/reset/downgrade or rollback is present.
6. Confirm upload-volume ownership is UID/GID 1000 before starting the non-root
   gateway. Keep KB execution disabled until a separately reviewed isolated
   embedding/worker/storage plan exists. Do not mount repository uploads.
7. Gateway image health uses Python's standard library `/readyz` probe, covering
   shipped schema and configured identity readiness, with no missing `curl`.
   Frontend image health checks `/login` availability; this is **not** backend,
   authentication or role acceptance. Compose waits on gateway readiness.
8. Obtain sanitized ingress rules, cloudflared network mode and memberships
   before proposing any private attachment or verified-free loopback binding.
   Do not assume `localhost` means the host. No tunnel/provider/DNS mutation is
   authorized. Preserve `fourstacks.my`, `www` and their landing service.
9. Before release, separately test login/logout/CSRF/roles/tenant denial, secure
   cookie forwarding, uploads and accepted worker flows, readiness refusal on
   missing migrations/identity, disabled legacy ingress, and apex/`www`
   preservation. Keep M6 local-only/unverified/setup-required/synthetic-demo
   labels. No live TikTok/Gmail/Calendar or authoritative inventory is enabled.

The historical [compose.yml](./compose.yml) remains reference only: it assumes
the legacy database/network, automatic migrations and occupied host port 8080.
Do not run it for this release. Protected promotion, rollback compatibility,
backup restore and ingress acceptance remain separate release gates. Historical
latency failures and unchanged `<200 ms` thresholds remain in
[progress](../docs/PROGRESS.md); packaging success cannot waive them.

## Disposable packaged-console acceptance

Run the repository's [packaged acceptance test](../tests/test_packaged_console.py)
with `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_packaged_console.py -q`.
It requires the existing Python test dependencies, Node on `PATH`, and a local
Linux Docker daemon whose private bridge addresses are reachable from the test host.
Remote-daemon and Docker Desktop networking are not covered.

The test materializes fresh allowlisted contexts outside the checkout, builds both
candidate images and applies baseline/migrations only to its Testcontainers database.
The gateway/frontend run non-root, read-only and without published ports on a new
internal-only network. A disposable loopback TLS terminator forwards to the frontend;
the client trusts only its generated certificate. No environment file, provider key,
existing container network, persistent volume or staging Compose service is used.
The database retains its normal Testcontainers host connection for synthetic setup
and fault injection; gateway/frontend have no external network attachment.

Assertions cover HTTPS CSRF/login/logout, Secure/HttpOnly/SameSite cookies, mandatory
password change/session rotation, platform/tenant-admin/staff/reader boundaries,
cross-tenant list/detail denial, legacy marketplace path denial, M6 local-only
rendered warnings, and readiness refusal/recovery for missing schema or unsafe/missing
identity grants. The test cleans its candidate tags, contexts, app containers,
network attachment and TLS files; Testcontainers owns database teardown.

This is an HTTP-client and server-rendered-page test, not a JavaScript browser,
Cloudflare, public DNS, persistent staging, upload/worker or live-provider acceptance
test. Images built from the dirty worktree remain unreleased candidates; the full-SHA
label does not turn that worktree into a release of the labelled commit.

### Disposable bootstrap and storage rehearsal

The same module includes `test_packaged_staging_bootstrap_and_storage`.
It starts a separate fresh Testcontainer named logically `staging-db` with database
`csa_staging`; neither the persistent proposal nor existing Docker networks are used.
It reads the baseline from the newly built Python image and applies it with the
database image's `psql` (the Python runtime deliberately has no `psql` dependency).
Synthetic uppercase migration secret files live only in task-owned container tmpfs.

The packaged migration entry point refuses absent approval before reading files and
rejects either incorrectly targeted owner/checkpoint DSN without creating migration
relations or runtime roles. Explicit approval then provisions every shipped migration,
identity login and checkpoint tables. Reapplication preserves the migration ledger
and its original timestamps.

The test calls the packaged `bootstrap_console_admin.run` entry point with a synthetic
password supplied through the exec environment, not argv; the interactive
`getpass` prompt/confirmation itself is **not** tested. Exactly one forced-change
platform admin and one bootstrap audit entry persist; a second bootstrap is refused.
No admin is installed outside the disposable database.

A new task-owned upload volume initially rejects UID/GID 1000 writes. The application
container has all capabilities dropped; even an exec as root cannot chown it.
A separate explicit, network-disabled root initializer with only `CHOWN` initializes
the volume's directory to 1000:1000. The original non-root container then writes,
reads, checks file ownership and deletes a synthetic probe. Containers are removed
before the owned volume/network. This is a filesystem permission rehearsal, not
HTTP upload, ingestion, durable backup/restore or persistent staging approval.

### Disposable synthetic database restore

`test_packaged_synthetic_backup_restore` in the same module uses `pg_dump --format=custom`
from its migrated synthetic source Testcontainer and restores into a second fresh
Testcontainer with `pg_restore --exit-on-error --single-transaction`.
Roles are handled separately with `pg_dumpall --roles-only --no-role-passwords`.
The test omits only `CREATE ROLE csa;` because the target Testcontainer has already
created that owner; its role attributes are retained. This is a fixture-specific
restore procedure, not a general-purpose script for existing clusters.

After restoring role definitions, owners, grants, RLS and database contents, the
test provisions **new synthetic** app/identity passwords rather than backing up role
password hashes. It compares migration versions and original applied timestamps,
two tenant identities, a human identity, membership and audit payload, and checks
checkpoint table presence. The packaged runtime verifies domain and identity
readiness, tenant-isolated membership reads, denied app access to session secrets,
denied identity access to domain tables and immutable audit privileges.
Removing the restored 0360 ledger entry simulates incomplete migration metadata:
readiness refuses it and recovers after the exact row is restored.

Dump bytes are transferred only in memory and into the owned target container's
temporary files; teardown removes both dump files with that container. No real
database, environment file, backup service, repository dump or artifact upload is used.
This is a synthetic logical restore drill, not measured RPO/RTO, WAL/PITR,
encrypted-credential/key recovery, session-login continuity, checkpoint replay,
upload-volume backup, production restore or staging rollout acceptance.

### Exact hosted-artifact runtime evidence (2026-10-20)

The three acceptance cases above also pass with the exact downloaded OCI images
from [run 39367800618](https://github.com/amsyrzmi/CSAV2/actions/runs/39367800618),
source `da1b50da5b40479370507020ce7d17cd1317707f`: **3 passed**, one existing
warning, **233.60 s**; repository lint passes. A session-only adapter replaces the
image-build fixture with verified OCI imports, leaving all acceptance tests unchanged.
It verifies the GitHub ZIP digest, file checksums and source/platform/origin metadata
before loading, and checks the exact imported manifest IDs and revision labels.
All 22 OCI blob hashes were independently checked before import.
The initial adapter's config-versus-manifest ID setup error is retained in the
[handoff](../docs/handoffs/2026-10-19-domain-release-next-session.md), alongside
exact digests, commands and scope. No application images were rebuilt for this run.
This closes the exact-artifact runtime gap only; public browser/ingress, live
providers, workers/uploads and separately approved staging remain unverified.
