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

### Staging image publication workflow

[publish-staging-images.yml](../.github/workflows/publish-staging-images.yml)
is the reviewed path from a CI review archive to an immutable registry
reference. It is manual (`workflow_dispatch`) and takes one `ci_run_id`. It
does not rebuild. Instead, it:

1. requires the run to be a successful `push` of `.github/workflows/ci.yml` on
   `main` in this repository, with a 40-hex head SHA that is an ancestor of
   `main`;
2. downloads that run's `csa-review-only-<sha>` artifact, verifies every
   `SHA256SUMS` entry and `release.txt` revision/platform, and requires each
   OCI archive to contain exactly one `linux/amd64` image manifest whose
   manifest/config hashes match and whose revision label equals the SHA;
3. copies each archive with `skopeo copy --preserve-digests` to
   `ghcr.io/<owner>/csa-{python,frontend}:<full-sha>` (never `latest`);
4. re-reads each registry manifest, fails unless its digest equals the archive
   manifest digest, and writes the `@sha256` references to the job summary.

Only this job has `packages: write`; `ci.yml` remains build-only. Before the
first dispatch, the repository owner must create the GitHub environment
`staging-images` with a required reviewer and deployment branches restricted to
`main`. GitHub auto-creates an unprotected environment if a job references a
missing one, so dispatching before this setup removes the approval gate. The
source run must be within the seven-day artifact retention. Packages created
by the workflow are private by default; staging-host pull credentials are a
later, separate gate.

Publication is not deployment, staging startup or acceptance. A published
digest identifies a new build; it does not inherit evidence recorded for an
earlier artifact unless its manifest digest matches. Static tests and synthetic
OCI-layout execution of the archive-verification block are in
[test_publish_staging_images.py](../tests/test_publish_staging_images.py); no
registry or GitHub call was made to produce that evidence.

#### First publication: PR #11 merge images (2026-10-22)

PR #13 merged the workflow as `b60cde5adca806a073b580a7fa80527e6344aaa4`;
main CI 39833976733 passed. The owner created environment `staging-images`
before dispatch, with required reviewer `amsyrzmi` and a single deployment
branch policy `main` (read back through the environments API; self-review is
not prevented, and the owner is the only collaborator).

