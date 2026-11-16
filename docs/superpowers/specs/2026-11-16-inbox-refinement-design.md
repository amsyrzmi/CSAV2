# Customer Inbox refinement: design

Date: 2026-11-16. Owner: Amsyar. Status: draft for owner approval (nothing is built).
Branch: `feat/inbox-refinement` (worktree `.claude/worktrees/feat-inbox-refinement`).
Migrations: 0620 to 0629 only.

## 1. Intent

A client company's customers will number in the thousands, buying, asking or doing whatever the
company subscribes to. Today the Inbox is a list ordered by last message with five filters. It cannot
tell staff what needs them, how long it has waited, who is on it, or what to do next, and nothing
reminds anyone when something waits too long.

This project makes the Inbox a work queue: every conversation has a state that says who has the ball,
staff can claim, snooze and resolve, the list answers "what needs us" quickly at tens of thousands of
conversations per company, and reminders and notifications fire when something waits.

The reminder and notification layer is built as a generic module. The HR workflow project (a separate
session, later) consumes it for due dates and approvals.

### Goals

- One shared queue per company, worked by tenant admins and tenant staff, with optional claiming.
- A state model that separates "the bot is handling it", "waiting on us", "waiting on the customer",
  "snoozed" and "resolved".
- Priority, a due time (SLA), a reason and a topic on every item that needs staff.
- Filters, saved views, counts, sort and bulk actions that stay fast at tens of thousands of
  conversations per company.
- Reminders for items waiting too long, overdue items, snoozes that end, items assigned to you and
  claims gone stale, with dedupe, repeat limits, an escalation step and quiet hours.
- A console notification centre, Telegram to the company's staff chat, and email to individual
  people, plus a console page that manages notification targets.
- A reusable reminder layer and per-person notification inbox that HR can use without touching Inbox
  code.

### Non-goals and anti-goals

- No change to what the customer bot says or when it escalates (see "Out of scope"). A change that
  alters customer-facing behaviour would fail this project's intent.
- No hard locks. A claim is a visible marker, not a lock: a person who is off sick must never block
  the queue.
- No new way for staff to see customer data they cannot see today (the list keeps masking phones).
- No state that can disagree with the conversation: the queue state lives on the conversation row.

## 2. Decisions the owner made

- **One shared queue with optional claiming** (2026-11-16). Every tenant admin and tenant staff member
  sees every conversation of the company. Claiming is optional and soft.
- **Approach A** (2026-11-16): queue state on the `conversations` row, plus a generic reminder module
  that never reads inbox tables.

Approaches considered: (A) queue state on the conversation plus a generic reminder module, chosen;
(B) make tickets the work item, rejected because tickets exist only for escalations, the assignee is
free text and staff would see two records per conversation; (C) one generic work-items table for all
modules, rejected because it duplicates conversation state and adds a join to every Inbox query, while
the reminder module gives HR the reuse without it.

## 3. What exists today (verified in the code, 2026-11-16)

- `conversations`: `status` ('open' or 'escalated'; nothing ever writes 'closed'), `owned_by`
  ('ai' or 'human_support'), `ai_muted_until`, `opened_at`, `closed_at` (never written),
  `inbox_version` (optimistic concurrency, bumped by staff actions), `last_inbound_at` (WhatsApp only,
  for the 24-hour service window).
- Takeover (`inbox/set_owner.sql`) sets `ai_muted_until` to NULL, which is an indefinite mute. A
  conversation a person took over and forgot is silent for the customer until someone releases it.
- Escalation (`csa/agents/escalation/agent.py`) writes an `escalations` row, a `tickets` row (with
  `assignee` as text and `sla_due_at` 1, 4, 24 or 72 hours by priority), mutes the AI for a day, and
  calls `notify(event='escalation')`. The console shows none of it except the newest handoff note.
