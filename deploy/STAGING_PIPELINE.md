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

**Forward-only data shape (assistant language).** From the rollout that brought the assistant's
Malay support, new assistant turns store `result.language` and new proposal cards store
`card.language`. The previous images read both with `extra="forbid"`, so rolling back to them
after such a turn exists breaks listing the turns, the planner's history and reading proposals.
Treat a rollback past that rollout as fix-forward. If a rollback is ever needed, the owner runs
these two statements in `csa_staging` first (they remove only the language fields):

```sql
UPDATE public.console_workspace_turns SET result = (result - 'language') #- '{proposal,card,language}';
UPDATE public.console_proposals SET card = card - 'language';
```

**After a rollout: retest the assistant.** Once `apply` has finished, work through
`docs/ASSISTANT_RETEST.md`, the retest checklist with English and Malay rows for every assistant
feature and failure path. Optionally, first run the model smoke run, which covers the checklist
rows with a smoke id. It runs inside the gateway container, which already has the model key, and
uses synthetic facts only (no database, Gmail or tenant data). A run is about 46 model calls (one
planner call per case plus one drafting call per draft case), up to about 81 with re-plans:

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

**Rollout that includes booking reminders (the worker sends mail):** the worker now mounts the
same two SMTP secret files and the same `CSA_STAGING_INVITE_MAIL` switch as the gateway. This is
a compose change, so the rollout recreates the worker with the new file, and compose will not
recreate it while the SMTP secret files are missing (section 10). The `smtp-install` step is
already done on staging, so nothing new is written. The scripted rollout
(`scripts/staging_rollout.py`) reads `deploy/staging-rollout.env`, which already sets
`CSA_STAGING_INVITE_MAIL=true`, so the recreated worker sends mail at once; only a hand-run compose
needs `export CSA_STAGING_INVITE_MAIL=true` in the shell, or the worker's mail is off even though
the gateway's is on.

Expect, within the first five minutes of the first such rollout: a reminder for every confirmed
booking that starts within its company's reminder time (by default the next 24 hours), mailed to
each person on it with an address, each guest and each customer with an address, and a Telegram
message to each customer with a Telegram chat at a company with Customer support active and
replies on, bookings made while PR 1 was tested included. Only a booking made or moved after its
reminder was due is skipped (its confirmation was enough). The mail job then sends up to 50 every
five minutes. Check which addresses and Telegram contacts the staging companies hold first.

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
container, forwards only `POST /hooks/*` (Telegram), `GET`/`POST
/channels/whatsapp` (section 9) and `POST /api/v1/channels/tiktok_shop/webhook`
(section 12) to `127.0.0.1:18031`, and returns 404 for every other path. The live block predates the
WhatsApp and TikTok matchers; section 12.6 brings it level in one edit. It uses an ordered `route` so the deny is evaluated first.

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
The gateway sends invitations and resets, and the worker sends booking reminders (by default a
day before a booking, to the customer, the people on it and its guests) and a notice that a
booking the customer's bot held ran out of time. Both mount the same two relay secrets and read
the same `CSA_STAGING_INVITE_MAIL` variable, and the worker reaches the relay through
`provider-egress`, the bridge it already used for the model provider, so it gets no new network.
Mail is off unless **both** are true: `CSA_STAGING_INVITE_MAIL=true`
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

Then, in the section-2 shell, recreate the gateway with the flag on (and the worker too, once
the release has booking reminders):

```bash
export CSA_STAGING_INVITE_MAIL=true
csa_pipeline_compose config --quiet || exit 1
csa_pipeline_compose up --detach --wait --wait-timeout 300 --no-deps --pull never gateway
```

With the flag off, a reminder is queued and waits: the worker's mail job leaves the rows
`queued` and sends them once mail is on, as long as the booking has not started; a notice that a
held booking ran out of time is dropped instead once it is a day old. To check the
worker's side, `csa_pipeline_compose logs --no-log-prefix worker 2>&1 | grep -o 'smtp_[a-z_]*' |
sort | uniq -c` lists the same categories as the gateway's check below, never an address.

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
connect their own Gmail mailbox. It uses an **External** Google OAuth client, in testing, in the
fourstacks.my Google Cloud project with the redirect
`https://staging-console.fourstacks.my/console/v1/email/oauth/callback`. External and in
testing means only the test users listed in Google Cloud can connect, at most 100 of them,
and each one's access expires every 7 days, so they have to connect again. Any Google
account can be a test user, so the mailbox does not have to be a fourstacks.my one. The test
users live in Google Cloud, so a staging reset does not touch them. Moving the client past
testing needs Google's verification and a yearly security assessment, because the Gmail
scopes are restricted. Only the gateway uses it, through `provider-egress`. The assistant is off
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

1. Connect a test user's mailbox on the Email assistant tab: Google's consent screen
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

## 12. Connect TikTok Shop (owner runs; nothing secret is printed)

