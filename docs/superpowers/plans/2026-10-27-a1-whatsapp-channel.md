# A1 WhatsApp Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Customers of a tenant can message the tenant's WhatsApp number through Meta's Cloud API and receive the same bounded, human-supervised service the Telegram channel gives (questions, tickets, escalation, subscription reads and requests); staff see and answer those conversations in the existing inbox.

**Architecture:** One Meta app per deployment (Fourstacks is the technology provider): a single signed webhook `/channels/whatsapp` routes to the tenant by the payload's `phone_number_id`. A new adapter (`csa/channels/whatsapp.py`) and ingress (`csa/channels/whatsapp_ingress.py`) reuse the Telegram pipeline's tenant transaction, contact identity, conversation, dedupe and queue steps. The delivery worker gains a WhatsApp branch with the 24-hour service window check. Tenants connect their number from a Services card; inbox SQL that hardcodes Telegram widens to both customer channels.

**Tech Stack:** Python 3.12, FastAPI, httpx, psycopg 3 async, Pydantic; Next.js/TypeScript; Playwright; Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-27-client-growth-program-design.md` (section 6, A1; owner decisions in section 12).

## Global Constraints

- Migration range for this lane: `0470`–`0479`. This plan ships `0470_whatsapp_channel.sql` and raises `EXPECTED_SCHEMA_VERSION` to `"0470"` (one line, per W0).
- Deployment secrets `WHATSAPP_APP_SECRET` and `WHATSAPP_VERIFY_TOKEN` are secret files, never shown in any console response or log. Tenant tokens are encrypted with `CREDENTIALS_KEY` using `crypto.credential_aad("channel_credentials", "access_token", str(credential_id))`.
- Webhook signature: `X-Hub-Signature-256: sha256=<hex HMAC-SHA256(app_secret, raw body)>`, compared with `hmac.compare_digest`. A bad or missing signature is 403 with no side effects. Verification handshake: `GET` with `hub.mode=subscribe`, `hub.verify_token`, `hub.challenge`; answer the challenge as `text/plain` 200 when the token matches, else 403.
- Service window: a free-form reply is allowed only while `now - conversations.last_inbound_at <= 24 hours`; otherwise the delivery fails with `outside_service_window`, is not retried, and the inbox explains it in plain words (writing guide).
- Channel names on screen: "WhatsApp" and "Telegram". No internal codes in copy (`docs/WRITING_GUIDE.md`).
- Phase 1 scope (owner decision): no interactive buttons, no templates, no media download, no ordering on WhatsApp. The order agent keeps rejecting WhatsApp; non-text messages are enqueued with an attachment stub exactly as Telegram voice notes are today.
- Graph API base `https://graph.facebook.com/v21.0`; the version is a setting, not a literal in code.
- `NO_LOCAL_ENV=1 ENV=ci make lint test`; disposable Testcontainers only; full suite on CI; Playwright needs `export PATH=/tmp/fourstacks-toolbin:$PATH`.
- Lane A owns `csa/channels/`, `apps/delivery/`, `csa/console/inbox.py`, `csa/console/channels.py` (new), `components/console/InboxWorkspace.tsx`, the Services "WhatsApp number" card, `db/sql/inbox`, `db/sql/delivery`, `db/sql/ingress`. Do not touch `lib/navigation.ts`, `csa/console/packages.py`, `csa/subscriptions/`.
- Branch `feat/whatsapp-channel` from `main` after W0 merges, in a worktree under `.worktrees/`.

## Review Focus

1. A retried webhook (same message id, same body) after a crash between `insert_event` and the 200: must be a duplicate, never a second customer turn (Task 3 test).
2. Two tenants with numbers under the same Meta app: a message to tenant B's number must never resolve to tenant A's contact or conversation (Task 3 test with two credentials).
3. Status callback for an unknown provider message id: ignored and acknowledged, no error (Task 3 test).
4. Reply exactly at the 24-hour boundary and one second after: the first sends, the second fails `outside_service_window` with no retry and attempts unchanged (Task 4 test).
5. A tenant admin of tenant A calling the connect route with `?client_id=<tenant B>`: 403, nothing stored (Task 5 test).

---

### Task 1: Adapter, settings and service window (pure code, no database)

**Files:**
- Create: `csa/channels/whatsapp.py`
- Modify: `csa/core/settings.py` (three fields after `TELEGRAM_WEBHOOK_SECRET`)
- Modify: `.env.example` (document the three variables, blank)
- Test: `tests/test_whatsapp.py`

**Interfaces:**
- Produces (all in `csa/channels/whatsapp.py`):

