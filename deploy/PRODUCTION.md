# Production stack (interim single host)

How the production Compose stack differs from staging and how the owner switches a host to it.
The design and the reasons are in
`docs/superpowers/specs/2026-11-04-lightsail-s3-interim-hosting-design.md`. This file is the
operator's half. Nothing here names a cloud product, so the same steps apply on EC2 later.

The production stack is the three staging Compose files plus `deploy/compose.production.yml`,
applied last. The overlay changes only:

- files (knowledge base, HR and training library) live in S3 (`STORAGE_BACKEND=s3`) and no upload volume is mounted;
- two workers, and memory limits and OOM priorities so the kernel kills a worker or the frontend
  before Postgres;
- Postgres settings for a 2 GB host;
- the gateway joins the `edge` network so the tunnel connector can reach it;
- the database image can be named by the operator (`CSA_PROD_DB_IMAGE`) while the registry package
  is private.

`tests/test_production_overlay.py` renders the stack with synthetic values and checks all of this
against the real settings class. The memory numbers are starting values; the capacity gate decides
whether they hold.

## Keys and who holds what

| Service | S3 key | Allowed |
| --- | --- | --- |
| gateway | application key (`csa-prod-app`) | `kb/`, `hr/` and `training/` read, write, delete; `backups/` put-only |
| worker (two replicas) | worker key (`csa-prod-worker`) | `kb/` read only |
| delivery, frontend, database | none | |

The worker only reads knowledge-base files, so a leaked worker key cannot read HR documents or
training files, or change anything. The policy is in `docs/OBJECT_STORAGE.md`. If the owner chooses not to create a
second key, the worker key files may hold a copy of the application key instead; the stack works
the same and the worker's narrower scope is lost.

## Secret files the overlay needs

Under the host's secret directory (`CSA_STAGING_SECRET_DIR`, mode `0400`, owner uid 1000), in a
`storage/` folder next to `runtime/` and `pipeline/`:

- `storage/s3_app_access_key_id`, `storage/s3_app_secret_access_key`
- `storage/s3_worker_access_key_id`, `storage/s3_worker_secret_access_key`

Compose refuses to start a service whose secret file is missing. Create them with a hidden prompt
or a pipe, never as a command argument, so a key never lands in shell history or a log:

```bash
sudo install -d -m 0700 -o 1000 -g 1000 "$CSA_STAGING_SECRET_DIR/storage"
read -rsp "Worker access key id: " V; echo
printf %s "$V" | sudo install -m 0400 -o 1000 -g 1000 /dev/stdin "$CSA_STAGING_SECRET_DIR/storage/s3_worker_access_key_id"
read -rsp "Worker secret access key: " V; echo
printf %s "$V" | sudo install -m 0400 -o 1000 -g 1000 /dev/stdin "$CSA_STAGING_SECRET_DIR/storage/s3_worker_secret_access_key"
unset V
```

Do the same for the two `s3_app_*` files. Rotate by writing new files and recreating the services
(`docs/runbooks/05_rotate_credentials.md`).

## Host environment file

Beside the secrets the host keeps one sourced file with the non-secret values. It stays on the
host, never in git (the repository is public). Besides the staging variables
(`CSA_STAGING_PROJECT`, `CSA_STAGING_SECRET_DIR`, `CSA_STAGING_PYTHON_IMAGE`,
`CSA_STAGING_FRONTEND_IMAGE`, `CSA_STAGING_CONSOLE_ORIGIN`, `CSA_STAGING_HOOKS_ORIGIN`) it sets:

```bash
export CSA_PROD_S3_BUCKET=<bucket name>
export CSA_PROD_S3_REGION=<region, for example ap-southeast-1>
# Optional: only while the database image package is private and the image was loaded by hand.
export CSA_PROD_DB_IMAGE=csa/pg16-postgis-vector:prod-local

csa_compose() {
  ( cd ~/CSAV2 && docker compose --env-file /dev/null --project-name "$CSA_STAGING_PROJECT" \
    --file deploy/compose.staging-local-proposal.yml \
    --file deploy/compose.staging-shared-caddy.yml \
    --file deploy/compose.staging-pipeline.yml \
    --file deploy/compose.production.yml \
    --profile staging-approved --profile pipeline-approved "$@" )
}
```

This replaces the hand-written override file used during the first rehearsal: the database image
and the gateway's `edge` network now come from `compose.production.yml`, so delete that override
file and drop it from `csa_compose`. The tunnel connector uses the extra
`--profile dedicated-connector-approved`.

## Check before starting anything

```bash
source ~/csa-prod-env.sh
csa_compose --profile dedicated-connector-approved config --quiet && echo "config ok"
```

An error naming `S3 bucket name required` or `S3 region required` means the two `CSA_PROD_S3_*`
lines are missing from the environment file. A missing secret file is named in the error.

## Switching the gateway to S3

Until cutover the worker and delivery stay stopped: the restored data holds live channel
credentials. Start only the services that serve the console.

1. Pre-checks: the secret files above exist and `config ok` printed.
2. Recreate the gateway on the S3-capable image:
   `csa_compose up --detach --wait --wait-timeout 300 --no-deps --pull never gateway`.
   The gateway checks the bucket at startup and stops if the bucket is unreachable or the key is
   refused; read `docker logs <project>-gateway-1` if `--wait` times out.
3. Run the real-bucket check from inside the gateway (prints PASS or FAIL lines, no keys):
   `docker exec <project>-gateway-1 python -m scripts.storage_smoke`.
   Every line must be PASS.
4. Copy the existing upload files into the bucket with the block in `docs/OBJECT_STORAGE.md`
   ("Copy existing local files into S3"), using an admin profile, before any user relies on
   uploads.

## Tuning without a code change

Every number the capacity test may change is read from the host's env file, with the values the
tests pin as defaults. After editing the env file, recreate the affected services
(`csa_compose up --detach --no-deps <service>`). A migration is never involved.

| Variable | Default | What it sets |
| --- | --- | --- |
| `CSA_PROD_WORKER_REPLICAS` | `2` | Number of workers. Set `1` first if memory is tight. |
| `CSA_PROD_MEM_DB`, `_GATEWAY`, `_WORKER`, `_DELIVERY`, `_FRONTEND`, `_EMBEDDINGS`, `_CLOUDFLARED` | `640m`, `400m`, `320m`, `200m`, `256m`, `1100m`, `96m` | Container memory limit (the worker value is per replica). |
| `CSA_PROD_PG_SHARED_BUFFERS` | `192MB` | Postgres `shared_buffers`. |
| `CSA_PROD_PG_EFFECTIVE_CACHE` | `512MB` | Postgres `effective_cache_size`. |
| `CSA_PROD_PG_WORK_MEM` | `4MB` | Postgres `work_mem`. |
| `CSA_PROD_PG_MAINTENANCE_MEM` | `64MB` | Postgres `maintenance_work_mem`. |
| `CSA_PROD_PG_MAX_CONNECTIONS` | `60` | Postgres `max_connections`. |
| `CSA_PROD_GROQ_CHAT_URL` | Groq | Model endpoint. Capacity test only (below). |

## Pointing the model at a stub (capacity test only)

The gateway and worker read the model endpoint from `GROQ_CHAT_URL`. To load-test without spending
model calls, run a stub container on the `provider-egress` network and set
`CSA_PROD_GROQ_CHAT_URL=http://<stub service name>:<port>/v1/chat/completions` in the env file,
then recreate the gateway and worker. The setting accepts `https` anywhere, and plain `http` only
to a single-label internal name such as a Compose service; a dotted host or an IP address with
`http` stops the process at startup. The model key is sent to this URL, so unset the variable and
recreate both services as soon as the test ends.
