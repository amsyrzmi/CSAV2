# Inbox refinement PR 3: notification settings and email delivery

Status: draft for owner approval, 2026-11-19. Parent design:
`docs/superpowers/specs/2026-11-16-inbox-refinement-design.md` (owner-approved; sections 7.2 to 7.4, 8
and 9 define PR 3). This document records the decisions made on 2026-11-19 and fills the gaps the code
survey found. Where it differs from the parent, this one wins for PR 3.

## 1. Intent

Plans 1a to 2b built the work queue, the reminders and the bell. A team still cannot choose who is told
what and when, cannot use email, and cannot see when a staff-chat target has stopped working. PR 3 gives
tenant admins one page to do those things, and gives each person an opt-in email channel for their own
notifications. Owner priority: an overdue alert must not silently disappear.

## 2. Goals, non-goals, anti-goals

Goals
- A Notifications page: where alerts go (targets), Inbox timing (the 5.3 settings), my notifications.
- Email as a delivery channel for targets and for people, with 5 attempts, backoff and a dead letter.
- A visible failure path: when a target dies, every tenant admin gets a bell notification.
- Close the parent's known limit: a staff-chat message held by quiet hours is dropped if the item has
  moved on by the time the window ends.

Non-goals
- Digest emails, business-hours targets, WhatsApp and webhook targets (no sender exists; the page does
  not offer them), per-person Telegram messages, changes to the Telegram connect flow on Channels.
- Any notification for a platform admin (they have no personal inbox; unchanged from 2b).

Anti-goals (a failure even if it works)
- An email that contains customer message text.
- A second alert flood: one email per notification is accepted, but a retry or a replay must never
  create a second outbox row, and a dead target must not raise one bell row per failed attempt. Delivery
  is at-least-once, as the Telegram sender is: a crash after the relay accepts a message and before the
  row is marked delivered can send it a second time.
- A page that lets a non-admin change a company's targets or timings.

## 3. Constraints

- One pull request, backend and screen together (owner choice A on 2026-11-19; the other options were
  3a/3b and a split by feature). One migration, 0626. `EXPECTED_SCHEMA_VERSION` is the highest shipped
  version and 0626 is not the highest, so it does not change (as 0623 to 0625 did not).
- The layer rules of section 8 of the parent hold: `csa/reminders` and `console_notifications` code
  stays conversation-agnostic; `import-linter` must stay green.
- Tenant isolation by RLS on every new table, `app.client_id` policy as for `console_notifications`.
- Language: the console screens, the bell and person email are English only (the console has no
  language setting). Staff-chat and email targets render in the target's own `language` (en or ms),
  as the Telegram chat does since 0625.
- The sign-in email lives in `console_users`, which `csa_app` cannot read. It is read at send time
  through a SECURITY DEFINER function added in 0626 (as 0624 did for `inbox_team_user_ids`) and is never
  copied into an outbox row.

## 4. Approaches

A. One PR, backend and screen (chosen). Largest diff; one plan, one review, one rollout with the
   migration. B. Two PRs, 3a backend and 3b screen (recommended by me; declined). C. Split by feature
   (targets page, email targets, personal email); rejected because the page would offer email targets
   before any sender exists. The owner chose A, accepting the larger review.

## 5. What gets built

### 5.1 Delivery

Retry policy for both email paths is the staff sender's: at most 5 attempts, delay
`min(300, 2^(attempts-1))` seconds, stale-claim reaper, then status `dead`. An outbox row stores the
message key and parameters, never rendered text and never an address.

Where email is sent from (my call): the `worker` service, which already builds a `Mailer` and holds the
SMTP secrets. The `delivery` service has neither and no route to the relay, and giving it both is a
compose and secrets change on two hosts for no gain. A new `NotificationMailer` loop in the worker
claims two outboxes: `staff_notifications` rows whose target channel is `email`, and the new person
outbox. The existing telegram claim SQL excludes email targets so `delivery` never claims them.

Staff-chat email targets: reuse `staff_notifications` as it is (same enqueue through
`csa/notify/notifier.py`, same idempotency key, same quiet-hours rule with urgent bypass). Status gains
`cancelled` (below). Email body: event label, priority, the customer's display name when the payload has
one, and a link to the conversation or page. Never message text. The renderer is written per event and
reads only whitelisted payload fields.

Person email: when `notify_people` writes a `console_notifications` row for a person who has email on,
whose kind is in their kinds and whose priority is at or above their floor, it inserts a row into
`console_email_outbox` in the same transaction, unique on the console notification id. Replay inherits
the console dedupe, so no second email. This applies to all six kinds, `inbox.assigned` included.

Quiet-hours limit closed: when a held `inbox.overdue` staff-chat or email message is released, the
sender re-checks the item before sending: the item is still `needs_staff` and its `needs_staff_since`
still equals the cycle recorded in the payload; if not, the row is set to `cancelled` and nothing is
sent. The payload therefore carries the conversation id and the cycle. The check is a callable the
Inbox side registers, so `csa/notify` and the worker loop do not import the Inbox. Rows never held, and
other events, are unchanged. A held row keeps `defer.sql` semantics (attempts are not consumed by quiet
hours), for email as for Telegram.

### 5.2 Dead letters