```python
class WhatsAppParseError(ValueError): ...


class WhatsAppDeliveryError(Exception):
    def __init__(self, error_code: str, *, status_code: int | None, retry_after_seconds: int | None) -> None: ...


class WhatsAppStatus(ContractModel):
    provider_message_id: str
    status: Literal["sent", "delivered", "read", "failed"]
    recipient_wa_id: str
    occurred_at: datetime
    error_code: str | None = None


class WhatsAppEvent(ContractModel):
    kind: Literal["message", "status", "unsupported"]
    phone_number_id: str
    display_phone: str | None
    wa_id: str  # sender for messages, recipient for statuses
    profile_name: str | None
    provider_message_id: str  # also the idempotency key
    received_at: datetime
    message: Message | None  # csa.contracts.envelope.Message; attachments stub for non-text
    status: WhatsAppStatus | None
    unsupported_type: str | None

    @property
    def channel_thread(self) -> str:
        return self.wa_id


class WhatsAppDeliveryReceipt(BaseModel):
    status_code: int
    provider_message_id: str


class WhatsAppAdapter:
    @staticmethod
    def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool: ...
    @staticmethod
    def verify_challenge(
        verify_token: str, mode: str | None, token: str | None, challenge: str | None
    ) -> str | None: ...
    @staticmethod
    def parse(raw: bytes | str | Mapping[str, Any]) -> list[WhatsAppEvent]: ...
    @staticmethod
    async def send_text(
        phone_number_id: str, access_token: str, *, to: str, text: str, http_client: httpx.AsyncClient, base_url: str
    ) -> WhatsAppDeliveryReceipt: ...


SERVICE_WINDOW = timedelta(hours=24)


def inside_service_window(now: datetime, last_inbound_at: datetime | None) -> bool: ...
```

- Settings: `WHATSAPP_APP_SECRET: SecretStr | None = None`, `WHATSAPP_VERIFY_TOKEN: SecretStr | None = None`, `WHATSAPP_GRAPH_BASE_URL: HttpUrl = HttpUrl("https://graph.facebook.com/v21.0")`, plus `@property whatsapp_enabled -> bool` (both secrets set). A model validator raises when exactly one of the two secrets is set.

Parse rules: walk `entry[].changes[]` with `field == "messages"`; `value.metadata.phone_number_id` and `display_phone_number`; each `value.messages[]` with `type == "text"` becomes a `message` event with `Message(body_raw=text.body, body_redacted=redact(text.body), language="und")` (use `csa.core.pii.redact` as the Telegram adapter does; copy its language default); other types (`image`, `audio`, `video`, `document`, `sticker`, `location`, `contacts`, `interactive`, `button`, `reaction`, `unknown`) become `kind="message"` with `body_raw=""` and one `Attachment(kind=<mapped>, mime_type=<mime or "application/octet-stream">, file_id=<media id>)` when a media object exists, otherwise `kind="unsupported"` with `unsupported_type`; each `value.statuses[]` becomes a `status` event (`id`, `status`, `recipient_id`, `timestamp` seconds → aware datetime, `errors[0].code` as string if present). `timestamp` is epoch seconds as a string. Anything structurally wrong raises `WhatsAppParseError` with a short reason and no payload echo.

Send: `POST {base_url}/{phone_number_id}/messages` with bearer token, JSON `{"messaging_product":"whatsapp","recipient_type":"individual","to":to,"type":"text","text":{"preview_url":false,"body":text}}`; success returns `messages[0].id`. Error mapping by Graph `error.code`: `130429` → `rate_limited` (retry from `Retry-After` or 60 s); `131047` → `outside_service_window` (no retry); `131026` → `recipient_not_on_whatsapp` (no retry); `190` or HTTP 401 → `token_invalid` (no retry); HTTP 5xx or network → `provider_unavailable` (retry 30 s); anything else → `provider_rejected` with the HTTP status (no retry).

- [ ] **Step 1: Write the failing tests** (`tests/test_whatsapp.py`, no docker marker)

Test names and assertions:

