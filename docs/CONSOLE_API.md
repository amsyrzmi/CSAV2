# Console API contract (v1)

Current release/domain continuation:
[handoff and prompt](./handoffs/2026-10-19-domain-release-next-session.md).
M6 remains incomplete; this contract is not public deployment approval. M6A local
records are not a live provider feed, and the new M6B verifier has no supported
console callback/runtime registration. Mounted legacy marketplace OAuth routes are
separate and must not be exposed as the M6B flow.

The packaged Next.js and gateway artifacts have passed recorded disposable runtime
acceptance; public browser/ingress and staging have not. See
[PROGRESS.md](./PROGRESS.md) for exact evidence and current gates.

M0–M5 plus offline M6A implementation (M4 functionally closed with accepted dev performance exception, 2026-10-16): the retained Next.js presentation calls one CSAV2
gateway through same-origin `/console/v1/*`. The copied Python API is reference only.
See the [migration plan](./handoffs/2026-10-15-console-source-migration-plan.md) and
[frontend adoption instructions](../CSA_WebConsole/csa-console/web/README.md).
Offline M6B TikTok order-page validation is internal adapter groundwork only: it adds no
console routes or live connection claims. Complete provider response-contract verification,
tenant/store-bound credential resolution and safe browser projections remain required;
the normalized internal ecommerce order is not a console response model.
Official v202309 request paths, pagination placement and detail envelope have since been
verified/corrected; line-item quantity/price normalization still blocks provider readiness.
See the [verified scope and remaining response/grant gates](./handoffs/2026-10-15-console-source-migration-plan.md#m6b-official-tiktok-request-verification-2026-10-17-offline-correction).

## Authentication, tenancy and settings

Development reset safety: `make db-reset` is destructive. Its default target is loopback
port **5434**, never production port 5433. A host `psql` client is required by default;
there is no automatic Docker fallback. An explicit `PSQL` override must address the same
database as `DEV_OWNER_DSN`: preflight compares database/system identifiers before any
schema drop, failing closed on missing clients, probe failure or mismatch. Use a named
disposable test container for reset validation; do not reset persistent dev merely to
validate application changes. Keep credentials in the process environment, not command
arguments or copied logs. See [current dev recovery status](./PROGRESS.md).

Human routes are mounted independently of `CONSOLE_API_TOKEN`. Configure
`IDENTITY_DATABASE_URL` using **csa_identity**, `CONSOLE_ALLOWED_ORIGIN` using the exact
web origin, and the frontend server-only `CSA_CONSOLE_API_ORIGIN` using the gateway
origin. `CONSOLE_COOKIE_SECURE=true` is the default; false is accepted only on loopback
dev/CI origins. HTTPS is required elsewhere. No proxy/forwarded headers establish identity.
`CONSOLE_SESSION_IDLE_SECONDS=1800` and `CONSOLE_SESSION_ABSOLUTE_SECONDS=43200` are
bounded and configurable. Missing identity configuration returns an explicit unavailable
response; configured pool failures fail startup/readiness. Both pools close at shutdown.

Configured logging uses a background output writer with a bounded 1,024-event waiting
queue (plus one active write). It does not perform output I/O on the request event loop.
`/healthz` remains process-only; `/readyz` also returns **503** when queued output is
stalled for more than five seconds, overflows, or its writer fails. The readiness reason
contains a secret-free failure code and `dropped_events` count. Overflow/writer errors
are latched until logging is reconfigured or the process restarts; restoring a merely
stalled sink allows readiness to recover if no events were lost. Operators must monitor
readiness and keep the output collector draining; this is not lossless logging under
permanent sink failure. Shutdown draining has a bounded deadline and raises on failure.

An upload's HTTP 500/network error does not prove its transaction rolled back. The
frontend retains the selected files and idempotency key for an **explicit retry**; do
not reselect/re-upload with a new key for an uncertain result. Retrying the same key and
same files returns the committed job, or reports a conflict if contents differ.
Proxy socket-reset and real database lost-acknowledgement cases are regression-tested.

Migration **0270** creates users, sessions, memberships, tokens, throttle buckets and an
append-only audit ledger. The identity role has no application-domain access or role
memberships. Session/token tables allow global identity lookups only to that role;
membership and tenant audit operations set transaction-local user/tenant context. RLS is
enabled without FORCE. Domain mutations and their audit entries share a tenant transaction.

Provision the new login role by explicitly passing `CSA_IDENTITY_PASSWORD` (≥12 characters)
to `scripts.migrate` against an isolated database. It is optional: without it the new role
remains NOLOGIN. Re-running the migration runner with this variable deliberately provisions
or rotates the role password without altering the migration ledger. Keep values out of argv,
source and logs. Bootstrap the first admin deliberately:

```sh
NO_LOCAL_ENV=1 uv run python -m scripts.bootstrap_console_admin \
  --create-first-platform-admin --email admin@example.test --display-name 'Synthetic Admin'
```

The CLI reads only its explicit `IDENTITY_DATABASE_URL` environment value, prompts with
`getpass`, closes bootstrap after the first platform admin, and requires a password change.
No copied accounts, passwords or sessions are imported. Synthetic test identities are not
installed into an existing dev database.

Passwords support Unicode and use salted scrypt (N=131072, r=8, p=1), offloaded from the
event loop with bounded concurrency. Session secrets are 256-bit random opaque values;
only SHA-256 hashes persist.
The `csa_session` cookie is HttpOnly, host scoped, SameSite=Lax and Secure by default.
User disablement, session revocation, idle/absolute expiry and membership changes invalidate
access. Password changes revoke other sessions and rotate the current session and CSRF token.
Login throttling persists per normalized email and direct peer address; forwarding headers are
ignored. Invalid credentials use a generic response.

Every cookie mutation, including login, invite inspection/acceptance and reset requests,
requires an exact configured `Origin` plus double-submit `csa_csrf`/`X-CSRF-Token`; authenticated
CSRF is additionally bound to the session hash. Start with `GET /auth/csrf`. Responses are
`Cache-Control: no-store`; console validation errors omit raw input values. Forced password
change permits only identity inspection, CSRF, logout and password change before domain access.

Actors and roles come from verified sessions. Browser `X-Client-Id`, `X-Actor-Id`,
`X-Actor-Role`, `X-User-Id` and `X-Role` are rejected. An initial session selects its first
active membership; membership switching is deferred. Tenant users cannot select a different
`client_id`. Platform admins must explicitly select an existing tenant using `?client_id=UUID`
for tenant reads/mutations; they cannot invent an actor or grant platform status via JSON.

Machine access is separate: optional `CONSOLE_API_TOKEN` (≥32 chars), bearer authentication,
`X-Client-Id`, and `CONSOLE_MACHINE_CLIENT_IDS` (JSON UUID list). It permits existing domain
**reads only**, for allowlisted tenants. It cannot administer humans, onboard tenants, or
mutate capabilities/KB. Mixed bearer/cookie domain requests are rejected. Never expose the
machine token to browsers. Empty allowlist grants no tenant access.

## Private sandbox assistant (M4)

The `/chat` (platform) and `/portal/chat` (tenant) screens are **Sandbox assistant**,
not a customer inbox. They use the verified session and tenant selector above.
All chat reads additionally require a human session and exact conversation ownership;
even another staff member or platform administrator cannot read someone else's sandbox.
Machine principals are denied. Read-only members can read their existing history but
cannot create, send or archive. Mutations use the existing role/Origin/CSRF checks.

Migration **0290** adds participants, threads and turns with staged tenant RLS, not FORCE.
Each `(client_id, user_id)` has its own newly created sandbox contact; the session user
UUID is never treated as a contact UUID and existing customer contacts are not adopted.
The existing queue requires endpoint routing metadata: the participant has a **disabled,
internal-web-only** endpoint and a `channel='web'` metadata record with no tokens.
No Telegram endpoint, credential, external webhook or infrastructure setting is changed.
There is no external send in the web-delivery branch.

All paths below use `/console/v1/chat` and the existing tenant-selection query:

| Method/path | Body/query | Result |
|---|---|---|
| `POST /conversations` | `{request_key: UUID}` | 201 thread; same actor/tenant/key returns the same thread |
| `GET /conversations` | `before=<next_cursor>` optional | Active owned threads, newest creation first, 20/page |
| `POST /conversations/{id}/turns` | `{request_key: UUID, body: string}` | Atomic ingress/turn/audit commit, 202 `{id,trace_id,duplicate}` |
| `GET /conversations/{id}/turns` | `before=<next_cursor>` optional | Owned turn history, newest ID first, 50/page |
| `POST /conversations/{id}/archive` | `{}` | Idempotent 204, one `chat.archived` audit; no deletion or cancellation |

Both list responses are `{items,next_cursor}`. Cursors are positive signed-bigint
IDs; pass the returned cursor unchanged for older pages. Turn IDs, trace IDs and
request keys remain stable across polling/retry. The browser refreshes loaded history
pages every 2.5 seconds and merges no content from another tenant/conversation.
Archived conversations leave the list but remain readable by their owner if the ID is
known. Accepted work continues; new sends to an archived conversation return 409.
An already accepted same-key replay still returns its original turn after archive.

Text is nonblank, maximum **4000 input characters** and **32 KiB JSON request**.
The server creates Envelope 3.0/`Channel.WEB`, all authority fields and a namespaced
ingress key. Extra fields (including contact, channel, callback, voice and attachments)
are rejected. Deterministic PII redaction happens before storing text in the envelope,
turn or message; placeholders may expand to at most 8000 persisted characters.
This is common-pattern redaction, not a promise to remove all personal information.
The fingerprint distinguishes different original text even if redaction would match.
Same-key changed text returns 409; no client-provided identity becomes authority.

Only `kb_agent.query`, `order_agent.search_products` and `order_agent.product_detail`
can execute for web turns. The shared spoke runner denies everything else **before
agent execution**, including order status/customer history, KB administration,
invoice/payment/marketplace, booking/subscription/ticket, escalation and business writes.
Normal capability switches and grounding checks still apply to allowed queries.
Fact-free account-domain plans return the fixed sandbox-scope refusal instead of
asking for customer invoice/order/booking details, even when no agent task was planned.
Business confirmations are refused too. General policy answers grounded in allowed
KB facts remain available; this restriction does not change Telegram composition.
Runtime metadata, audit, KB gap telemetry and sandbox-local message persistence are
still written; “read-only” refers to business authority, not a read-only SQL transaction.
Callbacks are rejected before pending actions are consumed. No business confirmation
controls are exposed: this is the explicitly selected read-only narrowing of the M4 plan,
not an unimplemented mutation shortcut.

Turn statuses:

- `queued` / `processing`: accepted, not delivered; retries preserve the turn.
- `awaiting_delivery`: an outbox reply exists but is not yet locally available.
- `held`: tenant policy held the draft. The private held draft/reason are retained,
  not returned as a reply, not sent to staff, and not inserted into the expiring
  customer-approval queue. Release controls require a separately designed later slice.
- `available`: local web delivery and its attempt committed atomically. History returns
  only delivered replies, keyed by message ID; polling does not consume or duplicate them.
  This means private local availability, not a Telegram receipt or proof it was read.
- `failed`: terminal ingress/delivery failure or runtime fail-safe. A delivered local
  fail-safe may appear as text, but the turn remains failed; no staff follow-up is promised.
- `no_reply`: the runtime intentionally finished without an outbound reply.

Transient worker/delivery failures retain existing lease/retry/dead-letter behavior.
Web delivery validates the contact/conversation/endpoint/trace/message binding and never
decrypts Telegram credentials or calls an external transport. Telegram delivery remains
at least once; the web local-availability transaction does not change that guarantee.
Archive is audited hide, not retention-approved purge.

The browser distinguishes a **15-second HTTP timeout** (potentially accepted; retain the
same in-memory request key/text and explicitly retry) from a definite 4xx rejection and
from the **90-second turn deadline** (show delayed status, never invent delivery/failure).
Polling can reconcile an uncertain send by its request key. Reconnect only refreshes
history; it never resends. If the page/tenant is left or reloaded, check persisted history
before submitting again; the retained draft/key are not stored in browser storage.
Voice, arbitrary attachments and confirmation actions remain unavailable. KB upload is
a distinct action.

The user explicitly approved guarded dev rollout despite the disclosed full-suite TCP
latency failure. Forward-only **0290** and guarded gateway/worker/delivery/frontend
replacement are complete on isolated dev; persistent dev was not reset. Health/readiness,
login and anonymous-access gates pass. The scope-fix full rerun has **674 passed /
2 latency failures**: ASGI p95 **508.717 ms**, TCP **298.288 ms**, unchanged `<200 ms`.
Lint and disposable-only reset pass. After disclosure of both failures, the user
explicitly approved worker-only replacement; the fix is running.
User-operated refusal, sandbox KB and fresh Telegram checks are metadata-corroborated.
Audited archive, history reload and browser-tab offline/online recovery also pass.
With separate explicit approval, the user privately registered one synthetic read-only
fixture through the normal invite flow. Agent-operated `/portal/chat` checks pass:
creation disabled, send/archive controls absent, owned list 200/empty, another owner's
active and archived histories 404, cross-tenant selection 403. No credentials, invitation
tokens or cookies were inspected. Reading the reader's own pre-existing history remains
test-proven rather than live-replayed because this new account has no history.
The latest uninstrumented focused reader/latency run is **2 passed / 1 failed**:
reader backend denial and ASGI pass (p95 **93.234 ms**); TCP fails (**261.451 ms**).
Instrumented standalone passes do not supersede either this failure or the full run.
The user subsequently accepted **functional M4 closeout with a laptop-dev performance
exception**, carrying latency work forward. Laptop hardware is reported context, not a
proven cause. Benchmarks and `<200 ms` remain unchanged; release performance acceptance
is still required. M5 may begin as a separate staff surface, not expanded sandbox
authority. See [the closeout and M5 prompt](./handoffs/2026-10-16-m4-closeout-m5-start.md).
See [current evidence](./PROGRESS.md). Offline tests/build are not live-browser evidence.
Future replacements still require scoped approval. No public rollout is implied.

## Telegram customer inbox (M5; guarded dev)

The user confirmed Telegram customer conversations only for `platform_admin` (explicit
selected tenant), `tenant_admin` and `tenant_staff`. Readers, machine principals and
anonymous requests are denied, including content reads. Customer-web and stock-changing
order decisions are deferred. Private sandbox histories/contacts and held turn fields
are excluded, including archived or mislabeled sandbox rows. `/inbox` and `/portal/inbox`
are distinct staff surfaces; `/chat` remains owner-private.

All paths use `/console/v1/inbox` with the existing tenant-selection query.

| Method/path | Request | Result |
|---|---|---|
| `GET /conversations` | `before=UUID`, `limit=1..50` (20 default) | `{items,next_cursor}` |
| `GET /conversations/{id}` | — | Ownership, mute and version metadata |
| `GET /conversations/{id}/messages` | `before=UUID`, `limit=1..100` (50 default) | Bounded redacted text, direction, author and actual delivery status |
| `GET /approvals` | `before=bigint`, `conversation_id=UUID`, `include_handled=false`, `limit=1..50` | Pending reply approvals; optionally settled history |
| `POST /conversations/{id}/takeover` | `{request_key: UUID, expected_version: int}` | Audited indefinite human ownership |
| `POST /conversations/{id}/release` | Same | Audited AI ownership; old turns stay suppressed |
| `POST /conversations/{id}/reply` | Same plus `body` (1–2000 chars) | 202 structured receipt with `message_id`, `queued:true` |
| `POST /approvals/{id}/decide` | `{request_key: UUID, decision: approve/reject/edit, text?: string}` | Structured applied/already_handled/expired result, status, queue flag and warnings |

Mutations enforce Origin/CSRF and reject extra fields; JSON is capped at 32 KiB.
Only edits accept/require `text`, with shared 2,000-character/control-character validation.
Takeover is required before a human reply. Actions serialize per conversation; stale
versions or changed content under a used key return 409. Mutation receipts, audit,
message and outbox share the transaction. Explicit same-key retry recovers committed
results after lost acknowledgement. Reads use stable time/ID keyset ordering. No raw
envelopes, routing credentials, attachment payloads or arbitrary source URLs are returned.

Muted new turns skip model/planner/spoke/composer work. Already-running computation may
finish, but escalation, emit, hold, fail-safe and delivery recheck ownership. Taking
over a temporary escalation clears its expiry and establishes indefinite ownership;
the UI offers **Take over indefinitely** for a temporary pause. A late escalation
cannot replace that ownership or re-mute an explicitly released stale turn.
Takeover and release
record a suppression boundary so older turns cannot start a stale send after release.
Takeover skips pending automatic sends; explicit human/reviewed replies remain authorized.
A code-selected handoff acknowledgement requires a matching escalation and is still
subject to the takeover boundary. Rejection/expiry keeps its normal fallback unless
ownership suppresses automatic replies; the decision/audit still commits.

The shared approval transition locks conversation then approval, closes Telegram staff
actions, and writes domain audit/outbox. Console actors use the verified user UUID;
Telegram callback/edit binding stays in the Telegram adapter. No second SQL approval
implementation or generic order-status mutation is introduced. Queued is not delivered.
Existing Telegram order callbacks retain metadata-only completed ingress provenance
for their customer acknowledgement, atomically with that message/outbox, so the stricter
delivery endpoint/trace binding does not reject those replies. This does not add console
order-decision authority or fabricate a Telegram staff identity.

Delivery serializes by conversation, including retries. It holds the conversation lock
through Telegram send and local acknowledgement; takeover waits for a send already in
progress. It cannot recall an accepted remote request. Remote acceptance followed by a
crash before local commit can still duplicate; delivery remains at least once.

Additive **0300** has staged RLS without FORCE. After disposable validation and explicit
user approval it was applied forward-only on isolated dev, without reset, with coordinated
service replacement. Platform-admin browser and synthetic Telegram acceptance are
verified; live reader UI/direct read/CSRF-valid mutation denial replay passes (403).
Representative tenant-admin/staff browser replay also passes: scoped reads, cross-tenant/
sandbox denial, takeover/human reply/release. Fresh decision role matrix retains automated evidence.
Future migration/replacement/live-send scopes require separate approval. See [M5 scope and evidence](
./handoffs/2026-10-17-m5-telegram-inbox.md). M4 latency failures remain recorded under the
accepted laptop-dev exception; unchanged release thresholds apply at M7/M8.
Post-review offline full validation passes **692 tests** plus lint; ASGI/TCP ack p95
**108.284 / 174.623 ms** meet the unchanged `<200 ms` threshold in that run.
Neither offline nor guarded-dev evidence is a guarantee under all laptop loads or
approval for public deployment.

## Authenticated internal workspace (A1)

The `/workspace` (platform) and `/portal/workspace` (tenant) pages use a distinct,
session-authenticated internal staff surface. It does not reuse customer sandbox
conversations, customer contacts, `AgentContext`, Telegram staff actions, model tools
or callback authority. Platform administrators must select a tenant explicitly;
tenant administrators and staff are constrained to their active membership. Readers,
machine principals and anonymous callers are denied. Browser identity/role/tenant
claims are not accepted, and cookie-authenticated writes retain the global Origin/CSRF
checks.

Migration **0370** adds owner-private workspace threads and turns with tenant RLS,
session-bound turn provenance and same-owner idempotency. Reads require both the
transaction tenant and current human owner. Inputs are strict bounded page/entity
hints; an order ID is only an untrusted lookup hint and never grants access. Turn text
is redacted before persistence. No bearer, cookie, CSRF token, unrestricted HR payload
or employee/contact identity mapping is stored.

| Method/path | Body/query | Result |
|---|---|---|
| `POST /workspace/conversations` | `{request_key: UUID}`, tenant query | Creates an owner-private thread; same owner/tenant/key returns the same thread |
| `GET /workspace/conversations` | optional positive `before` sequence, tenant query | Active owner's threads, newest first, 20/page |
| `POST /workspace/conversations/{id}/turns` | `{request_key, body, page_hint}`, tenant query | 201 completed/denied read result; same-key replay is 200 when the original local resource version is unchanged |
| `GET /workspace/conversations/{id}/turns` | optional positive `before` turn ID, tenant query | Owner-only turn history, newest ID first, 50/page |

The only actions are fixed application guidance for supported workspace/business pages
and a single local order read through the same authorized `get_local_order` service as
the manual business console. Order projections identify `source:"local_records"`,
`marketplace_sync:"unavailable"` and an `as_of` timestamp; a missing/foreign order
returns only `resource_unavailable`. Reusing an idempotency key with different input,
or replaying an order result after its resource version changes, returns 409. This is
a synchronous read-only slice: no queued worker, model-generated instruction, business
mutation, approval, HR access, marketplace request or interactive voice service is
introduced.

## Business management (M6A/B; offline local reads and store records)

Only verified human platform administrators (explicit selected tenant), tenant
administrators and tenant staff may read `/console/v1/business`. Readers, machines
and anonymous principals are denied. No marketplace client is called. Store metadata
queries select only local identity/expiry/update columns, never token columns or `meta`;
no credential content is read, decrypted or inspected.
No write routes are added; normal session/tenant controls remain authoritative.

| Method/path | Query | Response |
|---|---|---|
| `GET /business/orders` | `before=<order id>`, `limit=1..50` (20 default), optional `channel` and `shop_id`, tenant query | `{source:"local_records",marketplace_sync:"unavailable",items,next_cursor}` |
| `GET /business/orders/{id}` | optional `channel` and `shop_id`, tenant query | Local order summary including persisted tracking/carrier, channel/store/order IDs and local timestamps |
| `GET /business/products` | `before=<UUID>`, same bounds/tenant query | `{source:"local_catalogue",marketplace_sync:"unavailable",items,next_cursor}` |
| `GET /business/products/{id}` | tenant query | Local product summary, decimal price and local stock/reserved/available quantities |
| `GET /business/stores` | optional `platform=tiktok_shop\|shopee\|lazada`, `before=<record UUID>`, `limit=1..50` (20 default), tenant query | `{source:"local_credential_records",marketplace_sync:"unavailable",items,next_cursor}` |
| `GET /business/stores/{record_id}` | optional `platform`, optional `shop_id` (requires platform), tenant query | Local store identity/expiry and stored lifecycle status; exact tenant/record/platform/store match when bound |

Order/product keysets sort `(created_at,id)` descending; foreign/missing resource or cursor404, bad
UUID/limit422. Monetary values serialize as decimal strings. Neither customer addresses/
contacts nor arbitrary items/attributes/credentials/provider payloads are projected.
Local update timestamps are not marketplace-sync evidence. Product local active status
is not a published listing; stock is not synchronized and is not cross-store authority.
These pages do not claim live tracking, connection health or import completeness.
Tickets, invoices/PDFs, preparation and provider-backed operations remain staged in the
[expanded M6 plan](./handoffs/2026-10-15-console-source-migration-plan.md#m6--business-tabs-integrations-and-staff-preparation).
Implementation is offline; the last approved M5 services have not been replaced.

Order filters match persisted `channel` (`telegram`, `web`, `tiktok_shop`, `shopee`,
`manual`) and `external_shop_id` exactly; they are not provider or connection discovery.
`shop_id` requires a `channel` (400 without it), is 1..100 characters, and stays opaque
(no trimming, wildcard matching or case folding). Invalid channel/empty or oversized store
IDs return422. Same store IDs on different platforms or tenants do not combine.
Filtered detail/cursor reads return404 if the order does not belong to the selected
tenant/channel/store, including a cursor moved to a different store. A missing store
returns an empty local page, not a connection-health claim. Omitting filters preserves
all tenant-local orders, including missing store IDs. Products remain tenant-wide local
catalogue/stock; no store-specific listing or inventory mapping is inferred.

The UI applies order filters explicitly and remounts order paging on changed applied
scope, aborting obsolete requests and starting from newest. Tenant changes reset filter
drafts and all record state. Draft filter edits do not change applied results until Apply.
The applied scope remains visible; inventory is explicitly labelled unaffected.

Store records contain `id`, `platform`, `shop_id`, recorded `expires_at`/`updated_at`,
derived `expiry` (`unknown`, `expired`, `not_expired`), and constant
`authorization:"unverified"`, `permissions:"unknown"`, `provider_reads:"unavailable"`.
They also contain `connection:{status,live_integration:"unavailable"}`. This is a
metadata-only stored lifecycle observation, not a lease, a provider request or proof
of current external authorization. List and detail use the same tenant-scoped
projection; one bounded batch query enriches each page, not one lookup per store.

`connection.status` is one of:

| Status | Local meaning |
|---|---|
| `unsupported` | Platform is not TikTok; this seller lifecycle is not implemented |
| `missing_shop_id` | Exact shop ID is absent |
| `revoked`, `disconnected` | Latest exact-store grant is terminal |
| `expired` | Credential or selected grant expiry timestamp has passed |
| `unverified` | No exact-store grant, future verification time or unknown credential expiry |
| `app_unavailable` | Referenced app is missing, disabled or on a different revision |
| `binding_missing` | No stored shop-binding evidence exists for the selected grant |
| `permissions_missing` | Required order/shop scopes are absent from stored grant metadata |
| `authorization_recorded` | Local app revision/generation/expiry/scope/binding metadata matches; **not verified live** |

The latest exact-tenant/record/platform/shop grant is selected by credential generation,
verification timestamp then grant ID; retained older terminal history cannot mask a
newer authorization. Terminal state precedes expiry; credential-generation mismatch
is disconnected. App revision/state checks precede grant expiry and stored binding/scope
checks. This advisory status may change immediately after reading and cannot authorize
provider dispatch. No token presence/validity, ciphertext integrity, external app registry
availability or fresh scopes are tested.

Connect/Reconnect controls are disabled with no handlers. No Business authorization
mutation or callback route is added, and no synthetic verifier/legacy OAuth path is invoked.
Live authorization requires eligible app/seller access and separate integration/approval;
Partner account registration alone does not prove eligibility or a Sandbox exemption.
Expiry is evaluated against an aware UTC clock with equality treated as expired.
`not_expired` means only a future local timestamp, not valid tokens, granted scopes,
seller authorization or working integration. Null/missing store IDs remain visible as
incomplete local records and cannot satisfy an exact store binding. Arbitrary `meta`,
tokens, app secrets, cipher values, scope values, attempt state/code and credential-presence
checks are excluded. Stored scopes are tested only as booleans inside SQL; values are
never returned. The shop-binding query tests row existence without selecting cipher data.

Store pages use immutable record UUID descending keysets, not update/connection recency.
Only marketplace-channel records are included; Telegram/other channel records are not.
Foreign/missing/platform-mismatched cursor or record404, invalid platform/UUID/limit422.
Detail `shop_id` without platform400; empty/oversized store IDs422. An exact binding
combines server-verified tenant, record UUID, platform and opaque store ID; it resolves
metadata only. This does not implement a credential decryptor or authorize provider calls.
Unknown permissions/verification, revocation and token validity cannot be inferred from
the existing table. No connect/revoke/token-refresh/write endpoints are added.
The UI resets store paging on platform/tenant changes and explicitly separates these
records from verified connections. No live or browser acceptance is implied by build/tests.

## Implemented M1 lifecycle and onboarding

All paths below have prefix `/console/v1`. Models reject extra fields. Login/me/accept/change
return `{user:{id,email,display_name,is_platform_admin,must_change_password}, memberships:
[{client_id,role}], active_client_id, role, csrf_token}`.

| Method/path | Body or query | Permission/result |
|---|---|---|
| GET `/auth/csrf` | — | Obtain/refresh CSRF; recover from stale session cookies |
| POST `/auth/login` | `{email,password}` | Verified session; generic invalid-credential error |
| GET `/auth/me` | — | Verified identity, current roles/memberships |
| POST `/auth/logout` | — | Revoke current session, clear cookies; 204 |
| POST `/auth/password/change` | `{current_password,new_password}` | Change own password (12–1024 chars), rotate session |
| POST `/auth/password/reset` | `{email}` | Generic 202 directing user to admin; no account disclosure |
| POST `/invites/inspect` | `{token}` | Valid invite/reset metadata; never a token-bearing API URL |
| POST `/invites/accept` | `{token,password,display_name?}` | Single-use acceptance, membership/session creation |
| GET/POST `/admin/clients` | POST `{slug,name,timezone?}` | Platform admin; list (up to 200) / audited creation |
| GET `/profile` | Platform selection if applicable | Verified tenant profile |
| POST `/admin/invites` | `{email,display_name,client_id,role}` | Platform admin; issue manual-delivery invitation |
| POST `/team/invites` | Same | Selected tenant admin/platform; no platform role grants |
| GET `/admin/invites` | `client_id=UUID` | Authorized tenant admin/platform; pending/used/revoked/expiry metadata |
| POST `/admin/invites/{id}/renew` | — | Revoke old pending token, issue replacement; same scoped authorization |
| DELETE `/admin/invites/{id}` | — | Revoke invitation; reset-token revocation requires platform admin |
| GET `/team` | `client_id=UUID` | Selected tenant admin/platform; member list |
| PATCH `/team/{user_id}` | query `client_id`; `{role?,is_enabled?}` | Scoped member change; revoke affected tenant sessions; protect last admin |
| GET `/admin/users` | bounded `limit` | Platform admin; safe user metadata |
| PATCH `/admin/users/{id}` | `{is_enabled}` | Platform admin; disable revokes sessions/tokens; protect last admins |
| POST `/admin/users/{id}/reset` | — | Platform admin; revoke sessions/tokens, force change, issue reset |
| POST `/admin/users/{id}/revoke-sessions` | — | Platform admin; audited revocation |
| GET `/admin/audit` | optional `client_id`, bounded `limit` | Platform admin; global events or selected tenant events |

Roles are `platform_admin`, `tenant_admin`, `tenant_staff`, `read_only`. Tenant admins can
manage their tenant memberships and client switches, staff can perform authorized tenant
writes, and read-only users cannot mutate. Membership changes and disablement protect the
last active tenant administrator; platform disablement also protects the last platform admin.

Invitations/reset tokens are hashed, expire after 48 hours, and are consumed under locks.
Raw tokens are returned once to the authorized issuer for **manual delivery**. The UI uses
`/invite#token=...` and immediately clears the fragment before submitting the token in JSON.
There is no email provider integration. Existing credentialed accounts cannot be overwritten
through invitations; cross-tenant account attachment and membership switching remain deferred.
Admin-issued password resets require an account that has already set its password.
A pending invitation returns 409 without revoking its token; renew or accept that invitation
instead. This prevents reset acceptance from bypassing the membership-creation step.

Tenant creation atomically writes the tenant, nine disabled registry rows with current
Pydantic input schemas, and an actor-attributed audit event. It starts no channel, provider,
worker or external operation. Deliberate subscription/tenant enablement is separate.

## Endpoints
### `GET /console/v1/traces?limit=50&before=<ISO time>`
One row per customer turn, newest first. Page by passing the last `started_at` as `before`.
```json
{"trace_id":"…","conversation_id":"…","started_at":"…","finished_at":"…","steps":14,"had_error":false,
 "route":"plan_args","intent":"order","gate_decision":"send","agents":["order_agent"]}
```
`gate_decision`: `send` (sent), `approve` (held for staff), `reject` (shadow mode, nothing sent).

### `GET /console/v1/events?trace_id=<uuid>&after_id=0&limit=100`
Ordered timeline steps. Poll for live updates by passing the largest `id` you have as `after_id`
(every 1–2 s while a trace is open). `trace_id` is optional (omit for a tenant-wide live feed).
```json
{"id":812,"trace_id":"…","conversation_id":"…","node":"run_spoke","agent":"order_agent",
 "status":"ok","duration_ms":41,"summary":{"task_id":"t1","ok":true,"agent_status":"ok"},"created_at":"…"}
```

### Nodes and summary keys
| node | meaning | summary keys |
|---|---|---|
| load_ctx, guard | context loaded, safety check | – |
| signals | configured classifier judged the message | `intent`, `provider` (`llm`, `jev` or callback `policy`) |
| route | policy chose a path | `route` |
| fast_plan / plan_args | plan made | `intent`, `strategy`, `confidence`, `tasks` |
| run_spoke | an agent ran (`agent` set) | `task_id`, `ok`, `agent_status`, `error_code`, `requires_human` |
| collect | results merged into facts | – |
| observe | agent loop looked at the results and chose read-only follow-ups | `step`, `next_tasks`, `loop_stop` |
| handoff | escalation decision | `handoff_required`, `handoff_reason` |
| compose / verify | reply written / checked | `language`, `fact_count`, `verified`, `verify_issue_count` |
| gate | policy verdict | `decision`, `reason`, `risk` |
| hold / emit | reply held for staff / queued to send | `messages` |
| finalize / fail_safe | turn closed / safe fallback used | `error` |

**Agent loop.** After the first agents run, `observe` may start another round of read-only lookups
(for example product details after a search). Follow-up `run_spoke` events have task ids `s<step>-<n>-<agent>`,
so the console can draw rounds: `plan → run_spoke × n → observe (step 1) → run_spoke → observe (step 2)`.
`loop_stop` says why the loop ended: `done`, `rejected:not_read_only`, `rejected:duplicate`,
`rejected:over_budget` or `observe_failed:<error>`. Turns that are not LLM-planned have no `observe` event.

`status` is `error` when the step failed (`summary.error` has the code). Summaries never contain customer text
or secrets; they are codes, counts and names, so they are safe to render.
Semantic rejection details stay in turn state for regeneration; only their count appears on the timeline.
Classifier comparisons are persisted separately in `decision_log`; a console endpoint for those rows
is not implemented yet.

## Capabilities and specialised tabs (LG13)
- `GET /console/v1/capabilities`: each agent has `name`, `is_enabled` (the admin subscription), `client_enabled` (the client's own switch), `active` (true only when both are on), `circuit_state`, `fail_count`, `capability_kind`, and `updated_at`.
- `PATCH /console/v1/capabilities/{name}` with body `{"is_enabled"?: bool, "client_enabled"?: bool}`. A human platform admin controls subscriptions; a human tenant admin/platform admin controls tenant switches. Actor fields such as `changed_by` are rejected. Required safety capabilities cannot be disabled. Changes and before/after audit share one locked transaction.
- `GET /console/v1/agents`: authoritative management catalogue. Each row has `name`, `title`,
  nullable `capability` (the existing registry contract), `implementation`, `readiness`,
  explanatory `detail`, `required_system`, `specialized`, `can_view_specialized`,
  `can_subscribe` and `can_toggle`. Unknown registry agents are explicitly `not_implemented`.
  A missing registry row is not a subscription. Permission flags are session-role-derived;
  machine principals receive no mutation permissions. Required safety switches may be
  enabled if initially off, but cannot be disabled.
- `implementation`: `local`, `prepared_only`, or `not_implemented`. Local invoice reads
  do not imply PDF delivery/issuance; booking/subscription preparation is not provider
  execution. MyInvois is not implemented. No e-commerce adapters are wired into the default
  turn runtime, even though adapter libraries exist.
- `readiness`: `available` for implemented local paths, `setup_required`,
  `configured_unverified`, or `reindex_required`. KB returns the selected embedding model
  and compatible/incompatible active chunk counts. Mixed indexes report both counts;
  only matching-model chunks are retrievable. Only incompatible chunks require reindexing.
  Dev hash retrieval is labelled lexical, not semantic readiness. A configured TEI endpoint
  is **not** a live health check. No URL/credential is exposed or provider contacted.
- `GET /console/v1/tabs/specialized`: the catalogue (`lhdn_myinvois`, `ecommerce`,
  `knowledge_base`, `invoices`, `bookings`, `subscriptions`) includes independent switch,
  implementation, readiness and `can_view`/`can_manage` metadata. `metrics` remains an
  empty compatibility object; hard-coded provider health/compliance metrics are removed.
  Specialised inspection navigation requires a subscription and an implemented surface,
  not a fabricated healthy state. Setup-required/prepared-only labels remain visible.
  `can_manage` governs capability toggling, not future feature-specific permissions.

### Switch enforcement

Production turn dependencies install a fresh tenant-scoped registry check in `SpokeRunner`
before **each** execution attempt, including callback and follow-up tasks. This authorization
does not use the 30-second discovery cache, so another worker's warm cache cannot retain
permission after a committed toggle. Missing/off subscriptions or tenant switches return
an explicitly recorded `capability_unavailable` result. Disabling during retry backoff
prevents another attempt and preserves the previous attempt's failure result.
The existing circuit breaker remains responsible for circuit/probe behavior.

Console KB text-ingest/delete/gap-resolve execution returns 409 when either KB switch is
off. Upload jobs are claimable only when both switches are on; disabled jobs remain queued
without consuming attempts. Work that has already started/been claimed is not cancelled.
This is not a transaction-wide cancellation lock or cross-process breaker reset.

## Runtime visibility (M2)

All endpoints use the same verified tenant selection as the catalogue. All human roles
may read their tenant's metadata; platform admins must explicitly select `client_id`.
The separate machine principal remains read-only and tenant-allowlisted.

- `GET /console/v1/overview`: recorded run counts (`runs`, `succeeded`, `failed`),
  `last_run_at` and `window_hours=24`. Counts describe recorded attempts, not provider
  health, revenue or complete historical workflow usage. Query failures propagate rather
  than being replaced by zero.
- `GET /console/v1/runs?limit=50&before_id=<id>&agent=<optional exact name>`:
  `{items, next_before_id}`; limit 1–200, descending unique run ID keyset pagination.
  Run IDs/cursors must be positive PostgreSQL bigint values (maximum `2^63 - 1`);
  out-of-range values are rejected with 422 before querying run data.
  Pass `next_before_id` unchanged for the next page; null means no older page.
  Each item contains `id`, `trace_id`, `conversation_id`, `agent`, `ok`, `status`,
  `error_code`, `error_category`, `latency_ms` and `created_at`.
- `GET /console/v1/runs/{id}`: the same metadata, or tenant-scoped 404.
  Raw run inputs/outputs, message text and model prompts are deliberately excluded.
- The frontend `/activity` and `/portal/activity` poll every five seconds and reuse
  `/events?trace_id=...&after_id=...` for incremental trace inspection. Errors are
  displayed; no success-shaped empty/zero fallback is rendered. Legacy trace listing
  remains compatible; the new runs screen uses ID keysets rather than its timestamp cursor.

## Knowledge base (LG13 / M3)
- `GET /console/v1/kb/documents?limit=50` lists documents with `version`, `is_active`, `file_name`, `chunk_count`, `kind`, and `embed_model`.
- `POST /console/v1/kb/documents` adds plain text. Body: `{title, content, category?, language?, source_uri?}`. Returns 503 when the embedding service is down.
- `POST /console/v1/kb/uploads`: authenticated multipart repeated `files` fields,
  **1–3 non-empty PDF/DOCX/CSV files, at most 10 MiB each**, UUID `Idempotency-Key`
  header. Returns **202 `{job_id, duplicate_request}`**, not a searchable/success result.
  Same tenant/key and ordered filename/content fingerprint returns the existing job;
  reuse with different files returns 409. Cookies/CSRF and verified tenant context
  are required; machine credentials cannot upload or read/download managed sources.
  Platform/tenant admins and tenant staff may write; readers may view/download.
  Upload/document/source deletion and gap resolution require both KB switches on (409).
  Reconciliation remains available with switches off.
- Parser bounds apply while streaming, before unbounded buffering: **31 MiB total**,
  **8 KiB headers per part**, at most 3 file parts and no non-file fields. Oversized
  input is 413; malformed/incomplete multipart, invalid filenames, empty files, unsupported
  types or invalid signatures/content are 422; non-multipart is 415. No client-selected
  server paths or base64 payloads. Existing extraction resource limits still apply
  (500 PDF pages, 5,000 CSV rows, 50 MiB DOCX expansion); image-only PDFs require OCR.
- Storage uses `KB_UPLOAD_ROOT/<client_id>/<server UUID>.<validated extension>`,
  private directory/file permissions, exclusive partial writes, fsync and atomic rename
  **before** committing queue/file/batch/audit rows. A failed/ambiguous commit reconciles
  against durable metadata rather than blindly unlinking possibly committed files.
  Cancellation finishes publication before releasing the shared tenant lock.
- `GET /console/v1/kb/files?limit=100`: public source metadata (`id,name,media_type,
  size_bytes,document_id,created_at`), never a storage path/hash.
  `GET /console/v1/kb/files/{id}/download` authorizes the tenant and human session again;
  deleted/foreign sources are 404, missing storage is an explicit 503. Responses are
  attachment downloads with no-store/nosniff. Downloads already authorized/open may finish
  during deletion; new downloads cannot.
- `DELETE /console/v1/kb/documents/{id}`: **204**, scoped/audited; missing is 404.
  Removes chunks/document, marks every linked source deleted and unlinks originals.
  `DELETE /console/v1/kb/files/{id}`: **204**, scoped/audited and repeatable for known
  sources; when linked it deletes the shared document and **all linked duplicate sources**.
  Deleting an unprocessed source invalidates it even if a worker already read its bytes.
  Superseded document versions remain visible until explicitly deleted.
- Worker deduplication, final storage and deletion share a tenant transaction advisory
  lock. Hash tombstones fence jobs queued before a content deletion (including legacy
  jobs without file IDs); managed file validity is rechecked after embedding, so deletion
  wins over in-flight/retried work. A deliberate new upload queued after deletion is allowed.
  Locks do not span embedding/network calls. Deletion fences persist; no automatic purge.
- `POST /console/v1/kb/storage/reconcile`: authenticated writer/CSRF, audited;
  returns `{removed_files}`. Explicit recovery after crashes/disk failures: under the
  same tenant lock removes only UUID-pattern orphan/deleted/partial files, retaining
  active DB-backed sources and unknown operator/legacy filenames. No recursive purge,
  legacy import or scheduler is implied. Available via **Reconcile orphaned storage**
  on the source panel; I/O/DB failures propagate and are logged, not reported as success.
- `GET /console/v1/kb/ingest-jobs?limit=20` and `GET /console/v1/kb/ingest-jobs/{id}`:
  curated typed job status (`queued`, `processing`, `done`, `partial`, `failed`),
  attempts/timestamps, public `{name,file_id}` files and per-file `meta.results`
  (`file,status,document_id,chunks,version,error`). Internal paths/requester/arbitrary
  metadata are omitted; errors containing path syntax are redacted. Legacy `upload:`
  document source locations are omitted too. This intentionally replaces raw job JSON.
- `GET /console/v1/kb/gaps` lists unanswered questions.
  `POST /console/v1/kb/gaps/{id}/resolve` atomically marks resolved and audits the action;
  it does not create an answer. Reader controls are absent and backend writes are denied.

Frontend `/knowledge-base` (platform tenant selection) and `/portal/faq` reuse same-origin
cookie/CSRF transport, multipart browser boundaries and stable retry keys. They poll jobs,
sources, versions/model metadata and gaps every five seconds, render explicit unavailable
errors, and confirm destructive actions. Next's proxy buffer is aligned at 31 MiB; the
gateway remains authoritative. No frontend DB connection/file-ingestion shortcut exists.

### Isolated dev rollout prerequisites

Migration **0280** adds upload batches/files/hash tombstones with staged tenant RLS
and application-role grants (no identity access). Gateway readiness requires 0280.
Forward-apply it to the verified isolated dev database **without a reset**, then deliberately
restart the gateway. Set the same writable `KB_UPLOAD_ROOT` for gateway and worker.
Enable `KB_INGEST_ENABLED=true` only on the intended ingestion worker, with both tenant
switches on. Dev/CI can use explicitly labelled lexical `hash-384`; TEI needs deliberate
configuration and compatible model indexing. Queued jobs do not prove a worker/TEI is live.
Do not use production connections/tokens or start the full provider worker just to exercise
uploads. Do not import copied legacy files without an approved inventory/re-index plan.
On 2026-10-15 the user explicitly authorized isolated-dev activation: 0280 was applied
without reset, gateway/frontend restarted, and platform-admin browser upload/download/
duplicate/delete/reconciliation checks passed through configured loopback TEI.
The configured upload root resolved to the repository root, so tasks override it to
`$HOME/.local/share/csav2-dev/kb-uploads` (private, pre-created). No environment-file
edit was made; do not assume generic launch commands inherit this override.

For this already-provisioned dev checkout, use **Tasks: Run Task** in VS Code:

1. **Console: isolated dev gateway**: loopback 18081; guards domain `csa_app` and
   identity `csa_identity` targets at `127.0.0.1:5434/csa`.
2. **Console: isolated dev frontend**: loopback 13000, upstream gateway 18081.
3. **Console: isolated dev KB ingestion**: enables ingestion only for synthetic
   tenant `7f797233-684a-47ab-8222-6e887b6480db`.

After the approved clean-dev recovery, gateway, frontend, full conversation/KB worker
and delivery tasks were restarted; the M1-only KB task was not. Do not start duplicates.
Forward **13000 privately** and open **http://localhost:13000**, then select
the intended tenant on Knowledge base. Recovery recreated both synthetic tenants with
all capabilities disabled and no KB documents; enable deliberately and upload fresh
fixtures. Old on-disk files were preserved, not adopted or reconciled. Stop only the corresponding
task terminal with Ctrl-C. Do not run `make run-dev` merely for this console smoke test.
The frontend task's existing `/tmp/fourstacks-toolbin` Node wrapper is host-local,
not a portable installation.

The ingestion task runs `python -m scripts.dev_kb_ingest --client-id UUID`, not the full
worker. Its guards require dev mode, the exact isolated database/application role,
an existing dedicated absolute root, and credential-free loopback HTTP TEI when configured.
It directly claims only that tenant, including onboarding state; the normal full worker's
active-tenant enumeration is not used. Other tenants' jobs are not serviced by this task.
No bots, delivery, other agent runtime, production connection or external provider startup
is implied. Missing/invalid configuration fails explicitly; a configured TEI failure does
not silently switch to hash embeddings.

An additional **Console: isolated dev conversation worker** task was added during
explicitly authorized Telegram acceptance. Unlike the KB-only task, it runs the
standard conversation worker, KB ingestion and scheduled jobs across eligible isolated-dev tenants;
do not launch it merely for console uploads or alongside another conversation worker.
It guards domain/checkpoint roles and targets at loopback 5434 and uses the same
dedicated upload root and credential-free loopback TEI. Do not run the separate KB-only
task alongside this task. It does not start delivery or a Telegram poller.
**Console: isolated dev delivery** separately guards the same domain/owner targets and
drains the outbox. Neither task changes the existing webhook. Fresh post-recovery KB and
Telegram acceptance subsequently passed for Local Telegram Development; see
[trace and recovery evidence](./PROGRESS.md). The recovered Telegram tenant is `pilot`,
with deliberately enabled KB and explicitly user-approved gate policy `auto`; M1 remains
onboarding/disabled. Normal KB polling processes only `pilot`/`active` tenants, and a
shadow gate intentionally produces no sends, even when KB ingestion succeeds.

## Invoices (LG13)
- `GET /console/v1/invoices?limit=50` lists invoices.
- `GET /console/v1/invoices/{id}/download-link` returns **501** until M6 supplies a real artifact-serving path. The placeholder signing-key fallback is removed. The adopted UI does not offer downloads.

## OpenAPI
The gateway serves `/openapi.json` and `/docs` only outside production; Redoc is disabled.
The safe static [console OpenAPI snapshot](./console_openapi.json) contains console contracts
only and is exported without settings, DB connections or providers:

```sh
NO_LOCAL_ENV=1 uv run python -m scripts.export_console_openapi
```

Pydantic is authoritative. The first slice has explicit typed TypeScript adapters in
`web/lib/identity-contracts.ts` and `identity-api.ts`, checked by TypeScript and transport
tests. Regenerate/review this snapshot with contract changes; automated TypeScript generation
from this versioned artifact is the follow-on strategy rather than fetching machine-protected
runtime OpenAPI during builds. Do not infer authentication from a successful schema build.

## Not built yet (planned, see LG15)
Wave 2/3 preparation libraries are now available under `csa/agents/staged/` (2026-10-15).
Booking/subscription customer agents have local reads and unexecuted requests. Staff email,
web-research and invoice-draft preparation are **not HTTP endpoints**. A future authenticated
adapter must construct `StaffActor` from verified identity, never from user-supplied request
fields, and supply the tenant/contact context. No console source is needed for these libraries;
it is required to map existing UI calls and finish the actual turnover.

Server-sent live stream, customer-web inbox, order decision UI, policy and
agent settings editing, channel/integration connect, notification targets, DLQ/redrive,
usage/billing. The offline Telegram M5 slice above does not complete broader M5–M8 acceptance.
Authentication/onboarding, M2 agents/activity and M3 KB pages are adopted; all project/PM/terminal/Hermes/diagram/workroom/voice and
unmigrated business pages return 404. No legacy API/n8n rewrite remains active.
