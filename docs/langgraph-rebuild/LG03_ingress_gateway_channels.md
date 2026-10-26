# LG03 — Ingress gateway + channel adapters (Telegram first; WhatsApp, Gmail, webhooks next)

## CONTEXT FOR CODING AGENT
Replace `01_ingress_telegram` and the future WhatsApp/Gmail/webhook ingress workflows with one FastAPI gateway. The gateway does **no business logic and no LLM calls**: verify → normalize → resolve tenant → dedupe → persist → ack. Everything else happens in the worker. Read `LG00` §3–4 and `LG02` (Envelope) first. Reuse tables: `ingress_events` (the queue), `webhook_endpoints` → `channel_credentials` (sources/tenancy), `pending_actions` (buttons), `dlq`, `contacts`, `conversations`, `messages`, `voice_transcripts`. Known columns are summarized in LG01 §1; anything marked *verify* depends on the `\d+` output you haven't sent yet.

## Goal
Any channel event becomes a durable `ingress_events` row and a `200` in well under a second; a worker picks it up exactly-once-effectively (at-least-once delivery + idempotent processing), serialized per conversation.

## Common pipeline (all channels)
Every inbound event becomes a row in **`ingress_events`**, which is already shaped like a queue: `status received → dispatched → completed | failed | dead`, `attempts`, `next_attempt_at`, `dispatched_at`, `last_error`, `raw` + `raw_purged_at`, `payload_hash`, unique `(client_id, idempotency_key)` and unique `trace_id`.