```python
def test_signature_verifies_only_matching_hmac():  # valid header True; wrong secret False; missing header False; "sha1=" prefix False
def test_challenge_echoes_only_for_matching_token():  # returns challenge when mode "subscribe" and token matches; None otherwise
def test_parse_text_message_yields_message_event():  # fixture TEXT_WEBHOOK: phone_number_id "1065…", wa_id "60123456789", provider_message_id "wamid.HBg…", body "Hello", received_at from timestamp "1761523200"
def test_parse_image_yields_attachment_stub():  # kind message, body_raw "", attachments [Attachment(kind="image", mime_type="image/jpeg", file_id="MEDIA1")]
def test_parse_statuses_yield_status_events():  # delivered + read for the same id → 2 events, kinds status, statuses in order
def test_parse_two_entries_two_numbers():  # events carry their own phone_number_id each
def test_parse_rejects_malformed_payload():  # {"object":"whatsapp_business_account"} without entry → WhatsAppParseError; message text with no body → WhatsAppParseError
def test_service_window_boundaries():  # None → False; exactly 24h → True; 24h+1s → False
async def test_send_text_posts_bearer_json_and_returns_message_id():  # httpx.MockTransport asserts URL, Authorization header, body; returns {"messages":[{"id":"wamid.OUT"}]}
@pytest.mark.parametrize(("status", "code", "expected", "retry"), [(429, 130429, "rate_limited", 60), (400, 131047, "outside_service_window", None), (400, 131026, "recipient_not_on_whatsapp", None), (401, 190, "token_invalid", None), (503, None, "provider_unavailable", 30), (400, 100, "provider_rejected", None)])
async def test_send_text_maps_graph_errors(...)
def test_settings_require_both_whatsapp_secrets_together():  # Settings with only one → ValidationError; both → whatsapp_enabled True; neither → False (use the settings fixture pattern from tests/test_console_identity_config.py)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_whatsapp.py -q`
Expected: ImportError on `csa.channels.whatsapp`.