TikTok Shop connect lets a company's admin connect a seller shop from Business → Store
connections, then browse its orders and products and ask the assistants about them. It is read
only: nothing is ever written to TikTok. One TikTok app (the owner's Partner Center app) serves
every company. The App Key, App Secret and Service ID stay in three secret files; the assistant
that maintains this repository never reads them, and none of them belongs in chat, git or a
pull request.

Two origins are involved, and mixing them up is the usual mistake:

- **Connect callback (where TikTok sends the seller back): the console origin**,
  `https://staging-console.fourstacks.my/console/v1/business/stores/tiktok/callback`. It runs behind
  the admin's signed-in session. It needs no Caddy change.
- **Webhook (TikTok calls us): the hooks host**,
  `https://hooks.fourstacks.my/api/v1/channels/tiktok_shop/webhook`. This is the only TikTok path
  on the hooks host and it needs the live hooks Caddy edit (step 6).

TikTok is off while any of the three files holds the literal `disabled`: connect answers 503
`tiktok_unavailable`, the webhook answers 404, sync jobs skip, and the Store connections page shows
Connect disabled. Run the steps in this order, in the shell of section 2 (where
`csa_pipeline_compose` and its exports are active), from a checkout of the merge commit (an older
checkout lacks the `tiktok-defaults` action). `scripts/staging_rollout.py` (Routine rollouts) can do the stop, `migrate` and recreate parts of
steps 2 and 3 for you, but it does **not** run the count queries of 12.2: run those first, before
`staging_rollout plan` and `apply`, then use either route. Step 1 must come first either way, because the rollout's preflight and compose both refuse a service whose
secret file is missing.

### 12.1 Create the three placeholder files

```bash
cd /home/amsyrzmi/CSAV2 && python3 -m scripts.staging_secrets tiktok-defaults --secret-dir /srv/csa-staging/secrets
```

Expected: `created pipeline/staging_tiktok_app_key (0400)`, the same for `staging_tiktok_app_secret`
and `staging_tiktok_service_id`, then `OK 10 staging secret files; values not displayed`. Existing
files are never overwritten, so a second run prints only the `OK` line.

### 12.2 Check the data, then apply migrations 0490 to 0493

Two things in these migrations cannot be undone or can abort, so count first. Run each query in
the section-2 shell; they only read.

**`0490` deletes legacy TikTok stores** (a `channel = 'tiktok_shop'` credential with no
authorization grant) together with their webhook endpoints, ingress events and outbound deliveries.
None are expected on staging. If the first number is not `0`, look at what they are before going on:

```bash
csa_pipeline_compose exec -T staging-db psql -U csa -d csa_staging -tA -v ON_ERROR_STOP=1 -c "
WITH legacy AS (
  SELECT c.id FROM channel_credentials c
  WHERE c.channel = 'tiktok_shop'
    AND NOT EXISTS (SELECT 1 FROM ecommerce_grants g WHERE g.record_id = c.id)),
ep AS (
  SELECT e.id FROM webhook_endpoints e JOIN legacy l ON l.id = e.credential_id)
SELECT (SELECT count(*) FROM legacy) AS stores,
       (SELECT count(*) FROM ep) AS endpoints,
       (SELECT count(*) FROM ingress_events WHERE endpoint_id IN (SELECT id FROM ep)) AS events,
       (SELECT count(*) FROM outbound_deliveries WHERE endpoint_id IN (SELECT id FROM ep)) AS deliveries"
```

`0490` also marks every TikTok ingress event still `received` as `dead` (`last_error =
'legacy_tiktok_store_removed'`), so it never reaches the turn worker. Count them; a non-zero number
is expected only if the old webhook ever received traffic:

```bash
csa_pipeline_compose exec -T staging-db psql -U csa -d csa_staging -tA -v ON_ERROR_STOP=1 -c "
SELECT count(*) FROM ingress_events WHERE raw ->> 'channel' = 'tiktok_shop' AND status = 'received'"
```

**`0491` builds a unique index on the shop id**, so two stored TikTok credentials for one shop
that both have a grant would abort it. Expect `0`:

```bash
csa_pipeline_compose exec -T staging-db psql -U csa -d csa_staging -tA -v ON_ERROR_STOP=1 -c "
SELECT count(*) FROM (
  SELECT c.shop_id FROM channel_credentials c
  WHERE c.channel = 'tiktok_shop' AND c.shop_id IS NOT NULL
    AND EXISTS (SELECT 1 FROM ecommerce_grants g WHERE g.record_id = c.id)
  GROUP BY c.shop_id HAVING count(*) > 1) duplicated"
```

If it is not `0`, stop and decide which duplicate to remove before migrating.

**Use the new image for `migrate`.** The `migrate` one-shot applies the migrations that are inside
the image exported as `CSA_STAGING_PYTHON_IMAGE`. The section-2 shell exports a fixed older digest;
with that one `migrate` applies nothing, still prints `migrate exit=0`, and the new gateway then
fails `/readyz` with "missing migrations: 0490…". So export the two image digests of the merged
commit first (the publish run prints them, or `scripts/staging_rollout.py plan` does):

```bash
export CSA_STAGING_PYTHON_IMAGE=ghcr.io/amsyrzmi/csa-python@sha256:<digest of the merged commit>
export CSA_STAGING_FRONTEND_IMAGE=ghcr.io/amsyrzmi/csa-frontend@sha256:<digest of the merged commit>
```

Then stop the four services (a migration never runs beside old code) and run the one-shot, as for
any release (`deploy/RELEASE.md`):

```bash
csa_pipeline_compose stop gateway frontend worker delivery
CSA_STAGING_MIGRATION_APPROVAL=approved-isolated-staging \
  csa_pipeline_compose --profile migration-approved run --rm --no-deps migrate
echo "migrate exit=$?"
```

`migrate exit=0` alone proves nothing (see above), so check that the four versions are recorded;
expect `4`:

```bash
csa_pipeline_compose exec -T staging-db psql -U csa -d csa_staging -tA -v ON_ERROR_STOP=1 -c "
SELECT count(*) FROM schema_migrations WHERE version IN ('0490','0491','0492','0493')"
```

If it prints `0`, the image did not contain them: fix the exported digest and run `migrate` again.
`0491` adds `disconnected_at`, the shop index and the marketplace sync state; `0492` adds the order
and product sync tables; `0493` stops the 24-hour approval expiry from cancelling synced
marketplace orders. These four sort below the highest applied version (`0530`), so older images
still pass `/readyz` after them: nothing warns you if you recreate on an old image, so do step 12.3
with the new digests.

### 12.3 Recreate the services with TikTok still off

With the new digests from 12.2 still exported in this shell:

```bash
csa_pipeline_compose config --quiet || exit 1
csa_pipeline_compose up --detach --wait --wait-timeout 300 --no-deps --pull never gateway worker delivery frontend
```

Check `/readyz`. TikTok is still off, so the Store connections page says "TikTok Shop isn't set up
on this server yet." and Connect is disabled.

### 12.4 Write the three real values (owner; nothing is printed)

Get the App Key, App Secret and Service ID from Partner Center → the app. The files are mode 0400,
so they are replaced, not edited. Paste each value at its prompt (nothing echoes):

```bash
(
  umask 077
  d=/srv/csa-staging/secrets/pipeline
  for file in staging_tiktok_app_key staging_tiktok_app_secret staging_tiktok_service_id; do
    read -r -s -p "$file: " value; echo
    [ -n "$value" ] || { echo "empty, unchanged: $file"; continue; }
    rm -f "$d/$file"
    printf '%s' "$value" > "$d/$file"
    chmod 400 "$d/$file"
    echo "$file: $(stat -c %s "$d/$file") bytes, mode $(stat -c %a "$d/$file")"
  done
  unset value
)
python3 -m scripts.staging_secrets check --secret-dir /srv/csa-staging/secrets
```

All three must hold real values for TikTok to turn on; while any one is still `disabled` it stays
off (there is no half-on state and no start-up error).

### 12.5 Recreate, register and enable the app

The gateway and worker read the files at start, so recreate them, then register and enable the
app row with the `tiktok-app` one-shot. It uses the owner connection, the same approval variable
and the same isolated-database check as `migrate`, and prints only the app id, state and revision:

```bash
csa_pipeline_compose up --detach --wait --wait-timeout 300 --no-deps --pull never gateway worker
CSA_STAGING_MIGRATION_APPROVAL=approved-isolated-staging \
  csa_pipeline_compose --profile tiktok-app-approved run --rm --no-deps tiktok-app register
CSA_STAGING_MIGRATION_APPROVAL=approved-isolated-staging \
  csa_pipeline_compose --profile tiktok-app-approved run --rm --no-deps tiktok-app enable
```

Expected: `tiktok_shop: disabled at revision 1`, then `tiktok_shop: active at revision 2`. If the
app was registered before, `register` answers `refused: app_registration_conflict` and exits 1: that
is harmless, go on to `enable`. The
Store connections page now offers Connect. `disable` is the same command with `disable`.

**Rotating the App Secret later:** `disable`, replace the secret file as in step 12.4, recreate
gateway and worker, `enable`. Each of those bumps the app revision, which stops every in-flight
read. Existing connections keep working without a reconnect: grants are immutable, so the worker's
refresh job notices that a grant is older than the app's revision and, on its next run, refreshes
the shop's token and records a new grant at the new revision. Until that run (it looks every
15 minutes and also refreshes any token that expires within 24 hours) a store can read "Needs
reconnecting". If it still does after a run, the admin presses Reconnect.

