# Customer Inbox refinement: design

Date: 2026-11-16. Owner: Amsyar. Status: draft for owner approval (nothing is built).
Branch: `feat/inbox-refinement` (worktree `.claude/worktrees/feat-inbox-refinement`).
Migrations: 0620 to 0629 only (plan: 0620 to 0622 in PR 1, 0623 to 0625 in PR 2 and 3).

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

**Owner decision pending (recommended default is written into this document):** payment proofs, order
approvals and pending bookings stay on their own pages (Payments, Orders, Bookings) and are not queue
items in the Inbox. The queue holds conversation work only: escalations, held replies, customers
waiting on a person, and items a person took over. See section 12.

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
- The `mute_expiry` job (every 5 minutes) ends an escalation's one-day mute by setting `owned_by` back to
  `ai` and `status` to `open`; the Telegram staff-chat "Resume AI" button does the same.
- Approved held replies are inserted as an `assistant` message with `send_authority = automation` and
  updated to `reviewed` afterwards. Automated payment notices, booking messages and staff-chat order
  decisions are also written as `assistant` and updated to `reviewed` (or `human`) so a takeover does
  not hold them back. So neither `author` nor `send_authority` says "a person answered".
- There is one Telegram staff target per company: saving the staff chat on the Channels page disables
  every other enabled Telegram target (`console/telegram_staff_target_replace.sql`).
- `pg_trgm` is already installed (`db/schema_baseline.sql`). The 0581 index on `phone_digits` is a plain
  btree and does not serve the contains match the search uses.
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
   held reply is created, or the customer writes while a person owns the conversation, or a person
   takes the conversation over, the item enters NEEDS US with a reason, a priority and a due time.
2. **Triage.** The queue lists NEEDS US items, overdue first, then oldest waiting. Each row shows the
   customer, a one-line last message, reason, topic, priority, how long it has waited and the
   assignee.
3. **Claim or assign.** Any member can claim an unclaimed item ("take it"). A first human reply claims
   an unclaimed item automatically. Tenant admins can assign to any enabled member.
4. **Act.** Reply (after takeover, as today), decide a held reply, change priority or topic, add a
   reason.
5. **Resolve, snooze, reopen.** Resolve ends the item and hands the conversation back to the bot
   (a single action, so a taken-over conversation is never left silent). It closes the open escalation
   record and its ticket so a later escalation starts a new one. Resolve is refused while a held reply
   is still pending (decide it first), and when the customer has written since the person loaded the
   item. Snooze hides the item until a chosen time, then it returns to NEEDS US and the assignee is
   notified. A customer message on a snoozed, waiting or resolved item puts it back in NEEDS US when a
   person still owns the conversation or a held reply is pending; otherwise the state returns to `bot`.
6. **Words.** "Release claim" gives up the assignment only. "Hand back" (the existing `/release` route
   and the staff-chat "Resume AI" button) returns the conversation to the bot, and counts as resolving
   the item. The automatic end of an escalation's one-day mute is not a hand-back by a person: the bot
   answers again, but the item stays in NEEDS US until a person replies, resolves or snoozes it, so an
   escalation nobody handled never disappears.
7. **Takeover.** Taking over a bot conversation puts the item in NEEDS US with reason `manual`,
   claimed by the person who took it over, so a takeover nobody follows up with becomes overdue and
   reminds its owner instead of leaving the customer silent.

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
| `queue_reason` | why it needs us: `escalation`, `held_reply`, `customer_message`, `manual` (a person took the conversation over) |
| `topic` | `purchase`, `question`, `complaint`, `payment`, `booking`, `subscription`, `other`; set by a fixed mapping from the escalation's reason or the conversation's last intent where one matches, else `other`; staff can change it with one tap |
| `priority` | `low`, `normal`, `high`, `urgent`; from the escalation, else `normal`; staff can raise or lower |
| `assignee_user_id`, `claimed_at` | console user holding the item (nullable) |
| `needs_staff_since` | when the item last entered NEEDS US; the "waiting on us" clock |
| `due_at` | `needs_staff_since` plus the company's response target for the priority |
| `snoozed_until` | set while snoozed |
| `resolved_at` | set when resolved |
| `last_activity_at` | last message time, else `opened_at` (replaces the lateral lookup) |
| `last_customer_at`, `last_staff_reply_at` | last inbound message; last reply by a person or an approved or edited held reply |