- Held replies are `approvals` rows; "needs review" counts conversations with a live pending approval.
- Inbound messages are inserted by the worker (`apps/worker/main.py`, `_persist_inbound`); outbound
  messages by seven code paths (bot turn, approval send, human reply, payment notices, booking
  messages, staff callbacks, echo). All insert into `public.messages` with `direction` and `author`
  ('contact', 'assistant', 'human_support') and `send_authority`.
- The list SQL orders by a per-row lateral last-message time, so a `LIMIT` query cannot use an index.
  The only inbox index is partial on `channel = 'telegram'`. Each row also runs lateral lookups for
  orders, subscriptions and pending approvals.
- `csa/notify`: `notify()` fans an event to matching `staff_notification_targets` as idempotent
  `staff_notifications` rows (dedupe key `notify:<event>:<key>:<target>`). Targets carry channel,
  events, `min_priority` and `quiet_hours` (`{"start","end","tz"}`; urgent bypasses). The sender
  supports Telegram only; `format_notification` is English only and knows six events. No console page
  manages targets except the single "staff chat" Telegram field.
- No bell, badge or notification centre exists in the console. The Overview strip shows the needs
  review count only.
- Background jobs: `csa/jobs/registry.py` periodic jobs under a per-job advisory lock, so several
  worker processes run each job once. Booking reminders are the precedent (scan, dedupe by unique
  key, send, settle).
- Roles: platform admin, tenant admin and tenant staff have identical Inbox rights; read-only members
  get 403 on the whole Inbox. The team list API is admin-only.

## 4. The staff flow

```
customer writes
   |
   +-- bot answers ------------------------------> state: bot (not in the queue)
   |
   +-- bot escalates / holds a reply / customer writes to a taken-over conversation
          |
          v
   NEEDS US  (needs_staff; clock starts; due time set from priority)
      |  claim (optional)            |  snooze until T           |  resolve
      |                              v                           v
      |                          SNOOZED -- at T --> NEEDS US    RESOLVED (and the bot takes over again)
      v  staff reply
   WAITING ON CUSTOMER  --- customer writes ---> NEEDS US (if a person still owns it)
      |  no reply for N hours: follow-up reminder
      +--> resolve
```

1. **New message.** If the bot is handling the conversation nothing changes. If the bot escalates, or a
   held reply is created, or the customer writes while a person owns the conversation, the item
   enters NEEDS US with a reason, a priority and a due time.
2. **Triage.** The queue lists NEEDS US items, overdue first, then oldest waiting. Each row shows the
   customer, a one-line last message, reason, topic, priority, how long it has waited and the
   assignee.
3. **Claim or assign.** Any member can claim an unclaimed item ("take it"). A first human reply claims
   an unclaimed item automatically. Tenant admins can assign to any enabled member.
4. **Act.** Reply (after takeover, as today), decide a held reply, change priority or topic, add a
   reason.
5. **Resolve, snooze, reopen.** Resolve ends the item and hands the conversation back to the bot
   (a single action, so a taken-over conversation is never left silent). Snooze hides it until a
   chosen time, then it returns to NEEDS US and the assignee is notified. A customer message on a
   RESOLVED item reopens it only if a person still owns the conversation; if the bot owns it, the
   state returns to `bot`.

### Who sees and does what

| | Tenant staff | Tenant admin / platform admin | Read-only |
|---|---|---|---|
| See every conversation and the queue | yes | yes | no (403, as today) |
| Claim or release own claim | yes | yes | no |
| Assign to another member, take over someone's claim | no | yes | no |
| Resolve, snooze, set priority and topic, bulk actions | yes | yes | no |
| Acting on an item claimed by someone else | allowed, shows who holds it and records an event | allowed | no |
| Read inbox settings | yes | yes | no |
| Change inbox settings and notification targets | no | yes | no |

The assignee picker needs the member list. The existing team route is admin-only, so a new
`GET /inbox/assignees` returns id and display name of enabled tenant admins and staff to both roles.

