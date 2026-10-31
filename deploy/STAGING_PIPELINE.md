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

## Routine rollouts

After a merge, `publish-staging-images` starts by itself once the push-to-main `ci` run is green
and waits for the owner's approval on the `staging-images` environment. Once the owner has
approved it and it has finished, the images of that commit are rolled out with
`scripts/staging_rollout.py`. The first-time setup in the sections below stays manual.

1. In the main checkout `/home/amsyrzmi/CSAV2` (never a worktree), bring it to the commit to roll
   out: `git pull --ff-only origin main`.
2. Show what a rollout would do. This changes nothing:

   ```bash
   .venv/bin/python -m scripts.staging_rollout plan
   ```

   It prints the target SHA, the two image digests, the digests of the current containers (the
   rollback references), the pending migrations, the steps, the check files and the exact `apply`
   command.
   Exit 0 is ok, 2 means secret files are missing (`apply` will refuse), 1 is a stop printed as
   `Stopped at <step>: ...`. If the images are not published yet it stops with
   `images for <sha7> are not published yet (<state>)`, for example "waiting for approval": the
   owner approves the `staging-images` environment on the publish run.
3. Run `apply` with exactly the command line `plan` printed last:

   ```bash
   .venv/bin/python -m scripts.staging_rollout apply --sha <full 40-character sha>
   .venv/bin/python -m scripts.staging_rollout apply --sha <full 40-character sha> --migrations 0486,0487
   ```

   `--sha` is required and must be the SHA `plan` showed: if HEAD moved since (a `git pull`),
   `apply` stops with `HEAD changed since plan (<old> → <new>); run plan again` before changing
   anything. With migrations pending, `--migrations` must list exactly the pending versions that
   `plan` printed; a mismatch means staging or the checkout changed, so run `plan` again. With
   nothing pending, leave the flag out.

`apply` re-derives the plan and, before changing anything, prints its target (`Target: <sha>`,
both image refs and `Pending migrations: ...`). It prints one line per step as the step starts.
It pulls both images by digest and verifies them, then recreates
`gateway`, `worker`, `delivery` and `frontend` in that order on the new images, checks `/readyz`
and runs every check file in `deploy/rollout_checks/`. A service that reports unhealthy at first
counts as started once it is healthy on the new image within 300 seconds (the gateway can take
about 45 seconds on a memory-starved host).

**A migration rollout stops all four services.** Owner decision of 2026-10-30, for every
migration: `gateway`, `frontend`, `worker` and `delivery` are stopped before `migrate` runs, so no
old code runs against the new schema. The console is unavailable and bot replies are delayed
(queued, not lost) until the services are back, usually a few minutes.

**Recovery.** On any failure `apply` stops and prints `Stopped at <step>: ...`, the state staging
is in (which of the four services are stopped, recreated or still on the previous images), the
recovery lines for how far it got, and last `Re-run plan, then apply, once the cause is fixed.`
It never retries a migrate and never rolls one back (a migration cannot be un-applied). The
recovery lines are:

- **Nothing changed** (the confirmation, secret preflight, `compose config` or a pull failed):
  `Nothing on staging was changed.` Fix the cause and run `plan` and `apply` again.
- **Services stopped, no planned migration recorded** (the stop or `migrate` failed and
  `schema_migrations` has none of the planned versions): `To bring the previous version back:`
  followed by a copy-ready `docker --host unix:///var/run/docker.sock start
  csa-staging-gateway-1 csa-staging-frontend-1 csa-staging-worker-1 csa-staging-delivery-1`,
  which restarts the old containers and needs no environment. If `schema_migrations` could not be
  read, `apply` says the migration state is unknown: check `schema_migrations` before restarting.
- **A planned migration was recorded**: `Migrations were applied: <versions>.` The previous images
  fail `/readyz` against the new schema, so do not roll back: fix forward, then run `plan` and
  `apply` again. The plan is derived afresh, so a recorded migration is no longer pending. No
  rollback images are printed in this case.
- **Image-only rollout failed during or after the recreate** (including
  `services are up on the new images but /readyz is failing`): `Rollback images:` lists each
  current container's image with its revision (`none known (not running)` when there is none).
  When they share one revision it prints `To roll back: git checkout <previous sha> in
  /home/amsyrzmi/CSAV2, run plan and apply, then git checkout main.`; otherwise pick one of the
  listed revisions and do the same.
- **A failed check** stops the four services again and they stay stopped until investigated:
  investigate what the check found, then run `plan` and `apply` again.