- [ ] **Step 3: Implement `csa/channels/whatsapp.py` and the settings fields** as specified. Model the module on `csa/channels/telegram.py` (`TelegramAdapter.parse/verify/send_message`, `_attachments`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_whatsapp.py tests/test_console_identity_config.py -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
NO_LOCAL_ENV=1 ENV=ci make lint
git add csa/channels/whatsapp.py csa/core/settings.py .env.example tests/test_whatsapp.py
git commit -m "feat(whatsapp): Cloud API adapter, settings and service-window rule"
```

---

### Task 2: Migration 0470 and channel-neutral SQL

**Files:**
- Create: `db/migrations/0470_whatsapp_channel.sql`
- Modify: `apps/gateway/main.py` (`EXPECTED_SCHEMA_VERSION = "0470"`)
- Modify: `db/sql/ingress/insert_conversation.sql`, `db/sql/ingress/get_conversation.sql` (take the channel as a parameter)
- Modify: `csa/channels/ingress.py:542-570` (`_resolve_conversation` passes `"telegram"`)
- Modify: `db/sql/inbox/list_conversations.sql`, `get_conversation.sql`, `get_reply_route.sql`, `get_approval.sql`, `list_approvals.sql`; `db/sql/staff/get_order_conversation.sql`, `get_awaiting_edit.sql`; `db/sql/delivery/get_message.sql`, `mark_delivered.sql`
- Create: `db/sql/ingress/resolve_whatsapp_route.sql`, `db/sql/ingress/resolve_whatsapp_contact.sql`, `db/sql/ingress/touch_last_inbound.sql`, `db/sql/delivery/record_provider_status.sql`
- Test: `tests/test_whatsapp_ingress.py` (schema part), existing `tests/test_ingress.py`, `tests/test_console_inbox.py`

**Interfaces:**
- Migration `0470_whatsapp_channel.sql` (additive; header comment with pre-check "0460 applied" and rollback note):
  - `ALTER TABLE public.conversations ADD COLUMN last_inbound_at timestamptz;`
  - `ALTER TABLE public.outbound_deliveries ADD COLUMN provider_message_id text, ADD COLUMN provider_status text CHECK (provider_status IN ('sent','delivered','read','failed'));`
  - `CREATE INDEX outbound_deliveries_provider_message_idx ON public.outbound_deliveries (client_id, provider_message_id) WHERE provider_message_id IS NOT NULL;`
  - `CREATE UNIQUE INDEX channel_credentials_whatsapp_number_key ON public.channel_credentials (shop_id) WHERE channel = 'whatsapp';` (one number belongs to one tenant across the deployment)
  - No RLS changes (the tables already have `tenant_isolation`); grants unchanged (`csa_app` already has DML on all tables from `0100`).
- `insert_conversation.sql` → `INSERT ... (client_id, contact_id, channel, channel_thread) VALUES (%s, %s, %s, %s) ON CONFLICT ... RETURNING id, contact_id, status, turn_count`; `get_conversation.sql` → `WHERE client_id = %s AND channel = %s AND channel_thread = %s`. The Telegram call site passes `"telegram"`.
- Inbox/staff SQL: replace `c.channel = 'telegram'` with `c.channel IN ('telegram','whatsapp')`; in `get_reply_route.sql` replace `k.channel = 'telegram'` with `k.channel = c.channel`; in `delivery/get_message.sql` replace `conversation.channel <> 'telegram'` with `conversation.channel NOT IN ('telegram','whatsapp')` and add `conversation.last_inbound_at` to the select list; `mark_delivered.sql` gains a `provider_message_id = %s` assignment (Telegram passes `None`).
- `resolve_whatsapp_route.sql` (params `phone_number_id`): returns `endpoint_id, client_id, credential_id, rate_limit_rpm, is_enabled, disabled_reason, access_token, meta, runtime, n8n_webhook_url, deployment_mode` by joining `channel_credentials c` (`c.channel='whatsapp' AND c.shop_id=%s`) → `webhook_endpoints e ON e.credential_id=c.id` → `clients`. Runs on the owner/app pool before the tenant transaction (like `resolve_endpoint.sql`), so it needs a `SECURITY DEFINER` function or the same pattern `resolve_endpoint` uses; follow that function's definition in `db/migrations/` and add `public.resolve_whatsapp_route(text)` in 0470 with identical grants.
- `resolve_whatsapp_contact.sql` (params `client_id, wa_id, display_name`): if exactly one `contacts` row in the tenant has `regexp_replace(phone, '\D', '', 'g') = %(wa_id)s`, link it (insert `contact_identities(channel='whatsapp', external_id=wa_id)` on conflict do nothing) and return it with `first_contact=false`; otherwise insert a new contact with `phone = '+' || wa_id`, `full_name = display_name`, and the identity, returning `first_contact=true`. A known identity (`contact_identities` match) wins before the phone match.
- `touch_last_inbound.sql`: `UPDATE public.conversations SET last_inbound_at = now() WHERE client_id = %s AND id = %s`.
- `record_provider_status.sql`: `UPDATE public.outbound_deliveries SET provider_status = %s WHERE client_id = %s AND provider_message_id = %s AND (provider_status IS NULL OR %s = 'failed' OR provider_status < %s) RETURNING id` with ordering sent < delivered < read implemented by a CASE rank, not string comparison.

- [ ] **Step 1: Write the failing schema tests** (start `tests/test_whatsapp_ingress.py`; fixtures modelled on `tests/test_ingress.py::ingress_context` but inserting a `whatsapp` credential with `shop_id` = phone_number_id and a `webhook_endpoints` row)

```python
async def test_0470_columns_indexes_and_function_exist(app_dsn, pg_container):  # information_schema checks for the two new columns, the partial unique index, provider_status CHECK, and that csa_app can EXECUTE resolve_whatsapp_route
async def test_whatsapp_number_is_unique_across_tenants(...):  # second tenant inserting the same shop_id with channel whatsapp → UniqueViolation
```

- [ ] **Step 2: Run to verify they fail**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_whatsapp_ingress.py -q`
Expected: failures on missing columns.

- [ ] **Step 3: Write the migration and SQL files; bump the version**

- [ ] **Step 4: Run the schema tests, the Telegram ingress and inbox suites**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_whatsapp_ingress.py tests/test_ingress.py tests/test_console_inbox.py tests/test_migrate.py tests/test_health.py -q`
Expected: all pass (Telegram behaviour unchanged with the parameterised conversation SQL).

- [ ] **Step 5: Run the disposable reset test and commit**

```bash
NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_make_reset.py -q -k reset_only_disposable
git add db/migrations/0470_whatsapp_channel.sql apps/gateway/main.py db/sql tests/test_whatsapp_ingress.py csa/channels/ingress.py
git commit -m "feat(whatsapp): migration 0470, channel-neutral inbox SQL, route and contact SQL"
```

---

### Task 3: WhatsApp ingress and gateway routes

**Files:**
- Create: `csa/channels/whatsapp_ingress.py`
- Modify: `apps/gateway/main.py` (construct `WhatsAppIngress` when `settings.whatsapp_enabled`; add `GET`/`POST /channels/whatsapp`)
- Test: `tests/test_whatsapp_ingress.py` (ingress part), `tests/test_console_identity_gateway.py` (route presence)

**Interfaces:**
- Consumes: `WhatsAppAdapter` (Task 1); `IngressError`, `IngressReceipt`, `EndpointRateLimiter`, `_canonical_payload_hash` from `csa/channels/ingress.py` (import them; do not duplicate); `Envelope`, `Contact`, `ConversationRef`, `Meta` from `csa/contracts/envelope.py`; SQL from Task 2.
- Produces:

```python
class WhatsAppIngress:
    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        app_secret: str,
        verify_token: str,
        adapter: type[WhatsAppAdapter] = WhatsAppAdapter,
        rate_limiter: EndpointRateLimiter | None = None,
    ) -> None: ...
    def challenge(self, mode: str | None, token: str | None, challenge: str | None) -> str | None: ...
    async def receive(self, signature_header: str | None, body: bytes) -> list[IngressReceipt]: ...