## 5. The queue model

### 5.1 Columns on `conversations` (migration 0620)

| Column | Meaning |
|---|---|
| `queue_status` | `bot` (default), `needs_staff`, `waiting_customer`, `snoozed`, `resolved` |
| `queue_reason` | why it needs us: `escalation`, `held_reply`, `customer_message`, `payment_proof`, `order_approval`, `booking_pending`, `manual` |
| `topic` | `purchase`, `question`, `complaint`, `payment`, `booking`, `subscription`, `other`; set by the cause (payment proof gives `payment`, booking gives `booking`, order approval gives `purchase`), by a fixed mapping from the escalation's reason where it matches, else `other`; staff can change it with one tap |
| `priority` | `low`, `normal`, `high`, `urgent`; from the escalation, else `normal`; staff can raise or lower |
| `assignee_user_id`, `claimed_at` | console user holding the item (nullable) |
| `needs_staff_since` | when the item last entered NEEDS US; the "waiting on us" clock |
| `due_at` | `needs_staff_since` plus the company's response target for the priority |
| `snoozed_until` | set while snoozed |
| `resolved_at` | set when resolved |
| `last_activity_at` | last message time, else `opened_at` (replaces the lateral lookup) |
| `last_customer_at`, `last_staff_reply_at` | last inbound message; last message by a person or an approved held reply |

`last_inbound_at` keeps its WhatsApp service-window meaning and is not reused.

An append-only `inbox_queue_events` table (0621; tenant isolated) records every transition: actor,
action, from and to state, details. It feeds the item's history line, audit and the reminder re-check.

### 5.2 How the state is maintained

- **From messages: one trigger.** An `AFTER INSERT` trigger on `messages` updates `last_activity_at`,
  `last_customer_at`, `last_staff_reply_at` and applies the message transitions below. A trigger
  covers every insert path (the worker's inbound insert and seven outbound paths) without touching any of them. It does not bump `inbox_version` (a bump
  per inbound message would make every staff reply fail with 409). Cost: one more `UPDATE` of the
  conversation per message; the worker already updates `turn_count` there.
- **From events: the code that causes them.** Escalation, approval creation and expiry, payment proof
  submitted, order awaiting approval and pending booking call one function in a new
  `csa/inbox_queue` module (`enter_needs_staff(reason, priority, topic)`). Staff actions (claim,
  assign, snooze, resolve, priority, topic) go through the same module. Each transition writes an
  event row and, for staff actions, bumps `inbox_version`.

Message transitions:

| Message | Condition | Result |
|---|---|---|
| inbound | person owns the conversation (`owned_by = human_support`) and state is any except `needs_staff` | `needs_staff`, reason `customer_message`, clock starts |
| inbound | bot owns it and state is `resolved` or `waiting_customer` or `snoozed` | `bot` |
| outbound, author `human_support` | any | `waiting_customer`; claims for the author if unclaimed |
| outbound with `send_authority = reviewed` (approved held reply) | any | `waiting_customer` |
| outbound by the assistant | any | no change |

Other transitions: escalation to `needs_staff` (priority from the escalation); a held reply to
`needs_staff` with reason `held_reply`; the decision of the last pending held reply leaves
`needs_staff` only through the outbound message it produces (a rejected or expired reply stays
`needs_staff` with reason `customer_message` and a note, because the customer got no answer); release
or resolve returns the item to `bot` and the conversation to the AI.

Migration backfill (one statement per rule): `last_*` from the newest message; escalated or
human-owned conversations and conversations with a live pending approval become `needs_staff`, with
`needs_staff_since` from the open escalation, pending approval or newest customer message; human-owned
conversations whose newest message is from staff become `waiting_customer`; everything else is `bot`.

### 5.3 Settings per company (`inbox_settings`, 0624)

Same shape as `booking_settings` (client id primary key, `version`, check ranges, `GET` for any
member, `PUT` for admins, audited in `console_audit`, 409 on a stale version).