**After a rollout: retest the assistant.** Once `apply` has finished, work through
`docs/ASSISTANT_RETEST.md`, the retest checklist with English and Malay rows for every assistant
feature and failure path. Optionally, first run the model smoke run, which covers the checklist
rows with a smoke id. It runs inside the gateway container, which already has the model key, and
uses synthetic facts only (no database, Gmail or tenant data); a run is about 40 to 50 model calls:

```bash
docker exec csa-staging-gateway-1 python -m scripts.assistant_smoke
```

(Use `docker --host unix:///var/run/docker.sock exec ...` when the shell's docker context is not
the host daemon.) Add `--case <id>` for one case or `--repeat N` for a pass rate per case. It
prints one `PASS|FAIL <id> action=<action>` line per case, with the failing field names, and ends
with `N passed, M failed`; it never prints a prompt, model text or address. Exit 0 is all passed,
1 is a failure and 2 is a setup error such as no model key. It is owner-triggered and never runs in
CI.

**Switches.** The non-secret staging switches live in `deploy/staging-rollout.env`. To change one,
open a pull request; do not export it by hand. The script never opens the secret files; compose
reads them.

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

**First rollout that includes B4 (invitation mail):** before applying the migration or
recreating the gateway, run `sudo python3 -m scripts.staging_secrets smtp-install --secret-dir /srv/csa-staging/secrets`
once (section 10); compose will not recreate the gateway while the SMTP secret files are missing.

**Once the email assistant is wired (section 11):** the two Google OAuth files must also
exist before the gateway is recreated; compose will not recreate it while either is missing.

Run from an approved checkout of `main` that contains this file. The gateway is
recreated because it gains the embedding settings, the hooks network and the
loopback port; the console is unavailable for a few seconds while that happens.

