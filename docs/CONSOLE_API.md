# Console API contract (v1)

Current release/domain continuation:
[handoff and prompt](archive/handoffs/2026-10-19-domain-release-next-session.md).
M6 remains incomplete; this contract is not public deployment approval. M6A local
records are not a live provider feed, and the new M6B verifier has no supported
console callback/runtime registration. Mounted legacy marketplace OAuth routes are
separate and must not be exposed as the M6B flow.

The packaged Next.js and gateway artifacts have passed recorded disposable runtime
acceptance; public browser/ingress and staging have not. See
[PROGRESS.md](./PROGRESS.md) for exact evidence and current gates.

M0–M5 plus offline M6A implementation (M4 functionally closed with accepted dev performance exception, 2026-10-16): the retained Next.js presentation calls one CSAV2
gateway through same-origin `/console/v1/*`. The copied Python API is reference only.
See the [migration plan](archive/handoffs/2026-10-15-console-source-migration-plan.md) and
[frontend adoption instructions](../CSA_WebConsole/csa-console/web/README.md).
Offline M6B TikTok order-page validation is internal adapter groundwork only: it adds no
console routes or live connection claims. Complete provider response-contract verification,
tenant/store-bound credential resolution and safe browser projections remain required;
the normalized internal ecommerce order is not a console response model.
Official v202309 request paths, pagination placement and detail envelope have since been
verified/corrected; line-item quantity/price normalization still blocks provider readiness.
See the [verified scope and remaining response/grant gates](archive/handoffs/2026-10-15-console-source-migration-plan.md#m6b-official-tiktok-request-verification-2026-10-17-offline-correction).

## Staff HR contract (source implementation, 2026-10-21)

The `/console/v1/hr` router and `/hr` / `/portal/hr` pages implement staff-only
directory, training, recruitment, attendance, leave and local payroll-review
workflows. See the [HR implementation and validation](./hr/PROGRESS.md) and
[setup and operating boundaries](./hr/SETUP.md) before enabling anything.
Ordered migrations 0380-0410 follow workspace migration 0370; 0430 adds the staff email assistant. This is not
persistent migration, deployment, live-provider or payroll-compliance acceptance.

- Only verified human sessions are accepted. Platform admins select a tenant
  explicitly and still need its active membership and explicit HR authority.
  Console roles alone grant no HR business access. Mutations require CSRF and
  reject read-only membership; service transactions recheck session/grants.
- `/context`, `/employees`, `/training-requests` and `/workflows` expose bounded,
  scoped projections. Training and workflow mutations use `Idempotency-Key`,
  current versions and coupled receipts/audit. Supervisor decisions remain human;
  payroll review requires a different reviewer and the exact private snapshot digest.
- `GET /guidance` returns only `{kind:"hr_navigation", actions, source:"current_hr_grants",
  as_of}`. Actions summarize current explicit directory/training grants, not permission
  to a particular employee/request. It reuses HR's transaction-bound session and
  authority checks, accepts only optional `client_id` (required for platform admins),
  and returns an empty action list when a verified tenant member has no such grants.
  No employee references, private areas, records or model input are returned.
  The staff workspace renders fixed guidance and same-origin links to the existing
  HR pages; this is separate from persisted workspace turns and their action union.
  HR navigation accepts only `section=directory|training`; platform links retain the
  explicitly selected `client_id`. URL hints never establish authority. Opening a
  page rechecks resource eligibility, and read-only sessions still cannot mutate.
- `/operations` handles explicitly granted administration and private operations;
  `/records/{area}` and `/private/{area}/{resource_id}` return typed, separately
  authorized records. Candidate text/notes and payroll amounts are absent from
  generic operation receipts.
- HR agents use a separate staff catalog and session context, not customer
  contacts, the customer capability registry or the workspace action union.
  `/agents`, `/agents/contracts`, `/agents/execute` and session-bound
  `/confirmations/{confirmation_id}/confirm` preserve that distinction.
- `/assistant` is disabled by default through `HR_ASSISTANT_ENABLED` and a
  per-tenant switch. When explicitly configured, submitted messages use the
  existing model provider; private HR stores are not model context. Model plans
  cannot grant authority, approve/reject or prepare payroll. Browser voice is
  local dictation/status playback, not LiveKit or a conversational voice ingress.

All HR responses are no-store; validation errors do not echo submitted HR data.
External calendar/email delivery, hiring/ranking, payments, filings and automatic
PCB calculation are not implemented by these local receipts.

### HR access page

The "Who can do what" page (People & HR) reads and changes each member's HR access as
eight switches. The server works out the switches, the "Custom access" rows, the
warnings and the version; the page shows what it returns. Switch ids:
`directory`, `own_requests`, `approves_team`, `hr_team`, `recruitment`,
`attendance`, `payroll_prepare`, `payroll_review`.

- **Owner only.** All three routes need the HR client owner. Anyone else, including
  another tenant's owner, gets 404 `resource_unavailable`, as for
  `/records/members`.
- `GET /access/people?cursor=&q=&team=&has=&reports_to=` returns
  `AccessPeoplePage`: up to 50 people (`items`), an opaque `next_cursor`, the
  `version` stamp, `counts` per switch and the current `warnings`. `q` matches part
  of the name (any case), `team` the department, `has` a switch that is on or
  included, and `reports_to` the manager's employee id. `counts`, `warnings` and
  `version` always cover the whole client, whatever the filters.
- Each person carries `on` (every switch whose rows are all present),
  `included` (a switch whose rows sit inside another switch that is on, which
  cannot be turned off on its own), `custom` (the rows no switch describes, in
  plain words), `team_size` and `is_owner`. Every save keeps the `custom` rows,
  so the owner's own decision rows on `client_owner` are never dropped here.
- `POST /access/changes` takes `AccessChanges`: `batch_id`, the `version` the
  page showed, `dry_run` and 1–50 `changes`, each `{user_id, on, base}`: the
  member's full desired switch set (`on`) and the `on` set the page loaded them
  with (`base`), both at most 8 unique switch ids; user ids are unique. The body
  cap is 65536 bytes.
  - A `version` that no longer matches gives 409 `access_changed` and writes
    nothing (a dry run too). The page reloads, keeps its own edits over each
    member's latest switches, and lets the owner save again.
  - A person whose current switches differ from their `base` is `refused` with
    `changed_since_load` and nothing is written for them (a dry run too). This
    covers a member who was not on the reloaded page.
  - Each person is saved through `/operations`' own path (`access.save`, then
    `private_access.save` per changed area), with idempotency keys derived from
    `batch_id` and `user_id`, so each gets the usual receipt and authority event.
    The version is checked once per batch and each **operation** is its own
    transaction: a batch is not atomic, and neither is one person's save. A save
    that stops after one of the person's operations landed is `refused` with
    `partly_saved`.
  - **Batch ids:** every new submit needs a new `batch_id`. Reuse one only to
    retry the same body after a lost response (the page's dry run and its
    confirm share one).
  - **Replay:** if any person of the batch already has a receipt for that
    `batch_id`, the batch is a retry and the version stamp is not checked. The
    per-person guards still apply: a person with an `access.save` receipt is
    compared with it (the same rows finish any missing area changes and report
    `saved`; different rows are `refused` with `access_changed` and write
    nothing); a person with only area receipts finishes the missing area
    changes; and a person with no receipt is checked against `base` and saved as
    usual.
  - `dry_run: true` writes nothing and returns the outcomes and the `warnings`
    for the proposed state.
  - `AccessChangeResult.results` has one entry per person: `saved`, `unchanged`
    (nothing to write) or `refused` with a `reason` (`not_a_member`,
    `too_many_rows`, `changed_since_load`, `partly_saved`, `access_changed` on a
    replay, or an HR policy code). Other people in the batch still save. It also
    returns the new `version` and the `warnings`.
  - A `cursor` that is not one this API returned gives 409 `invalid_cursor` (404
    `resource_unavailable` for a non-owner), on `/access/people` and
    `/access/recent`.