```

`receive`: body size limit as Telegram (`_MAX_UPDATE_BYTES`); signature check first (403 `IngressError(403, "invalid signature")`, nothing else runs); parse (422 on `WhatsAppParseError`); for each event: `resolve_whatsapp_route(phone_number_id)` (unknown or disabled → log `whatsapp_number_unknown` and skip that event, do not fail the batch); rate limit per endpoint; inside `tx(pool, client_id)`: `message` events → resolve contact (phone match SQL), conversation (`insert_conversation` with `"whatsapp"`), `touch_last_inbound`, build `Envelope(channel=Channel.WHATSAPP, idempotency_key=provider_message_id, contact.external_id=wa_id, ...)`, `insert_event` with `raw.source = {"channel": "whatsapp", "kind": kind, "phone_number_id": ...}`, duplicate detection exactly as Telegram (`get_event_hash`); `status` events → `record_provider_status` (unknown id → no-op); `unsupported` events → insert an `ingress_events` row with the stub envelope (so a retry is a duplicate) and log `whatsapp_unsupported_type`. Return one `IngressReceipt` per message/unsupported event (statuses return none). The n8n runtime branch is not supported for WhatsApp: a tenant whose `runtime == "n8n"` gets 503 `whatsapp_runtime_unsupported`.

Gateway routes (in `create_app`, next to `telegram_webhook`):
- `GET /channels/whatsapp`: 404 unless `settings.whatsapp_enabled`; returns `PlainTextResponse(challenge)` or 403.
- `POST /channels/whatsapp`: 404 unless enabled; content-type check and streamed body limit as Telegram; calls `app.state.whatsapp_ingress.receive(request.headers.get("X-Hub-Signature-256"), body)`; on `IngressError` returns its status (403 for signature, 422 for parse); on success returns 200 `{"ok": true, "accepted": n, "duplicates": m}`. Always 200 once events are persisted, even when every event was a duplicate.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_whatsapp_ingress.py`; fixture `whatsapp_context` yields `(ingress, pool, client_id, phone_number_id, app_secret)` and a helper `signed(body: dict) -> tuple[str, bytes]`)

```python
async def test_text_message_creates_contact_conversation_event_and_last_inbound(...)  # one contact with phone '+60123456789', identity whatsapp, conversation channel whatsapp thread wa_id, ingress_events idempotency_key = wamid, last_inbound_at not null
async def test_duplicate_webhook_is_acknowledged_without_second_event(...)  # same body twice → second receipt duplicate True; one ingress_events row
async def test_bad_signature_has_no_side_effects(...)  # 403; counts of contacts/conversations/events unchanged
async def test_sender_links_to_single_phone_match_only(...)  # pre-existing contact phone '+60 12-345 6789' → linked (first_contact False); two contacts with that phone → new contact created
async def test_two_tenants_two_numbers_stay_isolated(...)  # second credential/endpoint for tenant B; message to B's number → rows only in B
async def test_status_updates_delivery_by_provider_message_id(...)  # seed outbound_deliveries with provider_message_id → delivered then read; unknown id → no error
async def test_image_message_is_enqueued_with_attachment_stub(...)  # event raw envelope has attachments[0].kind image, body_raw ""
async def test_unsupported_type_is_recorded_once(...)  # reaction → one ingress_events row; retry duplicate
async def test_gateway_routes_hidden_when_unconfigured_and_verify_challenge(...)  # TestClient: without secrets GET/POST → 404; with secrets GET with right token → 200 text challenge, wrong → 403
```

- [ ] **Step 2: Run to verify they fail**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_whatsapp_ingress.py -q`
Expected: ImportError on `csa.channels.whatsapp_ingress`.

- [ ] **Step 3: Implement `WhatsAppIngress` and the two routes**

- [ ] **Step 4: Run to verify they pass**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_whatsapp_ingress.py tests/test_ingress.py tests/test_console_identity_gateway.py tests/test_health.py -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
NO_LOCAL_ENV=1 ENV=ci make lint
git add csa/channels/whatsapp_ingress.py apps/gateway/main.py tests/test_whatsapp_ingress.py
git commit -m "feat(whatsapp): signed webhook ingress routed by phone number"
```

---

### Task 4: Delivery branch with the service window

**Files:**
- Modify: `apps/delivery/main.py` (`process_once`, `_load_delivery`)
- Test: `tests/test_whatsapp_delivery.py`

