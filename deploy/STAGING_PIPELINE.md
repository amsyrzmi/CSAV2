# Staging pipeline activation: worker, delivery, embeddings, Telegram

Status: owner-approved activation plan for the running `csa-staging` project.
It turns the console-only staging deployment into one that processes sandbox
turns, ingests knowledge-base uploads, answers with a real model and receives
Telegram webhooks. Each numbered step is separately approved and recorded.

## Why the console alone does nothing

Sandbox messages and KB uploads are written to queues that only the worker
process consumes. Replies to Telegram are sent only by the delivery process.
The gateway's model key is a disabled placeholder, there is no embedding
service, and the public hostname reaches only the frontend, which has no
webhook path. None of that is a code defect; the pipeline was never started.

## 1. Secret files (owner runs; nothing is printed)

The keys live in the owner's ignored `.env.local`. Copy the two values into the
protected pipeline directory without echoing them. The model key must be a real
key, not the disabled placeholder; the bot token is the Telegram bot that will
be bound to the Meranti staging tenant.

```bash
(
  umask 077
  d=/srv/csa-staging/secrets/pipeline
  mkdir -p -m 700 "$d"
  env_file=/home/amsyrzmi/CSAV2/.env.local
  for pair in GROQ_API_KEY:staging_model_key TELEGRAM_BOT_TOKEN:staging_telegram_bot_token; do
    key=${pair%%:*}; file=${pair##*:}
    [ -e "$d/$file" ] && { echo "exists, unchanged: $file"; continue; }
    grep -E "^${key}=" "$env_file" | head -1 | cut -d= -f2- | tr -d '\r\n' > "$d/$file"
    chmod 400 "$d/$file"
    echo "$file: $(stat -c %s "$d/$file") bytes, mode $(stat -c %a "$d/$file")"
  done
)
```

Expected: `staging_model_key` around 56 bytes and `staging_telegram_bot_token`
around 46 bytes, both mode 400. A 0-byte file means the key is missing from
`.env.local`; delete the empty file and fix the source before continuing.

**Consequence of reusing the development bot token:** a Telegram bot has one
webhook. Step 5 moves it from the development hostname to staging, so the
development gateway stops receiving updates until it is re-registered. The
development gateway is not running as a service today, so nothing is lost now.

## 2. Render and start the pipeline

Run from an approved checkout of `main` that contains this file. The gateway is
recreated because it gains the embedding settings, the hooks network and the
loopback port; the console is unavailable for a few seconds while that happens.

```bash
(
  cd /home/amsyrzmi/CSAV2-seed || exit 1
  export NO_LOCAL_ENV=1 ENV=ci COMPOSE_PROFILES=
  export CSA_STAGING_PROJECT=csa-staging
  export CSA_STAGING_SECRET_DIR=/srv/csa-staging/secrets
  export CSA_STAGING_PYTHON_IMAGE=ghcr.io/amsyrzmi/csa-python@sha256:f42239697940243492cfbc8fabcd691e7479328ff013c4675b52634ffbbfc764
  export CSA_STAGING_FRONTEND_IMAGE=ghcr.io/amsyrzmi/csa-frontend@sha256:af62b0cab9074c9d177c6b74413d44c1fc0af3fbeeb9162d22eefd9c8c803e4f
  export CSA_STAGING_CONSOLE_ORIGIN=https://staging-console.fourstacks.my
  export CSA_STAGING_HOOKS_ORIGIN=https://hooks.fourstacks.my

  csa_pipeline_compose() {
    docker compose --env-file /dev/null --project-name csa-staging \
      --file deploy/compose.staging-local-proposal.yml \
      --file deploy/compose.staging-shared-caddy.yml \
      --file deploy/compose.staging-pipeline.yml \
      --profile staging-approved --profile pipeline-approved "$@"
  }
  csa_pipeline_compose config --quiet || exit 1
  csa_pipeline_compose config --services

  # One-shot: make the embeddings cache volume writable by the runtime UID.
  csa_pipeline_compose --profile pipeline-bootstrap-approved run --rm --no-deps embeddings-init || exit 1

  for service in staging-embeddings gateway worker delivery; do
    csa_pipeline_compose up --detach --wait --wait-timeout 300 --no-deps --pull never "$service" || exit 1
  done
  csa_pipeline_compose ps --format '{{.Service}} {{.Status}}'
)
```