| Setting | Default | Range |
|---|---|---|
| response target urgent / high / normal / low | 15 min / 60 min / 240 min / 1440 min | 5 to 10080 |
| `repeat_minutes` (between reminders for one item) | 60 | 15 to 1440 |
| `max_repeats` | 3 | 0 to 10 |
| `follow_up_hours` (waiting on customer) | 48 | 1 to 720; 0 turns it off |
| `stale_claim_hours` | 8 | 1 to 168; 0 turns it off |
| `handback_prompt_hours` (person still owns a quiet conversation) | 24 | 1 to 720; 0 turns it off |

Response targets run on the wall clock. Business-hours targets are out of scope; reminders that fall in
a target's quiet hours wait for the window to end (urgent bypasses), which gives the practical effect.

## 6. Filters, views, counts, sort, bulk actions

### 6.1 Views (fixed tabs) and saved views

Fixed tabs: **Needs us** (state `needs_staff`), **Mine** (assignee is me, state `needs_staff` or
`waiting_customer`), **Unassigned** (`needs_staff`, no assignee), **Overdue** (`needs_staff` and
`due_at < now()`), **Waiting on customer**, **Snoozed**, **Resolved**, **All**. Each tab shows its
count.

Saved views: a person can save the current filter set under a name (up to 20 each, table
`inbox_saved_views`, 0621; the filter is stored as JSON validated by the same model as the query
parameters, private to the person).

### 6.2 Filter semantics (all combine with AND)

| Filter | Meaning |
|---|---|
| `state` | one or more of the five states |
| `assignee` | `me`, `none` or a member id |
| `priority_min` | at least this priority |
| `reason` | one or more reasons |
| `topic` | one or more topics |
| `channel` | `telegram` or `whatsapp` |
| `sender` | `known` or `new` (unchanged rule) |
| `overdue` | state `needs_staff` and `due_at < now()` |
| `waiting_over` | minutes; state `needs_staff` and `needs_staff_since < now() - n` |
| `needs_review` | kept for compatibility: a live pending approval (reason `held_reply`) |
| `q` | name, phone or @username (unchanged parsing and rules; never logged) |

### 6.3 Sort and cursor

`order = activity` (newest first; default on All and Resolved), `waiting` (oldest waiting first),
`due` (soonest due first; default on Needs us, Mine, Unassigned, Overdue), `priority` (highest first,
then due). The cursor becomes an opaque keyset token (sort value plus id); a bare conversation id from
older clients is still accepted and treated as an activity cursor.

### 6.4 Counts

`GET /inbox/queue/counts` returns `needs_us`, `mine`, `unassigned`, `overdue`, `waiting_customer`,
`snoozed`, and the oldest waiting time, computed from the partial indexes. The Overview strip and the
navigation badge read it. `needs_review_count` stays in the list response for current clients.

### 6.5 Indexes (0620, additive; `CREATE INDEX` without `CONCURRENTLY`, as the migration rules require)

- `(client_id, last_activity_at DESC, id DESC)` for All, Resolved and any `activity` list.
- `(client_id, due_at, id) WHERE queue_status = 'needs_staff'` for Needs us, Unassigned, Overdue.
- `(client_id, assignee_user_id, due_at, id) WHERE queue_status IN ('needs_staff','waiting_customer')
  AND assignee_user_id IS NOT NULL` for Mine.
- `(client_id, needs_staff_since, id) WHERE queue_status = 'needs_staff'` for `waiting`.
- `(client_id, snoozed_until) WHERE queue_status = 'snoozed'`.
- `(client_id, last_staff_reply_at) WHERE queue_status = 'waiting_customer'`.
- Search: trigram indexes on contact name and @username (the generated `phone_digits` column from 0581
  already serves phone search with a suffix match), if the `pg_trgm` extension is available to the
  migration owner; otherwise name and @username search keep today's behaviour and the limit is
  documented. The builder must confirm availability before the plan is fixed.