`last_inbound_at` keeps its WhatsApp service-window meaning and is not reused.

An append-only `inbox_queue_events` table (0621; tenant isolated) records every transition: actor,
action, from and to state, details. It feeds the item's history line, audit and the reminder re-check.

### 5.2 How the state is maintained

What a message row says cannot decide who answered, so the trigger only records facts and the code
that knows who acted makes every other transition.

- **Trigger on `messages` (inbound only, `AFTER INSERT`).** It updates `last_activity_at` on every
  message and, for `author = 'contact'`, `last_customer_at`, and applies the one inbound transition
  below. It does not bump `inbox_version` (a bump per inbound message would make every staff reply fail
  with 409). Cost: one more `UPDATE` of the conversation per message; the worker already updates
  `turn_count` there. Conversations of other channels (the sandbox `web` channel) and console chat
  conversations stay `bot` forever; the trigger checks the channel and the console chat tables.
- **Explicit transitions in a new `csa/inbox_queue` module**, called by the code that knows the
  actor and the cause. Each writes an `inbox_queue_events` row; staff actions also bump
  `inbox_version`:

| Cause | Called from | Result |
|---|---|---|
| bot escalation | escalation agent | `needs_staff`, reason `escalation`, priority from the escalation |
| held reply created | approval creation | `needs_staff`, reason `held_reply` |
| takeover of a bot conversation | `/takeover` route | `needs_staff`, reason `manual`, claimed by the actor |
| person's reply | `/reply` route | `waiting_customer`; claims for the author if unclaimed; sets `last_staff_reply_at` |
| held reply approved or edited | approval settle | `waiting_customer`; sets `last_staff_reply_at`; claims for the decider when the decider is a console user |
| held reply rejected or expired | approval settle | stays `needs_staff`, reason `customer_message` (the customer got no answer) |
| hand back | `/release` route, staff-chat "Resume AI" | `bot`, claim cleared, escalation closed |
| resolve | queue routes, bulk | as hand back; refused while a held reply is pending or the customer wrote since the person loaded the item |
| snooze | queue routes, bulk | `snoozed` with `snoozed_until` |
| snooze ends | `inbox_snooze_wake` job (every minute, in PR 1) | `needs_staff` with a fresh clock |
| claim, unclaim, assign, priority, topic | queue routes, bulk | field change plus an event |

Messages that are not a person's answer never change the state: automated payment reminders and
notices, booking messages, staff-chat order decisions and the bot's own replies. The mute expiry job
changes `owned_by` and `status` as today and does not touch the queue state.

Inbound transition (the trigger): when the item is `waiting_customer`, `snoozed` or `resolved`, it
becomes `needs_staff` with reason `customer_message` and a fresh clock if a person owns the
conversation or a live held reply is pending, and `bot` otherwise. An item already `needs_staff` keeps
its reason and clock.

The decision of a held reply made outside the Inbox (Telegram staff-chat buttons, the staff assistant's
cards) goes through the same approval settle code, so it applies the same rows above.

Migration backfill (one statement per rule): `last_*` from the newest message; conversations that are
escalated and open, human-owned with an inbound newest message, or holding a live pending approval, and
whose triggering event is within the last 30 days, become `needs_staff` (`needs_staff_since` from the
open escalation, pending approval or newest customer message); human-owned conversations whose newest
message is from staff become `waiting_customer`; everything else, including older unhandled
escalations, is `bot`. No reminders are scheduled by the backfill itself (see 7.3).

### 5.3 Settings per company (`inbox_settings`, 0622; ships in PR 1 because `due_at` needs it)

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
| `sender` | `known` or `new` (unchanged rule; evaluated per scanned row, see 6.5) |
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
- Search: `pg_trgm` is already installed, so GIN trigram indexes on contact name, @username and
  `phone_digits` (the plain btree from 0581 does not serve a contains match). The national-form suffix
  rule for phones stays in the query.