- Warnings (`AccessWarningOut`, names resolved by the server): `no_approver`,
  `no_manager`, `no_payroll_reviewer`, `manager_without_team` and
  `prepares_and_reviews`. They warn; they never block a save.
- `GET /access/recent?cursor=` returns `AccessEventPage`: up to 50 `access.save`
  and `private_access.save` events, newest first, with an opaque cursor. Each
  `access.save` lists `turned_on` and `turned_off` against that person's previous
  `access.save`; a private-area event gives `area` and `area_enabled`. A member who
  is no longer enabled has `actor_name` or `target_name` `null` ("Former member").
  Each event has a `source`: `page` for a save on this page, or `invitation` for access
  set on an invitation when it was accepted. An `invitation` event carries the `preset`
  (otherwise `null`); its actor is whoever chose the preset (a platform admin shows as
  "FourStacks admin"), and `turned_on` is only what the preset added to what the person
  already had.
- **Access chosen on an invitation** (`InviteIn.hr_preset`, see the invitation table
  below) is applied when the invitation is accepted. If it is not, it waits for the HR
  owner: a preset other than Staff whose chooser is no longer the HR owner (or no longer an
  enabled user with an enabled membership in the tenant), or any preset, Staff included,
  whose apply failed (for example the tenant's HR lock was held for more than 5 s). HR owner
  only, tenant query as for the other `/access` routes:
  - `GET /access/invitations` returns `PendingInvitationPage`: `items` of
    `{token_id, user_id, display_name, preset, chosen_by, chooser_name, accepted_at}`.
    `chooser_name` is "FourStacks admin" for a platform admin and `null` once the chooser
    is no longer an enabled member.
  - `POST /access/invitations/{token_id}/apply` applies the chosen preset now, on top of
    what the person already has (it never turns a switch off), and returns an
    `AccessPersonResult` with `outcome` `saved`. It checks again that the caller is the HR
    owner. Refusals are errors, never a `refused` outcome: 409
    `invitation_already_handled` when it was already applied or dismissed; 404
    `resource_unavailable` for an unknown token.
  - `POST /access/invitations/{token_id}/dismiss` leaves the person's access as it is; 204.
    The same 409 applies.
- The company features ("What the HR assistant can help with") stay on
  `PUT /agents`. They control only the HR assistant and agents; they never limit
  what staff can do in the console, and no switch or warning depends on them.

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

Add a further platform administrator only through the operator command; invites
create tenant members, never platform admins:

```sh
NO_LOCAL_ENV=1 uv run python -m scripts.add_platform_admin \
  --approver-email existing-admin@example.test \
  --email new-admin@example.test --display-name "New Admin" --add-platform-admin
```

The approver re-enters their own password (same limits as login, on a separate counter) and must be an
enabled platform administrator who has changed their initial password. The new
account must change its password at first login. Audit: `identity.platform_admin_added`
(approver as actor) or `identity.platform_admin_add_refused`.

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
authority. See [the closeout and M5 prompt](archive/handoffs/2026-10-16-m4-closeout-m5-start.md).
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
| `GET /conversations` | `before=UUID`, `limit=1..50` (20 default), `channel`, `needs_review=true`, `sender=known\|new`, `q` (2..64 characters) | `{items,next_cursor,needs_review_count}` |
| `GET /conversations/{id}` | — | Ownership, mute and version metadata, plus `contact` with the full `phone` |
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

## WhatsApp channel

Lane A1 (migration `0470`). One Meta app per deployment; each tenant connects its
own WABA number. Setup steps are in
[STAGING_PIPELINE.md section 9](../deploy/STAGING_PIPELINE.md#9-connect-whatsapp-owner-and-client).

Console routes, under `/console/v1/channels` with the usual tenant selection.
Human sessions only (the machine token is refused) and behind the
`customer_support` package gate:

| Method/path | Who | Result |
|---|---|---|
| `GET /whatsapp` | any tenant member, platform admin | `{connected, display_phone, phone_number_id_last4, waba_id, enabled, callback_url, connected_at}` |
| `PUT /whatsapp` | `tenant_admin`, platform admin | Body `{phone_number_id, waba_id, access_token}`. Before storing, the gateway calls Graph `GET {WHATSAPP_GRAPH_BASE_URL}/{phone_number_id}?fields=display_phone_number,verified_name` with that token and stores Graph's `display_phone_number`: 422 `whatsapp_token_rejected` when Graph refuses (auth, permission or not found), 502 `whatsapp_unreachable` on a network error, 429 or 5xx. Nothing is stored or audited on either. The token is sealed with `CREDENTIALS_KEY`, never returned and never logged; audited `channels.whatsapp.connected`. 409 `whatsapp_number_in_use` when another tenant holds the number |
| `DELETE /whatsapp` | `tenant_admin`, platform admin | 204; disables the endpoint, erases the token and releases the number; audited `channels.whatsapp.disconnected` |

`callback_url` is null while the deployment's WhatsApp secrets are unset; otherwise
it is `WHATSAPP_CALLBACK_BASE_URL` (or `PUBLIC_BASE_URL`) plus `/channels/whatsapp`.
The web card shows it to platform admins only.

Webhook (not a console route; 404 while `WHATSAPP_APP_SECRET` and
`WHATSAPP_VERIFY_TOKEN` are unset or `disabled`):

- `GET /channels/whatsapp` answers Meta's handshake (`hub.mode=subscribe`, matching
  verify token, echoes `hub.challenge`); otherwise 403.
- `POST /channels/whatsapp` requires `application/json` (415) and a valid
  `X-Hub-Signature-256` over the raw body (403, no side effects). A broken envelope
  (not a JSON object, no `entry` list, no `metadata.phone_number_id`) is 422,
  oversized bodies 413, an endpoint over its rate limit 429. Inside a signed batch, a
  single message or status the adapter does not understand (for example an unknown
  status such as `deleted`) is skipped with a `whatsapp_item_skipped` warning (no body
  or token) and the rest is stored. Otherwise 200 `{ok, accepted, duplicates}`.
  Messages for an unknown or disconnected number are dropped with a warning; a retried
  message id is a duplicate, never a second turn.
- Every customer message type other than text reaches the coordinator as a live turn
  with an empty body and an attachment stub: media keep their kind, mime type and
  media id (none when Meta sends none); location, contacts, interactive, button and
  other types carry `kind="other"` and `mime_type="application/vnd.whatsapp.<type>"`.
  Reactions, `system`, `unsupported`, `unknown` and `request_welcome` events are
  stored as completed, inert rows.
- The planner is told that WhatsApp cannot take orders or show buttons, so it answers
  in words.

Delivery and statuses:

- Replies are free-form text and are sent only within 24 hours of
  `conversations.last_inbound_at`; later sends fail with `outside_service_window`.
- Terminal codes (no retry, dead-lettered, staff notified): `outside_service_window`,
  `recipient_not_on_whatsapp`, `token_invalid`, `provider_rejected`. `rate_limited`
  retries after Meta's delay. `channel_not_configured` while the secrets are unset.
- Inbox messages expose the code as `delivery_error`; the web inbox shows the
  service-window copy from the [writing guide](WRITING_GUIDE.md#errors).
- Meta's status callbacks set `outbound_deliveries.provider_status` to `sent`,
  `delivered`, `read` or `failed`, matched by `provider_message_id`; `failed` is never
  overwritten by a later status. A `failed` callback also stores Meta's error code in
  `outbound_deliveries.provider_error` (for example `131047`, the closed 24-hour
  window; `131026`, undeliverable). Most per-recipient failures arrive this way, after
  the send returned 200.
- Inbox messages carry `provider_status` and `provider_error`. The web inbox labels a
  failed status "not delivered" and shows a plain line: the service-window sentence for
  `131047`, otherwise "Not delivered." with the reason. Dead deliveries with
  `recipient_not_on_whatsapp`, `token_invalid`, `whatsapp_endpoint_unavailable` and
  `channel_not_configured` have their own plain-words lines; codes never reach the
  screen.
- HTTP 429 and Graph codes 130429, 131056, 80007 and 4 retry as `rate_limited`.
- Inbox conversations carry `channel` (`telegram` or `whatsapp`) and the list accepts
  `?channel=telegram|whatsapp`.

### Inbox read fields, filters, search and order

Each conversation also carries these additive fields (no behaviour change for existing
clients):

- `contact`: `display_name` (the stored name, or `null` when it is blank or only the
  contact's own number, as WhatsApp stores when the customer has no profile name),
  `telegram_username` (the customer's Telegram @username without the `@`, from migration `0500`; `null` when they have none, for WhatsApp and for any value that is not 4 to 32 letters, digits or underscores), `phone_masked`
  (last four digits, for example `••••0148`; `null` when no number is stored), `phone`,
  `kind` (`customer`) and `known`. `phone` is `null` in list rows; only `GET /conversations/{id}` returns the full number.
  Takeover, release and reply results carry no contact data, preview or approval count
  (`contact` and `last_message` are `null`), and neither do their stored receipts.
- `known` is true when the contact has at least one order, at least one subscription,
  or a non-blank email or address. Otherwise the sender is new.
- `last_message`: `preview` (first 120 characters of the redacted body, whitespace
  collapsed; the same text the thread shows) and `at`; `null` when there are no messages.
- `pending_approvals`: count of the conversation's approvals that are `pending` and not
  past `expires_at`.

The page also returns `needs_review_count`: the number of conversations in the whole
tenant with at least one such approval. It ignores `channel`, `needs_review`, `sender`
and `q`, so a filtered list shows the same chip count.

List filters, all applied on the server across every page and combined with `channel`:

- `needs_review=true`: only conversations with a pending approval that has not expired.
- `sender=known` or `sender=new`: split by `known` above. Any other value is 422.
- `q`: search over the contact's name, phone and Telegram @username (so `isyah`,
  `aisyah_r` and `@AISYAH_R` all find `aisyah_r`). The text is trimmed, one leading `@` is ignored, and fewer than two characters
  is 422 `search needs at least two characters`; more than 64 characters is 422. Matching
  is case-insensitive and substring; `%`, `_` and `\` match only themselves. Phone numbers
  match on digits only when the text holds two or more digits, so `012 555`,
  `0125550148` and `+60125550148` agree. Message bodies are never searched. The text is
  personal data: it is not written to the audit log, and the gateway's access log drops
  query strings on `/console/` paths.

Order is last activity, newest first: the time of the latest message, or `opened_at`
when there are no messages, then `id` descending. `before` stays the id of the last
conversation on the previous page; the server derives its position from that
conversation's current activity time.

## Workspace assistant

The floating assistant dock (on every console tab) and the tab actions share one
session-authenticated, owner-private surface under `/console/v1/workspace`. It does not
reuse customer sandbox conversations, customer contacts, `AgentContext`, Telegram staff
actions or callback authority. The Staff workspace tab is removed and its routes
redirect. Platform administrators must select a tenant explicitly (`?client_id=`);
tenant administrators and staff are constrained to their active membership. `read_only`
members, machine principals and anonymous callers are denied by the workspace router,
and the dock is hidden for `read_only` and for platform admins without `?client_id=`.
`GET /auth/me` (and the other `MeOut` responses) carries `workspace_assistant`, which
mirrors `WORKSPACE_ASSISTANT_ENABLED`; while it is `false` (the default) the dock is not
rendered for anyone.
Browser identity, role and tenant claims are never accepted, and cookie-authenticated
writes keep the global Origin/CSRF checks. Responses are `Cache-Control: no-store`.

Migration **0370** added owner-private threads and turns (tenant RLS, session-bound
provenance, same-owner idempotency). Migration **0460** adds `console_proposals`
(including the stored `card`, its `origin`, `claim_token` and `claimed_at`), relaxes the turn CHECKs for
the new actions, and creates the `company_directory` and `company_directory_me` views.
Turn text is redacted before persistence. No bearer, cookie, CSRF token, unrestricted HR
payload or employee/contact identity mapping is stored.

| Method/path | Body/query | Result |
|---|---|---|
| `POST /workspace/conversations` | `{request_key: UUID}`, tenant query | Creates an owner-private thread; same owner/tenant/key returns the same thread (200) |
| `GET /workspace/conversations` | optional positive `before` sequence, tenant query | Active owner's threads, newest first, 20/page |
| `POST /workspace/conversations/{id}/turns` | `{request_key, body (1..4000), page_context \| page_hint}`, tenant query | 201 turn with the planned result; same-key replay is 200. `page_context` and `page_hint` together are 422 |
| `GET /workspace/conversations/{id}/turns` | optional positive `before` turn ID, tenant query | Owner-only turn history, newest ID first, 50/page |
| `POST /workspace/proposals` | `{kind, payload, thread_id?}`, tenant query | 201 `ProposalOut` for a tab-initiated action; the kind's executor gates the package and validates the payload before any row exists |
| `GET /workspace/proposals/{id}` | tenant query | The owner's proposal with its stored card and, once executed, its result |
| `POST /workspace/proposals/{id}/confirm` | none, tenant query | 200 `ProposalOut` (see below) |
| `POST /workspace/proposals/{id}/cancel` | none, tenant query | 200 `ProposalOut`; cancelling a requested proposal sets `cancelled` |
| `GET /workspace/people` | `q` (1..80), tenant query | Up to 10 people (see People) |
| `GET /workspace/people/me` | tenant query | The caller's employee link and manager |

`page_context` is `{tab, section?, record?}` where `tab` is one of `overview`,
`services`, `inbox`, `knowledge_base`, `business`, `hr`, `email`, `subscriptions`, `activity`, `chat` and
`record` is `{kind, id, version?}`. The legacy `page_hint` path (fixed page guidance and a
single local order read with `source:"local_records"`, `marketplace_sync:"unavailable"`
and an `as_of` timestamp) is unchanged; reusing a request key with different input, or
replaying an order result after its resource version changes, returns 409. A
missing or foreign order returns only `resource_unavailable`.

### Planner

A turn with `page_context` is planned by one model call that returns exactly one typed
proposal: `answer`, `navigate`, `directory_lookup`, `email_compose`, `email_reply`,
`email_team`, `inbox_summary`, `training_submit`, `inbox_decision` or `clarification`. The planner only proposes; role, package, switch and
record-version gates are enforced afterwards by the turn service and the proposal
registry, so a wrong plan can at worst produce a refused turn or a card the user
declines. It runs only when `WORKSPACE_ASSISTANT_ENABLED=true` (default false; the
gateway then builds the planner from `GROQ_API_KEY` and `GROQ_MODEL`). Without a
configured planner a `page_context` turn returns 503 `planner_unavailable`.

- The prompt receives a server-built summary only: role, package states, the page, a
  redacted record summary (ids, statuses, versions, redacted titles/subjects; an open
  customer conversation also lists its pending held-reply approval ids and statuses), and
  the thread's last 6 stored turns. Each stored turn contributes its (already redacted)
  body, its action and result kind, and for a directory result or clarification the
  employee ids or option values it offered and which one the next message picked.
  Directory listings, names, addresses and record bodies are never sent.
- When the message mentions the user's boss or manager, the server resolves the caller's
  own manager (as `GET /workspace/people/me` does) before planning and passes only the
  manager's `employee_id`, or a reason (`no_employee_link`, `no_manager`,
  `package_inactive`).
- When the message is about training (English or Malay words such as "training",
  "course", "latihan", "kursus"), the server passes `my_employee`: `{linked,
  can_request_training, reason}`, with `reason` one of `hr_inactive`, `no_hr_access`,
  `no_employee_link` or null. When either flag is false the planner answers with the reason
  instead of proposing `training_submit`.
- The dock repeats the originating request in follow-up turns: picking a directory
  candidate sends `Use employee <id> for: <original message>`, and a clarification answer
  sends the option value with its question and the original message.
- A `directory_lookup` without the `hr` package is answered ("not part of the subscribed
  services") rather than refused. An `email_compose` to a directory employee
  (`recipient_employee_id`) requires the `hr` package, on this path and on
  `POST /email/compose`, and is refused with 403 `package_inactive` before any draft is
  written.
- The user's own message is sent to the planner unredacted; the stored turn body is
  redacted before it is persisted.
- A `navigate` to a tab that the console would not offer (the package and role rule of
  `navigationFor`, in `csa/workspace/rules.py` `tab_available`) is turned into an `answer`:
  the package is not active, or the tab is not available to the role.
- Planner failures return 503 `planner_unavailable` (provider timeout, transport or HTTP
  error; retryable) or 502 `planner_invalid_output` (output that is not JSON, or a refusal
  the server cannot name). A plan refused for a named rule is re-planned once (one
  correction per turn, shared with the content correction) and, if refused again, answered
  in plain text (see `docs/EMAIL_ASSISTANT.md`).
- A failure of the email-drafting model (invalid output, provider error or timeout) on an
  `email_compose`, `email_reply` or `email_team` turn returns 503
  `email_model_unavailable`; nothing is drafted or stored.

### Proposals and confirmation

Every mutation is a proposal the user confirms on a card. The kinds are:

- `email_export`: freeze a draft and save it to the user's own Gmail drafts;
- `email_send`: send a draft from the user's own Gmail (see
  [Email management](#email-management));
- `training_submit`: submit a training request. When the card is made (and again at
  confirm) the caller must have their own employee record (409 `no_employee_link`) and the
  `training.submit` grant, plus `training.draft` for a new request (403
  `hr_training_not_permitted`), so a refused submit never leaves a draft behind;
- `inbox_decision`: approve or reject a held customer reply;
- `subscription_action`: a [subscription state change](#subscriptions), requested from
  the Subscriptions tab.

`ProposalOut` is `{id, kind, status, card, result, created_at, expires_at}`; the card
holds `proposal_id`, `kind`, `title`, `summary`, label/value `fields`, `expires_at` and `expected_version`.

Statuses are `requested`, then `confirmed`, then `executed`, or `cancelled` / `expired`.
A proposal lives 15 minutes and is visible only to its owner.

Confirm succeeds only when all of these hold: the caller is the login session that
created the proposal (and the request carries its CSRF token); the status is
`requested`; it has not expired; the stored payload still matches its SHA-256 hash; and,
when `expected_version` is set, the executor's re-read of the record still returns that
version. The package gate is re-run at confirm.

- Confirm takes an exclusive 2-minute claim lease (`claim_token`, `claimed_at`, measured
  on the database clock). A second confirm during the lease gets 409 `proposal_consumed`.
  A stale claim, left by a confirm that died, may be retaken after re-checking expiry and
  the package gate; the executor then runs again and replays its receipt.
- A repeat confirm of an executed proposal returns 200 with the stored result (replay).
- Executors use idempotency keys derived from the proposal id, so the underlying action
  never runs twice. If an executor fails, the caller's own claim is released.
- Navigating to a different tab (a planned turn whose tab differs from the thread's last
  turn) cancels the thread's `requested` proposals on the server. In the browser the dock
  also cancels its requested cards whenever the route's tab changes (sidebar links, back
  button or a `navigate` result), when the tenant changes, and when "New conversation"
  drops them. Logout does not cancel proposals: it ends the login session, and because
  confirm is bound to the session that created a proposal, its requested proposals can no
  longer be confirmed and simply expire.

| Code | Status | Meaning |
|---|---|---|
| `package_inactive` | 403 | The required package is not active (a tenant switch that is off surfaces as 409 `agent_disabled` instead) |
| `proposal_session_mismatch` | 403 | Confirmed from a different login session than the one that created it |
| `proposal_not_found` | 404 | Unknown, foreign or not owned by the caller |
| `proposal_expired` | 409 | Past its 15-minute lifetime; the status is persisted as `expired` |
| `proposal_consumed` | 409 | Already confirmed or cancelled, or another confirm holds the lease |
| `proposal_stale` | 409 | Payload hash or `expected_version` no longer matches |
| `planner_unavailable` | 503 | No planner configured, or the model provider failed |
| `planner_invalid_output` | 502 | The model output failed the strict proposal schema |
| `email_model_unavailable` | 503 | The email-drafting model failed on a compose, reply or team turn; try again |
| `mailbox_not_connected` | 409 | An `email_export` or `email_send` card was asked for while the user's mailbox is not connected; checked when the card is made, not only at confirm |
| `no_employee_link` | 409 | A `training_submit` card was asked for, or a training draft saved, while the caller's login is not linked to an employee record |
| `hr_training_not_permitted` | 403 | A `training_submit` card was asked for without the training grants it needs (`training.submit`, plus `training.draft` for a new request) |

Audit actions carry the actor, tenant, proposal id and `{"kind": ...}` only:
`workspace.proposal.created`, `workspace.proposal.confirmed`,
`workspace.proposal.executed` and `workspace.proposal.cancelled`. A people search writes
`workspace.people.lookup` with details `{"candidates": n}` only, never the query or a name.

### Gates

| Action | Requires |
|---|---|
| Inbox decision | `customer_support` package `active` or `suspended` |
| Training submit | `hr` package active; training proposals created by the assistant (planner or HR chat) also require the tenant's `hr_assistant` switch; tab-originated proposals do not |
| People search and `people/me` | `hr` package active (`people/me` reports `package_inactive` as a `reason`, not an error) |
| Email export, compose and send | `staff_email` package active and `email_settings.enabled` |
| Email my team | The above, the `hr` package, and the HR access switch "approves team" |

### People

`GET /console/v1/workspace/people?q=` returns up to 10 of `employee_id`, `display_name`,
`job_title` and `work_email` from the `company_directory` view, for every tenant staff
member whose tenant has the `hr` package active. Nothing else about a person is exposed.
A best-effort, in-memory throttle allows 60 lookups per minute per login session (429
`people_lookup_rate_limited`); it resets on restart and is not shared across workers.

`GET /console/v1/workspace/people/me` returns `{employee_id, manager, reason}` for the
caller from the caller-only `company_directory_me` view, which is keyed on the caller's
identity and never on an HR grant. `reason` is `no_employee_link`, `no_manager` or
`package_inactive`. The organisation chart is not exposed to colleagues.

### Email compose

`POST /console/v1/email/compose` (201, with the usual email idempotency key) takes exactly
one of `recipient_employee_id` (resolved through the directory; 422
`recipient_unresolved` when the person has no work email) or `recipient_address`, plus a
subject, instructions and an optional language (default `auto`). It returns a model-written draft with
`thread_id` null and `source="model"`, exported through the existing export flow (and so
through an `email_export` proposal). Nothing is sent.

### Email management

All routes sit under `/console/v1/email`, take the tenant `client_id` query, need the
`staff_email` package and the tenant switch, and (except the reads) take an
`Idempotency-Key`. Sending uses the staff member's own Gmail; no scope beyond
`gmail.readonly` and `gmail.compose` is needed. The behaviour and limits are in
[EMAIL_ASSISTANT.md](EMAIL_ASSISTANT.md#sending).

| Route | Body | Result |
|---|---|---|
| `GET /drafts/{draft_id}` | none | `DraftOut` for the owner; 404 otherwise. The console uses it to show recipients and body on a card |
| `PUT /drafts/{draft_id}` | `{to?, cc?, bcc?, subject, body}` | Edited `DraftOut` (`recipient` is the old single-To field) |
| `POST /drafts/{draft_id}/send` | `{version}` | `SendResult` `{outcome, sent_at, gmail_message_id, remaining_this_hour}` |
| `POST /drafts/{draft_id}/release` | none | `DraftOut` back in `draft`, owner only |
| `POST /threads/{thread_id}/reply` | `{instructions?, language? (auto\|en\|ms), cc?, bcc?}` | 201 `DraftOut` addressed by the reply rule; 409 `reply_target_unavailable` when the only target is the owner |
| `POST /sync/more` | none | `Receipt` for the next page of 20 threads; `{"threads": 0, "done": true}` once the mailbox holds 200 threads or cleanup removed the whole page |
| `GET /attention` | none | `AttentionPage` `{as_of, items, skipped}` |

`DraftOut` carries `to`, `cc`, `bcc`, `recipients` (each address with an `outside` flag),
`status` (`draft`, `sending`, `sent` or `exported`), `sent_at`, `needs_staff_input`,
`reply_to_message_id` and `version`. The server computes `version` from the thread,
recipients, subject and body; a client sends it back on `send` and never computes it.
`EmailContext` gains `last_synced_at` and `more_available`.

Email errors:

| Code | Status | Meaning |
|---|---|---|
| `draft_changed` | 409 | The draft changed since the version was read; read it again |
| `draft_already_sending` | 409 | Another send of this draft is in flight |
| `draft_already_sent` | 409 | The draft was sent |
| `draft_already_exported` | 409 | The draft was saved to Gmail drafts and is frozen |
| `draft_needs_staff_input` | 409 | A `[STAFF:` placeholder is still in the draft |
| `send_limit_reached` | 429 | 20 sends in the last 60 minutes; the body has `retry_after` in seconds |
| `send_refused` | 409 | Gmail refused the message with another 4xx; the draft is back in `draft` |
| `mailbox_reauthorization_required` | 409 | Gmail answered 401, or 403 for a reason other than a quota limit; reconnect the mailbox |
| `provider_rate_limited` | 409 | Gmail answered 429, or 403 with the reason `userRateLimitExceeded`, `rateLimitExceeded` or `dailyLimitExceeded`; the draft is back in `draft` |
| `invalid_message` | 409 | Gmail could not accept the message; the draft is back in `draft` |
| `recipients_invalid` | 422 | Not 1 to 20 valid addresses in To, CC and BCC together |
| `send_outcome_unknown` | 502 | Timeout, 5xx, 408, 409, 499 or a 200 without an id. The draft stays `sending`; the body has `message` "Check your Gmail Sent folder before trying again." |
| `draft_not_releasable` | 409 | Release needs the owner, status `sending`, no Gmail id and a claim at least 120 seconds old |
| `send_released` | 409 | A replay of a send key whose outcome was unknown, after the owner released the draft (it is back in `draft`). Gmail is not called; send again with a new key |
| `reply_target_unavailable` | 409 | The only reply target is the mailbox owner |

A send makes at most one Gmail call, with a 25-second deadline and no retry, after
claiming the draft under a per-mailbox lock. Each claim carries a `send_claim_id`, so a
late outcome from a released attempt never changes the row.

`GET /attention` reads stored data only and runs at most 5 analyses. It leaves out
`newsletter_or_promotion`, `spam_or_phishing` and threads whose latest message the owner
sent, and returns up to 10 items. It skips threads it cannot analyse and counts them in
`skipped`. It is refused for `read_only` members. `GET /threads` lists every cached
thread, up to 200. A thread's time is Gmail's `internalDate` for its latest message (the
`Date` header only when that is missing), and a message labelled `SENT` (send-as aliases
included) counts as the owner's.

`email_send` proposal: the card follows the user's own words, never a planner question or
a clicked option, nor text equal to an option any earlier card offered. The first match wins:
a draft word (draft, drafts, draf, save, simpan) or a negated send (don't send, do not send,
never send, jangan hantar) gives an `email_export` card; a send word (send, hantar, kirim)
gives a send card unless a write word (write, tulis, karang) is there and the planner does not
ask to send; an email verb (email, e-mail, e-mel) gives a send card when the planner also asks
to send and the user wrote no write word; anything else is an `email_export` card (see
[EMAIL_ASSISTANT.md](EMAIL_ASSISTANT.md#workspace-assistant)). In the console only the newest
assistant card's options and candidates can be clicked; older cards are history.
Its stored payload is `{draft_id, version}`; the card's title, summary
and fields hold counts only, and so does the `email_export` card now. `expected_version` is
the draft version, so an edit after the card was made makes the confirm return 409
`draft_changed` and nothing is sent. The `email_export` card's `expected_version` is the
same `draft_version`, bound to To, CC, BCC, subject and body, so any edit makes it
`proposal_stale`. The executor uses an idempotency key derived from the
proposal id. A refused confirm returns the send error body above (for example
`send_limit_reached` with `retry_after`). The executed result is `{draft_id, outcome,
sent_at, gmail_message_id, remaining_this_hour}`, with no content.

Planner actions: `email_reply` takes a `thread_id` from the mailbox facts (its To is never
chosen by the model), `email_team` drafts one email to the caller's active direct reports
(refusals `package_inactive`, `not_team_approver`, `no_employee_link`, `no_reports`,
`no_work_email` and `team_too_large` come back as plain answers), and `inbox_summary`
answers from `GET /attention`; its failures are also plain answers, and the stored turn
says only "Inbox summary: N items." Any address the model proposes must appear in the
user's own message; an address seen only in the mailbox can never become a recipient. An
employee id in To, CC or BCC must be in the message, among the ids an earlier directory
turn offered, or `my_manager`; any other id is refused, corrected once, then answered in
plain text.

### HR assistant delegation

`POST /console/v1/hr/assistant` keeps its HR planner and plan replay (`hr_chat_plans` is
still written). A training submit no longer executes: it returns a `ProposalOut` with 201
(a replay of the same request key returns 200 with the same proposal id), to be confirmed
through the workspace proposals API. With the tenant `hr_assistant` switch off the call
returns 409 `agent_disabled`: training proposals created by the assistant also require the
tenant's `hr_assistant` switch (rechecked at confirm); tab-originated proposals
(`POST /console/v1/workspace/proposals`) do not, because the human confirming the card is
the safety boundary. Each stored proposal records its `origin` (`tab` or `assistant`).

## Subscriptions

Lane B1 (migration `0480`). The Subscriptions tab manages a tenant's plans and its
customers' subscriptions. Every state change is a confirm card (the workspace proposal
kind `subscription_action`); the tab never changes a subscription directly. Nothing is
charged: the module records state and dates only.

Routes, under `/console/v1/subscriptions` with the usual tenant selection (the collection
has no trailing slash). Human sessions only (the machine token is refused) and behind the
`subscriptions` package gate (active status only):

| Method/path | Who | Result |
|---|---|---|
| `GET /settings` | any tenant member | `{timezone}`: the tenant's IANA zone (`clients.timezone`), so the tab shows dates on the tenant's calendar |
| `GET /plans?include_archived=false` | any tenant member | `PlanOut[]`: `{code, name, price, currency, billing_interval, features, sort_order, is_active}` |
| `POST /plans` | writers | Body `{code, name, price, currency?, billing_interval?, features?, sort_order?}`; 201. `code` is `[a-z0-9_-]{1,64}`, `price` is a non-negative amount with two decimals, `billing_interval` is `month` or `year`. 409 `plan_exists`; audited `subscriptions.plan.created` |
| `PATCH /plans/{code}` | writers | Any of `name`, `price`, `billing_interval`, `features`, `sort_order`, `is_active` (`false` archives). 404 `plan_not_found`; 409 `plan_in_use` for a changed `billing_interval` while an active or paused subscription is on the plan or has it as `pending_plan` (subscriptions keep their price but renew on the plan's interval; price and other edits stay allowed); audited `subscriptions.plan.updated` |
| `GET` (the collection) | any tenant member | `{items, next_cursor}` of `SubscriptionOut`. Filters `q` (name, email or, when the term reads like a phone number, digits), `status`, `plan`; keyset `before` and `limit` (1-100, default 50) |
| `GET /customers?q=&limit=20` | any tenant member | `CustomerRef[]` (`{id, full_name, phone, email}`): the tenant's contacts to put on a plan, by name or email (`%` and `_` are literal) or, when the term reads like a phone number with 3 or more digits, by phone digits; no `q` lists everyone by name. `limit` 1-50 |
| `GET /{id}` | any tenant member | `{subscription, events}`, events being the timeline. 404 `subscription_not_found` |
| `POST` (the collection) | writers | Body `{contact_id, plan_code, started_at?, renews_at?}`; 201 `SubscriptionOut`, active at the plan's current price. An explicit `renews_at` sets the cycle's day; a defaulted one keeps `started_at`'s day. 422 `plan_unavailable` (missing or archived plan), 422 `renews_at_before_start`, 404 `contact_not_found`; audited `subscriptions.created` |
| `POST /{id}/actions` | writers | Body `{action, reason?, ...}`; 201 `ProposalOut` (the card), `Cache-Control: no-store`. Nothing changes until the same login session confirms the card at `POST /console/v1/workspace/proposals/{id}/confirm` |
| `GET /requests?status=open\|decided` | any tenant member | `{items, next_cursor}` of `RequestOut`, the customer requests staged by the subscription agent, with the decision once one exists |
| `POST /requests/{id}/decide` | writers | Body `{decision: approve\|decline, note?, resume_at?}`; returns the decided `RequestOut` |

"Writers" are `tenant_admin` and `tenant_staff` in their own tenant, and platform admins
who select a tenant with `?client_id=`. Every member of the tenant may read, including
`read_only`; a `read_only` writer attempt and any other tenant's id are 403. The static
paths (`/settings`, `/plans`, `/requests`, `/customers`) are registered before `/{id}`. The Customers tab's
"Add subscription" form uses `GET /customers` to find the customer, then `POST` (the
collection) with an active plan and an optional start date.

### Statuses and flags

`status` is `active`, `paused` or `cancelled` (a database check since `0480`; legacy values
become `cancelled` in the migration). `version` increases on every change and binds a card.
Flags: `cancel_at_period_end` (cancel when `renews_at` passes), `pending_plan` with
`pending_effective_at` (a scheduled plan change) and `resume_at` (a date on which a paused
subscription resumes).

### Actions

`ActionIn` is `{action, reason?, resume_at?, plan_code?, effective_at?, renews_at?, price_override?}`.
A field outside the action's own set is refused (422), so a card never shows less than the
payload carries.

| Action | From | Fields | Effect |
|---|---|---|---|
| `pause` | active | optional `resume_at` (date, after today on the tenant's calendar) | paused; `resume_at` set when given |
| `resume` | paused | none | active; `resume_at` cleared; a lapsed or missing (`NULL`) `renews_at` becomes one billing interval from now |
| `change_plan` | active, paused | `plan_code` (required), `effective_at`, `price_override` | absent or past `effective_at` changes now at the new plan's current price; a future one sets `pending_plan` and `pending_effective_at` |
| `cancel_at_period_end` | active, not already flagged, with a `renews_at` | none | sets `cancel_at_period_end` |
| `cancel_now` | active, paused | none | cancelled immediately; `cancelled_at` set; pending plan, resume date and period-end flag cleared |
| `extend_renewal` | active | `renews_at` (required) | moves `renews_at`; must be after the current one |

Each executed action records one timeline event keyed by the proposal id (`paused`,
`resumed`, `plan_changed`, `plan_change_scheduled`, `cancel_scheduled`, `cancelled`,
`renewal_extended`), so a confirm replayed after commit returns the same receipt rather
than acting twice. The card runs the same checks at create and again at confirm; a
subscription that changed in between fails the confirm with 409 `proposal_stale`. An
immediate `change_plan` card also binds the target plan's price, currency and billing
interval (`expected_version` reads `<version>:<price>:<currency>:<interval>`, for example
`1:59.90:MYR:month`); the executor reads them again under the row lock, so a plan edit
between the card and its execution is 409 `proposal_stale` and nothing is applied. A
scheduled change binds the version only, and its card says the price will be the plan's
price on that day (with today's price for reference).

Refusals (422 unless noted), raised before any proposal row is written and again at confirm:

| Code | Meaning |
|---|---|
| `action_not_allowed` | The current status does not allow the action, the period-end flag is already set, or a period-end cancel has no `renews_at` (the period would never end; extend the renewal or cancel now instead). An approval of `request_cancel` for such a subscription is refused the same way and records no decision |
| `plan_unavailable` | The target plan is missing or archived |
| `plan_unchanged` | `change_plan` to the plan already held, with no override |
| `price_override_requires_immediate_change` | An override on a change scheduled for later |
| `renews_at_not_after_current` | `extend_renewal` to a date that is not later |
| `renews_at_before_start` | A new subscription whose `renews_at` precedes `started_at` |
| `request_has_no_subscription` | A request whose agent payload named no subscription (it can be declined, not approved) |
| `request_subscription_mismatch` | The request's subscription is missing or belongs to another customer |
| `request_has_no_plan` | A change request that names no plan |
| `request_action_mismatch` | The proposal is not the action the request asked for |
| `resume_at_applies_to_pause_only` | `resume_at` on the approval of anything but a pause request |
| `resume_at_not_after_today` | A pause (or a pause approval) whose `resume_at` is today or earlier on the tenant's calendar |
| `request_decided` (409) | The request already has a decision |
| `proposal_stale` (409) | The subscription changed after the card was made, or (immediate `change_plan`) the target plan's price, currency or interval did |

### Price rule

`change_plan` takes the new plan's current price. Editing a plan's price never changes an
existing subscription; the price a subscription pays moves only through a plan change. A
`price_override` needs a non-empty `reason` and applies only to an immediate change; the
timeline records `price_override: true` with the old and new price. A scheduled change is
applied by the due job at the plan's price on the day, not the price quoted when it was
scheduled.

### Request queue

The subscription agent stages customer requests (`request_pause`, `request_cancel`,
`request_change`) without acting on them. `GET /requests?status=open` lists the undecided
ones, `decided` the rest (newest first, keyset `before`, `limit` 1-100).

- Decline writes a decision row (`declined`, optional note) and audits
  `subscriptions.request.declined`.
- Approve makes the server create and confirm the card itself, as the approving user:
  `request_pause` runs `pause` (the approval may add `resume_at`), `request_cancel` runs
  `cancel_at_period_end` and `request_change` runs an immediate `change_plan` to the
  requested plan at its current price. The executor checks that the proposal is exactly
  that action, on the subscription the request names, and that the subscription belongs to
  the requesting customer, so a card made through the workspace proposals route cannot
  approve a request with a different action or for another customer's subscription.
- The approved decision row is written in the same transaction as the subscription
  change (and audited `subscriptions.request.approved` there). A request has one
  decision, so approve, decline and a second approve are mutually exclusive: the first to
  commit wins and the others change nothing and answer 409 `request_decided`. A failed
  approval leaves the request open for any staff member to retry.

### Due job

`subscriptions_due` runs every 5 minutes for each active tenant, with actor
`system:subscriptions_due` as the timeline actor. It changes nothing (zero counts) for a
tenant whose `subscriptions` package is not `active`: a requested or suspended package, or
none, gets no automatic changes, using the same package check as the route gate. Four rules run in this order, each claiming up
to the job's batch size (100) of rows with `FOR UPDATE SKIP LOCKED`:

1. Cancel at period end: an active, flagged subscription whose `renews_at` has passed
   becomes `cancelled` (`cancelled_at` is that date). A scheduled plan change is dropped
   with it, as `cancel_now` does; the event payload is `{cleared_pending_plan}` (`null` when
   there was none). It runs first so a cancelling subscription is never renewed in the same
   pass.
2. Pending plan: a due pending change applies at the plan's current price and clears the
   pending fields (`plan_changed`). An archived or missing pending plan, or a cancelled
   subscription, only clears them (`pending_change_skipped`).
3. Resume: a paused subscription whose `resume_at` is today or earlier resumes, with a lapsed
   or missing `renews_at` set as in a manual resume.
4. Renew: an active, unflagged subscription whose `renews_at` has passed advances to the
   next step of its renewal cycle (`renewed`).

A row moves at most one step per rule per run, so a long-lapsed renewal catches up one
interval per run. Nothing is charged. Renewal and resume arithmetic and the resume date
use the tenant's calendar (`clients.timezone`, default `Asia/Kuala_Lumpur`).

Renewals keep the cycle's day of the month. Each subscription counts its renewals from a
stored anchor (`subscriptions.renewal_anchor`, not exposed by the API) in whole billing
intervals of its plan, so a renewal clamps to the last day only in a shorter month and
returns to the day afterwards (31 Jan, 28 Feb, 31 Mar). A subscription created with an explicit `renews_at` takes that date's day as the cycle's day; a defaulted one keeps `started_at`'s day. An extension moves the cycle to
the chosen date. A resume with a lapsed or missing `renews_at` (manual or by the job)
restarts the cycle from the resume time. A plan change between monthly and yearly, applied
now or by the job, counts the new interval from the end of the current period.

Audit actions: `subscriptions.plan.created`, `subscriptions.plan.updated`,
`subscriptions.created`, `subscriptions.request.approved`, `subscriptions.request.declined`,
plus the `workspace.proposal.*` rows the registry writes for every card.

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
[expanded M6 plan](archive/handoffs/2026-10-15-console-source-migration-plan.md#m6--business-tabs-integrations-and-staff-preparation).
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
| GET `/auth/me` | — | Verified identity, current roles/memberships, `workspace_assistant` flag (dock shown only when true) |
| POST `/auth/logout` | — | Revoke current session, clear cookies; 204 |
| POST `/auth/password/change` | `{current_password,new_password}` | Change own password (12–1024 chars), rotate session |
| POST `/auth/password/reset` | `{email}` | Generic 202 directing user to admin; no account disclosure |
| POST `/invites/inspect` | `{token}` | Valid invite/reset metadata; never a token-bearing API URL |
| POST `/invites/accept` | `{token,password,display_name?}` | Single-use acceptance, membership/session creation |
| GET/POST `/admin/clients` | POST `{slug,name,timezone?}` | Platform admin; list (up to 200) / audited creation |
| GET `/profile` | Platform selection if applicable | Verified tenant profile |
| POST `/admin/invites` | `{email,display_name,client_id,role,hr_preset?}` | Platform admin; issue an invitation, emailed when mail is on |
| POST `/team/invites` | Same | Selected tenant admin/platform; no platform role grants |
| GET `/admin/invites` | `client_id=UUID` | Authorized tenant admin/platform; pending/used/revoked/expiry metadata, and `hr_preset` (`null` for none) |
| POST `/admin/invites/{id}/renew` | — | Revoke old pending token, issue replacement (keeps `hr_preset` and who chose it); same scoped authorization |
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
The UI uses `/invite#token=...` and immediately clears the fragment before submitting the
token in JSON.

**Delivery (B4).** Invite, renew and admin-issued reset return `IssuedTokenOut`:
`{id, token, expires_at, delivery, delivery_note}`. With invitation mail on
(`INVITE_MAIL_ENABLED` and both `SMTP_USERNAME` and `SMTP_PASSWORD` secrets, sent through
the Google Workspace relay `smtp-relay.gmail.com:587` with STARTTLS, 10 s timeout),
the link is emailed after the token commits: `delivery` is `email`, `delivery_note` is
`null` and **`token` is `null`**, so the raw token never leaves the server. Otherwise
`delivery` is `manual` and the raw token is returned once to the authorized issuer, with
`delivery_note` saying why: `mail_off` (mail is not configured, which is normal) or
`send_failed` (the relay refused, timed out or failed; the user and token still exist
once, with no retry). Failures are logged by category only (`smtp_disabled`,
`smtp_auth_failed`, `smtp_refused`, `smtp_timeout`, `smtp_error`), never with the address,
link or body; a message that fails to build is `smtp_error` too. The gateway names itself
in EHLO as `SMTP_EHLO_HOSTNAME` (default `fourstacks.my`). With `INVITE_MAIL_ENABLED` on but
a relay secret missing or empty, startup logs one warning, `console_mail_inactive` with
category `secrets_missing`, and mail stays off. Reset links expire in `Asia/Kuala_Lumpur`
time; invitation links in the tenant's `clients.timezone`, which is checked before the
invitation is written only while mail is on (with mail off an invalid zone does not block
inviting). Renewing an invitation that has no tenant is 404 `pending invitation not found`.

**HR access on an invitation.** `InviteIn.hr_preset` is one of `staff`, `manager`,
`hr_team`, `payroll_preparer`, `payroll_reviewer`, or omitted for no HR access. Any
inviting admin may choose none or `staff`; the other presets only the tenant's HR owner
(403 `hr_preset_requires_hr_owner`, checked again at apply time). A preset on a tenant
without the `hr` package is 422 `hr_not_available`. The preset and who chose it are stored
on the token (migration 0483) and applied when the invitation is accepted; see
`/access/invitations` under "HR access page" for one that must wait for the HR owner.
Existing credentialed accounts cannot be overwritten
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

## Services and packages
Capabilities are grouped into service packages (catalogue in `csa/console/packages.py`; add-ons require `customer_support`). Packages are the management surface; the capability switches below remain the runtime enforcement.

| Package | Kind | Capabilities | Tabs | Sellable |
|---|---|---|---|---|
| `customer_support` | base | `kb_agent`, `ticket_agent`, `escalation_agent`, `order_approval` | inbox, knowledge_base | yes |
| `orders_products` | addon | `order_agent`, `invoice_agent` | business | yes |
| `subscriptions` | addon | `subscription_agent` | subscriptions | yes |
| `bookings` | addon | `booking_agent` | bookings | yes |
| `marketplaces` | addon | `ecommerce_agent` | ecommerce | no |
| `e_invoicing` | addon | `lhdn_agent` | lhdn_myinvois | no |
| `hr` | standalone | none; covers `hr_agent_settings` modules `hr_directory`, `hr_training`, `hr_recruitment`, `hr_attendance`, `hr_payroll`, `hr_assistant` | hr | yes |
| `staff_email` | standalone | none; covers `email_settings` | email | yes |

State is in `console_service_packages` (migration `0450`): `status` is `requested`, `active` or `suspended`, with requester, activator, `reference`, `note` and timestamps. Tenant RLS applies; only `csa_app` has grants. The migration backfills a package as `active` with reference `backfill-0450` when all its member capabilities are `is_enabled` (`hr`: any enabled `hr_agent_settings` row; `staff_email`: `email_settings.enabled`).

- `GET /console/v1/services`: catalogue joined with the tenant's state, including `consistent` and `inconsistencies` (drift between the package record and member switches) per package. `staff_email` also reports `inner_switch`.
- `GET /console/v1/services/active`: `{"packages": [{key, status}]}` for packages that are `active` or `suspended`, used to build navigation; the console shows the Inbox for a suspended Customer Support package and every other gated tab only when its package is active. A platform admin without `client_id` receives `{"packages": []}`.
- `POST /console/v1/services/{key}/request`: tenant admin or platform admin.
- `POST /console/v1/services/{key}/activate` with body `{"reference": str, "note"?: str}`: platform admin. Flips member `is_enabled` and `client_enabled` through the same SQL as the capability toggle; audits `capability.updated` and `service.activated`.
- `POST /console/v1/services/{key}/suspend` with body `{"note"?: str}`: platform admin. Sets `is_enabled=false` only for members not in another active package, and never for `escalation_agent` or `order_approval`.

Errors: 409 on state conflicts (already active, requirements inactive, dependent add-on active); 422 for an unknown or unsellable key; 403 for the wrong role or `human session required` for machine tokens. Audits `service.requested`, `service.activated` and `service.suspended` carry details `{before, after, reference}`; `requested_by` and `activated_by` are user ids.

Gates: a route behind an inactive package returns 403 `package_inactive`, and gates run before role checks, so every role sees it.
- Inbox requires `customer_support` in status `active` **or `suspended`**, so staff can still resolve held replies and escalations while a client is suspended (the required safety capabilities keep running); Business requires `orders_products` or `marketplaces` active.
- All `/kb/*` routes (uploads, files and downloads for every role) require `customer_support`; `/invoices*` require `orders_products`.
- The HR API requires `hr`; the Email API, including the OAuth callback, requires `staff_email`.
- Not gated: `/tabs/specialized`, `/agents`, `/capabilities`, runtime views, sandbox chat, audit, team and settings.

Activation does not flip the HR module switches or the email inner switch; the HR owner and the tenant admin keep control of those. The web navigation shows Inbox, Knowledge base, Business, People & HR and Email to tenant members only when the package is active (platform admins see all); the former Agents page is the Services tab at `/services` and `/portal/services`.

## Capabilities and specialised tabs (LG13)
Packages (see Services and packages) are the management surface; the switches below remain the enforcement.
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

## Recorded sandbox voice

The authenticated `/console/v1/chat/voice` configuration endpoint and
`/console/v1/chat/conversations/{conversation_id}/voice/{request_key}` POST/GET
routes support recorded audio to a reviewable text draft. They do not submit a
chat turn automatically. Submission uses raw audio bytes and a supported audio
Content-Type, with the normal session, CSRF, writer-role and private ownership
checks. Jobs preserve exact-key replay and expose processing/completed/failed/
timeout/expired states. See [configuration, retention and recovery limits](./CONSOLE_VOICE.md).