The list query drops the per-row lateral lookups: last message and times come from the row; the
"known sender" check and the pending approvals count are computed only for the page of rows returned.
Counts and queue tabs touch only the partial indexes, so `bot` and `resolved` rows (the large majority)
never enter them. A test seeds 50,000 synthetic conversations for one company and asserts each tab's
query plan uses its index and returns a page without a sort node.

### 6.6 Bulk actions

`POST /inbox/bulk` takes up to 50 conversation ids and one action: claim, assign (admin), release
claim, resolve, snooze, set priority, set topic. Each item runs in its own savepoint with its own
`inbox_version` check; the response lists per-item outcomes (done, version conflict, not allowed), so
one stale item does not fail the batch. Bulk reply is not offered.

## 7. Reminders and notifications

### 7.1 The reminder layer (`csa/reminders`, generic)

Interface HR will use:

```python
register_kind(kind: str, handler: ReminderHandler)         # at worker start-up
await schedule(conn, client_id, kind, subject_type, subject_id, due_at, payload, dedupe_key)
await cancel(conn, client_id, kind, subject_type, subject_id)
```

- Table `reminders` (0622; tenant isolated): `kind`, `subject_type`, `subject_id` (opaque text),
  `due_at`, `state` (`scheduled`, `fired`, `cancelled`, `done`), `fire_count`, `last_fired_at`,
  `payload`, unique `(client_id, dedupe_key)`.
- A periodic job (`reminders_fire`, every minute, under the job advisory lock) claims due rows with
  `FOR UPDATE SKIP LOCKED` and calls the kind's handler. The handler **re-checks the subject** (is it
  still waiting?) and returns a decision: notify these recipients with this message and reschedule at
  T, or finish. A reminder for work that already moved on is dropped silently.
- The layer never imports inbox code. Inbox registers its kinds; a test registers a fake `demo.due`
  kind to prove a second consumer works without touching Inbox files.
- Retries and failures: a handler exception reschedules with backoff and is logged; three failures
  mark the row `fired` with an error, never loop.

### 7.2 Notification delivery

Two delivery paths share one call, `notify_people(...)`, which takes a language-neutral message (a key
plus parameters) so each surface renders it in its own language:

1. **Console notification centre** (`console_notifications`, 0623; tenant isolated). One row per
   recipient: `user_id`, `kind`, `priority`, message key and parameters, link (route and parameters),
   `read_at`, `created_at`. A bell in the shell header shows the unread count; the panel lists the
   latest 50; a row opens the conversation (`/inbox?conversation=<id>`); "mark all read". Rows older
   than 30 days are purged by a job. Count refresh by polling, as the console does elsewhere.
2. **Company staff chat** through the existing `csa/notify`: matching targets, `min_priority`, quiet
   hours, outbox, retries. `format_notification` becomes language-aware through a `language` column on
   targets (`en` default, `ms`) and learns the new events.
3. **Email to a person** through the existing console mailer, off by default and set per person
   (`console_notification_prefs`, 0623): choose kinds, priority floor. Emails never include customer
   message text; they name the customer by display name only and link to the conversation.

Per-person Telegram messages are out of scope (the console user has no Telegram identity).

### 7.3 Inbox reminders

| Kind | Fires | To | Notes |
|---|---|---|---|
| `inbox.assigned` | immediately when an item is assigned to someone other than the actor | the assignee: console, plus email if chosen | not sent on self-claim |
| `inbox.overdue` | at `due_at`, then every `repeat_minutes`, up to `max_repeats` | assignee if any, else the whole team: console to everyone with Inbox access, plus the company staff chat | notification priority is the item's priority; from the second repeat it is raised one level so `min_priority` and quiet hours treat it as more urgent; the last repeat also goes to every tenant admin by name |
| `inbox.snooze_due` | at `snoozed_until` | assignee, else team (console) | the item returns to NEEDS US with a fresh clock before notifying |
| `inbox.stale_claim` | `stale_claim_hours` after a claim with no staff action while the item is `needs_staff` | the claimer, then tenant admins | cleared when the claim is released or reassigned |
| `inbox.follow_up` | `follow_up_hours` after the last staff reply while `waiting_customer` | assignee, else team (console only, never the staff chat) | text: follow up or resolve |
| `inbox.handback` | `handback_prompt_hours` after the last staff reply while a person owns the conversation and nothing is waiting | assignee, else team (console only) | text: hand it back to the assistant |