1. **Resolve the source to an endpoint.** `ingress_events.endpoint_id` is NOT NULL → `webhook_endpoints`, which has `public_id` (16–64 chars, unique) and a **unique** `credential_id` into `channel_credentials` (one endpoint per credential). So **every inbound source is a `webhook_endpoints` row**: one per Telegram bot, WhatsApp phone number, Gmail mailbox, generic webhook and web-console embed. URL: `/hooks/{public_id}`. `endpoint → credential → client_id` is the tenant (fixes today's `WHERE telegram_id=$1 LIMIT 1`); the channel comes from the credential (the endpoint has no channel column). The external account id lives in `channel_credentials.shop_id` (bot id / WA `phone_number_id` / mailbox). Reject if `is_enabled=false` (return `disabled_reason` in logs), and enforce `rate_limit_rpm` per endpoint (an in-process token bucket is enough at your scale; move to Postgres/Redis only with several gateway replicas).
2. **Verify authenticity** (channel-specific) with secrets from `channel_credentials.meta` (encrypted, LG01 §6).
3. **Normalize** → `Envelope` (LG02). Redact PII with `csa/core/pii.py` (port your regexes, then fix: Luhn-check card candidates so 13+ digit tracking numbers aren't swallowed; E.164-normalize Malaysian phones including the `+60` prefix).
4. **Resolve/upsert the contact** through **`contact_identities`** — `PK (client_id, channel, external_id) → contact_id`; a trigger rejects an identity whose contact belongs to another client, so **create the contact first, then the identity**. No workflow uses it today; identities live as columns on `contacts` (`telegram_id`, `email`, `phone`, `console_user_id`, each unique per client). Use `contact_identities` for every channel (Telegram user id, WhatsApp E.164 number, lower-cased email, console user id) and backfill from the columns (LG11 M11). **Dual-write the legacy column** (e.g. `contacts.telegram_id`) while any client is still on the n8n runtime, because n8n resolves tenants through it. **Never auto-merge identities on an unverified claim:** `contact_verifications` is an OTP table (`code_hash`, `pending_field`, `pending_value`, `attempts` / `max_attempts` = 3, `status`, `expires_at`, `verified_at`) — use it for "link my WhatsApp number to my account": send a code over an already-verified channel, store only its hash, allow 3 tries, expire it, and only then insert the new identity. Unknown sender ⇒ new contact, `is_first_contact=true` (today ingress throws "Client not found" before `11_context`'s upsert can run).
5. **Persist**: `INSERT INTO ingress_events … ON CONFLICT (client_id, idempotency_key) DO NOTHING RETURNING id`. No row back ⇒ duplicate ⇒ ack 200 and stop. On conflict compare `payload_hash`: a *different* payload reusing a key is logged and alerted. (`ingress_dedup` becomes redundant; keep it until cutover.)
6. **Ack** immediately (target p95 < 200 ms). For Telegram callbacks call `answerCallbackQuery` here so the spinner stops.

### Worker pickup (`apps/worker`)
```sql
-- claim
UPDATE ingress_events SET status='dispatched', dispatched_at=now(), attempts=attempts+1
WHERE id = (SELECT id FROM ingress_events
            WHERE status='received' AND next_attempt_at <= now()
            ORDER BY next_attempt_at FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING *;
```
- **Per-conversation ordering:** `ingress_events` has no `conversation_id`, so key the lock on `(client_id, channel, channel_thread)`: take a **session-level** `pg_try_advisory_lock(hashtextextended(...))` on a dedicated connection held for the whole turn (released automatically if the process dies). If busy, set the row back to `received` with `next_attempt_at = now() + 1s`. (A transaction-level lock would not span a multi-second turn.)
- **Reaper (cron, every minute):** rows `dispatched` for longer than the turn deadline + margin (e.g. 3 min) go back to `received`; after max attempts → `dead` plus a `dlq` row (`stage='turn'`, `envelope` = `raw`, `error`). Your `idx_ingress_events_inflight` index exists for exactly this.
- **Completion:** `completed` + `completed_at`. Failure: `failed` with exponential backoff in `next_attempt_at`; after N attempts `dead` + `dlq`. Replays use `dlq.replay_attempts / max_replays`.
- **Retention:** nightly job sets `raw=NULL, raw_purged_at=now()` for completed events older than N days.
- Enforce the envelope's `turn_deadline_ms`: if exceeded, send a deterministic "we're on it" + escalate rather than silence.

### Channel adapter interface (`csa/channels/base.py`)
```python
class ChannelAdapter(Protocol):
    channel: Channel
    capabilities: (
        Capabilities  # buttons, max_buttons, max_len, markup, templates_required, threading
    )

    async def verify(self, request: Request, cred: Credential) -> None: ...
    async def parse(self, request: Request, cred: Credential) -> list[InboundEvent]: ...
    def render(
        self, msg: OutboundMessage, conv: ConversationRef
    ) -> ChannelPayload: ...  # LG07 uses this
    async def send(self, payload: ChannelPayload, cred: Credential) -> DeliveryResult: ...
```
Adapters are the **only** place that knows tokens, markup flavors, limits, or `telegram_id`/`wamid`/`threadId`.

## Implementation status (local development only)

### Runtime investigation follow-up (2026-10-16)

The normal Telegram path now reads credentials and client runtime in one tenant-scoped
lookup, removing one sequential round trip without caching credentials or changing
authentication, routing, staff callbacks or idempotency. The ingress/cutover/ambiguous
commit selection plus both original 50-rps/<200-ms benchmarks passed 19 tests. The final
full lint/test run passed **655 tests**; ASGI ack/scheduled-ack p95 was
**114.186/120.390 ms**, TCP **129.436/130.810 ms**, at 50 requests/s, with 150 completed
turns and pending deliveries per transport. Earlier failure/diagnostic runs remain in
[progress](../PROGRESS.md); one clean run is not a guarantee under all shared-host loads.

An isolated actual gateway reproduced an event-loop stall when synchronous logging met
undrained stdout. With explicit user approval, configured logging now has a bounded
background writer and readiness failure on stalls/overflow/writer errors, with a visible
dropped-event count and responsive liveness. The gateway/identity/poller/logging
selection passed 29 tests, including real blocked-stdout liveness/readiness and exact
stall-threshold/recovery regressions.
This fixes the reproduced failure mode, not a proven historical incident cause.
No live rollout, production/n8n request or infrastructure change was performed.

The Telegram-first webhook path is implemented and verified in the isolated
development stack: authentication, normalization, PII redaction,
endpoint-to-tenant resolution, contact/conversation resolution, idempotent
`ingress_events` persistence, ordered worker processing, echo through `messages`
and `outbound_deliveries`, Telegram delivery, and ingress/delivery reaping and
DLQ handling. The gateway enforces JSON content type and a 1 MB request-body
limit. Callback spinner acknowledgement runs after the ingress transaction
commits.

Verification: `make lint` passed; `ENV=ci make test` passed (117 tests), including
the 50-rps/p95 acknowledgement test; Gitleaks found no leaks when excluding
`.env.local` and `.venv`. The isolated dev database was reset and is bound to
`127.0.0.1:5434`. Through the dedicated dev hostname, local and public HTTPS
health checks returned 200. A real dev-bot exchange produced completed ingress
events, inbound/outbound message pairs, and delivered outbox rows with Telegram
HTTP 200 responses. No production database or bot credential was used.

After that verification, the isolated database was reset again with approval.
It now contains only the two sandbox tenant seed rows; the development bot's
credential and endpoint plus prior contact, conversation, and ingress evidence
were cleared. Re-run `make setup-dev-telegram` and register its newly printed
URL before repeating a live Telegram exchange. The historical integration
verification above remains evidence of the earlier run, not of the post-reset
database state.

The checked criteria below cover the Telegram webhook path. WhatsApp, Gmail,
and generic webhook adapters plus channel-specific media/voice or approval
flows remain follow-on work. An optional local Telegram polling mode is
implemented but is not active in the verified server configuration. The
additional-channel checklist and live-test gates remain unchecked until those
integrations are implemented and verified.

### LG08 gateway performance follow-up (2026-10-12)

The current isolated dev gateway is ready on schema 0220; the older reset/bootstrap narrative
above is historical. Current runtime instructions are in [RUNBOOK.md](../RUNBOOK.md).
Contact resolution now combines contact/identity operations, retaining subsequent-statement
conflict handling. Gateway-only autocommit avoids implicit transactions for endpoint reads;
tenant operations remain inside explicit `tx()`. Tracing is batched/compact and records server
request spans without ASGI receive/send child spans. Pool capacity remains eight.

`ENV=ci make lint test` passed **325 tests**. Separate-process TCP ingress, using synthetic
traffic and a disposable database, passed 150 arrivals at 50/s: latest ack p95 **108.808 ms**,
scheduled p95 **110.330 ms**, 150 unique durable turns/replies/pending deliveries. The
[LG08 report](../archive/evals/lg08_validation_report.md) retains failed experiments and a post-fix
ASGI scheduling failure. These short bursts do not prove proxy/provider delivery latency or
sustained 50-turn/s worker capacity. No new live Telegram exchange is claimed in this slice.
Two spawned-process echo-worker tests also verify per-conversation ordering, idempotent
outbox creation, session-lock release after process termination and reaper-based recovery.
They do not benchmark multi-process coordinator/checkpoint throughput.

Follow-up on 2026-10-13: `ENV=ci make lint test` passed **328 tests**, including both
process-level worker tests; `make coverage` passed with **100%** policy statement/branch
coverage. The disposable database reset and local Gitleaks source scan passed. The existing
isolated dev gateway stayed ready; its database, worker, delivery, webhook and production
services were not changed.

---

## 3A — Telegram (build first)
- Webhook URL contains an unguessable path segment **and** `secret_token` (verify `X-Telegram-Bot-Api-Secret-Token`).
- `setWebhook(allowed_updates=["message","edited_message","callback_query"], drop_pending_updates=false)`. (Your n8n trigger export lists only `message`.)
- Local-only polling alternative: `CHANNEL_MODE=polling` makes `make run-dev` start `apps.telegram_poller`. It is restricted to `ENV=dev` and the isolated `127.0.0.1:5434/csa` database, loads the dedicated local dev bot credentials from encrypted storage, and calls `deleteWebhook(drop_pending_updates=false)` before polling. Update offsets advance only after each update has been durably accepted by ingress; replay is safe through ingress idempotency. Keep webhook mode for the current live dev bot unless intentionally switching that bot to polling.
- Handle: text, caption, photo/document (attachments → policy: approval), voice/audio (→ transcription step, below), `callback_query` (decode via LG02 codec), ops-group callbacks (approval buttons → LG07).
- Rendering: use **HTML parse mode** with `html.escape` — far safer than legacy Markdown escaping. Buttons via `inline_keyboard`; `callback_data` = codec id (≤ 64 bytes).
- Rate limits: ~1 msg/s per chat, ~30 msg/s global → delivery worker throttles (LG07).
- Voice notes: see **3F** (async STT job, internal HMAC callback, redaction, reaper).

## 3B — WhatsApp (official Cloud API only)

### Account and API prerequisites
- **Development:** create a Meta developer account and a Meta app with the WhatsApp use case. Connect the developer test WABA/phone number and add a recipient phone number you control to the test allow-list. Enable two-factor authentication on the developer account. Use the temporary test token only for setup and test traffic.
- **First business integration:** create/verify a Meta Business Portfolio and WhatsApp Business Account (WABA), then register an eligible business phone number and configure its display name/profile. Create a system user with only the required WABA/phone-number assets and permissions, and issue a revocable long-lived access token. Request only the required WhatsApp permissions (typically `whatsapp_business_messaging` and `whatsapp_business_management` for messaging and account/webhook management); add `business_management` only if the chosen onboarding flow needs it. For the first pilot, prefer a WABA/number owned by the customer and customer-granted access; add Embedded Signup only after committing to Meta Tech Provider enrollment and its current review requirements.
- **APIs:** WhatsApp Business Platform **Cloud API** (versioned Graph API) for sending messages and media; WhatsApp webhooks for inbound updates and delivery statuses; Meta App Dashboard for app subscriptions, permissions/review, assets, and tokens. Configure the webhook callback on the dedicated HTTPS dev hostname. Pin the Graph API version in configuration and schedule version upgrades.
- **Credentials:** global Meta app ID/secret and webhook verify token are application configuration. The WABA, `phone_number_id`, and customer/system-user access token are per-connection: store the number ID in `channel_credentials.shop_id`, token encrypted in `access_token`, and webhook/app secrets encrypted in `meta`. Never put customer tokens in source, committed examples, logs, or shared prompts.
- **Before live business traffic:** check whether the number is already registered in WhatsApp or WhatsApp Business and verify current migration/coexistence support for the account and country. Verify current opt-in, template, pricing, messaging-window, display-name, and Meta review rules rather than relying on old limits. Do not use unofficial WhatsApp clients for customer traffic.

### Behavior and acceptance
- `GET` verifies Meta's challenge; `POST` verifies `X-Hub-Signature-256` (HMAC-SHA256 of the **raw** body with the app secret) before JSON parsing.
- Payload: `entry[].changes[].value.messages[]` (inbound) and `.statuses[]` (sent/delivered/read/failed → update `outbound_deliveries`). Dedupe on message id (`wamid`).
- **24-hour customer-service window**: track `conversations.last_inbound_at`. Inside the window free-form replies are allowed; outside it only pre-approved **templates**. Add table `wa_templates(client_id, name, language, body, status)`; the egress renderer picks free-form vs template (LG07). *Verify current Meta rules and limits before building.*
- Interactive replies: a few quick-reply buttons (low single digits, short titles) or a list message; confirm exact current limits in Meta docs. Button ids carry the LG02 codec id.
- Media: download requires the access token and short-lived URLs; fetch, store, then reference.
- Opt-out handling (STOP-style keywords) → mark contact opted-out; egress must refuse to send.
- Phone numbers normalized to E.164 in `contact_identities.external_id`.
- **Live-test gate:** use the Meta test number and an allow-listed recipient first. Verify challenge/signature, inbound and duplicate delivery, status callbacks, retry behavior, template/out-of-window response, opt-out suppression, and safe media handling before any business number is connected.

Official setup guide: [WhatsApp Cloud API Get Started](https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started).

## 3C — Gmail

### Account and API prerequisites
- **Development:** create a Google Cloud project under an account you control; enable **Gmail API** and **Cloud Pub/Sub API**; configure the Google Auth Platform/OAuth consent screen and a Web application OAuth client with the exact dev HTTPS redirect URI. Add a dedicated throwaway/test mailbox as a test user while the OAuth app is in Testing. Do not test using production/customer mailboxes.
- **Push:** attach billing to the Cloud project and create a Pub/Sub topic and push subscription targeting the dedicated HTTPS gateway callback. Grant `roles/pubsub.publisher` on the topic to `gmail-api-push@system.gserviceaccount.com`. Configure authenticated push with a dedicated service account; grant the Pub/Sub service agent `service-{PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com` the `roles/iam.serviceAccountTokenCreator` role needed to mint push OIDC tokens. Validate token signature, issuer, audience, and authorized service-account identity at the gateway. Keep the push identity separate from per-mailbox OAuth credentials. Prefer workload identity/federation where available; otherwise keep a narrowly scoped service-account key outside the repository with restrictive file permissions and rotation.
- **Per mailbox:** each mailbox owner must authorize the application. Store refresh tokens encrypted in `channel_credentials`; never put user refresh tokens in `.env.local`. Workspace domain-wide delegation is only for Workspace domains you control, requires explicit administrator configuration and scope authorization, and does not apply to consumer Gmail accounts.
- **APIs:** Gmail API methods `users.watch`, `users.history.list`, message/attachment reads, and initially draft creation; Cloud Pub/Sub API for Gmail change notifications. The push payload is only a wake-up signal: fetch incremental history/messages and commit the resulting ingress rows before acknowledging Pub/Sub.
- Request scopes incrementally: start with `gmail.readonly` for inbound processing and `gmail.compose` for draft creation. **Both are restricted scopes; `gmail.compose` also permits sending**, so enforce draft-only behavior in the adapter and do not expose `users.messages.send` until a separately approved auto-send rollout. Google's restricted-scope verification and server-side security-assessment requirements may apply; verify the current rules before external users authorize. Restrict testing to approved test users until approval is complete.

### Behavior and acceptance
- `users.watch` → Pub/Sub topic → authenticated push callback (`POST /hooks/gmail/push`). **Watch expires; renew daily** through LG10 and persist `historyId` and expiry in `channel_credentials.state`. Recover expired history with a bounded full resync and alert; never silently lose mailbox changes.
- On push: `users.history.list(startHistoryId)` → fetch new message ids → fetch message → one `InboundEvent` each. Idempotency key `gm:{messageId}`.
- Threading: `conversation.channel_thread = threadId`. Replies must carry `In-Reply-To`, `References`, same `threadId`, `Re:` subject.
- Parsing: HTML→text, strip quoted history and signatures (e.g. `talon` / `mail-parser-reply`), keep attachments out of the model (policy: attachment ⇒ approval).
- **Loop protection (critical):** drop/flag mail with `Auto-Submitted` ≠ `no`, `Precedence: bulk|junk|list`, `List-Id`, `X-Autoreply`, no-reply senders, your own address; rate-limit per sender (e.g. >3 replies/hour ⇒ stop and escalate).
- Default `deployment_mode=suggest`: egress creates a **Gmail draft** + notifies ops; flip a client to auto-send only after metrics (LG08) justify it.
- **Live-test gate:** verify OAuth state/CSRF and refresh, Pub/Sub OIDC, initial sync, incremental history, duplicate/out-of-order notifications, watch renewal, threading, draft creation, attachment policy, loop suppression, and expired-history recovery using the test mailbox.

Official guides: [Gmail push notifications](https://developers.google.com/workspace/gmail/api/guides/push), [Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes), [OAuth restricted-scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification).
Scope reference: [Gmail API OAuth scopes](https://developers.google.com/workspace/gmail/api/auth/scopes).

## 3D — Generic webhooks
- `webhook_endpoints` (+ `webhook_credentials` view): per-endpoint id, secret, status, mapping, rate limit, callback URL.
- Auth: HMAC-SHA256 over `timestamp + "." + body`, headers `X-CSA-Timestamp` / `X-CSA-Signature`; reject if timestamp skew > 5 min; replay protection through `ingress_dedup`.
- Mapping: per-endpoint JMESPath/JSONata expressions to Envelope fields (`contact.external_id`, `message.body`, `conversation.channel_thread`). Validate with Pydantic; reject unknown shapes with a 422 and a helpful error.
- **Reply mode comes from `webhook_endpoints.reply_mode`:** `sync` — the gateway holds the request open up to `sync_wait_ms` (1–60 s, default 20 s): after inserting the outbound message `emit` issues `pg_notify('turn_done', trace_id)`; the waiter returns the reply and, if the trigger already created a callback delivery row, marks it `skipped` (the trigger delays that row by 10 s precisely so the synchronous reply can win). On timeout return `202 {turn_id}`; the callback (if `callback_url` is set) goes out after that delay, otherwise the caller polls `GET /v1/turns/{id}`. `callback` — respond `202 {turn_id}` at once; delivery to `callback_url` (https only, signed) goes through the outbox. Conversations on this channel use `channel_thread = '{public_id}:{caller_thread_id}'` — `enqueue_webhook_delivery()` derives the endpoint from that prefix. Enforce payload size and `rate_limit_rpm`.
- Also the outbound side: platform events (`order.submitted`, `ticket.created`) can be emitted as signed webhooks to client systems through the same outbox.

## 3F — Voice notes (replaces `01_voice_submit_gateway` + `02_livekit_transcript_callback`)
What exists: ingress submits a job (`voice_transcripts` row, `status='processing'`, envelope stored) to the LiveKit-based STT gateway at `http://172.17.0.1:8082/v1/voice-jobs`; the gateway calls back an n8n webhook with the transcript; the callback rebuilds the envelope and calls the coordinator. Good shape (async job, idempotent `UPDATE … WHERE status='processing'`). Problems: transcripts skip PII redaction, no failure/timeout path, hard-coded bearer token, public callback URL, Docker bridge IP, no dedup at submit.
New flow:
1. Gateway receives the voice message → **dedupe first** (`ingress_events`-style key `voice:{endpoint}:{provider_file_id}` or the message key) → insert `voice_transcripts (processing)` → submit to the voice gateway (URL from settings, not an IP literal; 5 s connect/30 s total timeout; one retry; on failure mark the job `failed` immediately).
2. The voice gateway calls back an **internal** endpoint on your network (`/internal/voice-callback`, not via the public n8n URL), authenticated with a **per-job HMAC** over `voice_job_id + timestamp` (constant-time compare, 5-min skew). Update `voice_transcripts … WHERE id=$1 AND status='processing'` (idempotent), then **create the `ingress_events` row at callback time** (that is what happens today) with key `voice:{voice_transcript_id}`.
3. **Run the transcript through the same `pii.py` redaction as typed text** and set `body_redacted`/`pii_map` from it (fixes finding #28); `body_raw` keeps `[voice note] …` for the record.
4. Language: use the callback's language only as a hint into LG06's detector; unsupported ⇒ the unsupported-language path.
5. **Voice reaper (LG10):** jobs `processing` longer than the deadline (e.g. 90 s) become `failed` and create an ingress event flagged `voice_transcription_failed=true` ⇒ the existing "voice failed → escalate" fast path. No voice note ever ends in silence.
6. Voice turns keep `has_attachment=true`, which sends them to review under the default policy; make that a policy choice per client (`always_review_if`) rather than an accident.
Rotate the old callback token now; delete the public n8n callback route at cutover.
**Source vs STT:** Telegram supplies the audio *file* (`telegram_file_id`); your LiveKit-based gateway (`:8082`, model `livekit-stt`) does the *transcription* — so the gateway alone decides which languages work. Find out which STT model sits behind it and test Malay, Manglish, Tamil and Chinese on it before promising voice support per client. **If you can't change the gateway code:** keep its static bearer secret but store it in the secret manager, compare in constant time, accept callbacks only on the internal network, and require that `voice_job_id` exists and is `processing` (the `WHERE status='processing'` update already makes replays harmless). The gateway request already accepts `audio_uri`, so WhatsApp voice notes (downloaded with the Meta media API and the access token) and email audio attachments can reuse it with a short-lived signed URL or a file on a shared volume.
Use the columns you already have: `voice_transcripts.message_id` (set it when the inbound message row is created), `callback_attempts` (increment on every callback; ignore once final), `confidence`, `duration_secs`. Add a CHECK on `status` (`processing|completed|failed|timeout`) and a partial index `(created_at) WHERE status='processing'` for the reaper (LG11 M03). Null `envelope` once the job is final — it carries contact data.

## 3E — Web console (already in `11_context`)
`11_context` handles `channel = 'web_console'`: contacts linked by `console_user_id`, matched by verified email, and an `actor_type` that creates `contact_kind = 'client_user'` vs `'customer'`. This is a fifth channel not on your list. Treat it like a webhook endpoint: your dashboard backend signs requests with the user context (id, verified email, actor type); the gateway maps them to the envelope. `client_user` messages are staff-side and must never see other contacts' data. Confirm how it is fed today and what `contacts` columns it relies on after `\d+ contacts`.

## Acceptance criteria
- [x] Telegram: send "hello" to a **test bot** → row in `ingress_events` → worker echoes via the outbox (no graph yet) → reply arrives; duplicate delivery of the same update creates one reply.
- [x] Gateway ack p95 < 200 ms in a local load test (fake channel, 50 rps).
- [x] Two rapid messages from the same conversation are processed strictly in order (test with a sleep in the worker).
- [x] Unknown Telegram user → contact created, `is_first_contact=true`, no exception.
- [x] Every source has a `webhook_endpoints` row; tenant is resolved endpoint → credential → client, never from the contact.
- [x] A turn stuck in `dispatched` is reclaimed by the reaper; after max attempts it lands in `dlq` with its envelope.
- [x] Wrong/missing signature/secret → 401/403, nothing persisted.
- [x] `callback_query` round-trip works through the codec.
- [ ] Each additional channel adds: adapter + fixtures of real payloads + signature tests + one end-to-end test.
- [ ] WhatsApp Cloud API pilot passes the live-test gate with a Meta developer test number and allow-listed recipient before using a business WABA/number.
- [ ] Gmail pilot passes the live-test gate with a throwaway mailbox, authenticated Pub/Sub push, daily watch renewal, and draft-only egress.

## Pitfalls
- Verify signatures on raw bytes; framework body parsing can alter them.
- Telegram retries aggressively on slow/non-2xx responses — never do real work before the ack.
- Gmail `watch` silently expiring is the #1 "email stopped working" cause.
- Keep WhatsApp/Gmail pilots isolated from production until Telegram parity/cutover is approved (LG09); each adapter is additive and gated by external account/API reviews.