The owner dispatched run
[39838393620](https://github.com/amsyrzmi/CSAV2/actions/runs/39838393620)
from `main` (`b60cde5`) with `ci_run_id=39754225130`. It paused for the
environment and was approved by `amsyrzmi`, then concluded `success` with every
step passing. Its log shows the download of artifact `12414924895`
(`csa-review-only-7cdc1562a33bf3a33c4561e53d26263a6a08f731`, 195,234,264
bytes, expected digest
`sha256:920ac9637341ef6ed38e7763a867a6d2cf6e5f3329c6cfb274c5f168d800cf91`),
`skopeo` 1.13.3, the two recorded config blobs, and the registry-compare step
running with these expected manifest digests, which it found equal:

| Image | Registry reference |
| --- | --- |
| Python | `ghcr.io/amsyrzmi/csa-python@sha256:f42239697940243492cfbc8fabcd691e7479328ff013c4675b52634ffbbfc764` |
| Frontend | `ghcr.io/amsyrzmi/csa-frontend@sha256:af62b0cab9074c9d177c6b74413d44c1fc0af3fbeeb9162d22eefd9c8c803e4f` |

Both are tagged `7cdc1562a33bf3a33c4561e53d26263a6a08f731` and equal the
manifest digests already identity-verified and mount-probed above, so that
evidence applies to these references. The workflow logged out of GHCR at the
end. These references are the candidate values for `CSA_STAGING_PYTHON_IMAGE`
and `CSA_STAGING_FRONTEND_IMAGE`; they are supplied at run time and are not
committed into the Compose proposal.

Not established: package visibility (expected private; not read back because
the agent token lacks package-list access), staging-host pull credentials,
any pull from the staging host, and every runtime gate. Nothing was deployed or
started.

### Staging host image pull and secret files

Both steps run on the staging host by its operator. Neither starts a container,
database or tunnel. Credentials and generated values must never be pasted into
chat, issues, logs or source.

**Image pull.** GHCR accepts only a personal access token (classic) for package
pulls. Use a dedicated token with only `read:packages` and a short expiry. Log in
with a dedicated `DOCKER_CONFIG` directory (mode `0700`) so the operator's
default Docker credentials and other services are untouched. Pull both images
**by digest**, run `docker logout ghcr.io` with the same `DOCKER_CONFIG`, then
revoke the token or let it expire. Compose does not pull an image that is
already present, so no registry credential remains on the host for startup.
Before relying on a pulled image, confirm that its `RepoDigests` contain the
approved reference and that its revision label is the published source SHA.

**Secret files.** Generate the application secrets with
[staging_secrets.py](../scripts/staging_secrets.py):

```sh
python3 -m scripts.staging_secrets create --secret-dir /ABSOLUTE/csa-staging/secrets
python3 -m scripts.staging_secrets check --secret-dir /ABSOLUTE/csa-staging/secrets
```

Run it as UID 1000, the Python containers' runtime user. It refuses a relative
path, a path inside the checkout, an existing directory and any other UID. It
writes a complete new directory atomically, then renames it into place. It
creates these files:

- **Passwords:** fresh 48-hex-character owner, `csa_app`, `csa_ro` and
  `csa_identity` passwords.
- **DSNs:** each consistent with its password and targeting
  `staging-db:5432/csa_staging`. The owner DSN uses `csa`; the domain and
  checkpoint DSNs use `csa_app`; the identity DSN uses `csa_identity`.
- **Credentials key:** a `base64:` key of 32 random bytes.
- **Model key:** a fixed, non-functional model-key placeholder.

Every directory is `0700` and every file is `0400`, owned by UID 1000. The
tool prints only relative paths, and `check` reports failures without values.
The tunnel token (`connector/staging_tunnel_token`, `65532:65532`, `0400`) is
excluded; it belongs to the separately approved tunnel gate.

Synthetic evidence is in
[test_staging_secrets.py](../tests/test_staging_secrets.py). Those tests use
temporary directories only and cover:

- layout parity with the Compose secret sources;
- modes and ownership;
- fresh, distinct values;
- no values in output;
- refusal cases;
- tamper, symlink and loose-mode detection;
- acceptance of the generated runtime values by the real `Settings` validation.

They do not prove PostgreSQL accepts these credentials; that is established at
the isolated database bootstrap gate. Protected console test identities are
created only after migration, using the existing `csa_identity` bootstrap.

#### Host execution (2026-10-23)

PR #15 merged as `88a6711173e81d358802cb23995e81c0839f1108`; main CI 39882125951
passed. The owner then ran the procedure on the staging host (`nexusserver`).

**Image pull.**

1. The owner logged in to GHCR as `amsyrzmi` with a classic token, using a
   fresh `0700` `mktemp` `DOCKER_CONFIG` directory. Docker's unencrypted-storage
   warning referred only to that temporary directory.
2. Both images were pulled by digest. Docker reported each requested digest.
3. The owner logged out (`Removing login credentials for ghcr.io`) and deleted
   the temporary directory.
4. A later metadata check found no `/tmp/csa-ghcr-*` directory.

`docker image inspect` then showed:

| Image | `RepoDigests` | Revision label | Config user |
| --- | --- | --- | --- |
| Python | `ghcr.io/amsyrzmi/csa-python@sha256:f42239697940243492cfbc8fabcd691e7479328ff013c4675b52634ffbbfc764` | `7cdc1562a33bf3a33c4561e53d26263a6a08f731` | `1000:1000` |
| Frontend | `ghcr.io/amsyrzmi/csa-frontend@sha256:af62b0cab9074c9d177c6b74413d44c1fc0af3fbeeb9162d22eefd9c8c803e4f` | `7cdc1562a33bf3a33c4561e53d26263a6a08f731` | `node` |

Pull-token revocation: the owner confirmed that the temporary `read:packages` token was revoked after the pulls.

**Secret files.**

1. The owner created `/srv/csa-staging` with mode `0700`.
2. From the clean `88a6711` checkout, the owner ran
   `python3 -m scripts.staging_secrets create --secret-dir /srv/csa-staging/secrets`
   as UID 1000. It listed the ten expected relative paths, each `(0400)`.
3. A separate `check` printed `OK 10 staging secret files; values not displayed`
   and exited 0.
4. A later metadata-only listing (`stat`, no content read) showed every
   directory as `0700` and every file as a regular `0400` file, all owned by
   `amsyrzmi:amsyrzmi`. It found no `connector/` directory and nothing else
   under `/srv/csa-staging`.

No secret value was displayed, transmitted or committed. The files are
unencrypted at rest. Any host backup of `/srv` would include them; that
exposure has not been assessed.

Not established: that PostgreSQL accepts these credentials; Compose secret
mounts; the tunnel token; and any database, container, startup, network or
ingress behaviour. Nothing was started.

### Isolated staging database bootstrap (operator procedure, not yet run)

The heading retains the original procedure/anchor. The owner subsequently ran
steps 0-6; see the [execution record](#staging-bootstrap-host-execution-2026-10-23).
Commands below using `stg` assume the shell function from step 0, not an
installed executable. The recorded execution used explicit commands instead.

This procedure initializes the isolated staging database, upload volume and
first platform administrator. It never starts the gateway, frontend or
connector, publishes no port, and joins no shared network. **Running it needs
separate owner approval.**

The [local proposal](compose.staging-local-proposal.yml) adds two one-shot
services. Their configuration is verified only by synthetic render tests in
[test_staging_compose.py](../tests/test_staging_compose.py):

- `uploads-init` (`migration-approved`). It runs as root on the approved Python
  image with only `CHOWN`, `network_mode: none`, a read-only root and no
  secrets. It runs `chown 1000:1000` on the project's `staging-uploads` volume.
  This matches the
  [disposable storage rehearsal](#disposable-bootstrap-and-storage-rehearsal),
  plus `no-new-privileges`, which keeps capabilities but blocks privilege gain
  on exec.
- `admin-bootstrap` (its own `admin-bootstrap-approved` profile). It is non-root
  on the internal `data` network only, and its single secret is
  `staging_identity_dsn`. An inline gate requires
  `CSA_STAGING_ADMIN_BOOTSTRAP_APPROVAL=approved-isolated-staging` before reading
  that file. It also requires a `csa_identity@staging-db:5432/csa_staging` target.
  It then delegates to the packaged `scripts.bootstrap_console_admin`, which
  asks for the password with `getpass`.

The gate is inline because the approved image (`7cdc156`) predates it. The
tests run that exact rendered code against a stub module. Between `7cdc156` and
this procedure's base `2a4a037`, nothing changed in `db/`, `scripts/migrate.py`,
`scripts/staging_migrate.py`, `scripts/lg_setup.py`,
`scripts/bootstrap_console_admin.py` or `csa/console/identity.py`. The baseline
SHA-256 is `1b716cf00067ca0c56664a1ce55398d8d5c530ddc79a9247a84cfe2c46656578`,
with 31 migrations ending at `0410`.

`stg` enables only the two one-shot approval profiles. The
`staging-approved` profile (gateway, frontend, connector) is never enabled here.
Run the commands from a clean, detached checkout of the commit that merged
this procedure, as UID 1000 on the staging host. Run each step only after the
previous step's expected output matches. On any mismatch, stop and use
[teardown](#teardown-and-recovery).

**0. Session variables and read-only preflight.**

```bash
export CSA_STAGING_PROJECT=csa-staging
export CSA_STAGING_SECRET_DIR=/srv/csa-staging/secrets
export CSA_STAGING_PYTHON_IMAGE=ghcr.io/amsyrzmi/csa-python@sha256:f42239697940243492cfbc8fabcd691e7479328ff013c4675b52634ffbbfc764
export CSA_STAGING_FRONTEND_IMAGE=ghcr.io/amsyrzmi/csa-frontend@sha256:af62b0cab9074c9d177c6b74413d44c1fc0af3fbeeb9162d22eefd9c8c803e4f
export CSA_STAGING_CONSOLE_ORIGIN=https://staging-console.fourstacks.my
stg() {
  docker compose --file deploy/compose.staging-local-proposal.yml \
    --profile migration-approved --profile admin-bootstrap-approved "$@"
}
python3 -m scripts.staging_secrets check --secret-dir "$CSA_STAGING_SECRET_DIR"
docker ps -a --filter label=com.docker.compose.project=csa-staging --format '{{.Names}}'
docker volume ls --filter label=com.docker.compose.project=csa-staging --format '{{.Name}}'
stg config --quiet && echo RENDER-OK
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --entrypoint sha256sum \
  "$CSA_STAGING_PYTHON_IMAGE" /app/db/schema_baseline.sql
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --entrypoint python "$CSA_STAGING_PYTHON_IMAGE" \
  -c "import pathlib; v=sorted(p.name.split('_')[0] for p in pathlib.Path('/app/db/migrations').glob('*.sql')); print(len(v), v[-1])"
```

Expected results:

- `OK 10 staging secret files; values not displayed`.
- No container and no volume listed.
- `RENDER-OK`.
- Baseline hash `1b716cf0…6578`.
- `31 0410`.

The origin is used only for rendering; nothing serves it in this procedure.

**1. Start only the database.**

```bash
stg up --detach --wait staging-db
stg ps --format '{{.Service}} {{.Status}}'
```

Expect a single `staging-db` row reporting `healthy`. The entrypoint
initializes `csa_staging` with owner `csa` from the `0400` password file. That
file is owned by UID 1000; an earlier probe showed this works for init.

If Compose refuses because the connector token file
(`connector/staging_tunnel_token`) is absent, stop. Synthetic rendering accepts
its absence, but `up` behaviour has not been observed. Never create a
placeholder token.

**2. Apply the baseline once, to an empty database.**

```bash
stg exec -T staging-db psql -U csa -d csa_staging -tAc \
  "SELECT to_regclass('public.schema_migrations') IS NULL AND to_regclass('public.contacts') IS NULL"
```

Continue only if this prints `t`. Then:

```bash
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --entrypoint cat \
  "$CSA_STAGING_PYTHON_IMAGE" /app/db/schema_baseline.sql \
  | stg exec -T staging-db psql -U csa -d csa_staging -v ON_ERROR_STOP=1 -q -f - >/dev/null
echo "baseline exit=${PIPESTATUS[*]}"
```

Expect `baseline exit=0 0`. Like the rehearsal, `psql` runs inside the database
container over its local socket. The Python image has no `psql`.

**3. Migrate and set up checkpoints (owner role, explicit approval).**

```bash
CSA_STAGING_MIGRATION_APPROVAL=approved-isolated-staging \
  stg run --rm migrate
echo "migrate exit=$?"
stg exec -T staging-db psql -U csa -d csa_staging -tA \
  -c "SELECT count(*) || ' ' || max(version) FROM schema_migrations" \
  -c "SELECT string_agg(rolname, ',' ORDER BY rolname) FROM pg_roles WHERE rolname IN ('csa_app','csa_ro','csa_identity')" \
  -c "SELECT to_regclass('lg.checkpoints') IS NOT NULL AND to_regclass('lg.checkpoint_blobs') IS NOT NULL AND to_regclass('lg.checkpoint_writes') IS NOT NULL"
```

Expected output:

- `migrate exit=0`;
- `31 0410`;
- `csa_app,csa_identity,csa_ro`;
- `t`.

A successful migration proves that PostgreSQL accepts the generated owner DSN
over TCP. It also sets the app, read-only and identity role passwords from the
generated files.

**4. Initialize the upload volume.**

```bash
stg run --rm uploads-init
echo "uploads-init exit=$?"
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true \
  -v csa-staging_staging-uploads:/var/lib/csa/uploads:ro --entrypoint python \
  "$CSA_STAGING_PYTHON_IMAGE" \
  -c "import os; s=os.stat('/var/lib/csa/uploads'); print(s.st_uid, s.st_gid)"
```

Expect `uploads-init exit=0` and `1000 1000`.

**5. Create the first platform administrator (interactive).**

Choose the administrator email and display name. The initial password must be
at least 12 characters; keep it in a password manager, never in chat or files.
It must be changed at first login.

```bash
CSA_STAGING_ADMIN_BOOTSTRAP_APPROVAL=approved-isolated-staging \
  stg run --rm admin-bootstrap \
  --email 'ADMIN_EMAIL' --display-name 'ADMIN_DISPLAY_NAME' --create-first-platform-admin
stg exec -T staging-db psql -U csa -d csa_staging -tA \
  -c "SELECT count(*) FROM console_users WHERE is_platform_admin AND must_change_password" \
  -c "SELECT count(*) FROM console_audit WHERE action = 'identity.bootstrap'"
```

Expect `Created platform administrator <id>; password change required at first
login.` followed by `1` and `1`. This proves PostgreSQL accepts the generated
`csa_identity` DSN. Bootstrap then closes permanently, and a second run is
refused.

**6. Stop, keeping the volumes.**

```bash
stg stop staging-db
docker volume ls --filter label=com.docker.compose.project=csa-staging --format '{{.Name}}'
```

Expect `csa-staging_staging-db` and `csa-staging_staging-uploads`. Application
startup is the next, separately approved gate.

#### Teardown and recovery

Steps 1 to 5 create no customer data. If any expectation fails, remove only
this project's containers, networks and volumes, then restart from step 0:

```bash
stg down --volumes --remove-orphans
docker ps -a --filter label=com.docker.compose.project=csa-staging --format '{{.Names}}'
docker volume ls --filter label=com.docker.compose.project=csa-staging --format '{{.Name}}'
```

Both listings must be empty. This leaves the secret directory and the images
untouched. Teardown destroys the staging database, so it is **not** a recovery
method once real staging data exists. Backup and restore for persistent
staging is a separate gate.

Not established by this procedure:

- application-role (`csa_app`) credential acceptance at runtime;
- gateway, frontend and connector startup;
- HTTP uploads;
- ingress;
- backups.

### Staging bootstrap host execution (2026-10-23)

PR #17 merged as `a5eb450ae6fdcbff71c673abaedc3bd8abb8639e`.
PR CI `39914864494`, branch CI `39914798398` and main CI `39917438918`
passed. The owner ran the separately approved steps from the clean detached
checkout at `/home/amsyrzmi/CSAV2-staging-run`, using the previously accepted
Python image (source `7cdc156`, digest recorded above). This record describes
owner-reported terminal results, not an independent query of staging data.

| Step | Reported result | Evidence boundary |
| --- | --- | --- |
| Preflight | Secret checker passed; no staging containers/volumes; Compose rendered; baseline SHA-256 matched; migration inventory `31 0410` | Files/configuration and offline image checks, not runtime readiness |
| Database startup | Only `csa-staging-staging-db-1`, `csa-staging_data` and `csa-staging_staging-db` were reported created; database healthy | Database process/healthcheck, not application readiness |
| Baseline | Both checked relations absent (`t`); owner reported `baseline exit=0 0` | Baseline application, not migrations |
| Migration/checkpoints | `migrate exit=0`; `31 0410`; `csa_app,csa_identity,csa_ro`; all three checkpoint tables present (`t`) | Owner DSN accepted over TCP; migration/role setup, not app-role login |
| Upload initialization | Volume created; `uploads-init exit=0`; ownership `1000 1000` | Directory ownership only, not HTTP upload/ingestion |
| First administrator | Creation succeeded; `admin-bootstrap exit=0`; forced-change admin count `1`; bootstrap audit count `1` | Identity DSN accepted; not browser login or password-change acceptance |
| Stop | `status=exited`; both `csa-staging_staging-db` and `csa-staging_staging-uploads` listed | Stopped database with volumes retained; no destructive teardown |

The initial `stg up`/`stg ps` attempts failed with `Unknown command`: the
shell resolved `/usr/bin/stg` rather than the temporary Compose function.
No staging resources existed after those failed attempts. Subsequent commands
used `docker compose`/`docker exec` explicitly, with image pulls prohibited
and automatic Compose environment-file loading disabled for startup/one-shots.
Database-only startup succeeded without a connector token or placeholder.

No administrator email, display name, identifier or password is recorded here.
Secret contents were not requested or inspected by the assistant. The
administrator must change the initial password at first login.

The database volume now contains schema and a protected staging identity;
the upload volume is initialized. Do not rerun baseline/first-admin bootstrap,
regenerate secrets, or delete either volume as a routine next step.
Persistent backup/restore remains unaccepted.

**Next bounded slice:** review and validate a private gateway/frontend startup
and readiness procedure against the accepted image identities, mounts and
current source. Obtain separate approval before restarting the database or
starting either application. Do not enable the connector implicitly by
starting an entire profile. No host ports, shared networks, Caddy edits,
tunnel activation, DNS changes or live provider delivery are authorized.

Application-role credential acceptance, authenticated browser/password change,
tenant isolation, public ingress and recovery acceptance remain open. The
proposal has no worker, delivery or KB consumers and does not establish
end-to-end agent conversations. Protected repeatable staging CD is not yet
accepted; this is not a production deployment.

### Private staging application startup (procedure only, not run)

This heading preserves the original procedure anchor. The owner subsequently
executed startup, private checks and stop; see the
[host execution record](#private-staging-host-execution-2026-10-23).

Prerequisite: the [bootstrap execution](#staging-bootstrap-host-execution-2026-10-23)
is complete, the database is stopped and both volumes remain. Obtain separate
approval before executing this block: it restarts the database, starts gateway
and frontend, and performs private HTTP checks. It publishes no host ports and
does not start the connector. Full hosted CI for the procedure's merge must
pass first; run from a clean checkout of that accepted merge.

Supported scope is private console process/schema readiness and unauthenticated
login-page delivery. Gateway startup opens the domain and identity pools and
checks identity/HR schema readiness. It does not start worker, delivery or KB
consumers. Model access remains a disabled placeholder; HR assistant and KB
ingestion are off by default. Do not submit turns, uploads or provider actions
as part of these checks. No functioning end-to-end agent conversations are
claimed.

Source review at `1012b705` found no changes since accepted image source
`7cdc156` in gateway, Python settings, console identity, either Dockerfile or
the frontend runtime launcher. The gateway image healthcheck calls `/readyz`;
the frontend image healthcheck requires `/login` to return 200. Frontend stays
bound to its accepted build/runtime API origin, `http://gateway:8080`, and
receives no database, model or encryption secrets.

The configuration test renders exactly these three services with a missing
connector token file and checks their hardening, secret targets, dependency
health conditions and frontend API origin. This is not evidence that `up`
will succeed or that mounts, credentials or HTTP work on this host.

**Owner-run startup and private checks (separate approval).** The block is
self-contained in a Bash subshell. It disables automatic Compose environment
files and inherited profiles, forbids pulls and uses explicit service targets
with `--no-deps`. The baseline, migrations and first-admin bootstrap are
deliberately not invoked.

```bash
(
  cd /home/amsyrzmi/CSAV2-staging-run || exit 1
  export NO_LOCAL_ENV=1 ENV=ci COMPOSE_PROFILES=
  export CSA_STAGING_PROJECT=csa-staging
  export CSA_STAGING_SECRET_DIR=/srv/csa-staging/secrets
  export CSA_STAGING_PYTHON_IMAGE=ghcr.io/amsyrzmi/csa-python@sha256:f42239697940243492cfbc8fabcd691e7479328ff013c4675b52634ffbbfc764
  export CSA_STAGING_FRONTEND_IMAGE=ghcr.io/amsyrzmi/csa-frontend@sha256:af62b0cab9074c9d177c6b74413d44c1fc0af3fbeeb9162d22eefd9c8c803e4f
  export CSA_STAGING_CONSOLE_ORIGIN=https://staging-console.fourstacks.my

  test -z "$(git status --porcelain)" || exit 1
  git log --oneline -1
  python3 -m scripts.staging_secrets check --secret-dir "$CSA_STAGING_SECRET_DIR" || exit 1
  for volume in csa-staging_staging-db csa-staging_staging-uploads; do
    project=$(docker volume inspect "$volume" \
      --format '{{index .Labels "com.docker.compose.project"}}') || exit 1
    if [ "$project" != csa-staging ]; then
      printf 'Unexpected project label for %s\n' "$volume" >&2
      exit 1
    fi
    printf '%s project=%s\n' "$volume" "$project"
  done

  csa_private_compose() {
    docker compose --env-file /dev/null --project-name csa-staging \
      --file deploy/compose.staging-local-proposal.yml \
      --profile staging-approved "$@"
  }

  csa_private_compose config --quiet || exit 1
  for service in staging-db gateway frontend; do
    csa_private_compose up --detach --wait --wait-timeout 120 \
      --no-deps --pull never "$service" || exit 1
  done
  csa_private_compose ps --format '{{.Service}} {{.Status}}' || exit 1

  csa_private_compose exec -T gateway python - <<'PY'
import json
import urllib.request

for path, expected in (("/healthz", "ok"), ("/readyz", "ready")):
    with urllib.request.urlopen("http://127.0.0.1:8080" + path, timeout=5) as response:
        body = json.load(response)
        assert response.status == 200 and body["status"] == expected
    print(path, expected)
PY
  result=$?
  if [ "$result" -ne 0 ]; then exit "$result"; fi

  csa_private_compose exec -T frontend node --input-type=module - <<'JS'
const options = { signal: AbortSignal.timeout(5000), redirect: "manual" };
const login = await fetch("http://127.0.0.1:3000/login", options);
if (login.status !== 200) throw new Error(`Login status ${login.status}`);
console.log("/login 200");
const ready = await fetch("http://gateway:8080/readyz", {
  signal: AbortSignal.timeout(5000), redirect: "manual",
});
if (ready.status !== 200 || (await ready.json()).status !== "ready") {
  throw new Error("Gateway not ready from frontend network");
}
console.log("frontend -> gateway ready");
JS
)
```

Before startup, require the printed Git revision to be the accepted procedure
merge (verify before issuing the command), the checker to pass and both volume
labels to read `csa-staging`. The block exits if a volume is missing or its
project label differs. Both existing volumes are
required; this is not an empty-database initialization procedure.

Expected runtime results: exactly three healthy service rows (`staging-db`,
`gateway`, `frontend`), `/healthz ok`, `/readyz ready`, `/login 200` and
`frontend -> gateway ready`. Gateway readiness demonstrates app-role database
access plus configured identity/HR readiness, not full tenant authorization
or checkpoint read/write acceptance. Login-page delivery is not authentication,
password change, proxy/CSRF, browser or ingress acceptance. No responses,
cookies or secret values are printed.

If startup or a check fails, stop. Earlier services may remain running; do not
retry blindly, create a connector token, rerun bootstrap, or use `down -v`.
Request a bounded diagnostic operation rather than printing complete logs,
container environments or secret files.

**Stop/recovery command (separate approval):**

```bash
docker container stop csa-staging-frontend-1 csa-staging-gateway-1 &&
docker container stop csa-staging-staging-db-1 &&
docker container inspect csa-staging-frontend-1 csa-staging-gateway-1 \
  csa-staging-staging-db-1 --format '{{.Name}} {{.State.Status}}' &&
docker volume ls --filter label=com.docker.compose.project=csa-staging \
  --format '{{.Name}}'
```

This stops applications before the database, preserving both volumes, secrets
and images. If startup only partially completed, obtain an adjusted stop command
for the exact containers that exist. No destructive cleanup is authorized.
The next gate after private startup acceptance is separately reviewed dedicated
tunnel/hostname activation and authenticated browser/tenant/ingress acceptance;
shared connector, Caddy, apex/www and existing websites remain untouched.

### Private staging host execution (2026-10-23)

PR #19 merged as `8ff63c29a37b8b9802b668e14594da286f7c6f12`.
PR CI `39933597315`, branch CI `39933412519` and main CI `39935706204`
passed. The owner executed the separately approved procedure from a clean
detached checkout at `/home/amsyrzmi/CSAV2-staging-private-run`, using the
previously accepted Python/frontend image digests (source `7cdc156`).
The following is owner-supplied terminal evidence, not independent assistant
queries of persistent staging.

| Check | Reported result | Scope |
| --- | --- | --- |
| Secrets and volumes | Checker passed; both volume project labels were `csa-staging` | Preflight; no secret values displayed |
| Explicit startup | Database, gateway and frontend healthy; project-local `app` and `edge` networks created | Three selected services, no connector startup |
| Gateway liveness | `/healthz ok` | Process liveness |
| Gateway readiness | `/readyz ready` | Domain app-role access and configured identity/HR readiness |
| Frontend local page | `/login 200` | Unauthenticated login-page delivery |
| Private network request | `frontend -> gateway ready` | Frontend can reach gateway readiness over the private app network |
| Separately approved stop | All three containers `exited`; both volumes listed | Applications stopped before database; no data deletion |

The inline Node check completed successfully during this execution. This
resolves the earlier inability to syntax-check that snippet locally; it does
not retroactively change that recorded validation limitation.

Final reported state: `csa-staging-frontend-1`, `csa-staging-gateway-1` and
`csa-staging-staging-db-1` are stopped. Volumes `csa-staging_staging-db` and
`csa-staging_staging-uploads`, staging secrets and accepted images remain.
Do not repeat baseline/migrations/admin bootstrap or remove volumes to
continue. No host ports were configured by the procedure. No connector,
tunnel, DNS, Caddy, shared network or existing website change was performed.

Still unaccepted: browser login and required initial password change,
tenant isolation, CSRF/proxy behaviour, public HTTPS ingress, checkpoint
read/write, HTTP upload/ingestion, durable backup/restore and failure/recovery.
No worker, delivery or KB consumers are configured; private readiness is not
end-to-end agent-conversation acceptance, production readiness or completed CD.

**Next bounded task:** prepare the dedicated staging tunnel/hostname activation
and rollback plan, reconciling the pinned connector and token-file ownership
requirements with current official Cloudflare guidance before execution.
Resolve the account/zone authority and whether the planned staging hostname
has an existing route without changing it. Never request credentials or a
tunnel token in chat. Separate approvals remain required for tunnel creation,
token provisioning, application restart, connector activation and hostname
routing. Do not reuse or modify the shared connector or Caddy. Public
browser/tenant/ingress and recovery acceptance precede protected staging CD.

### Consolidated ingress readiness runbook (not executed)

[STAGING_INGRESS.md](STAGING_INGRESS.md) consolidates dedicated tunnel and
token provisioning, exact-host protection/routing, application restart,
connector activation, browser/tenant acceptance, failure drills and
non-destructive rollback. It also lists unresolved backup/restore and
protected-CD prerequisites. It changes no runtime or Compose configuration.
Current official Cloudflare guidance was checked directly. The owner later
selected a public login page with owner-only CSA login, without Access.
The runbook records subsequent owner-reported authority/route findings and
public-login safeguard hold points; public routing is not yet approved.

The existing configuration tests additionally render the connector alone,
verify no implicit dependencies, application-secret grants or host exposure,
and syntax-check the runbook's shell and embedded provisioning Python without
executing it. Render/syntax evidence is not a tunnel, token, DNS or ingress
acceptance result. Every infrastructure step remains separately approved.
After approved execution, batch outcomes into one evidence update rather
than opening a separate PR for each owner command. Full hosted CI remains
required before merge; no status-only commits are needed.

### Public demo exposure revision (not activated)

The owner subsequently approved local preparation of a shared host-tunnel/
Caddy alternative; use the [shared hosting procedure](STAGING_SHARED_CADDY.md)
rather than the dedicated connector instructions. The original no-host-port
and no-Caddy-change proposal remains an alternative, not the selected mode.

The current [ingress runbook](STAGING_INGRESS.md) supersedes its original
owner-only Access recommendation with the owner's selected public-login/
owner-presented demo. Anonymous console access, signup, shared credentials,
visitor accounts and provider delivery are not authorized. Review of unchanged
accepted-image identity source found login throttling, CSRF/Origin checks,
secure sessions and initial password change; public proxy/abuse behaviour is
not accepted. Exact-host abuse-control selection and compatibility remain
explicit public-routing hold points.

The owner reported adding staging to the shared tunnel and receiving 502,
then separately approved removal of only that staging route. The owner
reported both landing-page routes retained, no explicit staging DNS record,
the existing wildcard unchanged, the zone Active, no dedicated staging tunnel
and an empty Access application list. These are reported findings, not
assistant dashboard verification. No DNS values, dashboard links, personal
identifiers or credentials are reproduced. The failed route is not a staging
acceptance result; do not repeat it. At that point no Caddy/shared-connector
change was approved; the later shared-mode proposal below is still not applied.

### Shared host tunnel and Caddy proposal (not applied)

The owner explicitly selected the existing host-system-service connector and
requested a staging Caddy site instead of a dedicated connector. The
[shared-Caddy overlay](compose.staging-shared-caddy.yml) publishes only frontend
`127.0.0.1:18030:3000` and moves the unused dedicated connector behind a
separate profile. The original local proposal is unchanged. Compose 2.24.4+
is required for profile replacement with `!override`.

The [site snippet](staging-console.Caddyfile) matches only the staging
hostname, refuses non-loopback peers and proxies to that loopback frontend.
The proposed shared-tunnel route is `http://127.0.0.1:80` with the original
Host preserved, not Docker's `frontend` hostname. No application secrets,
database/gateway ports, shared Docker networks or new tunnel/token are needed.

Read-only host checks found Caddy 2.6.2 on ports 80/443 and no listener on
18030 during preparation. Port use must be rechecked at execution. No live
Caddy edit/reload, startup, route or DNS change was performed. The
[operator procedure](STAGING_SHARED_CADDY.md) requires separate approvals,
full candidate validation, existing-site checks and scoped rollback before
changing the shared service. Public-login abuse-control and proxy acceptance
hold points remain unresolved; shared hosting does not waive them.

#### Shared-mode startup failure and proposed network correction

PR #22 merged as `a9a212f`; final branch `39970840315`, PR `39970848027`
and merged-main `39970918017` CI passed. After separate approval, retained
image identities and the redacting ten-file secret checker passed; startup
made database/gateway/frontend healthy. However, host curl to port 18030
failed (exit 7, HTTP 000). Docker 29.1.3 metadata showed the requested
`127.0.0.1:18030:3000` binding but actual `3000/tcp: null`, with frontend
attached only to internal app/edge networks. Synthetic render acceptance
did not establish host publishing.

The owner approved preparing a correction adding a normal frontend-only
`frontend-host` bridge to the shared overlay. This intentionally gives
frontend outbound capability, without publishing gateway/database or
changing existing internal networks. The loopback-only binding remains.
No live network attachment/recreation or runtime proof of the correction
has occurred. All three services remain running privately, volumes retained;
Caddy/DNS/tunnel remain untouched. Ask before commit/push/PR, then require
full hosted CI and separate frontend recreation/probe approval.

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

### Post-merge planning checkpoint (2026-10-21)

PR [#2](https://github.com/amsyrzmi/CSAV2/pull/2) merged as
`0264c0ade3739f1285ab1a06e577573b71984d44`. Its PR and push checks passed.
At the final planning checkpoint, the
[post-merge main run](https://github.com/amsyrzmi/CSAV2/actions/runs/39527322898)
passed gate, frontend and package. Its review artifact
`csa-review-only-0264c0ade3739f1285ab1a06e577573b71984d44`
(artifact ID `12315215226`, 195119606 bytes) expires
**2026-10-28 01:41:52 UTC**. It was listed, not downloaded, checksum-verified or
runtime-tested in this slice. Do not substitute older artifact identity for this head.
The local workspace was fast-forwarded to this main after explicit approval;
the two unrelated historical review-report deletions remain excluded.

The next slice is **staging preparation only**, not execution:

| Prerequisite | Evidence or decision required before execution |
| --- | --- |
| Exact release inputs | Main CI passed; next verify archive checksums/source/platform/origin and approve Python/frontend registry manifest digests. CI only creates seven-day review archives; registry publication requires separate approval. |
| Secret delivery | Choose and validate the deployment mechanism for the proposal's `external: true` secrets. Docker Compose 2.40.3 is installed, but successful secret provisioning has not been demonstrated; the file is not an executable recipe for ordinary local Compose as-is. Do not assume Swarm-style external secrets are available. |
| Separate credentials | Generate new staging-only owner/app/read-only/identity passwords and encryption key through a private provisioning procedure, with a disabled synthetic model key. Do not copy `.env.local` or rotate existing dev/production credentials as a staging shortcut. |
| Database and storage | Approve a new `csa_staging` database/volume, baseline plus ordered migrations, identity bootstrap, and UID/GID 1000 upload-volume initialization. No existing dev or production database is a staging target. |
| Browser origin | The user selected `https://staging-console.fourstacks.my` for planning only. Before execution, verify control/routing/TLS, matching public/allowed origins and Secure cookies. `console.fourstacks.my` remains a future console selection, not staging approval. Preserve apex/`www`. |
| Ingress boundary | Review sanitized hostname-to-service rules and explicitly approve connectivity to the frontend only. The existing `cloudflared` container uses `network_default`, whereas the proposal's network is isolated; no network attachment or routing was changed or approved. |
| Acceptance and teardown | Plan synthetic login/logout/CSRF/roles/cross-tenant denial, cookie forwarding, readiness refusal/recovery, legacy-path denial, landing-page preservation and task-owned cleanup. Keep workers, delivery, KB ingestion and provider integration disabled. |

No staging-named containers or volumes were found during the checkpoint.
No secret values, tunnel tokens or ingress-rule contents were read. Obtain
separate approval for publication, provisioning, migration and ingress changes
after reviewing their exact scope; passing repository checks does not authorize
those actions or establish operational cutover/rollback acceptance.

### Verified artifact and selected staging design (2026-10-21)

The main artifact above was downloaded and verified without importing or
executing images. Its ZIP SHA-256 matches GitHub's reported
`c05f6f6769191bf909eec31859bdfe6c933fb5838df28210c54df492d69c3204`.
All seven `SHA256SUMS` entries and all 22 OCI blobs passed hash verification.
Release metadata and image revision labels identify source `0264c0a`, platform
`linux/amd64`, gateway origin `http://gateway:8080` and promotion disabled.

| Image | Verified archive/platform manifest digest |
| --- | --- |
| Python | `sha256:dd7b5199e634128fb6174fbbf32eb415abf1eb67fa4842a050da316e1ebc6701` |
| Frontend | `sha256:a203fc6a79bb60133fef2d8abe153088ef3d3d8a5983159502285661d31879de` |

These are **archive manifests, not approved registry references**. Publication
may preserve or change manifest representation; verify the destination digest
after any separately approved publication. Python's configured user is
`1000:1000`; frontend uses named user `node`, verified from its image passwd
entry to be UID/GID 1000:1000. The frontend's baked origin file also matches
`http://gateway:8080`. No new exact-artifact runtime acceptance was run.
The initial session verifier incorrectly expected the frontend user to be the
literal string `1000:1000`; correcting the verifier to validate the named user
required no image or repository-code change.

The user selected **protected staging-only files outside the repository** for
ordinary Compose secret delivery and confirmed that Cloudflare hostname routes
are managed in the **dashboard / Zero Trust**. These are design decisions only:

- Replace the proposal's external-secret assumptions in a separately reviewed
  local-Compose variant with `file:` sources beneath a dedicated absolute
  staging secret directory. Do not reuse `.env.local`, commit secrets, pass
  values on command lines or print rendered configuration containing credentials.
  Preserve the existing per-service grants and uppercase runtime secret targets.
- Compose file secrets are bind mounts, not an encrypted secret store.
  Host file ownership/modes must be checked for each consumer, including
  non-root Python UID 1000 and the database image's own user. Do not assume
  secret `uid`/`gid` settings can remap bind-mounted files or that host `0600`
  files are readable by UID 1000. Use a restricted parent directory and
  deliberately provisioned file access, verified first with synthetic fixtures.
  The frontend receives no database, model or encryption secrets.
- Preferred ingress design for review: a **dedicated staging connector**, not
  the existing shared connector, on a private staging edge network shared only
  with the frontend, plus a separate connector-only outbound network for
  Cloudflare. The database/migration use a private data network; the gateway
  bridges data and app networks; the frontend joins app and edge. It gets no
  outbound network. No host ports are published. This is a proposed topology,
  not an existing attachment.
- In the dashboard, the eventual route would map only
  `staging-console.fourstacks.my` to the staging frontend's internal HTTP
  listener (currently port 3000), after TLS/origin and network review.
  Do not add this route yet or expose `gateway:8080`, legacy callbacks, DB ports,
  apex or `www`. A connector credential is a separate staging infrastructure
  secret, never an application environment value or chat input.
- Before public reachability, decide the staging access policy and protected
  test identities, and rehearse authentication/CSRF/cookies/tenant denial and
  exact-image readiness on disposable fixtures. The application login remains
  mandatory even if an additional edge access policy is selected.

Sources: Docker's [Compose secret definitions](https://docs.docker.com/reference/compose-file/secrets/)
and [file-mount delivery guidance](https://docs.docker.com/compose/how-tos/use-secrets/).

### Local-Compose staging variant (configuration-only validation, 2026-10-21)

[compose.staging-local-proposal.yml](./compose.staging-local-proposal.yml) is a
separate inert variant for ordinary local Compose. It replaces `external: true`
secret assumptions with file sources under required absolute
`CSA_STAGING_SECRET_DIR` paths. It does not create secret files or provide their
values. The original proposal remains unchanged. Both variants still require
explicit approval profiles and are not invoked by CI or deployment automation.

The local variant separates three internal networks: `data` contains the
database, gateway and migration service; `app` connects only gateway/frontend;
`edge` contains only the frontend. All services are profile-gated, no host ports
are published, and no connector or outbound network is declared. An eventual
dedicated connector must be separately reviewed and attached only to `edge` and
its own outbound network. This file is not ready to expose a hostname.

The synthetic test
`NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_staging_compose.py -q`
passed **5 tests in 0.88 s**. `NO_LOCAL_ENV=1 ENV=ci make lint` also passed:
Ruff, formatting, Pyright (zero diagnostics) and import-linter. The focused test
calls `docker compose config` with both
profiles and temporary sentinel-only files; it checks profile gates, service
secret grants, internal network membership, absence of host ports/connector,
absolute file sources, synthetic file mode `0400`, and that the sentinel is not
rendered. It does not start a container or service. The permission cases model
owner-only POSIX read bits for UID 1000 and a distinct synthetic UID.

A separate, explicitly approved probe used the already-local pinned database
image with `--pull=never`, `--network none`, tmpfs database/socket paths and a
single synthetic file bind mount. The server process and `postgres` account UID
were both **999**. The host-owned `1000:1000`, mode-`0400` file was readable by
the root entrypoint and the database initialized using it via
`POSTGRES_PASSWORD_FILE`; it was not readable as UID 999 after startup. This is
consistent with the image's bootstrap flow, where the root entrypoint reads the
password file before starting PostgreSQL as `postgres`. The temporary container
and file were removed. This does not test gateway/migration image mounts: those
services are configured as UID 1000, but their required image references remain
unprovided. Verify their exact image mounts with synthetic files before
provisioning. File-backed secrets are not encrypted; never assume Compose can
remap their UID/GID/mode.

The Compose file deliberately omits Cloudflare credentials. The official
[tunnel run guidance](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/run-parameters/)
shows `--token` in service arguments, but the official
[`cloudflared` source at release 2026.10.2](https://github.com/cloudflare/cloudflared/blob/2026.10.2/cmd/cloudflared/tunnel/subcommands.go)
also defines `--token-file` and `TUNNEL_TOKEN_FILE`, then reads the token from
the referenced file. This establishes a file-only design option for that
release: give the token only to a dedicated connector as a file-backed secret
and configure `TUNNEL_TOKEN_FILE` with its path, not the token value. Before
using it, pin the connector image and digest and verify that exact image's
version, runtime UID and mounted-file access; no connector has been added or
started. No real credentials, DNS, network, persistent database or application
service were changed, and no app image was imported or published. Registry
publication, persistent secret creation, database bootstrap and the dashboard
route each still require separate approval.

### Dedicated connector image and token-file probe (2026-10-22)

Selected candidate (not yet added to a Compose file at the time of this probe):
`cloudflare/cloudflared:2026.10.3@sha256:faa8e0a1475f137c910be6025f43c8e4092fdd35461b31e19fd1390657a1ff39`
(multi-platform index; amd64 manifest
`sha256:c3f24df7c5e68c1467afc5486c155689f247eb02246b3a124fc5f42458728672`).
It is the official
[2026.10.3 release](https://github.com/cloudflare/cloudflared/releases/tag/2026.10.3)
(published 2026-10-21). That tag's
[`subcommands.go`](https://github.com/cloudflare/cloudflared/blob/2026.10.3/cmd/cloudflared/tunnel/subcommands.go)
still defines `--token-file` / `TUNNEL_TOKEN_FILE`, and its
[`Dockerfile.amd64`](https://github.com/cloudflare/cloudflared/blob/2026.10.3/Dockerfile.amd64)
uses a distroless `nonroot` base, `USER 65532:65532` and
`ENTRYPOINT ["cloudflared", "--no-autoupdate"]`.

With explicit approval, the image was pulled by digest only and inspected:
`Config.User` `65532:65532`, amd64, `cloudflared version 2026.10.3`. Probes
used `--pull=never --network none --read-only --rm`, one random **fake** token
file bind-mounted read-only at `/run/secrets/tunnel_token` and
`TUNNEL_TOKEN_FILE` naming that path:

| Host file owner/mode | Container UID | Result (exit 255 in all cases) |
| --- | --- | --- |
| `1000:1000` `0400` | 65532 (image default) | `Failed to read token file: ... permission denied` |
| `1000:1000` `0444` (probe only) | 65532 | read, then `Provided Tunnel token is not valid.` |
| `65532:65532` `0400` | 65532 | read, then `Provided Tunnel token is not valid.` |
| `65532:65532` `0400` | 1000 (`--user` override) | `permission denied` |

Conclusion: unlike the database image, this connector has **no root
entrypoint**, so its token file must be readable by UID 65532 itself; provision
it as `65532:65532` mode `0400` inside a restricted parent directory. Do not
reuse the Python services' UID 1000 ownership or world-readable modes.

Two `sudo chown` attempts by the user were not applied to the probe file (the
first targeted `/tmp`, which this session sees as a private tmpfs); the session
stopped on the mismatch instead of probing. With further explicit approval the
owner was then set by a throwaway `--network none --read-only` container from
the already-local `postgres:16-alpine@sha256:cf78e766...` image running only
`chown`. The fake files/directories and probe containers were removed; the
pinned image was kept by request. The existing shared connector and its
`cloudflared:latest` image, tunnels, DNS, dashboard routes, Caddy and
application services were not touched. No real token was created or used, and
nothing contacted Cloudflare. This establishes the image identity and file
permissions only, not tunnel connectivity, edge routing or access policy.

[compose.staging-proposal.yml](./compose.staging-proposal.yml) is the original
inert proposal:
all services are behind explicit approval profiles. It has **no published ports**,
no external network, no reference to the old `csa-platform` database and no
cloudflared attachment. The separate local variant is updated below.
`gateway:8080` is a container-only port, not host 8080.
A separate `staging-db` owns database `csa_staging`, its own volume and synthetic
fixtures. Internal networking deliberately prevents provider access. There are
no worker/delivery/KB consumers in this proposal, so queued jobs/chat do not
constitute working end-to-end acceptance. No provider keys/tokens are provisioned.

### Dedicated connector in local staging Compose (configuration-only, 2026-10-22)

The separate inert [compose.staging-local-proposal.yml](./compose.staging-local-proposal.yml)
now declares the reviewed Cloudflare image by full digest, behind the existing
`staging-approved` profile. It runs as `65532:65532`, read-only, with all
capabilities dropped and `no-new-privileges`; no host port, host network,
privileged mode or Docker socket is configured. It joins only the internal
frontend `edge` network and its own project-scoped `connector-egress` bridge.
Only `cloudflared` joins the outbound network; the gateway, frontend, database
and migration service do not.

The connector alone receives `staging_tunnel_token` from
`${CSA_STAGING_SECRET_DIR}/connector/staging_tunnel_token` and uses
`TUNNEL_TOKEN_FILE=/run/secrets/staging_tunnel_token`. No token value is in the
Compose definition. Any future secret file must be owned by `65532:65532` with
mode `0400`, under a host secret directory restricted to its operator (for
example mode `0700`). Compose file secrets are host bind mounts: do not rely on
Compose `uid`, `gid` or `mode` settings to remap their host ownership. No real
secret file was created or provisioned by this configuration change.

`NO_LOCAL_ENV=1 ENV=ci /home/amsyrzmi/CSAV2/.venv/bin/python -m pytest tests/test_staging_compose.py -q`
passed **7 tests in 0.59 s**. It verifies the pinned image, profile, network
isolation, non-root/read-only hardening, connector-only file-secret grant,
token-file path, absent host ports/external networks and non-rendering of
synthetic secret contents. It uses `docker compose config` only; no Compose
`up`, service or container is run. Full `NO_LOCAL_ENV=1 ENV=ci
UV_PROJECT_ENVIRONMENT=/home/amsyrzmi/CSAV2/.venv make lint` passed Ruff,
formatting, Pyright and import-linter. Its first run stopped only on formatting
of a new test assertion; that formatting was corrected before the passing rerun.
`git diff --check`, edited Markdown fence/added-link validation and a
credential-shaped-content scan also passed.

The Compose render itself is configuration evidence only—not connector runtime
egress, connector-token readability/authentication, DNS, public ingress or
staging readiness. The separate exact-image application probes below establish
synthetic app-secret file readability and frontend mount behavior only. The
original staging proposal, shared connector and existing hostnames/routes remain
unchanged. Matching immutable registry image references, staging secret
provisioning, database bootstrap and route activation remain separate gates.

### PR #11 merged artifacts: identity verification (2026-10-22)

PR #11 merged as `7cdc1562a33bf3a33c4561e53d26263a6a08f731`. Post-merge main
CI run [39754225130](https://github.com/amsyrzmi/CSAV2/actions/runs/39754225130)
completed successfully: gate, frontend and package jobs passed, including
Gitleaks. Its review artifact `csa-review-only-7cdc1562a33bf3a33c4561e53d26263a6a08f731`
(artifact ID `12414924895`; 195,234,264 bytes; expires 2026-10-29 09:36:53 UTC)
was downloaded for offline inspection with explicit approval and then removed.
GitHub's artifact metadata reports digest
`sha256:920ac9637341ef6ed38e7763a867a6d2cf6e5f3329c6cfb274c5f168d800cf91`.

The artifact's `SHA256SUMS` entries all verified. `release.txt`, both OCI
indexes, manifests and config blobs identify source/revision
`7cdc1562a33bf3a33c4561e53d26263a6a08f731`, platform `linux/amd64`, frontend
origin `http://gateway:8080` and `promotion=disabled`; manifest and config
blob hashes were recomputed and matched their descriptors.

| Image | OCI manifest digest | Config digest | Runtime user |
| --- | --- | --- | --- |
| Python | `sha256:f42239697940243492cfbc8fabcd691e7479328ff013c4675b52634ffbbfc764` | `sha256:c7c681096ddd73b17177ec642157a9ee887a80cab4cf88b2519784230c2ac0b1` | `1000:1000` (`csa`) |
| Frontend | `sha256:af62b0cab9074c9d177c6b74413d44c1fc0af3fbeeb9162d22eefd9c8c803e4f` | `sha256:af86f20a3d23e942d91e68f2fd9962f8273ae56ebf9a3708e3d1bd61638ba210` | config user `node`, resolved from image `/etc/passwd` as `1000:1000` |

### Exact-image synthetic mount probes (2026-10-22)

With separate explicit approval, artifact `12414924895` was downloaded again.
Its run/revision, artifact metadata, all `SHA256SUMS` entries and the Python and
frontend OCI manifest/config digests matched the identities above. Neither
expected commit-specific Docker tag existed before import. The exact archives
were loaded locally, inspected for the expected image IDs and revision labels,
then their task-created tags, all `--rm` containers, the synthetic secret files
and private temporary artifact directory were removed. No registry operation
occurred.

Three short-lived probes passed, each using the exact image, `--pull=never`,
`--network none`, `--read-only`, UID/GID 1000, dropped capabilities and
`no-new-privileges`:

1. The Python image constructed the real `csa.core.settings.Settings` using
   synthetic files at `/run/secrets` and synthetic non-secret origin settings.
   Required database/checkpoint settings, the separate `csa_identity` setting,
   the disabled model-key placeholder and the validated synthetic credentials
   key all resolved. The fixture directory was mode `0700`; each file was mode
   `0400`, owned by UID 1000. No database connection was attempted.
2. The Python image could read all five Compose-targeted migration files
   (`MIGRATE_DATABASE_URL`, `LG_SETUP_DATABASE_URL`, `CSA_APP_PASSWORD`,
   `CSA_RO_PASSWORD`, `CSA_IDENTITY_PASSWORD`) as UID 1000. The migration
   runner and database were not invoked.
3. The frontend ran a Node assertion script only, not its server. It ran as
   UID/GID 1000, matched the baked `http://gateway:8080` origin and rejected a
   mismatched origin. `/` was mounted read-only; `/tmp` and `/app/.next/cache`
   were writable tmpfs mounts; the probe confirmed no `/run/secrets` mount.

The first frontend attempt failed because the probe looked for the baked origin
under `.next/standalone`; inspection of the image Dockerfile showed that this
tree is copied to `/app`, so the correct runtime path is `/app/console-api-origin`.
The same bounded probe passed after that path correction. The failed
`--rm` container left no persistent container.

This closes only exact-image, synthetic configuration-file readability and
frontend mount/origin checks. It does not exercise Compose secret mounting,
application server startup/readiness, database behavior, live network
connectivity, tunnel authentication, DNS, public ingress, staging operations or
end-to-end agent conversations.

The proposal currently includes only database, gateway, frontend, migration and
the dedicated connector. It has **no worker, delivery or KB-ingestion consumer**;
it does not support accepted end-to-end agent conversations, and no real
provider delivery is enabled or implied.

Before any separately approved execution:

1. The verified artifacts are CI review archives only; this CI run provides no
   registry image references for the staging Compose proposal. Select approved
   Python/frontend registry `@sha256:` digests and verify source, platform and
   origin before staging configuration can use them. Publishing the archives,
   importing them into a registry, or deploying them requires separate approval.
   Required Compose image variables have no default, but Compose does not itself
   enforce digest syntax; the operator must verify it. A read-only metadata
   lookup on 2026-10-22 was blocked: the GitHub Packages list API returned 403
   for the current token; anonymous HEAD requests to the candidate GHCR
   manifests returned 401, and anonymous token requests returned 403. This does
   not establish that the packages are absent. No credential was changed and no
   registry image was downloaded or published. A subsequent user-run lookup
   under an authorized `gh` identity returned no `csa-python`/`csa-frontend`
   packages and HTTP 404 for both version lists: no registry image exists yet.
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
[handoff](../docs/archive/handoffs/2026-10-19-domain-release-next-session.md), alongside
exact digests, commands and scope. No application images were rebuilt for this run.
This closes the exact-artifact runtime gap only; public browser/ingress, live
providers, workers/uploads and separately approved staging remain unverified.