The existing immediate events (`escalation`, `approval_needed`, `booking_pending`) stay as they are and
are not duplicated; the inbox reminders cover waiting time.

- **Dedupe.** A reminder's dedupe key is `kind:subject:cycle` (the cycle is the item's
  `needs_staff_since` or claim time), and a notification's key is the reminder id plus fire number, so
  a worker crash or replay adds nothing. Console rows are unique on that key per recipient.
- **Cancel.** Leaving the state (reply, resolve, snooze, release, reassign) cancels the item's
  scheduled reminders in the same transaction as the transition.
- **Quiet hours.** Apply to the company staff chat only, with today's rule (urgent bypasses). The
  console notification centre is passive and has none.

### 7.4 Notification targets page

Settings, Notifications (new; tenant admin writes, staff read): list the company's targets with
channel, address (masked), events, minimum priority, quiet hours, language, enabled and verified
state; add, edit, disable and remove. Channels offered: Telegram (the existing staff-chat
verification flow is reused) and email (a shared mailbox address, sent through the console mailer).
WhatsApp and webhook targets exist in the schema but have no sender, so the page does not offer them.
The same page holds the Inbox settings of 5.3 and each person's own notification preferences.

## 8. The reusable layer (statement for HR)

`csa/reminders` (timers, handler registry, firing, re-check, repeat and escalation) and
`console_notifications` (per-person inbox, preferences, email path) know nothing about conversations.
HR registers kinds such as `hr.due` or `hr.approval_pending`, calls `schedule` and `cancel` from its own
code, and supplies a handler that re-checks its subject and renders message keys in `en` and `ms`.
`import-linter` forbids the layer from importing `csa/console/inbox` and `csa/inbox_queue`. The design
of HR is not part of this project.

## 9. Delivery in three pull requests

1. **Queue and flow (migrations 0620, 0621, 0624 if settings ship here).** Queue columns, trigger,
   `csa/inbox_queue`, events and saved views, the new list, counts and bulk routes, the indexes, and
   the Inbox UI: tabs with counts, filters, saved views, claim, snooze, resolve, priority and topic,
   bulk selection, settings (read and write). No reminders yet.
2. **Reminders and the notification centre (0622, 0623).** `csa/reminders`, the job, the Inbox
   reminder kinds, `console_notifications`, the bell and panel, Overview strip and navigation badge.
3. **Targets page and delivery options.** Notification targets page, language-aware staff chat
   messages, the email sender and per-person preferences.

Each PR is independently shippable. Migrations stop all four services during a staging rollout; the
columns are additive and defaulted, so old code keeps working against the new schema.

## 10. Acceptance scenarios (synthetic data only)

1. *Escalation enters the queue.* "Customer A" (known sender, Telegram) asks for a refund; the bot
   escalates with priority high. The item appears in Needs us with reason `escalation`, topic from the
   mapping, due in 60 minutes, and the staff chat receives the existing escalation alert once.
2. *Claim and reply.* Staff "Person 1" claims it, takes over, replies. The state becomes waiting on
   customer, the claim stays, Needs us drops by one, Mine shows it.
3. *Customer answers.* Customer A writes again; the person still owns the conversation, so it returns
   to Needs us with reason `customer_message` and a new clock; Person 1 gets a console notification.