Expected services: `staging-db`, `gateway`, `frontend`, `staging-embeddings`,
`worker`, `delivery`. All six carry `restart: unless-stopped` in the compose files, so they
return after a host reboot or crash; one-shot services (migrate, init, bootstrap) do not. The first embeddings start downloads the pinned
`intfloat/multilingual-e5-small` revision through `model-egress` and can take a
few minutes; the worker waits for the gateway health check, not for the model.

Verify the queues drain: KB jobs in the console move from `queued` to
completed, a new sandbox message receives a reply, and the gateway's
`/console/v1/kb/readiness` no longer reports the embedder as unavailable.
Model calls now cost money; the worker holds the only real key.

## 3. Hooks Caddy site (owner runs; sudo)

Append `deploy/staging-hooks.Caddyfile` to `/etc/caddy/Caddyfile` as its own
site block, following the same backup, diff, validate and reload sequence as
[the shared Caddy runbook](STAGING_SHARED_CADDY.md#3-prepare-the-live-caddy-edit-separate-approval).
The block answers 403 to anything that is not loopback or the connector
container, forwards only `POST /hooks/*` to `127.0.0.1:18031`, and returns 404
for every other path. It uses an ordered `route` so the deny is evaluated first.

Local check after reload:

```bash
curl --noproxy '*' --silent --output /dev/null --write-out 'GET hooks HTTP %{http_code}\n' \
  --header 'Host: hooks.fourstacks.my' http://127.0.0.1/readyz
curl --noproxy '*' --silent --output /dev/null --write-out 'POST hooks HTTP %{http_code}\n' \
  --header 'Host: hooks.fourstacks.my' --request POST http://127.0.0.1/hooks/not-a-real-id
```

Expected 404 for the first (the gateway's other paths are not exposed) and 404
from the gateway for the second (unknown endpoint id), which proves the proxy
path works without exposing anything else.

## 4. Public route (owner, Cloudflare dashboard)

On the existing shared tunnel add one published application:
hostname `hooks.fourstacks.my`, path empty, service `http://192.168.0.50:80`,
no Host header override. This is the same origin the other routes use. Do not
touch the console route or any other hostname.

## 5. Bind the bot and register the webhook

```bash
(
  cd /home/amsyrzmi/CSAV2-seed || exit 1
  export NO_LOCAL_ENV=1 ENV=ci COMPOSE_PROFILES=
  export CSA_STAGING_PROJECT=csa-staging
  export CSA_STAGING_SECRET_DIR=/srv/csa-staging/secrets
  export CSA_STAGING_PYTHON_IMAGE=ghcr.io/amsyrzmi/csa-python@sha256:f42239697940243492cfbc8fabcd691e7479328ff013c4675b52634ffbbfc764
  export CSA_STAGING_FRONTEND_IMAGE=ghcr.io/amsyrzmi/csa-frontend@sha256:af62b0cab9074c9d177c6b74413d44c1fc0af3fbeeb9162d22eefd9c8c803e4f
  export CSA_STAGING_CONSOLE_ORIGIN=https://staging-console.fourstacks.my
  export CSA_STAGING_HOOKS_ORIGIN=https://hooks.fourstacks.my
  docker compose --env-file /dev/null --project-name csa-staging \
    --file deploy/compose.staging-local-proposal.yml \
    --file deploy/compose.staging-shared-caddy.yml \
    --file deploy/compose.staging-pipeline.yml \
    --profile staging-approved --profile test-bot-setup-approved \
    run --rm --no-deps staging-bot-setup --tenant demo-meranti --test-bot --replace-webhook
)
```

The script reads the token from the mounted secret, calls `getMe`, refuses to
move a bot that is already bound to another tenant, encrypts the token and a
fresh webhook secret into `channel_credentials`, creates the webhook endpoint,
and registers `https://hooks.fourstacks.my/hooks/<public id>` with Telegram.
`--replace-webhook` is required because the bot currently points at the
development hostname. Nothing is printed except the registered URL.

## 6. Acceptance

Send one consented test message to the bot. Expect, in order: an
`ingress_events` row for the Meranti tenant, a worker-produced reply held in the
console inbox (the tenant is in `auto` mode after the 2026-10-23 activation;
replies that the gate holds appear here for approval), approval in the inbox, a delivery row, and the message arriving in
Telegram. Record the trace id and the counts in PROGRESS. Also upload a PDF to
the knowledge base and confirm the job completes and a question about it is
answered in the sandbox.

## Rollback

Stop only `worker`, `delivery` and `staging-embeddings`; the gateway can stay
with its new settings. Remove the hooks route from the tunnel and the Caddy
block with the same care as the console block. To release the bot, call
`deleteWebhook` from the development setup, not from here. Volumes are kept.