The list query drops the per-row lateral lookups: last message and times come from the row, and the
pending approvals count is computed for the page of rows returned. The "known sender" check (orders,
subscriptions, email, address) cannot be stored on the row because other tables change it, so it is
computed per scanned row when the `sender` filter is on; the scan stops after one page, and a test with
50,000 conversations bounds that case. The existing exclusions (channel `telegram` or `whatsapp`, no
console chat conversations) stay in the query; the queue partial indexes never contain such rows.
Counts and queue tabs touch only the partial indexes, so `bot` and `resolved` rows (the large majority)
never enter them. A test seeds 50,000 synthetic conversations for one company and asserts each tab's
query plan uses its index and returns a page without a sort node.

### 6.6 Bulk actions

`POST /inbox/bulk` takes up to 50 conversation ids and one action: claim, assign (admin), release
claim, resolve, snooze, set priority, set topic. Each item runs in its own savepoint with its own
`inbox_version` check; the response lists per-item outcomes (done, version conflict, not allowed), so
one stale item does not fail the batch. Outcomes include `held_reply_pending` and `customer_wrote` for resolve and snooze. Bulk reply is not offered.

## 7. Reminders and notifications

### 7.1 The reminder layer (`csa/reminders`, generic)

Interface HR will use:

```python
register_kind(kind: str, handler: ReminderHandler)         # at worker start-up
await schedule(conn, client_id, kind, subject_type, subject_id, due_at, payload, dedupe_key)
await cancel(conn, client_id, kind, subject_type, subject_id)
```

- Table `reminders` (0623; tenant isolated): `kind`, `subject_type`, `subject_id` (opaque text),
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

1. **Console notification centre** (`console_notifications`, 0624; tenant isolated). One row per
   recipient: `user_id`, `kind`, `priority`, message key and parameters, link (route and parameters),
   `read_at`, `created_at`. A bell in the shell header shows the unread count; the panel lists the
   latest 50; a row opens the conversation (`/inbox?conversation=<id>`); "mark all read". Rows older
   than 30 days are purged by a job. Count refresh by polling, as the console does elsewhere.
2. **Company staff chat** through the existing `csa/notify`: matching targets, `min_priority`, quiet
   hours, outbox, retries. `format_notification` becomes language-aware through a `language` column on
   targets (`en` default, `ms`) and learns the new events.
3. **Email to a person** through the existing console mailer, off by default and set per person
   (`console_notification_prefs`, 0625): choose kinds, priority floor. Emails never include customer
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
| `inbox.handback` | `handback_prompt_hours` after the last staff reply while the item is `waiting_customer` and a person owns the conversation | assignee, else team (console only) | text: hand it back to the assistant |

The existing immediate events (`escalation`, `approval_needed`, `booking_pending`) stay as they are and
are not duplicated; the inbox reminders cover waiting time.

- **Existing items at rollout.** A one-time job in PR 2 schedules the overdue reminder for each
  backfilled `needs_staff` item with a fresh clock (due one response target after rollout), so no company
  receives a burst of reminders for old items.
- **Existing staff-chat targets.** A migration adds `inbox.overdue` to the events of existing enabled
  targets, as 0512 did for `booking_pending`; the other new kinds are console-only by default.
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
state. There is one Telegram staff chat per company, still set on the Channels page (its flow replaces
the previous chat); this page edits that chat's events, minimum priority, quiet hours and language.
Email targets (a shared mailbox address, sent through the console mailer) can be added, edited,
disabled and removed, several per company. WhatsApp and webhook targets exist in the schema but have no
sender, so the page does not offer them.
The same page holds the Inbox settings of 5.3 and each person's own notification preferences.

## 8. The reusable layer (statement for HR)

`csa/reminders` (timers, handler registry, firing, re-check, repeat and escalation) and
`console_notifications` (per-person inbox, preferences, email path) know nothing about conversations.
HR registers kinds such as `hr.due` or `hr.approval_pending`, calls `schedule` and `cancel` from its own
code, and supplies a handler that re-checks its subject and renders message keys in `en` and `ms`.
`import-linter` forbids the layer from importing `csa/console/inbox` and `csa/inbox_queue`. The design
of HR is not part of this project.

## 9. Delivery in three pull requests

