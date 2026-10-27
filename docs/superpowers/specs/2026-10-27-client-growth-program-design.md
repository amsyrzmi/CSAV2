# Client growth program: WhatsApp support, Subscriptions, HR access, friendly console, fourstacks.my admins and AWS

Status: design approved for planning on 2026-10-27 (owner: Amsyar). This is a
program-level design: it fixes the decisions shared by six pieces of work and
the detail each piece needs to be planned on its own. Plans are written per
lane under `docs/superpowers/plans/`.

## 1. Intent

One paying client (one tenant) with thousands of customers wants properly run
customer support, including WhatsApp, and a place for their staff to manage the
customers' subscriptions. The owner wants the console to read plainly for
non-technical staff, HR access that a client can switch per staff member, proper
fourstacks.my administrator accounts that use the registered Google Workspace
domain, and hosting on AWS that is cheap today and grows as clients join. Two
developers must be able to build in parallel from separate sessions.

## 2. Goals, non-goals, anti-goals

Goals

- Customers of the client can message on WhatsApp and get the same bounded,
  human-supervised service the Telegram channel gives today.
- Staff handle support in one inbox with ownership, tags, a customer card and
  quick replies, on every channel.
- Staff manage plans and their customers' subscriptions from a Subscriptions tab;
  every change is confirmed and audited; the customer-facing agent's requests
  land in a queue staff decide.
- A tenant admin switches HR features on and off per member with plain-language
  switches, on top of the existing grant model.
- Every label, description and error a staff member sees reads in plain words.
- Platform admins exist on `muhammad.amsyar@fourstacks.my` and
  `danial@fourstacks.my` (more later); the Gmail account becomes break-glass.
- Production runs on AWS in Malaysia for a cost in the low hundreds of US dollars
  a month, with a known step to a larger shape.

Non-goals (this program)

- Payment collection, invoicing or charging for subscriptions. Wanted later; this
  program leaves hooks and builds no billing engine.
- WhatsApp media download, template management UI and proactive campaigns
  (second WhatsApp phase).
- Google sign-in for the console (later step after Workspace OAuth is in place).
- Voice for the assistant (separate follow-on already recorded).
- Kubernetes.

Anti-goals (would make this a failure even if it "works")

- New console roles. Three roles stay; HR authority stays explicit per-member
  grants.
- Any model output executing a mutation without the existing confirm card and
  server-side gates.
- Reseller lock-in for WhatsApp or a shared "visitor" login.
- Secrets in source, compose files, docs or tickets.
- Breaking the existing staging rollout contract (digest-pinned images, additive
  ordered migrations, `/readyz` schema check).

## 3. Constraints (from the repository and the host)

- Repository rules in `AGENTS.md`: psycopg 3 async only; SQL files under `db/sql/`
  through `csa.core.db.q`; Pydantic contracts with `extra="forbid"`; additive
  ordered migrations, RLS never `FORCE`; only `csa/graph/` imports LangGraph;
  `NO_LOCAL_ENV=1 ENV=ci make lint test`; disposable Testcontainers only.
- Frontend: Next.js app under `CSA_WebConsole/csa-console/web/`; navigation is
  built from active packages in `lib/navigation.ts`; actions that mutate go
  through `ProposalCard` when they are assistant-originated and through CSRF-bound
  tab calls otherwise; brand tokens in `ui-guide/fourstacks-tokens.css`.
- Channels: `Channel.WHATSAPP` exists in `csa/contracts/envelope.py` but only
  Telegram has an adapter (`csa/channels/telegram.py`, `TelegramIngress` in
  `csa/channels/ingress.py`, webhook route in `apps/gateway/main.py`, delivery in
  `apps/delivery/main.py`, which raises `unsupported_delivery_channel` for
  anything but Telegram and web).
- Subscriptions: package `subscriptions` (tab key `subscriptions`) exists in
  `csa/console/packages.py`; tables `plans`, `subscriptions`,
  `subscription_requests`, `subscription_events` exist in the baseline but
  nothing writes `subscription_requests` (its status CHECK allows only
  `prepared` and `csa_app` cannot update it). `SubscriptionAgent` records
  `request_pause|request_cancel|request_change` in `agent_preparation_requests`
  (`agent = 'subscription_agent'`, status always `prepared`, `csa_app` has
  SELECT and INSERT only). No console page or route exists; `NavKey` has no
  `subscriptions`. `console_proposals.kind` has a CHECK limited to the three
  existing kinds.