```bash
(
  cd /home/amsyrzmi/CSAV2 || exit 1
  export NO_LOCAL_ENV=1 ENV=ci COMPOSE_PROFILES=
  export CSA_STAGING_PROJECT=csa-staging
  export CSA_STAGING_SECRET_DIR=/srv/csa-staging/secrets
  export CSA_STAGING_PYTHON_IMAGE=ghcr.io/amsyrzmi/csa-python@sha256:f42239697940243492cfbc8fabcd691e7479328ff013c4675b52634ffbbfc764
  export CSA_STAGING_FRONTEND_IMAGE=ghcr.io/amsyrzmi/csa-frontend@sha256:af62b0cab9074c9d177c6b74413d44c1fc0af3fbeeb9162d22eefd9c8c803e4f
  export CSA_STAGING_CONSOLE_ORIGIN=https://staging-console.fourstacks.my
  export CSA_STAGING_HOOKS_ORIGIN=https://hooks.fourstacks.my
  export CSA_STAGING_INVITE_MAIL=true  # keep on once switched on (section 10)
  export CSA_STAGING_EMAIL_ASSISTANT=true  # keep on once switched on (section 11)

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
container, forwards only `POST /hooks/*` (Telegram) and `GET`/`POST
/channels/whatsapp` (section 9) to `127.0.0.1:18031`, and returns 404 for every
other path. It uses an ordered `route` so the deny is evaluated first.

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
  cd /home/amsyrzmi/CSAV2 || exit 1
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

## 7. Enable the workspace assistant (opt-in; owner decision)

The assistant planner runs inside the gateway and calls the model provider, so enabling
it gives the public-facing gateway the real model key (the same `pipeline/staging_model_key`
file the worker uses) and makes dock turns cost money. It is off by default. To enable it,
export two more variables in the same shell as section 2 and recreate only the gateway:

```bash
export CSA_STAGING_WORKSPACE_ASSISTANT=true
export CSA_STAGING_GATEWAY_MODEL_SECRET=staging_model_key
csa_pipeline_compose config --quiet || exit 1
csa_pipeline_compose up --detach --wait --wait-timeout 300 --no-deps --pull never gateway
```

`/auth/me` then reports `workspace_assistant: true` and the dock button appears on every
tab for tenant staff. To disable, unset both variables and recreate the gateway the same way.

## 8. Add a platform administrator (owner runs)

Invites create tenant members only. A further platform administrator is added by
an existing one, who re-enters their own password. Run it in the shell from
section 2, where `csa_pipeline_compose` and its exports are active:

Requires a Python image built from a `main` commit that includes the W0 foundations (PR after 2026-10-27); export that image digest as `CSA_STAGING_PYTHON_IMAGE` in the section-2 shell first. On the older pinned image the gate passes and the one-shot stops with "No module named scripts.add_platform_admin".

```sh
CSA_STAGING_ADMIN_ADD_APPROVAL=approved-isolated-staging \
csa_pipeline_compose --profile admin-add-approved run --rm --no-deps admin-add \
  --approver-email existing-admin@example.test \
  --email new-admin@example.test --display-name "New Admin" --add-platform-admin
```

Prompts: approver password, then the new initial password twice. Nothing is
printed except the new user id. The new account must change its password at
first login. On another database host (for example RDS)
`export` `CSA_IDENTITY_EXPECTED_HOST` and `CSA_IDENTITY_EXPECTED_DB` with the
expected values in that shell before running (the compose file ignores any env
file); the role must still be `csa_identity`.

## 9. Connect WhatsApp (owner and client)

Fourstacks owns one Meta app for the deployment; each client keeps its own Meta
Business and WhatsApp Business Account (WABA) and connects its number to that app.
Meta sends every tenant's webhooks to one callback, signed with the app secret.
The gateway and delivery read two secret files. While both hold the literal
`disabled`, WhatsApp is inert: `/channels/whatsapp` answers 404 and delivery
refuses WhatsApp rows with `channel_not_configured`.

**Before rolling out an image that contains migration `0470`**, create the two
placeholder files, because compose refuses to start a service whose secret file
is missing. Run from the main checkout as UID 1000; existing files are never
overwritten:

```bash
python3 -m scripts.staging_secrets whatsapp-defaults --secret-dir /srv/csa-staging/secrets
```

Expected: `created pipeline/staging_whatsapp_app_secret (0400)` and the same for
`staging_whatsapp_verify_token`, then no check failures. On the first rollout that
includes B4, also run `sudo python3 -m scripts.staging_secrets smtp-install --secret-dir /srv/csa-staging/secrets`
once (section 10) before recreating the gateway, which will not be recreated while the
SMTP secret files are missing. Likewise, write the two Google OAuth files (section 11)
first, or compose will not recreate the gateway. Then apply `0470` with
the `migrate` one-shot and recreate gateway, worker, delivery and frontend with
`--no-deps --pull never`, as for any release.

### 9.1 Meta app (owner, once per deployment)

1. In Meta for Developers, create a Business-type app owned by the Fourstacks
   Meta Business and add the WhatsApp product.
2. Choose a verify token: any long random string (for example
   `openssl rand -hex 32`). Keep it for step 9.2; it is never shown in the console.
3. Under WhatsApp → Configuration, set the callback URL to
   `https://hooks.fourstacks.my/channels/whatsapp` and the verify token from step 2.
   Do not press Verify yet; the gateway still holds `disabled`.
4. Copy the app secret from App settings → Basic.

### 9.2 Write the two secret files (owner; nothing is printed)

The files are mode 0400, so replace them rather than edit them. Paste each value
at its prompt:

```bash
(
  umask 077
  d=/srv/csa-staging/secrets/pipeline
  for file in staging_whatsapp_app_secret staging_whatsapp_verify_token; do
    read -r -s -p "$file: " value; echo
    [ -n "$value" ] || { echo "empty, unchanged: $file"; continue; }
    rm -f "$d/$file"
    printf '%s' "$value" > "$d/$file"
    chmod 400 "$d/$file"
    echo "$file: $(stat -c %s "$d/$file") bytes, mode $(stat -c %a "$d/$file")"
  done
  unset value
)
```

Then, in the section-2 shell, recreate only the two services that read them:

```bash
csa_pipeline_compose config --quiet || exit 1
csa_pipeline_compose up --detach --wait --wait-timeout 300 --no-deps --pull never gateway delivery
```

Back in the Meta app, press Verify and save, then subscribe the app to the
`messages` webhook field. Meta's check calls `GET /channels/whatsapp`; a 403
means the verify token does not match the file.

Both files must hold real values, or both `disabled`. One of each stops the
gateway at start-up with "WHATSAPP_APP_SECRET and WHATSAPP_VERIFY_TOKEN must be
set together".

### 9.3 Client's Meta setup (client, with the owner's help)

1. Create a Meta Business account and complete business verification.
2. Create a WABA and register the phone number on the Cloud API. A number used
   in the WhatsApp Business app must be removed from that app first.
3. Connect the WABA to the Fourstacks app.
4. In Business settings, create a System User, assign it the WABA, and generate a
   permanent token with `whatsapp_business_messaging` and
   `whatsapp_business_management`.
5. Note the phone number id and the WABA id from WhatsApp Manager.

### 9.4 Connect the number in the console (client's company administrator)

Services → WhatsApp number → enter the phone number id, WABA id and the token,
then press Connect. The gateway first reads the number from Meta's Graph API with
that token: a token Meta refuses (or a wrong phone number id) is rejected, an
unreachable Graph API asks the admin to try again, and the number customers see is
taken from Meta. The token is encrypted at rest and never shown again. A platform admin opening the same card sees the
callback address; it must read `https://hooks.fourstacks.my/channels/whatsapp`
(the gateway takes it from `CSA_STAGING_HOOKS_ORIGIN`). A number already
connected to another company is refused.

### 9.5 Acceptance

Send "hello" from a phone to the connected number. Expect an `ingress_events`
row for the tenant, the conversation in the Inbox with a WhatsApp badge, and the
reply on the phone once it is approved or sent. Record the trace id in PROGRESS.

Replies are free-form text only, and only within 24 hours of the customer's last
message; later replies fail with `outside_service_window` and the Inbox says so.
Template messages and ordering on WhatsApp are phase 2.

**Pricing note for the client.** Meta charges per message by category. Replies
inside the 24-hour service window have a free monthly allowance per number and
are billed above it from October 2026. Quote from Meta's official rate card,
not from a reseller's page.

To switch WhatsApp off again, write `disabled` into both files the same way and
recreate gateway and delivery.

## 10. Invitation mail (owner runs; nothing is printed)

Invitations, renewals and admin-issued resets are emailed from
`FourStacks <no-reply@fourstacks.my>` through the Google Workspace relay
`smtp-relay.gmail.com:587` (STARTTLS, SMTP auth, allowed from the staging host's IP).
Only the gateway sends. Mail is off unless **both** are true: `CSA_STAGING_INVITE_MAIL=true`
and the two relay secrets are present. Until then the console shows the link to copy,
as before. `CSA_STAGING_INVITE_MAIL` and `CSA_STAGING_EMAIL_ASSISTANT` (section 11) are
independent and both must be exported (`=true`) in the shell whenever the gateway is
recreated, or the feature whose variable is missing is switched off.

**Before rolling out an image that contains this wiring**, install the two secret files.
The owner leaves `smtp_username` and `smtp_password` (root-only) at the top of
`/srv/csa-staging/secrets/`. Compose refuses to recreate the gateway while either
secret file is missing, so do this first. `smtp-install` moves them into `runtime/` as
`1000:1000`, mode 0400, never prints them, refuses a symlink, and does nothing
if they are already installed. It needs root only because the files are root-owned. Run
from the main checkout, which has the action:

```bash
sudo python3 -m scripts.staging_secrets smtp-install --secret-dir /srv/csa-staging/secrets
```

Expected: `installed runtime/smtp_username (0400)` and the same for `smtp_password`, then
`OK 10 staging secret files; values not displayed` (a second run prints `already
installed; nothing to move`). `check` passes with or without the two files, but flags
one without the other.

Then, in the section-2 shell, recreate the gateway with the flag on:

```bash
export CSA_STAGING_INVITE_MAIL=true
csa_pipeline_compose config --quiet || exit 1
csa_pipeline_compose up --detach --wait --wait-timeout 300 --no-deps --pull never gateway
```

Verify, with nothing secret on screen:

1. Console → Your team → Invite a member, using an address you can read. The panel says
   "Invitation emailed to <address>" and shows no link; the mail arrives from
   `no-reply@fourstacks.my` with a link that opens `/invite`.
2. If the panel instead says "We couldn't send the email", the relay refused. Find the
   category (never the address or link) with
   `csa_pipeline_compose logs --no-log-prefix gateway 2>&1 | grep -o 'smtp_[a-z_]*' | sort | uniq -c`:
   `smtp_auth_failed` means a wrong username or app password, `smtp_refused` a sender or
   IP the relay does not allow, `smtp_timeout` that the relay is unreachable from the host.
   The invitation still exists; copy its link from the panel meanwhile.
3. To switch mail off again, unset `CSA_STAGING_INVITE_MAIL` (or set it to `false`) and
   rerun the `up` line above. The console then says "Copy this link and send it to
   <address>."

## 11. Staff email assistant (Google)

The staff email assistant (`csa/email/`, `docs/EMAIL_ASSISTANT.md`) lets each staff member
connect their own Gmail mailbox. It uses an **Internal** Google OAuth client in the
fourstacks.my Google Cloud project with the redirect
`https://staging-console.fourstacks.my/console/v1/email/oauth/callback`. Internal means
only fourstacks.my Google accounts can connect, with no test users and no seven-day
token expiry. Only the gateway uses it, through `provider-egress`. The assistant is off
unless `CSA_STAGING_EMAIL_ASSISTANT=true`. That variable and `CSA_STAGING_INVITE_MAIL`
(section 10) are independent and both must be exported (`=true`) in the shell whenever the
gateway is recreated, or the feature whose variable is missing is switched off.

**Before the gateway is next recreated from a checkout that contains this compose file**,
the owner writes the two secret files. Compose refuses to recreate the gateway while either file is missing, so do
this first. The files are root-written, owned `1000:1000`, mode 0400, and nothing is echoed
(the secret prompt uses `read -s`). There is no install action; `check` accepts both files
absent or both present and flags one without the other:

```bash
D=/srv/csa-staging/secrets/runtime
read -r -p "Google Client ID: " V; printf '%s' "$V" | sudo tee "$D/google_client_id" >/dev/null; unset V
read -r -s -p "Google Client secret: " V; echo; printf '%s' "$V" | sudo tee "$D/google_client_secret" >/dev/null; unset V
sudo chown 1000:1000 "$D/google_client_id" "$D/google_client_secret"
sudo chmod 0400 "$D/google_client_id" "$D/google_client_secret"
```

Then, in the section-2 shell, recreate the gateway with the flag on:

```bash
export CSA_STAGING_EMAIL_ASSISTANT=true
csa_pipeline_compose config --quiet || exit 1
csa_pipeline_compose up --detach --wait --wait-timeout 300 --no-deps --pull never gateway
```

Per tenant, to use it:

1. In the console, Services (platform administrator): activate the `staff_email` package
   for the client.
2. A tenant administrator turns on the Email assistant switch.
3. Each staff member connects their Gmail on the Email assistant tab.

Verify, with nothing secret on screen:

1. Connect a fourstacks.my mailbox on the Email assistant tab: Google's consent screen
   appears and, after approval, the tab shows the mailbox as connected.
2. Run a sync: threads from the last 14 days are listed.
3. If the gateway fails to start with `EMAIL_ASSISTANT_ENABLED requires GOOGLE_CLIENT_ID,
   GOOGLE_CLIENT_SECRET and GOOGLE_OAUTH_REDIRECT_URI`, a file is empty. If
   Google reports `redirect_uri_mismatch`, the client's redirect does not match the URI
   above exactly.
4. To switch it off, unset `CSA_STAGING_EMAIL_ASSISTANT` (or set it to `false`) and rerun
   the `up` line above. Stored mailbox tokens stay encrypted in the database; staff can
   disconnect first to revoke and erase them.

### 11.1 Rolling out email management (0484 and 0485)

`0485` scrubs the email content that the earlier `email_export` card wrote into
`console_proposals` and `console_workspace_turns` (recipients, subjects and bodies). It runs
once, when it is applied. The gateway still running the old code would keep writing old-shape
rows between the migration and the recreate, and those rows would never be scrubbed. So stop
the writers first. Only the gateway writes proposals and turns; `frontend` is stopped with it
so nobody works against a stopped API. `worker` and `delivery` write neither and keep
running. **The console is unavailable from step 1 until step 3 finishes** (a few minutes).

In the section-2 shell:

1. Stop the old writers:

   ```bash
   csa_pipeline_compose stop gateway frontend
   ```

2. Apply `0484` and `0485` with the `migrate` one-shot, as for any release
   (`deploy/RELEASE.md`). The one-shot applies both in order; each in its own transaction.
3. Recreate gateway, worker, delivery and frontend with `--no-deps --pull never`, with
   both `CSA_STAGING_INVITE_MAIL=true` and `CSA_STAGING_EMAIL_ASSISTANT=true` exported.
4. Check that no old-shape row is left. The rollout script runs this check itself on every
   apply; the query is `deploy/rollout_checks/0485.sql`. It uses the scrub's own predicates and
   must print `0`. To run it by hand in the section-2 shell:

   ```bash
   csa_pipeline_compose exec -T staging-db psql -U csa -d csa_staging -tA -v ON_ERROR_STOP=1 -f - < deploy/rollout_checks/0485.sql
   ```

   Anything other than `0` means a row was written in a shape the scrub does not cover:
   stop and investigate before the console is used again.

## Rollback

Stop only `worker`, `delivery` and `staging-embeddings`; the gateway can stay
with its new settings. Remove the hooks route from the tunnel and the Caddy
block with the same care as the console block. To release the bot, call
`deleteWebhook` from the development setup, not from here. Volumes are kept.