1. **Queue and flow (migrations 0620 queue columns and indexes, 0621 events and saved views, 0622
   settings).** Queue columns, the inbound trigger, `csa/inbox_queue` with the explicit transitions
   wired into the escalation agent, approval creation and settle, the takeover, reply and release
   routes, the `inbox_snooze_wake` job, the new list, counts and bulk routes, the indexes, settings, the
   assignee list, and the Inbox UI: tabs with counts, filters, saved views, claim, snooze, resolve,
   priority and topic, bulk selection. No reminders yet.
2. **Reminders and the notification centre (0623 reminders, 0624 console notifications).**
   `csa/reminders`, the firing job, the Inbox reminder kinds, the one-time schedule for existing items,
   `console_notifications`, the bell and panel, the Overview strip and navigation badge, the
   language-aware staff-chat formatter with the new events, and the migration that opts existing
   targets into `inbox.overdue`.
3. **Targets page and delivery options (0625 target language and per-person preferences).** The
   notification targets page, the email sender and per-person email preferences.

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
13. *Takeover nobody follows up.* Person 1 takes over a bot conversation and never replies: it is
    `needs_staff` (reason `manual`, claimed), overdue after the normal target, and Person 1 is reminded.
14. *Unhandled escalation.* An escalation sits unhandled; after a day the mute expires and the bot answers
    again, and the item is still in Needs us until a person replies, snoozes or resolves it.
15. *Resolve races a new message.* Person 1 loads an item as waiting on customer; the customer writes;
    Person 1 presses Resolve: refused with `customer_wrote`, the item shows in Needs us.
16. *Resolve with a held reply.* An item with a pending held reply cannot be resolved until the reply is
    decided; in bulk it is reported as `held_reply_pending`.
17. *Not a staff answer.* An automated payment reminder to a customer in Needs us changes nothing: the
    item stays in Needs us and its reminders continue.
18. *Sandbox and console chat.* Sandbox (`web`) and console chat conversations never enter any tab or
    count.
19. *Rollout.* After the migration, a company with old unhandled items sees them in Needs us and
    receives no burst of reminders; each gets its first reminder one response target later.
20. *Replay safety.* Delivering the same reminder twice (worker crash after send) creates one console
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

- Payment proofs, order approvals and pending bookings as queue items (they stay on Payments,
  Orders and Bookings; the reminder layer can carry them later, and the Overview can show them
  beside the Inbox counts).
- Bot behaviour: escalation rules, replies, tone (a separate topic, see the customer-bot escalation
  work).
- Tickets as a console feature; the ticket and its SLA remain the escalation record and are not shown.
- Internal notes and @mentions on a conversation. The reminder layer takes an `inbox.mention` kind
  when notes exist; notes need their own privacy design so they cannot reach the customer.
- Business-hours response targets, automatic assignment (round robin), auto-resolve, auto hand-back.
- WhatsApp and webhook staff targets (no sender exists), per-person Telegram messages.
- Analytics and reporting on response times (the events table keeps the data for it).
- Read-only member access to the Inbox.

## 14. The queue screen (UI design for plan 1b)

Owner request (2026-11-17): make the Inbox tab look more professional while matching the console theme.
This section is the design for plan 1b. It uses only what exists: the `--fs-*` tokens (so every theme,
including Night and Bureau, keeps working), Space Grotesk titles and Inter text, Lucide icons, the
shared controls in `components/console/ui.tsx`, `StatusLabel` tones, `StatusChips`, `CustomerAvatar`,
`ListState`, the 40 px controls and 6 px control radius, and `ui-guide/UI_GUIDE.md` (quiet white
surfaces, thin borders, blue only for selection and interaction, meaning always in words, never in
colour alone). No new colours, no shadows except overlays, no new fonts.

### 14.1 What changes and why

Today the list pane carries a title, a labelled search box, four chips and a channel select above the
rows, and every row ends in four pills (held reply, sender, owner, channel). That is noisy for a work
queue and it has no room for state, age, priority or assignee. The redesign moves navigation to one bar
above the three panes, makes each row answer "what is this, how long has it waited, who has it", and
keeps the thread and customer panes as they are.

### 14.2 Layout (desktop at 1100 px and wider)