- HR: console roles `tenant_admin | tenant_staff | read_only`; HR authority is
  `hr_grants` (action × scope × fields), `hr_private_grants` (recruitment,
  attendance, payroll) and `hr_workflow_grants`, applied only by the HR client
  owner through `public.hr_admin_apply` (`employee.save`, `access.save`,
  `private_access.save`; `access.save` replaces the member's whole grant set).
  Row-level security on `hr_grants` and `hr_workflow_grants` is self-only, so
  even the owner cannot read another member's rows through `csa_app` today.
  Tenant-level `hr_agent_settings` module switches are a plain update through
  `HrService.configure_agent` (no receipt). The current UI is the "Access
  profile" form in `components/console/HrAutomation.tsx` with profiles self,
  manager, hr, payroll_operator, payroll_reviewer, owner, none.
- Inbox and delivery: `InboxConversation.channel` is `Literal["telegram"]`;
  `outbound_deliveries` has no column for the provider's message id; the order
  agent accepts only `ORDER_CHANNELS` and `orders.channel` has a CHECK without
  `whatsapp`; order confirmation uses inline buttons.
- Schema version `"0460"` is asserted in `apps/gateway/main.py`,
  `tests/test_migrate.py`, `tests/test_hr_persistence.py` and
  `tests/test_health.py` (`max(shipped) == EXPECTED_SCHEMA_VERSION`).
- Identity: `IdentityService.bootstrap` closes after the first platform admin;
  `invite` creates tenant members only; invitation and reset tokens are
  `delivery: "manual"`.
- Host: staging runs on a 14 GB home machine behind a Cloudflare tunnel; one
  Testcontainers suite at a time. Full suites therefore run on CI.
- Migrations apply every not-yet-applied file in filename order
  (`scripts/migrate.py`), and the gateway requires `max(applied) ==
  EXPECTED_SCHEMA_VERSION`.

## 4. Approaches considered

| Piece | Chosen | Rejected and why |
|---|---|---|
| Friendly console | Writing guide + label pass + UI guide rewrite of the shell section to the package model | Full visual redesign: not needed, would stall both feature lanes |
| HR access | Per-member switches mapped onto existing grants, plus tenant module switches | New console roles: duplicates the grant machinery and contradicts "roles are not HR authority" |
| Second platform admin | Audited operator command that adds a platform admin | Re-running bootstrap (closed by design); making a tenant admin "act as platform" (breaks the trust model) |
| Google Workspace | Internal OAuth client for fourstacks.my staff, External client kept for tenants, Workspace mailbox sends invitations and resets | Google sign-in first: more code, and it needs the Workspace OAuth client anyway, so it comes after |
| AWS | One Graviton EC2 running the proven compose files + RDS Postgres + Cloudflare tunnel, Terraform, Malaysia region; ECS Fargate as phase 2 | Fargate now: roughly double the compute cost at this load and a rewrite of the compose operations; Kubernetes: far too much for one tenant |
| WhatsApp | Meta Cloud API directly, modelled on the Telegram adapter | Reseller (BSP): monthly fee and another dependency; unofficial web clients: against Meta terms and unreliable |
| Subscriptions | Console tab on the existing tables with confirm cards and a request queue; no payments | Building billing now: the client has not asked for charging and it would need a payment provider decision |

## 5. Shared foundations (workstream W0, one short change before the lanes split)

Done by one developer on `main` before the lanes branch; it is the only
sequencing point.

1. **Migration number reservation.** Lane A uses `0470`–`0479`, lane B uses
   `0480`–`0489`. Lanes never reference each other's tables. Whoever merges a
   migration sets the expected version to the highest merged version in all
   four places that assert it (`apps/gateway/main.py`, `tests/test_migrate.py`,
   `tests/test_hr_persistence.py`, `tests/test_health.py`); W0 collapses the
   three test literals into one import from the gateway module so later merges
   change one line. The migration runner applies pending lower numbers, so
   merge order does not matter. Record the reservation in `db/migrations/README`
   (create it if absent) in two lines.
2. **Navigation reservation.** Add `NavKey` `"subscriptions"` (label
   "Subscriptions", gate `["subscriptions"]`, `/portal/subscriptions` and
   `/subscriptions`) to `lib/navigation.ts` with no page yet (lane B adds the
   page). Nothing else in `navigation.ts` changes until lane B's label pass.
3. **Writing guide.** `docs/WRITING_GUIDE.md`: sentence case; verbs on buttons;
   say what happens next; no internal codes, table names or acronyms on screen
   (keep raw codes on `ApiError.code` only); one idea per sentence; Malaysian
   English spelling; dates as "27 Oct 2026, 09:15"; money as "RM 59.90". Both
   lanes follow it from their first screen.
4. **Platform admin command.** `scripts/add_platform_admin.py` plus
   `IdentityService.add_platform_admin(approver_email, approver_password,
   email, display_name, password)`: requires `IDENTITY_DATABASE_URL` with the
   `csa_identity` role; the approver proves identity by re-entering their own
   password (verified against `console_users.password_hash`, same throttle as
   login) and must be an existing enabled platform admin; refuses an email that
   already has a user row; creates the user with `is_platform_admin = true`,
   `must_change_password = true`, password via `getpass` (12–1024 chars,
   `hash_password`); writes `console_audit` `identity.platform_admin_added`
   with approver and target ids. A compose profile `admin-add-approved` mirrors
   the existing `admin-bootstrap` guard but takes the expected database host and
   name from two variables (`CSA_IDENTITY_EXPECTED_HOST`,
   `CSA_IDENTITY_EXPECTED_DB`, defaulting to the staging values) so the same
   one-shot runs against RDS on AWS; the role check stays `csa_identity`.
5. **Hot-file rules** (documented in the writing guide's appendix for developers):
   `docs/console_openapi.json` is regenerated on the lane branch as the last commit before merge (`python -m scripts.export_console_openapi`); a new console router must also be registered in `scripts/export_console_openapi.py` (`contract()`), and if the other lane merged first the lane merges `main` and regenerates, never hand-merging the JSON;
   `docs/PROGRESS.md` gets one entry per lane at the top (conflicts are trivial);
   W0 wraps the existing `include_router` calls in `apps/gateway/main.py` in a
   commented "lane registrations" block with one line reserved per lane;
   `csa/console/packages.py` is not touched by either lane.

## 6. Lane A: customer-facing (developer A)

### A1. WhatsApp channel (Meta Cloud API)

What the client sets up (owner runbook, not code): a Meta Business account,
business verification, a WhatsApp Business Account (WABA), one phone number
registered on the Cloud API, a Meta app with the WhatsApp product, a System User
with a permanent token holding `whatsapp_business_messaging` and
`whatsapp_business_management`. Pricing facts the client should know: Meta bills
per message by category; replies inside the 24-hour service window have a free
monthly allowance per number and are billed above it from October 2026; the
exact rate card is Meta's page, not a reseller's. See the sources list at the
end.

Decided design

- **Who owns the Meta app** (owner decision, see section 12): Fourstacks owns
  one Meta app per deployment, as a technology provider. A client keeps its own
  Meta Business and WABA, verifies its business, and registers its phone
  number; the client's number is connected to the Fourstacks app (today by the
  client's admin creating a System User token in their own Business and
  entering it in the console; Embedded Signup is phase 2). Meta therefore sends
  every webhook for every tenant to one callback and signs it with the one app
  secret.
- **Settings** (`csa/core/settings.py`): `WHATSAPP_APP_SECRET: SecretStr | None`
  (webhook signature), `WHATSAPP_VERIFY_TOKEN: SecretStr | None`,
  `WHATSAPP_GRAPH_BASE_URL: HttpUrl = "https://graph.facebook.com/v21.0"`.
  When either secret is unset the WhatsApp route returns 404 and delivery
  refuses WhatsApp rows with `channel_not_configured`. Both are deployment
  secrets (secret files), never shown in the console.
- **Tenant credentials**: a `channel_credentials` row with `channel =
  'whatsapp'`, `shop_id = <phone_number_id>`, `access_token` encrypted with
  `CREDENTIALS_KEY` (same helper the marketplace OAuth uses), `meta` holding
  `waba_id` and `display_phone`. Routing from webhook to tenant is by the
  payload's `metadata.phone_number_id`, looked up in `channel_credentials`
  (unique on `(channel, shop_id)` for `whatsapp`). A `webhook_endpoints` row
  per number is still created (name "WhatsApp <display_phone>", a random
  `public_id` that is never used for routing) because `ingress_events`,
  `outbound_deliveries` and the inbox reply route bind through `endpoint_id`.
  Inbox and delivery SQL that hardcode `channel = 'telegram'` widen to the two
  customer channels. The console exposes this on the Services
  tab as a "WhatsApp number" card (connect with phone number id, WABA id and
  token; status; disconnect) for `tenant_admin` and platform admins. The card
  never shows the app secret or verify token; the owner configures the callback
  once in the Meta app.
- **Contact matching**: the sender's `wa_id` is an E.164 number. If exactly one
  contact in the tenant has the same normalised `phone`, the message is linked
  to it and a `contact_identities` row (`channel = 'whatsapp'`) is added;
  otherwise a new contact is created with that phone. The customer card and
  the agent then see the client's existing subscription customers.
- **Ingress** (`csa/channels/whatsapp.py`, `WhatsAppAdapter.parse/verify/send_text`
  and `WhatsAppIngress` in `csa/channels/ingress.py` sharing the existing
  contact resolution, dedupe and queue steps):
  `GET /channels/whatsapp` answers the Meta verification handshake
  (`hub.mode=subscribe`, verify token, echo `hub.challenge`);
  `POST /channels/whatsapp` verifies `X-Hub-Signature-256` (HMAC SHA-256 of the
  raw body with the app secret, constant-time compare), rejects bad signatures
  with 403 and no side effects, accepts text messages into the envelope as
  `Channel.WHATSAPP` with `channel_thread = <customer wa_id>`,
  `idempotency_key = <message id>`, records `sent/delivered/read/failed` status
  callbacks against the outbox row by provider message id (new column
  `outbound_deliveries.provider_message_id`, lane A migration), and enqueues
  non-text message types with an attachment stub (type, mime, media id) exactly
  as Telegram voice notes are enqueued today, so the coordinator decides the
  reply (planning ruling 2026-10-27: no fixed canned reply in the ingress).
  Always return 200 within Meta's timeout once the event is persisted;
  reprocessing a retried event is a duplicate, never a second customer turn.
- **Phase 1 scope on WhatsApp** (owner decision, see section 12): questions and
  answers from the knowledge base, tickets, escalation to the human inbox, and
  subscription reads and requests. Ordering with inline buttons stays on
  Telegram; the order agent keeps rejecting WhatsApp, and the planner is told
  the channel cannot take orders so it answers in words. Interactive button
  replies are treated like any other non-text type in phase 1. Phase 2 adds
  interactive messages and the `orders.channel` CHECK value.
- **Service window**: a per-conversation `last_inbound_at` (new column on
  `conversations`, lane A migration) feeds `inside_service_window(now, last_inbound_at)`
  (24 hours). Delivery of free-form text outside the window fails with
  `outside_service_window`, is not retried, and the conversation shows a plain
  notice in the inbox ("WhatsApp only allows replies within 24 hours of the
  customer's last message"). Template sends are phase 2.
- **Delivery** (`apps/delivery/main.py`): branch on `details["channel"]`:
  `whatsapp` sends through `WhatsAppAdapter.send_text(phone_number_id, token,
  to, text)`; maps Graph errors to `DeliveryFailure` codes (`rate_limited` with
  retry-after, `recipient_not_on_whatsapp`, `token_invalid` → suppress and
  notify staff); the per-thread throttle mirrors Telegram's. Takeover and
  handoff suppression stay as they are.
- **Inbox**: `InboxConversation.channel` widens to `Literal["telegram",
  "whatsapp"]`; conversation rows show a channel badge ("WhatsApp",
  "Telegram") and filters include channel. Staff replies from the inbox go
  through the existing outbox.
- **Tests**: synthetic webhook fixtures (text, status, retry duplicate, bad
  signature, unknown type), fake Graph transport for delivery, service-window
  unit tests, contract snapshot for the new routes, one Playwright flow
  connecting a number and seeing the badge.

Left to the builder: exact Pydantic shapes for the Graph payloads, retry
backoff numbers, how the attachment stub is displayed.

### A2. Support refinements (after A1 merges)

- **Ownership and assignment**: `conversations.assigned_user_id uuid NULL`
  (FK `console_users`), `conversations.tags text[] NOT NULL DEFAULT '{}'`,
  `conversations.priority text NOT NULL DEFAULT 'normal' CHECK (priority IN
  ('low','normal','high'))`. Routes `POST /console/v1/inbox/conversations/{id}/assign`
  (self or a member of the same tenant), `/tags`, `/priority`; direct tab calls
  with CSRF, audited (`inbox.conversation.assigned` etc.). Filters: mine,
  unassigned, channel, status, tag, priority.
- **Customer card**: `GET /console/v1/inbox/conversations/{id}/customer` returns
  the contact, last 5 orders, active subscriptions (via the existing
  `agents/subscription_list.sql` shape), open tickets. Read-only; package gates
  decide which blocks appear (`orders_products`, `subscriptions`).
- **Quick replies**: table `quick_replies(client_id, id, title, body, created_by,
  updated_at)` with tenant RLS; CRUD for `tenant_admin`, read for staff; an
  "Insert" button in the reply box.
- **Handoff reason**: when the AI hands off, the inbox shows the recorded reason
  from the existing escalation/handoff record in plain words.
- Tests: service tests with a disposable database, contract snapshot, one
  Playwright flow (assign, tag, insert quick reply, see customer card).

Left to the builder: list pagination details, tag input UX, exact SQL file
names.

## 7. Lane B: console (developer B)

### B1. Subscriptions tab

- **Pages**: `/portal/subscriptions` and `/subscriptions` render
  `components/console/SubscriptionsWorkspace.tsx` with three views: Customers
  (search by name, phone, email; filter by status and plan; row shows customer,
  plan, status, renews on, price), Plans (list, add, edit, archive), Requests
  (queue of open `subscription_requests` with approve/decline). A detail drawer
  shows the subscription, a timeline from `subscription_events`, and actions.
- **API** (`csa/subscriptions/api.py`, router prefix `/console/v1/subscriptions`,
  gated by package `subscriptions` active and roles: read for all members,
  actions for `tenant_admin` and `tenant_staff`):
  - `GET /plans`, `POST /plans`, `PATCH /plans/{code}` (name, price, currency,
    billing_interval `month|year`, features, sort_order, is_active). Archiving a
    plan does not change existing subscriptions.
  - `GET /` (search and filters, 50 per page), `GET /{id}` (with last 100 events),
    `POST /` (attach a plan to a contact: contact_id, plan_code, started_at,
    renews_at).
  - `POST /{id}/actions` with `action ∈ {pause, resume, change_plan,
    cancel_at_period_end, cancel_now, extend_renewal}` plus fields
    (`resume_at`, `plan_code`, `effective_at`, `renews_at`, `reason`). The route
    does not execute: it creates a proposal through `ProposalRegistry.create`
    with executor key `subscription_action` and `origin="tab"`, and returns the
    `ProposalOut`; the frontend confirms it with `ProposalCard`. The executor
    re-checks the package gate and `expected_version`, applies the state change
    in one transaction, inserts a `subscription_events` row (`actor = <user id>`,
    `action`, `from_status`, `to_status`, `payload`), and is idempotent on the
    proposal id.
  - **Request queue**: the customer-facing agent's requests live in
    `agent_preparation_requests` (`agent = 'subscription_agent'`), which is
    insert-only for `csa_app`. The Requests view reads that table joined to a
    new lane B table `subscription_request_decisions(client_id, request_id PK
    → agent_preparation_requests.id, decision CHECK IN ('approved','declined'),
    note, decided_by uuid, proposal_id uuid NULL, decided_at)`; open means no
    decision row. `GET /requests?status=open|decided`, `POST /requests/{id}/decide`
    (`approve|decline`, note). Approving creates and confirms the equivalent
    action proposal in one step for the human who clicked and stores its id in
    the decision row, so audit shows both the request and the change. Lane B
    reads `csa/agents/staged/` but does not change it; the legacy
    `subscription_requests` table stays unused.
- **State model**: `subscriptions.status ∈ {active, paused, cancelled}` enforced
  by a new CHECK (validated against existing rows); `cancel_at_period_end`,
  `pending_plan/pending_effective_at` and `resume_at` are flags, not statuses.
  `extend_renewal` moves `renews_at` forward; no money moves.
- **Due changes job**: a worker job in `csa/jobs/` (`subscriptions_due`, runs
  every 5 minutes, one tenant transaction at a time) applies flags that have
  fallen due: `cancel_at_period_end` at `renews_at` → `cancelled`;
  `pending_plan` at `pending_effective_at` → plan and price switch;
  `resume_at` reached → `active`; otherwise an active subscription at
  `renews_at` gets `renews_at` advanced by one `billing_interval` and a
  `renewed` event. Events carry `actor = 'system:subscriptions_due'`. Nothing
  is charged.
- **Price rule** (owner decision, see section 12): `change_plan` sets
  `subscriptions.price` to the new plan's current price at the effective date
  and records both prices in the event payload; editing a plan's price never
  changes existing subscriptions (they keep their price until their plan
  changes). Staff may override the price on `change_plan` only with a reason.
- **Migration** (lane B range): CHECK constraints above,
  `subscription_request_decisions`, `subscriptions.version integer NOT NULL
  DEFAULT 1` for `expected_version`, indexes for search, the
  `console_proposals.kind` CHECK extended with `subscription_action`, and
  tenant RLS + `csa_app` grants on `plans`, `subscriptions`,
  `subscription_events` and the new table (the builder verifies what the
  baseline already has; add, never `FORCE`).
- **Assistant**: register `subscription_action` in the proposal executors so the
  workspace assistant can propose the same actions later; the planner's action
  list is not extended in this program.
- **Future payments hook** (do not build): every state change already records a
  `price` snapshot and an event payload; a later billing module attaches
  invoices to events. Do not add provider fields now.
- Tests: executor idempotency and version conflict, request approval path,
  package gate, RLS cross-tenant denial, contract snapshot, Playwright flow
  (search, pause via card, approve a request).

Left to the builder: exact search SQL, drawer layout, how declined requests are
shown.

### B2. HR access page (after B1)

- **Where**: People & HR → "Who can do what" (replaces the "Access profile"
  form's role in day-to-day use; the old form stays reachable under "Advanced").
  Visible only to the HR client owner, who alone can apply. Tenant admins who
  are not the HR owner do not see it (today they cannot read other members'
  grants either; this program does not widen that).
- **Read path**: a security-definer function `public.hr_owner_access_summary(p_client)`
  (lane B migration) returns, for the calling HR owner only
  (`hr_is_owner`), every member's `hr_grants`, `hr_workflow_grants` and
  `hr_private_grants` rows; the page derives switch states from it. No RLS
  policy changes.
- **Switches per member** and what they apply through `hr_admin_apply`
  `access.save` and `private_access.save` (one switch = one fixed grant set).
  Because `access.save` replaces the whole set, every save sends the union of
  all switched-on sets plus any rows the summary returned that no switch
  covers, so nothing is removed silently:
  - "Can see colleagues in the directory" → `directory.read` self with fields
    display_name, job_title, department, work_email, assigned_manager.
  - "Can request their own training, leave and attendance" → self profile
    (`training.read/draft/submit/withdraw` and attendance/leave workflow
    `read/prepare` on `self`).
  - "Approves training, leave and attendance for their team" → manager profile.
  - "HR team member (reads HR records, prepares recruitment)" → hr profile.
  - "Works on recruitment" / "Works on attendance" → `hr_private_grants` area
    rows.
  - "Prepares payroll" → payroll_operator profile + `payroll` area.
  - "Reviews payroll" → payroll_reviewer profile + `payroll` area. The area row
    is removed only when both payroll switches are off.
  - The owner profile (client-owner decisions) is not a switch; it stays under
    "Advanced" and is carried as an uncovered row.
- **Company features** block: the five `hr_agent_settings` modules plus the
  `hr_assistant` switch as plain switches ("Directory", "Training", "Recruitment",
  "Attendance and leave", "Payroll", "HR assistant"), saved through the existing
  `HrService.configure_agent` path (plain update, no receipt, as today).
- No new tables. Member access saves are existing `hr_operations` receipts, so
  audit and idempotency keys are unchanged. The mapping table lives in one
  TypeScript module and one Python test asserts the server accepts each set and
  that a save with every switch off leaves uncovered rows in place.
- Tests: mapping unit tests, one service test per switch (grant rows after on
  and after off), Playwright flow (owner toggles, staff member sees the change).

Left to the builder: layout, empty states, the explanation text under each
switch (follow the writing guide).

### B3. Friendly wording and UI guide (after B2)

- Apply `docs/WRITING_GUIDE.md` across the console: navigation labels
  ("Overview", "Services", "People & HR", "Activity", "Knowledge base", "Try the
  assistant", "Inbox", "Email", "Orders & products", "Subscriptions", "Tenants",
  "Members", "Team", "Password"), page titles and one-line descriptions, button
  verbs, empty states, and the `ApiError` plain-word map (extend the existing
  `package_inactive` pattern to the other codes staff can meet:
  `planner_unavailable`, `proposal_consumed`, `outside_service_window`,
  `recipient_unresolved`, `hr_owner_required`).
- Rewrite `ui-guide/UI_GUIDE.md` section 5 shell to the package-driven sidebar
  and the floating assistant dock, replace "Workspace switcher" with the
  platform-admin tenant selector, and add a "Writing" section that points to the
  guide. Mark the reference HTML as illustrative (it already is).
- Contradictions found: none with the architecture. The guide's example
  navigation (Agents, Orders, Approvals) predates packages and is replaced; the
  guide's suggestion of a 72 px icon rail is kept optional.
- Tests: the navigation unit test updates, Playwright label assertions, and the
  existing isolated frontend check.

### B4. Invitation and reset emails from fourstacks.my (small, after B3)

- `IssuedTokenOut.delivery` gains `"email"`. When `INVITE_MAIL_ENABLED=true`
  and `INVITE_MAIL_FROM` (`no-reply@fourstacks.my`) plus SMTP relay settings
  (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` as secret files)
  are present, the identity service emails the accept link from the gateway
  process right after the token transaction commits, and the response carries
  `delivery = "email"` without the raw token. The raw token is never stored:
  no mail outbox table, and the `csa_identity` isolation checks are untouched.
  If sending fails, the response falls back to `delivery = "manual"` with the
  token, exactly as today, and the failure is logged. Google Workspace SMTP
  relay is the transport (owner enables it in the Admin console and
  allow-lists the AWS egress IP); the Gmail API is not used for this.
- Tests: fake SMTP transport, template snapshot, manual fallback on failure.

## 8. Ops track (owner, with commands supplied step by step)

### O1. fourstacks.my administrators and Google Workspace

1. After W0 reaches staging: run `admin-add-approved` for
   `muhammad.amsyar@fourstacks.my` and `danial@fourstacks.my` (approver
   `mamsyar1404@gmail.com`), sign in once each, change the password. Then
   demote nothing yet; the Gmail account is break-glass and is reviewed in a
   month.
2. Google Cloud: create a project inside the fourstacks.my organisation, enable
   the Gmail API, create an **Internal** OAuth client (web application) with
   redirect `<console origin>/console/v1/email/oauth/callback`. Use it for
   fourstacks.my staff mailboxes (no 7-day refresh expiry, no test-user list).
   Keep the existing External client for client tenants and submit it for
   verification when a tenant has more mailboxes than the test-user limit.
   The gateway supports one client id per deployment today; running two
   clients needs a per-tenant client selection, which is recorded as a
   follow-up and is not in this program. Until then, the Internal client is
   used on the fourstacks.my-only deployments (staging), and tenants' production
   uses the External client.
3. Workspace Admin: enable the SMTP relay service for the AWS egress IP, create
   `no-reply@fourstacks.my`, and provide the relay settings as secret files for
   B4.
4. Later: Google sign-in for platform admins restricted to the fourstacks.my
   hosted domain.

### O2. AWS phase 1 (Malaysia, `ap-southeast-5`)

Shape

- One VPC, two AZs, private subnets for RDS, one public subnet for the EC2 host
  (no inbound rules; Cloudflare tunnel provides ingress; shell access through
  SSM Session Manager, no SSH keys).
- EC2 `t4g.xlarge` (4 vCPU, 16 GB, Graviton) with a 100 GB gp3 root volume
  running Docker Compose with the same six services (gateway, worker, delivery,
  frontend, cloudflared, embeddings) plus the migrate and admin one-shots, from
  GHCR images pinned by digest exactly as staging does. `KB_UPLOAD_ROOT` stays
  on an EBS volume (S3 is phase 2, it needs a code change).
- RDS PostgreSQL 16 `db.t4g.medium` (2 vCPU, 4 GB), 50 GB gp3, single-AZ,
  7-day automated backups, `pgvector` and `postgis` enabled, parameter group
  with `rds.force_ssl = 1`. Roles `csa`, `csa_app`, `csa_ro`, `csa_identity`
  created by the migration runner as today.
- Secrets in SSM Parameter Store (SecureString, KMS); a boot script on the host
  writes them to `/srv/csa/secrets/<FIELD>` files so compose and `Settings`
  (`/run/secrets/<FIELD>`) stay unchanged.
- Logs to CloudWatch via the Docker `awslogs` driver; one alarm each on host
  CPU, RDS storage, RDS connections, and gateway `/readyz` failures (checked by a
  small cron on the host that posts a CloudWatch metric).
- Terraform (OpenTofu-compatible) under `infra/aws/` with state in S3 +
  DynamoDB lock; GitHub Actions deploys through OIDC and SSM Run Command running
  the same compose helper used on staging. No AWS keys in GitHub secrets.

Estimated monthly cost (on-demand, to be confirmed in the AWS Pricing
Calculator before purchase; Malaysia prices are roughly a tenth lower than
Singapore for EC2)

| Item | Estimate (USD/month) |
|---|---|
| EC2 t4g.xlarge | 95–110 |
| RDS db.t4g.medium + 50 GB + backups | 60–75 |
| EBS 100 GB gp3 | 8–10 |
| CloudWatch logs and alarms | 5–10 |
| Data transfer, SSM, KMS, S3 state | 5–10 |
| Total | roughly 175–215 |

A one-year compute savings plan cuts the EC2 line by about a third once the
shape is stable. Thousands of customers on WhatsApp is light for this shape;
the limiting factor is the model provider's rate limits, not the host.

Phase 2 trigger and shape (recorded, not planned): a second tenant that needs
isolation, sustained host CPU above 70 %, or an availability requirement. Then:
gateway, worker and delivery move to ECS Fargate services behind an ALB (or keep
the tunnel), RDS becomes Multi-AZ, uploads move to S3, embeddings move to their
own small service.

## 9. Working in parallel (two developers, two sessions)

- Each lane works in its own git worktree under `.worktrees/` on its own branch
  (`feat/whatsapp-channel`, `feat/support-refinements`, `feat/subscriptions-tab`,
  `feat/hr-access-page`, `feat/friendly-console`, `feat/invite-mail`), branched
  from `main` after W0 merges.
- Files are disjoint by construction: lane A owns `csa/channels/`,
  `apps/delivery/`, `csa/console/inbox.py`, `components/console/InboxWorkspace.tsx`
  and the Services "WhatsApp number" card; lane B owns `csa/subscriptions/`,
  `components/console/SubscriptionsWorkspace.tsx`, the HR access page,
  `lib/navigation.ts` labels and `ui-guide/`. Shared files follow the hot-file
  rules in W0.
- Migration numbers come from the lane's reserved range; `EXPECTED_SCHEMA_VERSION`
  is set at merge to the highest merged version.
- Local runs are focused (`pytest tests/test_<area>*.py`, `npm run check:isolated`);
  the full `make test` runs on CI for every PR. Never two Testcontainers suites
  at once on the shared host.
- Each PR: per-task review, a whole-branch review, and the staging rollout
  procedure already in `deploy/STAGING_PIPELINE.md`. Staging rollouts are
  serialized by the owner.
- The subagent-driven workflow runs one controller per lane; the two
  controllers never share a plan workspace.

## 10. Order of work

1. W0 foundations (one PR, one developer, half a day).
2. In parallel: A1 WhatsApp (lane A) and B1 Subscriptions (lane B).
3. In parallel: A2 support refinements and B2 HR access page.
4. B3 friendly wording, then B4 invitation mail (lane B); lane A assists with
   WhatsApp phase 2 scoping or joins B3.
5. Ops: O1 steps 1–2 as soon as W0 is on staging; O2 Terraform can start at
   any time and first hosts staging, then production.
6. Project 3 (HR completeness) follows, using the HR access page as its first
   screen.

## 11. What is left to the builders

Component layouts, SQL file names, exact Pydantic field names not fixed above,
retry constants, test fixture shapes, and Terraform module boundaries. Any
choice that touches authority (who may call what), money, or customer-visible
behaviour must match this document or come back as a question.

## 12. Decisions made after the builder check (2026-10-27)

Owner decisions (confirmed by Amsyar on 2026-10-27):

- Fourstacks owns the Meta app; clients connect their own WABA and number to it.
- WhatsApp phase 1 is questions, tickets, escalation and subscription requests;
  ordering stays on Telegram until phase 2.
- `change_plan` takes the new plan's current price; plan price edits never
  touch existing subscriptions; staff may override with a reason.

Designer's calls (reversible, recorded here so planners do not re-ask):

- The request queue is `agent_preparation_requests` plus a decisions table;
  the legacy `subscription_requests` table stays unused.
- A worker job applies due subscription flags; nothing is charged.
- WhatsApp senders are linked to an existing contact only on an exact single
  phone match; otherwise a new contact is created.
- The HR access page is owner-only, reads through a security-definer summary
  function, and always re-sends uncovered grant rows so a save never removes
  authority the page does not show.
- Invitation mail is sent in-process with manual fallback; no token at rest.
- `add_platform_admin` requires the approver's password and a configurable
  expected database host and name.
- Schema-version literals in tests are collapsed to one import in W0.

## Sources consulted (2026-10-27)

- WhatsApp pricing structure and Malaysia rates: [Qiscus](https://www.qiscus.com/en/blog/whatsapp-api-malaysia/),
  [whautomate](https://whautomate.com/whatsapp-business-api-pricing-malaysia),
  [forwardchat.my](https://forwardchat.my/blog/whatsapp-api-cost-malaysia),
  [bixtech](https://bixtech.co/blog/whatsapp-business-api-pricing-malaysia),
  [flowcall](https://www.flowcall.co/blog/whatsapp-business-api-pricing),
  [ManyChat](https://help.manychat.com/hc/en-us/articles/14281380243740-WhatsApp-pricing-guide).
  Confirm against Meta's official rate card before quoting the client.
- AWS Malaysia region: [cloudprice.net ap-southeast-5](https://cloudprice.net/aws/region/ap-southeast-5),
  [AWS Malaysia guide](https://aqvantiq.com/blog/aws-malaysia-guide/),
  [Amazon RDS pricing](https://aws.amazon.com/rds/pricing/).
- Google OAuth Internal vs External testing expiry: [Unipile](https://www.unipile.com/google-oauth-refresh-token/),
  [Nango](https://nango.dev/docs/api-integrations/google/how-to-register-your-own-google-api-oauth-app).