4. *Overdue and escalation of reminders.* An unclaimed normal item passes 240 minutes. The team gets
   one overdue reminder (console and staff chat). With nobody acting, repeats follow every 60 minutes;
   the third also notifies each tenant admin by name. Resolving after the first repeat cancels the
   rest.
5. *Quiet hours.* An overdue reminder falls at 23:30 with quiet hours 22:00 to 07:00: the staff chat
   message waits until 07:00, the console row appears at once; an urgent item bypasses the window.
6. *Snooze.* Person 2 snoozes an item until tomorrow 09:00. It leaves Needs us and the counts; at
   09:00 it returns with a fresh clock and Person 2 is notified once, even if the worker restarts at
   09:00.
7. *Resolve hands back.* Resolving a taken-over conversation returns it to the bot; the next customer
   message is answered by the bot and the state stays `bot`.
8. *Held reply.* The bot holds a reply for review: Needs us, reason `held_reply`. Approving sends it:
   waiting on customer. Rejecting leaves it in Needs us with reason `customer_message`.
9. *Scale.* With 50,000 conversations for one company and 800 needing staff: every tab returns a page
   with an index scan and no sort node; counts return from the partial indexes; a search by name
   suffix or phone digits returns a page.
10. *Bulk.* Selecting 30 items and resolving: 29 succeed, one fails with a version conflict and is
    reported; nothing else changes.
11. *Roles.* Tenant staff cannot assign to another member (403); a tenant admin can. A read-only
    member gets 403 on every Inbox route. A company's staff never see another company's items (row
    security tests for every new table).
12. *Second consumer.* A test kind `demo.due` is scheduled, fires, re-checks and notifies through the
    same layer with no Inbox code involved.
13. *Replay safety.* Delivering the same reminder twice (worker crash after send) creates one console
    row and one chat message.

## 11. Constraints and checks

- Public repo: synthetic data only. No secret, token or real address anywhere.
- Migrations 0620 to 0629 only; each table with a `client_id` has the tenant-isolation policy
  (`ENABLE`, not `FORCE`), explicit grants for `csa_app` and `csa_ro`, and a row security test;
  `EXPECTED_SCHEMA_VERSION` and the migration README are updated by each PR.
- `uv run pyright` runs on the whole project including tests; ruff, ruff format and `lint-imports` pass.
- `docs/console_openapi.json` is regenerated with the repo tool; `docs/CONSOLE_API.md` documents the new
  routes and parameters; `docs/PROGRESS.md` gets an entry per PR; retest rows go to the Inbox retest
  list.
- The Inbox trigger must not slow ingestion: a test inserts messages in bulk and asserts the added cost
  per message is one conversation update.
- Customer-facing behaviour is unchanged, so the golden and eval harness is not triggered; the PRs say
  so explicitly. Any later change to when the bot escalates goes through that harness in English and
  Malay.
- No rollout, deploy or home-server action by this session; the owner merges.

## 12. Out of scope

- Bot behaviour: escalation rules, replies, tone (a separate topic, see the customer-bot escalation
  work).
- Tickets as a console feature; the ticket and its SLA remain the escalation record and are not shown.
- Internal notes and @mentions on a conversation. The reminder layer takes an `inbox.mention` kind
  when notes exist; notes need their own privacy design so they cannot reach the customer.
- Business-hours response targets, automatic assignment (round robin), auto-resolve, auto hand-back.
- WhatsApp and webhook staff targets (no sender exists), per-person Telegram messages.
- Analytics and reporting on response times (the events table keeps the data for it).
- Read-only member access to the Inbox.

## 13. What is left to the builder

Exact column and index names and the mapping table from escalation reasons to topics; the keyset cursor
encoding; the wording of notification messages in English and Malay (plain words per
`docs/CONSOLE_VOICE.md`); the layout of the queue, the bell and the settings page within the existing
console design tokens; the choice between `pg_trgm` and the documented fallback once availability is
confirmed.