```
┌─ Customer inbox ───────────────────────────────────────────────────────────────────────┐
│ Needs us 12 · Mine 3 · Unassigned 9 · Overdue 2 │ Waiting 18 · Snoozed 4 · Resolved · All│
│ [🔍 Search name or phone]  [Filters ▾ 2]  [Views ▾]            Sort: Due soonest ▾      │
│ Filters: Assigned to me ✕  Priority high+ ✕                            Clear filters   │
├───────────────────────┬───────────────────────────────────────┬────────────────────────┤
│ ☐ (AM) Aina Demo  2h 10m│ Aina Demo · Telegram     Needs us · Overdue 1h             │ Customer          │
│   Please cancel my…     │ Assigned to you ▾     [Resolve] [Snooze ▾] [More ▾]        │ details           │
│   Escalated · High · You│ ─────────────────────────────────────────────────────────  │                   │
│ ☐ (BK) Ben Sample  20m  │  messages …                                                │                   │
│   Where is my order?    │                                                            │                   │
│   Customer wrote · Unass│  [reply box, shown after "Take over"]                      │                   │
└───────────────────────┴───────────────────────────────────────┴────────────────────────┘
```

- **Queue bar** (new, above the panes, full width). A tab strip with counts on the left (the first four
  are the work tabs; the rest sit after a thin divider), search, Filters, Views and Sort on the second
  line. It replaces the chips, the channel select and the "Conversations" heading in the list pane.
  Sticky while the list scrolls. The page title stays "Customer inbox".
- **List pane** is rows only, with the quiet white surface, hairline dividers and the existing blue
  selected row with the inset bar.
- **Thread and customer panes** keep their current behaviour and widths; the thread gets the new header
  of 14.4. The grid is the existing three-pane grid, with the queue bar added on top.
- **Between 768 and 1100 px:** two panes as today; the tab strip scrolls horizontally inside its own
  labelled region and Filters opens as a sheet. **Below 768 px:** the queue bar becomes a row of
  scrollable chips with counts, search is one line, Filters and Views open full-width sheets, and the
  thread opens full width with Back, as today. At 320 to 390 px nothing scrolls the page sideways.

### 14.3 The conversation row

Three lines, 72 to 80 px, comfortable at 1 or 2 pills, never four:

1. **Name** (semibold) and, right-aligned in tabular numerals, the **clock**: "2h 10m" (time waiting on
   us) in the Needs us, Mine, Unassigned and Overdue tabs; "last message 14 Nov" in the others. The
   absolute time is the element's title, in the company timezone (`Asia/Kuala_Lumpur` by default).
2. **Preview** of the last message, one line.
3. **Meta line**, left to right, each only when it applies:
   - **State** as a `StatusLabel`: "Needs us" (attention tone), "Waiting on customer" (neutral),
     "Snoozed until Thu 09:00" (info), "Resolved" (success). Bot-handled rows show no state label.
   - **Due**: "Due in 20 m" (neutral), "Due soon" (attention) in the last quarter of the window,
     "Overdue 1h" (error tone with the words). Only for Needs us rows.
   - **Priority**: shown only for High and Urgent, as an icon plus the word ("Urgent", "High"), never a
     colour stripe.
   - **Reason** in plain words when it is not obvious from the state: "Escalated", "Held reply",
     "Customer wrote", "Taken over".
   - **Assignee**: a 20 px initials avatar with "You" or the first name; absent when unclaimed.
   - **New sender** keeps its dashed neutral label (known customers show nothing: "Customer" on every
     row was noise). The **channel** becomes a small icon with a text tooltip and a visually hidden
     name, and the full channel pill stays in the thread header.
- A **checkbox** appears at the left on hover, on keyboard focus and whenever anything is selected
  (it is a real `input type=checkbox` with a name like "Select Aina Demo"); touch devices show it in
  a "Select" mode toggled from the queue bar so rows stay tappable.
- The unread-style emphasis is a heavier name weight only on rows whose newest message is from the
  customer and unanswered. No dots, no bold colour.

### 14.4 The thread header and actions

One header row above the messages:

- Left: customer name, channel label, then the **state** and **due** labels as in the row.
- Right: **one primary action** that depends on the item, then secondary actions:
  - unclaimed and in the queue: primary "Take it" (claims; a first reply also claims);
  - claimed by you: primary "Resolve";
  - claimed by someone else: primary "Take it from {name}" with a one-line notice "Assigned to {name}";
  - waiting on customer or snoozed: primary "Resolve" with "Reopen" in More;
  - bot-handled: primary stays the existing "Take over" button.
  - **Snooze ▾** menu: Later today (+3 h), Tomorrow 09:00, Next Monday 09:00, Pick a time (the existing
    date and time field patterns).
  - **More ▾**: Assign to… (admins), Give up this item, Set priority, Set topic, Hand back to the
    assistant.