**Interfaces:**
- Consumes: `WhatsAppAdapter.send_text`, `inside_service_window`, `WhatsAppDeliveryError` (Task 1); SQL from Task 2.
- Produces: `_load_delivery` returns `(details, token, channel)`; `details` includes `last_inbound_at`. `process_once` dispatches on `details["channel"]`: `"telegram"` unchanged; `"whatsapp"`: when `not settings.whatsapp_enabled` → `DeliveryFailure("channel_not_configured")` (dead after max attempts, as any failure); when `not inside_service_window(now, details["last_inbound_at"])` → mark failed with `outside_service_window`, `next_attempt_at` far future is not used: instead the row goes straight to `dead` with `last_error = 'outside_service_window'` (reuse `_record_failure` with a new keyword `terminal: bool = False` that sets status `dead` regardless of attempts); otherwise throttle per `channel_thread`, `send_text(...)`, `mark_delivered(status_code, provider_message_id, ...)`. `WhatsAppDeliveryError` codes `outside_service_window`, `recipient_not_on_whatsapp`, `token_invalid`, `provider_rejected` are terminal; `rate_limited` and `provider_unavailable` retry with their seconds. Any channel other than telegram/whatsapp → `unsupported_delivery_channel` (unchanged).

- [ ] **Step 1: Write the failing tests** (`tests/test_whatsapp_delivery.py`; seed a tenant, whatsapp credential + endpoint, contact, conversation (`channel='whatsapp'`, `last_inbound_at`), `messages` row and `outbound_deliveries` row the way `tests/test_console_inbox.py::test_delivery_lock_waits_before_takeover_and_preserves_human_reply` does; `DeliveryWorker(owner_pool, app_pool, httpx.AsyncClient(transport=MockTransport(handler)))`; set the WhatsApp settings via monkeypatch like `tests/test_ingress.py`)

```python
async def test_whatsapp_reply_inside_window_is_sent_and_provider_id_stored(...)  # transport asserts Authorization and body; delivery delivered, provider_message_id 'wamid.OUT', attempt row status 200
async def test_reply_outside_window_is_dead_without_retry_or_provider_call(...)  # last_inbound_at = now - 24h - 1s; status dead, last_error outside_service_window, transport never called, attempts == 1
async def test_reply_at_exact_boundary_is_sent(...)
async def test_rate_limited_retries_with_retry_after(...)  # 429 code 130429 Retry-After 7 → pending, next_attempt_at ≈ now+7s
async def test_token_invalid_is_terminal(...)  # 401 → dead, last_error token_invalid
async def test_unconfigured_deployment_fails_whatsapp_rows_closed(...)  # secrets unset → last_error channel_not_configured; telegram rows unaffected
async def test_takeover_suppression_still_applies_on_whatsapp(...)  # send_authority automation + human owner → suppressed, no provider call
```

- [ ] **Step 2: Run to verify they fail**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_whatsapp_delivery.py -q`
Expected: failures with `unsupported_delivery_channel`.

- [ ] **Step 3: Implement the branch**

- [ ] **Step 4: Run to verify they pass, with the existing delivery and inbox tests**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_whatsapp_delivery.py tests/test_delivery_buttons.py tests/test_console_inbox.py tests/test_console_chat.py -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
NO_LOCAL_ENV=1 ENV=ci make lint
git add apps/delivery/main.py tests/test_whatsapp_delivery.py
git commit -m "feat(whatsapp): outbox delivery with the 24-hour service window"
```

---

### Task 5: Connect a number from the console; inbox shows the channel

**Files:**
- Create: `csa/console/channels.py` (router, registered in the lane A line of the gateway router block)
- Create: `db/sql/console/whatsapp_number_get.sql`, `whatsapp_number_upsert.sql`, `whatsapp_number_disable.sql`
- Modify: `csa/console/inbox.py` (`InboxConversation.channel: Literal["telegram", "whatsapp"]`, optional `channel` query filter on `GET /inbox/conversations`), `db/sql/inbox/list_conversations.sql` (channel filter param)
- Create: `CSA_WebConsole/csa-console/web/lib/channels-api.ts`
- Modify: `CSA_WebConsole/csa-console/web/components/console/ServicesWorkspace.tsx` (a "WhatsApp number" panel shown when `customer_support` is `active`), `components/console/InboxWorkspace.tsx` (channel badge on each row; filter "All channels / WhatsApp / Telegram")
- Test: `tests/test_console_channels.py`, `tests/test_console_inbox.py` (channel literal and filter), `CSA_WebConsole/csa-console/web/tests/channels-transport.test.mjs`, `tests/test_console_contract_snapshot.py` (regenerate `docs/console_openapi.json` with `python -m scripts.export_console_openapi`)