### 12.6 The live hooks Caddy edit (owner, sudo; one edit)

The live hooks site in `/etc/caddy/Caddyfile` forwards only Telegram. It still lacks the WhatsApp
matcher (section 9) as well as the TikTok one, so make **one** edit that brings it level with
`deploy/staging-hooks.Caddyfile`: that file forwards `POST /hooks/*`, `GET`/`POST
/channels/whatsapp` and `POST /api/v1/channels/tiktok_shop/webhook` to `127.0.0.1:18031`, answers
403 to any peer that is not loopback or the connector, and 404 to everything else. Follow
[the shared Caddy runbook section 3](STAGING_SHARED_CADDY.md#3-prepare-the-live-caddy-edit-separate-approval)
and section 3 above: obtain approval, back up, edit, diff, validate, reload. The backup name below
is new, so it refuses to collide with the earlier ones:

```bash
sudo test ! -e /etc/caddy/Caddyfile.before-csa-hooks-tiktok &&
sudo cp -a /etc/caddy/Caddyfile /etc/caddy/Caddyfile.before-csa-hooks-tiktok
```

If the backup already exists, stop and reconcile its origin; do not overwrite it. Only once the
backup was made:

```bash
sudoedit /etc/caddy/Caddyfile
```

In the editor, replace the existing `http://hooks.fourstacks.my { ... }` block with the whole of
`deploy/staging-hooks.Caddyfile`, touching nothing else. Then:

```bash
sudo diff -u /etc/caddy/Caddyfile.before-csa-hooks-tiktok /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

Read the diff: only the hooks site may change, by two added matchers, two added `reverse_proxy`
lines and the comment lines that describe them. Only after validation succeeds:

```bash
sudo systemctl reload caddy && systemctl is-active caddy
```

Local checks after the reload (TikTok is on, so an unsigned call is refused with 401; with TikTok
off the second would answer 404 instead):

```bash
curl --noproxy '*' --silent --output /dev/null --write-out 'GET tiktok path HTTP %{http_code}\n' \
  --header 'Host: hooks.fourstacks.my' http://127.0.0.1/api/v1/channels/tiktok_shop/webhook
curl --noproxy '*' --silent --output /dev/null --write-out 'POST tiktok webhook HTTP %{http_code}\n' \
  --header 'Host: hooks.fourstacks.my' --header 'Content-Type: application/json' \
  --request POST --data '{}' http://127.0.0.1/api/v1/channels/tiktok_shop/webhook
curl --noproxy '*' --silent --output /dev/null --write-out 'POST callback path HTTP %{http_code}\n' \
  --header 'Host: hooks.fourstacks.my' --request POST http://127.0.0.1/console/v1/business/stores/tiktok/callback
```

Expected `404` (a GET is not forwarded), `401` (reached the gateway, no signature) and `404` (the
callback is not on the hooks host). Repeat the section 3 checks for Telegram, and check that the
newly added WhatsApp matcher reaches the gateway (while WhatsApp holds `disabled` the gateway
answers 404; the point is that it is the gateway's answer, which the second command shows by
returning JSON rather than Caddy's plain "Not Found"):

```bash
curl --noproxy '*' --silent --write-out '\nGET whatsapp HTTP %{http_code}\n' \
  --header 'Host: hooks.fourstacks.my' http://127.0.0.1/channels/whatsapp
curl --noproxy '*' --silent --write-out '\nGET other HTTP %{http_code}\n' \
  --header 'Host: hooks.fourstacks.my' http://127.0.0.1/channels/other
```

The first curl (`/channels/whatsapp`) shows the gateway's JSON body (or a 403 once WhatsApp is live
and no handshake is sent); the second (`/channels/other`) shows Caddy's plain `Not Found`.

### 12.7 Partner Center settings (owner)

In Partner Center, on the app:

1. **Redirect URL:** `https://staging-console.fourstacks.my/console/v1/business/stores/tiktok/callback`.
   This is the **console** origin, not the hooks host.
2. **Webhook URL:** `https://hooks.fourstacks.my/api/v1/channels/tiktok_shop/webhook`.
3. **Events:** order status change, product status or audit change, and seller deauthorization.
   Read the exact event names and type numbers there and record them in the ledger table
   (12.11): the code accepts a small list that has not been checked against TikTok.
4. **Permissions:** orders, the shop's authorization info, and the product permission. Read the
   scope keys in Partner Center and record them in the ledger table (12.11).

### 12.8 Confirm the session survives the return from TikTok

The callback is a browser navigation from TikTok back to the console. It works only if the admin's
session cookie is sent with it. The console cookies are `SameSite=Lax`, which is sent on a
top-level GET like this (Gmail's callback relies on the same thing). Prove it on the first connect:
sign in, press Connect, approve on TikTok; you must land on Store connections still signed in, with
`?tiktok=connected`. If you land on the login page, the cookie did not come back: stop and do not
loosen the cookie without a decision.

### 12.9 Confirm who may authorize an unpublished app

While the app is unpublished, TikTok may let only the app owner's own shop, or allow-listed shops,
authorize it. Before connecting a client's shop, find out in Partner Center whether a shop that is
not the owner's can authorize it, or whether the app must be published or the shop added first.
Record the answer in the ledger table (12.11).

### 12.10 Enable, connect and test

1. As a platform admin: Services → the test company → enable the **Marketplaces** package. Marketplaces is sellable as of this project, so the staging roster accepts it and an Admin can also request it from Services.
2. As that company's Admin: Business → Store connections → Connect TikTok Shop, and approve with the
   owner's own shop. Expect `?tiktok=connected` and a store showing Connected.
3. Run the **done means** list: (a) a client admin connected a real shop; (b) within minutes staff
   browse its orders and products; (c) a new TikTok order appears in our copy with no manual step
   (place or change one, wait for the webhook, or for the catch-up job); (d) the customer assistant
   answers one order-number question and one stock question on Telegram or WhatsApp; (e) the
   workspace assistant answers a staff stock question.
4. Customer assistant smoke, by hand on the test Telegram bot (nothing in this list writes to
   TikTok): an order-status question with a TikTok order number, including one that is a Luhn-valid
   18-digit number (the personal-data filter masks about one in ten 18-digit numbers, and the
   assistant must still find the order); a stock question for an item that exists only on TikTok; a
   stock question for an item in the local catalogue; and "cancel my TikTok order", which must not
   write anything and must not cancel the order.
5. Workspace assistant: ask "how much stock do we have of <a TikTok product>?" in the dock, then
   work through `docs/ASSISTANT_RETEST.md`. Optionally run the model smoke run (section "Routine
   rollouts"); the five stock cases proposed in the Task 20 report are not in
   `csa/workspace/smoke_cases.json` yet.
6. Record the trace ids, counts and the answers to 12.8, 12.9 and the ledger table in
   `docs/PROGRESS.md`. Only then connect the client's shop.

### 12.11 Verify in Partner Center (ledger)

Everything the code knows about TikTok comes from documentation read from memory and made-up
fixtures; no provider call was ever made while building this. Work through the list, once, with the
owner's real app. Change the code in the one place named and record the answer in `docs/PROGRESS.md`.

| # | What to confirm in Partner Center | Where it is in the code |
|---|---|---|
| 1 | The authorize host for the Malaysia market (now `services.tiktokshop.com/open/authorize`). A wrong host makes Connect fail before it starts. | `AUTHORIZE_URL` in `csa/console/tiktok_connect.py`, and the allow-list `TIKTOK_AUTHORIZE_HOSTS` in `CSA_WebConsole/csa-console/web/lib/tiktok-connect.ts` (change both) |
| 2 | The product permission's scope key (placeholder `seller.product.basic`), and that the order and shop keys are the ones listed. **A real grant without the product key reads "Needs reconnecting".** | `PRODUCT_SCOPES` (and `_ORDER_SCOPES`, `_SHOP_SCOPES`) in `csa/agents/ecommerce/authorization.py` |
| 3 | The token endpoint and the refresh endpoint and its parameters (placeholders: `https://auth.tiktok-shops.com/api/v2/token/get` and `/token/refresh`), and whether TikTok issues a new refresh token on each refresh (if so, a lost response may strand a store until one Reconnect). | `TOKEN_ENDPOINT` in `csa/agents/ecommerce/token_exchange.py`; `REFRESH_ENDPOINT` and the `REFRESH_*` constants in `csa/agents/ecommerce/token_refresh.py` |
| 4 | Which provider error codes mean the refresh token is dead (revoked, expired, wrong app). **`REFRESH_REFUSAL_CODES` is empty until filled: until then a dead refresh token is retried on every run and the store is not marked "Needs reconnecting" until its access token lapses.** | `REFRESH_REFUSAL_CODES` in `csa/agents/ecommerce/refresh.py` |
| 5 | Webhook event type numbers and names for order status change, product status/audit change and seller deauthorization (placeholders `1`, `5`, `6`; deauthorization may be `12`, which could instead be a return-status event). A wrong number silently drops the event, and a wrong deauthorization number means a revoked shop is not disconnected. | `EVENT_KINDS` in `csa/marketplace/webhook.py` |
| 6 | The webhook signature recipe. Two are accepted until verified: A, HMAC-SHA256 of the body with the app secret; B, the same over the app key plus the body. Keep the real one and delete the other. Also whether TikTok sends a timestamp (there is no replay check today) and which header carries the signature. | `SIGNATURE_RECIPES`, `signature_body_only`, `signature_app_key_and_body` in `csa/marketplace/webhook.py`; the header lookup in `csa/channels/marketplace/router.py` |
| 7 | Order field names (create, update and paid times; payment sub-total, shipping fee, seller discount; recipient name, phone and address; packages and tracking) and the order status vocabulary and its mapping to our statuses. | the mapping comment in `csa/agents/ecommerce/tiktok_models.py`; the status table in `csa/marketplace/mapping.py` |
| 8 | Product field names, the product status vocabulary, and the allowlist of statuses that count as on sale (only `ACTIVATE` today; it fails closed, so a different word for "live" makes every product read as not for sale). | `SELLABLE_PRODUCT_STATUSES` in `csa/marketplace/reads.py`; the mapping comment in `csa/agents/ecommerce/tiktok_models.py` |
| 9 | The order search: whether results can be sorted by update time (the sync pages by window plus page token and does not request a sort), and whether the `update_time` window is inclusive at the start and exclusive at the end. | `_UPDATE_TIME_FROM_FIELD` / `_UPDATE_TIME_TO_FIELD` in `csa/agents/ecommerce/adapters/tiktok_shop.py` |
| 10 | The product search: the update-time window's inclusivity (assumed inclusive both ends), and whether it returns every SKU of each product or only some (if only some, stock for a variant can be missing). | `_PRODUCT_SEARCH_PATH`, `_PRODUCT_UPDATE_*_FIELD` in `csa/agents/ecommerce/adapters/tiktok_shop.py` |
| 11 | The provider code for "product not found". Until it is known a product refresh for a deleted product retries until it fails. | `_product_refresh` in `csa/marketplace/processor.py` |
| 12 | Rate limits, and the page size limits (the code asks for at most 50 per page). | `page_size` bounds in `csa/agents/ecommerce/adapters/tiktok_shop.py`; the sync pacing (`page_delay_seconds`, `retry_backoff_seconds`) in `csa/marketplace/processor.py` |
| 13 | Whether a shop that is not the owner's can authorize an unpublished app (12.9). | no code: procedure only |

Until a row is confirmed, treat what it covers as unverified: the safe failure in every case is
"nothing is shown or written", never a wrong write, because the code never writes to TikTok.

### 12.12 Probing TikTok's real response shape

When a backfill or refresh fails with `invalid_response`, TikTok answered but our models (rows 7
and 8 above) did not accept the shape. The sync never logs provider responses, so use the probe to
see the shape. It makes **one** read per run, through the same fenced path as the sync (fresh
authorized-shops check, the same signing, single attempt, no retry), and prints the **structure
only**:

```bash
docker exec csa-staging-gateway-1 python -m scripts.tiktok_probe orders
docker exec csa-staging-gateway-1 python -m scripts.tiktok_probe products
docker exec csa-staging-gateway-1 python -m scripts.tiktok_probe order <order_id>
docker exec csa-staging-gateway-1 python -m scripts.tiktok_probe product <product_id>
```

`orders` reads one page of 5 over the same 90-day update window as the backfill; `products` reads
one page of 5 of the catalogue; `order` and `product` read that one id. It picks the single
connected store among the active and pilot companies (listed the way the worker lists them). If
there is none, or more than one, it refuses (exit 2); with more than one it lists the choices: add
`--client-id <uuid>`, and `--store-id <uuid>` when the company has more than one store.

What the output means:

- The header gives the request path (a template: the id is never printed), the HTTP status,
  TikTok's `code` (`0` is success; the message is never shown), whether a next page token is
  present (never its value), and what our own parser made of the body (`ok` or its safe code, for
  example `invalid_response`).
- `STRUCTURE` lists every JSON path with its type: `object(N keys)`, `array of N` (with
  `mixed: ...` when the elements differ), `str(len=N)` (`, decimal` when the text reads as a
  number), `int`, `int(unix-seconds-like)`, `int(unix-millis-like)`, `float`, `bool`, `null`.
  `[]` is the first element of an array; arrays are judged on that element only. A key that does
  not look like a schema word (lower-case words joined by `_`, at most one trailing digit), and
  every key of an object with more than 100 keys, shows as `<key#N>`.
- Values appear only at these exact paths, and only when they fit a strict pattern (otherwise
  `<redacted>`): `str = "..."` for the order and product `status` (`AWAITING_SHIPMENT`-style
  words: capital letters and underscores only), and the order and SKU `currency` (three capital
  letters); `int = N` for a numeric status (0 to 999) and for
  `data.total_count`.
- `OUR MODEL SAYS` lists every provider field our models read, `required` or `optional`, as
  `present`, `MISSING`, `wrong type (got X, want Y)`, `not seen` (its array was empty, or its
  parent is not an object), or `absent (total_count 0, accepted)` (a search page with no list
  and `total_count` 0, which the adapter accepts as an empty page). A `required` line that is
  `MISSING` or `wrong type` is why the read failed: fix the name in the mapping block in
  `csa/agents/ecommerce/tiktok_models.py` and record it in the ledger (rows 7 and 8). If every
  required line is fine and the parser still says `invalid_response`, look at the lengths and
  number hints (a currency must be 3 letters, ids at most 100 characters, money must read as a
  number; an empty string is not one).

Exit codes: 0 printed (TikTok answered with code 0, whatever our parser thought); 2 refused
(TikTok off, no or several stores, bad arguments); 1 failed (a safe code or an error class name
only).

**Safety.** The shape report prints structure only, never TikTok's values: no names, phones,
addresses, emails, ids, tokens, ciphers, URLs or timestamps, and nothing about the signed request
beyond its path template. As a last guard any output line that still looks like an email, a run
of 9 or more digits or a URL is replaced with `<redacted>`. One risk is left: a key that is a plain
lower-case word cannot be told from a schema word, so a map keyed by such words would show its
keys. Only a refusal ever lists ids, and those are our own company and store ids, not TikTok's:
keep refusals on the host and do not paste them. It writes nothing to TikTok and never copies
orders or listings; the only rows it adds are the single-use read-operation rows
(`ecommerce_read_operations`) that every sync read adds. It is still the owner's call to paste
the shape report anywhere outside the host.

## 13. Bookings and Google Calendar (owner, in Google Cloud, then one reviewed PR)

Bookings can put each person's confirmed bookings on a calendar called "Bookings" in their own
Google account (`csa/bookings/google/`, design `docs/superpowers/specs/2026-11-06-bookings-google-calendar-design.md`).
It uses the same Google OAuth client as section 11 (`GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`,
files the gateway and the worker both read) with one more scope, `calendar.app.created`: the app
can make a calendar and manage the events on it, and nothing else in the account. Nothing is
requested from Google until a person presses Connect, and the feature is off until step 4.

1. **Enable the Calendar API** in the fourstacks.my Google Cloud project (APIs and services, Library,
   Google Calendar API, Enable).
2. **Add the scope** to the consent screen (Google Auth platform, Data access, Add or remove
   scopes): `https://www.googleapis.com/auth/calendar.app.created`. Note on that screen whether
   Google marks it sensitive. If it does, going beyond test users needs Google's verification;
   if not, it does not. Either way this is a separate submission from the Gmail scopes.
3. **Register the callback** on the OAuth client, under Authorised redirect URIs:
   `https://staging-console.fourstacks.my/console/v1/bookings/calendar/callback`.
   The email assistant's own address stays as it is.
4. **Turn it on** with a reviewed pull request that changes
   `CSA_STAGING_BOOKINGS_GOOGLE_CALENDAR=false` to `true` in `deploy/staging-rollout.env`, then a
   normal rollout. Meet links are a second switch, `CSA_STAGING_BOOKINGS_GOOGLE_MEET`, and need the
   first. The rollout tool exports both; nothing is exported by hand.

While the client is in testing, only the test users listed in Google Cloud can connect, and Google
ends their grant after 7 days, so their panel will say "Needs reconnecting" about weekly and they
connect again. That is the testing limit, not a fault. Connecting a person who is not a test user
returns to the Bookings page with "Google didn't give access". The worker needs the same two Google
secret files the gateway has, and is given them by the compose file; the worker and gateway
both call Google through `provider-egress`.

Retest: the Google Calendar rows in `docs/ASSISTANT_RETEST.md`, under Bookings.

## Resetting staging

`scripts/staging_reset.py` wipes the staging database and the three upload folders, rebuilds
them, and then sets up the real accounts and the three companies from a roster file. It
is for staging only. The project, database, host and volumes are fixed in the code and
cannot be changed by an argument.

It has three commands:

- `plan` only reads. It changes nothing. Anyone, including an agent, may run it.
- `wipe` backs up, wipes and rebuilds. Only the owner, at a terminal.
- `accounts` adds the other platform admins, then the companies, their packages, modes and
  profiles (status `pilot` and a brand name), and their invitations. Only the owner, at a
  terminal.

**What it wipes.** The database `csa_staging`: every account and session, all HR and
customer data, and every Telegram, WhatsApp and Gmail connection. It also empties the three
upload volumes `csa-staging_staging-uploads`, `csa-staging_staging-hr-uploads` and
`csa-staging_staging-training-uploads`, which hold the knowledge base files, the HR files and
the training library files.

**What stays.** The secret files under `/srv/csa-staging/secrets`, the role passwords,
`CREDENTIALS_KEY`, the embeddings cache and the Google test users (they live in Google
Cloud). The Postgres server and its roles stay too. Only one database is dropped.

**`wipe` takes staging down for a few minutes.** It stops the gateway, frontend, worker
and delivery. The console is unavailable and bot replies wait until it finishes.

### Before you start

1. The code is merged, published and rolled out by the routine rollout above.
   `staging_reset.py` runs from the checkout, but `staging_accounts.py` runs inside the
   gateway, so it must be in the running image. `plan` checks that all four services run
   the image for HEAD.
2. You are in the main checkout `/home/amsyrzmi/CSAV2`, on `origin/main`, with a clean
   tracked tree (`git pull --ff-only origin main`).
3. The roster file `deploy/staging-accounts.local.json` is in place, with mode 0600. If the
   running code has the `knowledge_base` add-on (migration 0530), a company's list must name it
   beside `customer_support`, or that company's bot hands every question to staff. The example
   file shows it. On older code the name is refused as an unknown package.
   It holds real addresses, so it is git-ignored and never goes in the repository, which
   is public. Copy the example to that path and set the mode:

   ```bash
   cd /home/amsyrzmi/CSAV2 && cp -n deploy/staging-accounts.example.json deploy/staging-accounts.local.json
   chmod 0600 /home/amsyrzmi/CSAV2/deploy/staging-accounts.local.json
   ```

   Then edit the local file and put in the real addresses. The example's `example.test`
   addresses pass the checks, and their invitations would go nowhere.

4. Invitation mail is on (`CSA_STAGING_INVITE_MAIL=true`, section 10). `accounts` refuses
   when it is off, because it would otherwise have to print an invitation link.
5. Every invited mailbox can receive mail now. An invitation lasts 48 hours.
6. There is room on the disk. `plan` shows the free space and the size of the backup.

### Run

`wipe` and `accounts` refuse unless both stdin and stdout are a real terminal. A pipe is
refused, so `| tee` does not work. This is on purpose: they ask for passwords, and the
output names people. They need the owner's passwords, so an agent can only run `plan`.

1. Read the plan. It prints what staging holds now, what will be lost, the size of the
   backup and what `wipe` would refuse. Before the first real wipe it also checks that
   the database container's maintenance database can be reached, that user 1000 can read
   all three upload volumes, and that user 1000 owns every folder in them, the top one included,
   because `wipe` could not empty them otherwise. If any of that fails, it refuses.

   ```bash
   cd /home/amsyrzmi/CSAV2 && .venv/bin/python -m scripts.staging_reset plan
   ```

   Exit 0 means it is ready. Exit 1 prints `Stopped at <step>: ...`. Fix the cause, then
   run `plan` again.

2. Wipe and rebuild. It asks you to type `wipe csa-staging`. No flag skips that. Later it
   asks you for the first platform admin's initial password, at least 12 characters, typed
   twice. You get three tries before it stops. Wait for the prompt before you type: it can
   take a few seconds, and anything typed before it shows on screen. If a password does
   show, change it and clear the terminal's scrollback.

   ```bash
   cd /home/amsyrzmi/CSAV2 && .venv/bin/python -m scripts.staging_reset wipe
   ```

   In order, it backs up, stops the four services, drops and creates the database, empties
   the three upload volumes, loads the base schema, migrates, creates the first platform admin,
   starts the services and waits until they are ready. If it fails at the stop or at the
   drop and the database is still there, nothing is lost, and it prints the command that
   brings staging back. If it fails later, it says which step failed and what state
   staging is in. It is safe to run again.

3. Sign in to the console as the first platform admin and change the password.
   `accounts` cannot add anyone until this is done.

4. Create the other admins, the companies and the invitations. It asks for the first
   admin's password, and for each further admin it asks again, then for that admin's
   initial password. Wait for each prompt before you type, as in step 2. Give each admin
   their initial password privately. They change it at first sign-in.

   ```bash
   cd /home/amsyrzmi/CSAV2 && .venv/bin/python -m scripts.staging_reset accounts
   ```

   The company step runs inside the gateway, so its lines appear when it finishes. Each
   line says `created`, `already there`, `invited, mail sent` or `mail not sent`, with an
   address but never a link. Exit 2 means some mail was not sent. Use Renew invitation on
   that person's Team page.

   It sets every company's status to `pilot` and its brand name to the company's name, and
   says so in its lines. Background jobs visit only `pilot` and `active` companies, so
   without this knowledge base uploads and subscriptions would silently never run, and
   the bot would call itself "the business".

   - Running `accounts` again is safe. It skips what exists and sends no second email.
   - It refuses when staging holds a company outside the roster, so it refuses an
     unwiped staging. Running `seed_staging` after a reset does the same, so do not.
   - A repeat run soon after may have to wait up to 15 minutes. Each run uses up to one
     `admin_add` throttle attempt for every admin it adds, plus one for the company step.
     The limit is 5 attempts per 15 minutes per address.

### After the tool

Do these in order:

1. Each invited person accepts within 48 hours and sets a password.
2. Team Jo's tenant admin sets the HR owner on the Team page, once that person has signed
   in. Syarikat Cekap's tenant admin makes themselves the HR owner.
3. Team Jo's HR owner gives the manager and staff accounts their HR access on the access
   page. Invitations carry no HR preset, so until then those two accounts look the same
   and have no HR rights. A platform admin cannot choose the manager preset. Only the HR
   owner can.
4. Team Jo's tenant admin turns on the email assistant's inner switch. Only a tenant
   admin of the company can change it. Activating the Staff email package does not do it.
5. Bind the test bot. Do not paste section 5 as it stands: it pins old image digests, and
   the code in an old image does not fit the rebuilt schema. Use the images that are
   running now. Run the reset tool's plan again (it only reads):

   ```bash
   cd /home/amsyrzmi/CSAV2 && .venv/bin/python -m scripts.staging_reset plan
   ```

   It prints them under "Running images": the `python` line and the `frontend` line. Put
   each full reference, starting `ghcr.io/`, in place of the two `<...>` below, then run
   the command. The wipe removed the old binding, so the bot is free.

   ```bash
   (
     cd /home/amsyrzmi/CSAV2 || exit 1
     export NO_LOCAL_ENV=1 ENV=ci COMPOSE_PROFILES=
     export CSA_STAGING_PROJECT=csa-staging
     export CSA_STAGING_SECRET_DIR=/srv/csa-staging/secrets
     export CSA_STAGING_PYTHON_IMAGE=<python image from plan>
     export CSA_STAGING_FRONTEND_IMAGE=<frontend image from plan>
     export CSA_STAGING_CONSOLE_ORIGIN=https://staging-console.fourstacks.my
     export CSA_STAGING_HOOKS_ORIGIN=https://hooks.fourstacks.my
     docker compose --env-file /dev/null --project-name csa-staging \
       --file deploy/compose.staging-local-proposal.yml \
       --file deploy/compose.staging-shared-caddy.yml \
       --file deploy/compose.staging-pipeline.yml \
       --profile staging-approved --profile test-bot-setup-approved \
       run --rm --no-deps staging-bot-setup --tenant team-jo --test-bot --replace-webhook
   )
   ```

   It is section 5's command with `--tenant team-jo` and the running images.
6. Connect WhatsApp and Gmail again when wanted. The Google test users stay as they are,
   but they must be listed there and their access expires every 7 days (section 11).
7. Do the pending retests in `docs/ASSISTANT_RETEST.md`.

### Backups

Each `wipe` writes `~/csa-staging-backups/<UTC timestamp>/`, mode 0700. It holds:

- `csa_staging.dump`, a `pg_dump` custom-format dump of `csa_staging`;
- `csa-staging_staging-uploads.tar`, `csa-staging_staging-hr-uploads.tar` and
  `csa-staging_staging-training-uploads.tar`;
- `manifest.json`, written last. It holds counts and image names, no addresses and no
  secrets.

A directory with no valid `manifest.json` is incomplete. The tool lists it as
`(incomplete, do not restore)`. A repeat `wipe` after a failure backs up an already empty
staging and leaves the earlier backups as they were, so the newest backup is not always
the one you want.

**Which backup to restore:** the newest complete one whose counts show real data, meaning
companies above 0. When a stop shows no data in this run's backup, the tool prints
`The most recent complete backup with real data is <directory>`. Use that one. To check
by hand:

```bash
ls -1 ~/csa-staging-backups
cat ~/csa-staging-backups/<timestamp>/manifest.json
```

Do not use a backup that says `no database dump`.

### Restore from a backup

Use this when a wipe failed and you want the old data back, or when you want the earlier
data back after a reset. These commands replace what is on staging now with what the backup
holds. Run them one block at a time and read each result. Set `B` first, with the
timestamp of the backup you chose in place of `<timestamp>`, and keep the same
terminal for all four steps.

```bash
B=~/csa-staging-backups/<timestamp>
```

1. Stop the four services. A container that does not exist gives an error. That is fine.

   ```bash
   docker --host unix:///var/run/docker.sock stop csa-staging-gateway-1 csa-staging-frontend-1 csa-staging-worker-1 csa-staging-delivery-1
   ```

2. Rebuild the empty database and load the dump. The dump goes in on stdin. Run these
   three lines one at a time, and stop if one fails.

   ```bash
   docker --host unix:///var/run/docker.sock exec csa-staging-staging-db-1 psql -U csa -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS csa_staging WITH (FORCE)"
   docker --host unix:///var/run/docker.sock exec csa-staging-staging-db-1 psql -U csa -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE csa_staging OWNER csa"
   docker --host unix:///var/run/docker.sock exec -i csa-staging-staging-db-1 pg_restore -U csa -d csa_staging --exit-on-error --single-transaction < "$B/csa_staging.dump"
   ```

3. Empty each upload volume and unpack its tar. Both steps use the same sealed container
   as the backup: no network, read-only, no capabilities and user 1000. The image is the
   one the gateway runs. If the gateway container is gone, set `IMAGE` to the `python_image`
   named in `manifest.json` instead.

   ```bash
   IMAGE=$(docker --host unix:///var/run/docker.sock inspect csa-staging-gateway-1 --format '{{.Image}}')
   for V in csa-staging_staging-uploads csa-staging_staging-hr-uploads csa-staging_staging-training-uploads; do
     docker --host unix:///var/run/docker.sock run --rm --network none --read-only --cap-drop ALL \
       --security-opt no-new-privileges:true --user 1000:1000 -v "$V":/data \
       --entrypoint find "$IMAGE" /data -mindepth 1 -delete || break
     docker --host unix:///var/run/docker.sock run --rm -i --network none --read-only --cap-drop ALL \
       --security-opt no-new-privileges:true --user 1000:1000 -v "$V":/data \
       --entrypoint tar "$IMAGE" -C /data -xf - < "$B/$V.tar" || break
   done
   ```

   If this prints an error, stop; the volume may be empty.

4. Bring the services back. First check that the restored data fits the images that will
   run it. Read the backup's schema and revision from its manifest or from the tool's
   backup line, and compare them with the checkout:

   ```bash
   cat "$B/manifest.json"
   ls /home/amsyrzmi/CSAV2/db/migrations/*.sql | tail -n 1
   git -C /home/amsyrzmi/CSAV2 rev-parse HEAD
   ```

   Start the existing containers only if all four still exist, the manifest's
   `schema_version` is the number the newest migration file begins with, and its `sha` is
   the checkout's HEAD. A backup taken before a later rollout does not meet this, because
   its database has an older schema than the images now expect. If the numbers differ, or
   you are in any doubt, do not start the containers. Roll out instead, as in Routine
   rollouts: `plan`, then `apply`. `apply` recreates the services and applies any migrations
   the restored database lacks.

   To start them:

   ```bash
   docker --host unix:///var/run/docker.sock start csa-staging-gateway-1 csa-staging-frontend-1 csa-staging-worker-1 csa-staging-delivery-1
   ```

   Then check readiness, the same `/readyz` probe the rollout uses. Exit 0 and no output
   means ready. The gateway can take about 45 seconds, so if it fails, wait a few seconds
   and run it again. If it still fails after a few minutes, stop and look at the gateway's
   logs.

   ```bash
   docker --host unix:///var/run/docker.sock exec csa-staging-gateway-1 python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=5)"
   ```

**When `wipe` refuses after a failed wipe.** If HEAD moved after a failed wipe, or a
service container no longer exists, `wipe` refuses, and `staging_rollout plan` cannot read
a missing ledger. The way out is the same each time: restore the named backup (steps 1 to
3 above), roll out (`plan`, then `apply`), then run `wipe` again.

### Deleting a backup

The tool never deletes a backup. A backup holds staging's data: accounts, customer and HR
records, and uploaded files. Keep one while you might need it. Delete it when you do not,
by naming the directory:

```bash
rm -r ~/csa-staging-backups/<timestamp>
```

## Rollback

Stop only `worker`, `delivery` and `staging-embeddings`; the gateway can stay
with its new settings. Remove the hooks route from the tunnel and the Caddy
block with the same care as the console block. To release the bot, call
`deleteWebhook` from the development setup, not from here. Volumes are kept.