- A **banner** under the header (the existing `Notice` pattern) when it helps: "Waiting on us for 3 h
  20 m, due 1 h ago", "A held reply needs a decision first" (when resolve is refused), "The customer
  wrote after you opened this. Reload to see it." (the `customer_wrote` case).
- Reply, takeover and held-reply cards keep today's behaviour and look.

### 14.5 Filters, views, sort

- **Filters** popover (a sheet below 768 px): Assigned to (anyone, me, unassigned, a person), Priority
  (any, high and above, urgent), Reason, Topic, Channel, Sender (anyone, customers, new senders),
  Overdue only, Waiting longer than. Applied filters appear as removable chips under the bar, each
  with its own accessible "Remove filter" name, plus "Clear filters". The Filters button shows the
  count of active filters.
- **Views** menu: the built-in tabs are not repeated here; it lists the person's saved views, "Save
  this view" (name field, max 60) and "Manage views" (rename, delete). Choosing a view sets tab,
  filters and sort at once.
- **Sort** dropdown: Oldest waiting, Due soonest, Newest activity, Priority (Priority is offered only
  in the Needs us tab, matching the API rule). Each tab opens with its default; the UI always sends
  `order` explicitly.
- Counts update after each action and on a gentle poll; the existing nudge animation runs only when a
  count rises and motion is allowed.

### 14.6 Bulk actions

Selecting rows replaces the queue bar's second line with the **bulk bar**: "3 selected", Claim,
Assign ▾ (admins only), Snooze ▾, Resolve, Clear. The result is one plain summary line with a list
of what did not apply and why in words ("2 resolved. 1 has a held reply: decide it first."). Failed
items stay selected so the person can act on them. The server caps at 50 ids; the UI disables select
all beyond the loaded page and says so.

### 14.7 States and copy

Empty, loading, partial, failed and forbidden keep the shared `ListState` presentations. New empty
copy per tab, plain words per `docs/CONSOLE_VOICE.md` and `docs/WRITING_GUIDE.md`: Needs us "Nothing
needs your team right now", Mine "Nothing is assigned to you", Overdue "Nothing is overdue", Waiting
"No one is waiting on a customer", Snoozed "Nothing is snoozed", Resolved "No resolved conversations
yet", with the assistant's resting avatar at 48 px beside the sentence in the Needs us tab only. A
failed counts request shows "–" and never zero.

### 14.8 Interaction, motion, accessibility

- Keyboard: the existing J and K and Escape stay; added C (claim), E (resolve), S (snooze menu) and `/`
  (search), active only when focus is not in a field or dialog (reusing `shortcutFor`), with a "?"
  help dialog listing them. Every action is also a visible button.
- The tab strip is a `tablist` (arrow keys move, Enter or Space selects, `aria-selected`, each tab
  named "Needs us, 12"), the list is the controlled panel; menus and sheets are native dialogs or the
  existing `SlideOver` with focus management and Escape.
- Motion: row removal and arrival use opacity over 180 ms on `--fs-ease-out`; nothing moves when
  `useMotionAllowed` is false; selected rows stay stable during refresh.
- Contrast: all label pairs come from `StatusLabel` tones (already contrast-tested in every theme); a
  node test lists the new classes against `theme-literals` so no hex or hsl appears.
- Performance: row components are memoised by id and version, the list is windowed beyond 200 loaded
  rows, and counts come from the one counts request, never from the loaded rows.

### 14.9 Not in this design

The notification bell and the notification centre (PR 2), the targets page (PR 3), internal notes,
customer timelines, and any change to the thread's message rendering, takeover flow or held-reply cards.

## 13. What is left to the builder

Exact column and index names and the mapping table from escalation reasons to topics; the keyset cursor
encoding; the wording of notification messages in English and Malay (plain words per
`docs/CONSOLE_VOICE.md`); the layout of the queue, the bell and the settings page within the existing
console design tokens; the choice between `pg_trgm` and the documented fallback once availability is
confirmed.