**Interfaces:**
- Routes (prefix `/console/v1/channels`, dependencies `package_gate("customer_support")` and `tenant`; roles: `GET` for every member, `PUT`/`DELETE` for `tenant_admin` and platform admins only, 403 otherwise):
  - `GET /whatsapp` → `WhatsAppNumberOut {connected: bool, display_phone: str | None, phone_number_id_last4: str | None, waba_id: str | None, enabled: bool, callback_url: str | None, connected_at: datetime | None}`; `callback_url` is `f"{settings.PUBLIC_BASE_URL}/channels/whatsapp"` only when `settings.whatsapp_enabled`, else `None` and the frontend says "WhatsApp is not switched on for this deployment yet."
  - `PUT /whatsapp` body `WhatsAppNumberIn {phone_number_id: str (digits, 5–32), waba_id: str (digits, 5–32), display_phone: str (1–32), access_token: SecretStr (20–4096)}` → encrypts the token, upserts the `channel_credentials` row (`channel='whatsapp'`, `shop_id=phone_number_id`, `meta={"waba_id":..,"display_phone":..}`) and its `webhook_endpoints` row (random 32-char `public_id`, name `"WhatsApp <display_phone>"`, `is_enabled=true`), audits `channels.whatsapp.connected` (never the token), returns `WhatsAppNumberOut`. A `phone_number_id` already used by another tenant → 409 `whatsapp_number_in_use`.
  - `DELETE /whatsapp` → sets the endpoint `is_enabled=false` with `disabled_reason='disconnected'`, erases `access_token`, audits `channels.whatsapp.disconnected`; 204.
- Inbox: `GET /inbox/conversations?channel=whatsapp|telegram` filters; omitted means both.
- Frontend `channels-api.ts`: `getWhatsAppNumber(tenantId)`, `connectWhatsAppNumber(tenantId, body)`, `disconnectWhatsAppNumber(tenantId)` using `api()` with CSRF like `services-api.ts`.

- [ ] **Step 1: Write the failing backend tests** (`tests/test_console_channels.py`, `pytest_plugins = ["tests.test_console_sessions"]`, `activate(sessions, tenant, "customer_support")`)

```python
async def test_tenant_admin_connects_number_and_token_is_encrypted_at_rest(...)  # PUT 200; channel_credentials.access_token != raw and decrypts with credential_aad; webhook_endpoints row enabled; audit row without the token; GET shows last4 and waba
async def test_staff_and_reader_cannot_connect_but_can_read(...)  # PUT 403 for tenant_staff and read_only; GET 200
async def test_cross_tenant_connect_is_refused(...)  # tenant A admin with ?client_id=B → 403, no rows in B
async def test_number_in_use_by_other_tenant_is_409(...)
async def test_disconnect_disables_endpoint_and_erases_token(...)
async def test_package_gate_applies(...)  # tenant without customer_support → 403 package_inactive
async def test_inbox_lists_whatsapp_conversations_with_channel_and_filter(...)  # seed one telegram and one whatsapp conversation; no filter → both with channel values; ?channel=whatsapp → one
```

- [ ] **Step 2: Run to verify they fail**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_console_channels.py -q`
Expected: 404s / import errors.

- [ ] **Step 3: Implement the router, SQL and inbox changes; register the router on the lane A line and in `scripts/export_console_openapi.py::contract()`** (the snapshot test builds its own router list)

- [ ] **Step 4: Regenerate the OpenAPI snapshot and run the backend tests**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run python -m scripts.export_console_openapi && NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_console_channels.py tests/test_console_inbox.py tests/test_console_contract_snapshot.py -q`
Expected: all pass.

- [ ] **Step 5: Write the failing frontend test** (`tests/channels-transport.test.mjs`, pattern of `services-api.test.mjs`): `getWhatsAppNumber` hits `/console/v1/channels/whatsapp?client_id=…` for a platform admin and without the query for a member; `connectWhatsAppNumber` sends `PUT` with the CSRF header and never logs the token.

- [ ] **Step 6: Implement `channels-api.ts`, the Services panel and the inbox badge/filter**

Panel copy (writing guide): title "WhatsApp number"; connected state "Connected: +60 12-345 6789 · Messages arrive in the Inbox."; disconnected state with a form (Phone number ID, WhatsApp Business Account ID, Display number, Access token as a password field) and the button "Connect number"; a note "Paste this callback address into your Meta app: <callback_url>" shown to platform admins only. Inbox rows: a small neutral badge "WhatsApp" or "Telegram"; filter select labelled "Channel".

- [ ] **Step 7: Run the isolated frontend check**

Run: `export PATH=/tmp/fourstacks-toolbin:$PATH; npm --prefix CSA_WebConsole/csa-console/web run check:isolated`
Expected: pass.

- [ ] **Step 8: Lint and commit**

```bash
NO_LOCAL_ENV=1 ENV=ci make lint
git add csa/console/channels.py csa/console/inbox.py db/sql/console db/sql/inbox docs/console_openapi.json tests/test_console_channels.py tests/test_console_inbox.py CSA_WebConsole/csa-console/web/lib/channels-api.ts CSA_WebConsole/csa-console/web/components/console/ServicesWorkspace.tsx CSA_WebConsole/csa-console/web/components/console/InboxWorkspace.tsx CSA_WebConsole/csa-console/web/tests/channels-transport.test.mjs
git commit -m "feat(console): connect a WhatsApp number; inbox shows and filters by channel"
```