When a target's notification goes `dead`, and that target has no earlier unreported failure
(`staff_notification_targets.failure_notified_at` is null), every tenant admin of the company gets one
console notification of kind `notify.target_failed` (priority high, links to the Notifications page) and
`failure_notified_at` is set. A later delivered notification, a successful test or an edit clears
`failure_notified_at`. A dead person email sends that person, and only them, one `notify.email_failed`
row, guarded the same way by `console_notification_prefs.failure_notified_at`, which a delivered email or a save of the person's
preferences clears. The two failure kinds are bell only; they are not in the person's email kinds.
Both apply to Telegram
and email targets. The page shows "Last delivery failed" with the time and attempt count, derived from
the latest dead `staff_notifications` row of the target.

### 5.3 API (under `/console/v1/notifications`, new router file)

- `GET /targets`: any member. Channel, masked address, events, min priority, quiet hours, language,
  enabled, verified, last failure. Platform admin reads a company with `client_id` as the Inbox does.
- `PUT /targets/{id}`: tenant admin. Events, min priority, quiet hours, language, enabled. Version check
  as `inbox_settings` (409 on stale). Works for the Telegram chat and email targets.
- `POST /targets/email`: tenant admin. Adds an email target. A test email is sent first; if the relay
  refuses, nothing is saved (the pattern of `set_staff_chat`). Success sets `verified_at`.
- `POST /targets/{id}/test`: tenant admin. Sends a test; success sets `verified_at` and clears
  `failure_notified_at`.
- `DELETE /targets/{id}`: tenant admin, email targets only (the Telegram chat stays on Channels).
- `GET/PUT /preferences`: the signed-in person's own `console_notification_prefs`. Any active member
  including read-only; a platform admin gets 404.
- Inbox timing uses the existing `GET/PUT /console/v1/inbox/settings`; no change.
- Every write is audited; a disabled mailer (no SMTP) makes email routes answer a typed 503 and the page
  shows "Email is not set up on this deployment".

### 5.4 Migration 0626

- `console_notification_prefs(client_id, user_id, email_enabled default false, kinds text[] default all
  six, min_priority default 'high', failure_notified_at, updated_at)`, primary key (client_id, user_id),
  foreign key to `console_memberships`. A row exists only after a person saves. No row means email off.
- `console_email_outbox(id, client_id, user_id, notification_id unique, kind, priority, message_key,
  params, link, status, attempts, next_attempt_at, last_error, created_at)`, status as staff outbox.
  `notification_id` is a plain uuid, not a foreign key, so the 30-day notification purge is unaffected;
  the existing purge job also deletes outbox rows older than 30 days. The SECURITY DEFINER lookup of a
  member's sign-in email is part of this migration.
- `staff_notification_targets.failure_notified_at timestamptz null`.
- `staff_notifications.status` check gains `cancelled`.
- RLS, grants (`csa_app` DML, `csa_ro` select) as 0624. Down-notes as the other migrations.

### 5.5 Screen

New rail item "Notifications" beside Team and Password, shown to every role except platform admin for
the personal section; platform admins see the two company sections through the `client_id` convention.
Three sections on one page:
1. My notifications: email on or off, the six kinds as checkboxes, the priority floor (default High).
   Shown to every member. Copy states plainly that emails carry the customer's name, never their message.
2. Where alerts go: one row per target (Telegram chat, email targets) with events, min priority, quiet
   hours, language, enabled, verified, last failure. Admins edit, add and remove email targets, send a
   test. Staff read only.
3. Inbox timing: the nine settings of 5.3 with their ranges, "0 turns it off" where it applies. Admins
   edit; staff read.
Form patterns follow `BookingSettingsPanel` (read-only values for non-admins, editor for admins,
version conflict reloads). New pure logic in `lib/notifications-settings-view.ts` with node tests.
Browser tests follow `tests/test_bookings_browser.py` with the stub helper of 2b.

## 6. Decisions and calls

Owner, 2026-11-19: retry 5 times with backoff then a dead letter; bell notification to every tenant admin
when a target dies; one email per notification, floor defaults to High, no digest; option A.
My calls (recorded for review):
- Email is sent from the worker, and the sign-in email is read by a SECURITY DEFINER function, so no
  compose or secret change is needed.
- A successful add or test verifies an email target; an unverified email target does not receive.
- Dead-letter bell rows are once per failure episode (the flags above); the failure kinds are bell only.
- Person email is English only; target email uses the target's language. A per-person language is a
  follow-up if wanted.
- A new email target gets the Telegram chat's default events, `inbox.overdue` and `booking_pending`
  included, and the same events list is offered. There is no domain restriction on addresses, and a
  company may have at most 10 email targets. Any admin can address any external mailbox, which then
  receives customer display names and links; I judged that acceptable for a company's own admin.
- Delivery is at-least-once (a crash after the relay accepts can send one duplicate).

## 7. Rollout

Migration 0626 stops the four services during a staging rollout (the rollout tool handles it). No
compose or secret change (email sends from the worker, which already has them). Email is live on a
deployment only where `INVITE_MAIL_ENABLED` and the SMTP secrets already are. Order: staging first,
retest rows added to `docs/INBOX_RETEST.md`.

## 8. Left to the builder

File and function names, SQL, the exact email wording and subject lines, the claim SQL split, the
re-check query for held overdue messages, test layout, and component structure.