---

### Task 6: Staging wiring, browser acceptance and documentation

**Files:**
- Modify: `deploy/compose.staging-pipeline.yml` (gateway and delivery: secrets `staging_whatsapp_app_secret` → `WHATSAPP_APP_SECRET`, `staging_whatsapp_verify_token` → `WHATSAPP_VERIFY_TOKEN`, both optional via `${CSA_STAGING_WHATSAPP:-false}` profile-less conditional is not possible in compose, so: add the two secret definitions and attach them unconditionally; the files may contain the literal `disabled`, and the gateway treats a secret file containing exactly `disabled` as unset — implement that in the Settings validator for these two fields only)
- Modify: `deploy/staging-hooks.Caddyfile` (`@whatsapp_ingress { path /channels/whatsapp }` for `GET` and `POST`, proxied like Telegram)
- Modify: `scripts/staging_secrets.py` (the two new secret names, default content `disabled`)
- Modify: `deploy/STAGING_PIPELINE.md` (new section "## 9. Connect WhatsApp (owner and client)")
- Modify: `docs/CONSOLE_API.md` (new "## WhatsApp channel" section: routes, window rule, statuses), `docs/ARCHITECTURE.md` (channel list), `docs/PROGRESS.md` (lane entry)
- Create: `tests/test_whatsapp_browser.py` (Playwright, stubbed upstream like `tests/test_services_browser.py`)
- Test: `tests/test_staging_pipeline.py` (secrets attached; `disabled` default), `tests/test_staging_compose.py::test_staging_caddy_site_adapts_to_exact_host_loopback_and_connector_only` (new matcher)

- [ ] **Step 1: Write the failing compose and Caddy tests**: the rendered gateway and delivery services carry both WhatsApp secrets; `scripts.staging_secrets check` accepts `disabled` for them; the Caddy site proxies `GET`/`POST /channels/whatsapp` and still 404s everything else.

- [ ] **Step 2: Run to verify they fail**, then implement the compose, secrets and Caddy changes and the `disabled` sentinel in `Settings`.

- [ ] **Step 3: Run**: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_staging_pipeline.py tests/test_staging_compose.py tests/test_whatsapp.py -q`
Expected: all pass.

- [ ] **Step 4: Browser acceptance** (`tests/test_whatsapp_browser.py`): stub upstream serves `/console/v1/channels/whatsapp` (disconnected → connected after PUT) and an inbox list with one WhatsApp and one Telegram conversation; the test connects a number as tenant admin (asserting the PUT body never appears in page text afterwards), sees "Connected", opens the Inbox and sees both badges and filters to WhatsApp.

Run: `export PATH=/tmp/fourstacks-toolbin:$PATH; NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_whatsapp_browser.py -q`
Expected: 1 passed.

- [ ] **Step 5: Documentation**

`deploy/STAGING_PIPELINE.md` section 9 must cover, in order: (owner) create the Meta app, add the WhatsApp product, set the callback `https://hooks.fourstacks.my/channels/whatsapp` with the verify token, subscribe to `messages`; (owner) write the two secret files and recreate gateway and delivery with `--no-deps --pull never`; (client) create a Meta Business, verify it, create a WABA and register the number, create a System User with the two permissions, generate a permanent token; (client admin) Services → WhatsApp number → Connect; (test) send "hello" from a phone and confirm it appears in the Inbox. Add the pricing note from the spec (service replies allowance; Meta's rate card is the source). `docs/CONSOLE_API.md` documents the three console routes, the webhook, the service-window failure and the `provider_status` values.

- [ ] **Step 6: PROGRESS entry and final focused run**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_whatsapp.py tests/test_whatsapp_ingress.py tests/test_whatsapp_delivery.py tests/test_console_channels.py tests/test_console_inbox.py tests/test_ingress.py tests/test_staging_pipeline.py tests/test_staging_compose.py tests/test_console_contract_snapshot.py -q`
Record counts and time in the entry; state that the full suite runs on CI.

- [ ] **Step 7: Commit**

```bash
git add deploy docs scripts/staging_secrets.py tests/test_whatsapp_browser.py tests/test_staging_pipeline.py tests/test_staging_compose.py
git commit -m "ops(whatsapp): staging secrets, hooks route, runbook, browser acceptance"
```

Then use superpowers:finishing-a-development-branch (push and open a pull request against `main`). Rollout: apply `0470` with the `migrate` one-shot, recreate gateway, worker, delivery and frontend; WhatsApp stays inert until the two secret files hold real values.
