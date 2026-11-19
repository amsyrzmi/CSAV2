# Inbox refinement PR 3: notification settings and email delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One Notifications page (my notifications, where alerts go, Inbox timing) and email delivery with 5 attempts, backoff and a dead letter, for company targets and for people.

**Architecture:** Migration 0626 adds per-person prefs, a person email outbox, a dead-letter flag on targets and a `cancelled` status. `notify_people` enqueues a person email in the same transaction as the console row. A new `NotificationMailer` loop in the `worker` (which already has the Mailer and SMTP secrets) sends both staff-target email and person email; the `delivery` Telegram sender keeps Telegram only. A new console router exposes targets and preferences; a new page consumes them.

**Tech Stack:** Python 3.12, FastAPI, psycopg3 with `db/sql/*.sql` named queries, Postgres RLS, Next.js App Router + TypeScript + Tailwind (`--fs-*` tokens), node:test, Python Playwright.

**Spec:** `docs/superpowers/specs/2026-11-19-inbox-pr3-delivery-design.md` (owner-approved 2026-11-19). Parent: `docs/superpowers/specs/2026-11-16-inbox-refinement-design.md` sections 7.2 to 7.4, 8, 9. Plan 2a/2b plans in the same folder for conventions.

## Global Constraints

- One PR, one migration `0626_inbox_delivery_settings.sql`; `EXPECTED_SCHEMA_VERSION` does NOT change (0626 is not the highest). Nothing else numbered 0626 to 0629 exists.
- Email never contains customer message text. It may name the customer by display name and links to `PUBLIC_BASE_URL` + `/portal/inbox?conversation=<id>` (tenant members are redirected away from `/inbox`, which drops the query) (or `/portal/notifications` for failure notices, which are bell only anyway).
- Retry policy (both email paths): at most 5 attempts, delay `min(300, 2 ** max(attempts - 1, 0))` seconds, stale-claim reaper at 180 s as `csa/notify/sender.py`, then status `dead`. Delivery is at-least-once; an outbox row is unique per notification (no second row on replay).
- Outbox rows store message key and parameters only: never rendered text, never an email address.
- Language: console screens, bell and person email are English only. Target email and staff chat use the target's `language` (en or ms) through `csa.notify.format`.
- Quiet hours: `csa.notify.sender.quiet_until` rule (urgent bypasses); a held row keeps `defer.sql` semantics (attempts not consumed).
- RLS: every new table has the `tenant_isolation` policy on `app.client_id`; grants `csa_app` DML, `csa_ro` SELECT (as 0624). `csa_app` cannot read `console_users`.
- Layering: `csa/notify` and `csa/reminders` must not import `csa/console/inbox*` or `csa/inbox_queue`; `import-linter` stays green. Wire Inbox-specific checks from `apps/*` by injection.
- Roles: tenant admin writes targets and Inbox timing; any member reads them; any active member (read-only included) reads and writes their own preferences; platform admin has no preferences (404) and reaches the two company sections with `client_id` as the Inbox does.
- Web: Tailwind with `--fs-*` tokens only (theme-literals test), lucide-react icons, form patterns of `components/console/bookings/BookingSettingsPanel.tsx`; new `.mjs`/browser tests registered in `scripts/check-isolated.mjs`. The local host cannot run `check-isolated.mjs`; CI's frontend job is the build gate.
- Test wrapper: `bash /tmp/claude-1000/-home-amsyrzmi-CSAV2/31dc140d-0929-4994-9647-6e6e38e51833/scratchpad/ci2.sh <command>` (npm/node are not on PATH otherwise). PR CI runs only changed test files; run the changed files and `make lint` locally.
- Commit trailers: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_012Xj1GQFBtX7jxjqQanM4hp`.

## Review Focus

Failure modes the spec implies that no task would test unless named. Each is pinned in the task named in brackets.
1. A replayed or crashed-and-retried enqueue makes one outbox row and one email, and a retry after a SMTP failure never makes a second row. [T2, T4]
2. A recipient who left, was disabled, or is a platform admin gets no outbox row; a person with no resolvable sign-in address at send time ends `dead` with `last_error='recipient_unavailable'` and raises no bell row. [T2, T5]
3. A hostile customer display name (newline, `<script>`, a 2000-character string, `\r\nBcc:`) cannot add a header, inject HTML or blow up the subject. [T3]
4. A held overdue message whose item was resolved or re-queued meanwhile is `cancelled`, never sent; urgent bypasses quiet hours; quiet hours never consume the 5 attempts. [T4, T7]
5. Two admins save the same target (409 on a stale version), another company's target id answers 404, a staff member's PUT answers 403, a read-only member can read and save their own prefs, a duplicate address answers 409, the 11th email target answers 409. [T8]
6. SMTP not configured: email routes answer a typed 503 and the page shows "Email is not set up on this deployment" with the email controls disabled, while Telegram targets and Inbox timing still work. [T8, T10]

---

### Task 1: Migration 0626 and its schema test

**Files:**
- Create: `db/migrations/0626_inbox_delivery_settings.sql`, `tests/test_inbox_delivery_schema.py`
- Modify: `db/migrations/README.md` (one bullet for 0626 in the style of 0623 to 0625)

**Interfaces:**
- Produces: tables `console_notification_prefs`, `console_email_outbox`; columns `staff_notification_targets.failure_notified_at timestamptz` and `staff_notification_targets.version int NOT NULL DEFAULT 0` (the optimistic-lock counter the API bumps on each edit), `staff_notifications.held_at timestamptz` (set when quiet hours defer a row); `staff_notifications.status` accepting `cancelled`; function `public.console_member_emails(p_client_id uuid, p_user_ids uuid[]) RETURNS TABLE (user_id uuid, email text)` (SECURITY DEFINER, enabled members of that company only, EXECUTE for `csa_app` only, `SET search_path = pg_catalog, public, pg_temp`, guarded by `app.client_id` as 0624 (call it inside `tx(app_pool, client_id)`)).

- [ ] **Step 1: Write `tests/test_inbox_delivery_schema.py`**, copying `tests/test_console_notifications_schema.py` for fixtures. Tests: `test_prefs_defaults_and_pk` (insert (client_id,user_id) only: `email_enabled` false, `kinds` equals the six inbox kinds, `min_priority` 'high'; second insert conflicts), `test_prefs_rejects_unknown_kind_and_priority`, `test_email_outbox_notification_id_unique`, `test_outbox_status_check` (pending, delivering, delivered, dead only), `test_staff_notifications_accepts_cancelled`, `test_target_columns_failure_notified_at_version_held_at` (nullable flag, version defaults 0, held_at nullable), `test_rls_isolates_prefs_and_outbox` (second company sees zero rows), `test_member_emails_function` (returns only enabled members of the named company, never another company's; `csa_app` can execute, `csa_ro` and PUBLIC cannot), `test_migration_is_idempotent` (apply twice).
- [ ] **Step 2: Run them; expect failure** (`relation ... does not exist`): `bash <wrapper> uv run pytest tests/test_inbox_delivery_schema.py -q`.
- [ ] **Step 3: Write the migration.** Also the three columns named under Produces. Header comment in the 0625 style (what, row security, pre-check, safe twice, rollback). `console_notification_prefs`: `(client_id, user_id)` primary key, FK `(user_id, client_id)` to `console_memberships` as 0624 does, `email_enabled boolean NOT NULL DEFAULT false`, `kinds text[] NOT NULL DEFAULT '{inbox.assigned,inbox.overdue,inbox.snooze_due,inbox.stale_claim,inbox.follow_up,inbox.handback}'` with a CHECK that every element is one of those six, `min_priority text NOT NULL DEFAULT 'high'` CHECK low/normal/high/urgent, `failure_notified_at timestamptz`, `updated_at timestamptz NOT NULL DEFAULT now()`. `console_email_outbox`: columns per the spec 5.4 (`notification_id uuid NOT NULL UNIQUE`, plain uuid, no FK; status CHECK pending/delivering/delivered/dead; `attempts int NOT NULL DEFAULT 0`; `next_attempt_at`, `locked_at`, `last_error`, `delivered_at`, `created_at`), index on `(status, next_attempt_at)`. The `cancelled` status: read the live constraint name and definition of `staff_notifications.status` the way `0531` reads its constraints, then replace it with the same list plus `cancelled`. Everything `IF NOT EXISTS`/guarded so a second run changes nothing.
- [ ] **Step 4: Run the schema test; expect PASS.** Also run `tests/test_staff_target_language_schema.py tests/test_bookings_bot_schema.py tests/test_console_notifications_schema.py` and the migration-runner test (`grep -l EXPECTED_SCHEMA_VERSION tests`) to confirm nothing pinned moved.
- [ ] **Step 5: README bullet, then commit** `feat(inbox): migration 0626 for notification prefs, email outbox and dead-letter flags`.

---

### Task 2: Preferences data layer and email enqueue

**Files:**
- Create: `csa/notify/prefs.py`, `db/sql/notify/prefs_get.sql`, `prefs_upsert.sql`, `email_outbox_put.sql`
- Modify: `csa/notify/people.py` (`notify_people`, `NotifyResult`), `db/sql/notify/put_console_notification.sql` only if it does not already RETURN the new row ids and recipient ids, `csa/jobs/console_notifications.py` plus a new `db/sql/notify/purge_email_outbox.sql` (purge outbox rows older than 30 days in the same job; the returned dict gains `email_purged`, `purged` keeps its meaning)
- Test: `tests/test_notify_people.py` (extend), `tests/test_notify_prefs.py` (new)

**Interfaces:**
- Produces:
  - `KINDS: tuple[str, ...]` (the six) and `PRIORITY_RANK: dict[str,int]` in `csa/notify/prefs.py`.
  - `@dataclass(frozen=True) class Prefs: email_enabled: bool; kinds: tuple[str, ...]; min_priority: str; version?`: no version; last write wins.
  - `async def get_prefs(conn, client_id: UUID, user_id: UUID) -> Prefs` (defaults when no row).
  - `async def save_prefs(conn, client_id: UUID, user_id: UUID, prefs: Prefs) -> Prefs` (upsert, sets `updated_at`, clears `failure_notified_at`).
  - `NotifyResult` gains `email_rows: int` (default 0, keyword-compatible with existing constructors).
- Consumes: `notify_people(...)` signature unchanged.

- [ ] **Step 1: Write failing tests.** `test_notify_people.py`: `test_person_with_email_on_gets_outbox_row` (prefs email_enabled, kind and priority qualify: one `console_email_outbox` row keyed to the console notification id, `message_key`/`params`/`link` copied, no address column populated), `test_floor_filters_priority` (floor high, priority normal: none; high: one), `test_kinds_filter` (kind not in kinds: none), `test_no_prefs_row_means_no_email`, `test_replay_makes_no_second_outbox_row` (call `notify_people` twice with the same dedupe key: still one row, `email_rows` 0 the second time), `test_left_or_disabled_member_gets_no_outbox_row`, `test_platform_admin_recipient_gets_none`, `test_every_kind_can_be_emailed` (loop the six). `test_notify_prefs.py`: defaults when absent, save then get round-trip, `save_prefs` rejects an unknown kind (`ValueError`) and priority, and saving clears `failure_notified_at`.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.** `email_outbox_put.sql` inserts one outbox row per just-written console notification whose recipient has `email_enabled`, `kind = ANY(kinds)` and priority rank at or above the floor, `ON CONFLICT (notification_id) DO NOTHING`, returning ids; `notify_people` runs it with the ids `put_console_notification.sql` returned, in the same connection (same transaction), and sets `email_rows`. Only rows actually written (not deduped) are enqueued.
- [ ] **Step 4: Run the two test files and `tests/test_console_notifications_*` / `tests/test_inbox_reminders.py`; expect PASS** (callers of `notify_people` must still work). Purge: extend the purge test for outbox rows older and younger than 30 days.
- [ ] **Step 5: Commit** `feat(inbox): per-person email preferences and outbox enqueue`.

---

### Task 3: Email rendering

**Files:**
- Create: `csa/notify/email_render.py`
- Test: `tests/test_notify_email_render.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class RenderedEmail: subject: str; text: str; html: str`.
  - `render_target_email(event: str, priority: str, payload: dict[str, Any], language: str, base_url: str) -> RenderedEmail`: the staff event (`escalation`, `approval_needed`, `order_submitted`, `dlq`, `circuit_opened`, `booking_pending`, `inbox.overdue`, plus a `test` event for the test mail); reads only a whitelist of payload keys per event (customer display name, ids for the link, counts, timestamps), never message text; wording en and ms (copy per `docs/WRITING_GUIDE.md`).
  - `render_person_email(kind: str, params: dict[str, Any], link: dict[str, Any], base_url: str) -> RenderedEmail` for the six kinds, English only.
  - `render_test_email(language: str) -> RenderedEmail`.
- The caller builds `csa.console.mailer.OutgoingMail`; this module imports nothing from `csa/console`.

- [ ] **Step 1: Write failing tests.** `test_no_message_text_for_any_event` (payload carrying `text`, `message`, `body`, `last_message` keys with a sentinel string: the sentinel appears in no output for any event or kind), `test_links_use_base_url` (conversation link `https://console.example/portal/inbox?conversation=<id>`; no double slash when `base_url` ends with `/`), `test_malay_target_email` (an en/ms pair differ and both contain the customer name), `test_hostile_display_name` (names `"A\r\nBcc: x@y"`, `"<script>alert(1)</script>"`, 2000 chars: `subject` has no `\r`/`\n` and is at most 200 chars; `html` contains the escaped form and no raw `<script>`), `test_unknown_event_gets_generic_mail` (no crash, no payload leak), `test_all_kinds_render`.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement** the three functions; subject truncation and one-line collapse reuse the idea of `csa/console/mail_templates._one_line`; HTML escapes every value; include a plain-text part.
- [ ] **Step 4: Run; expect PASS.** `make lint` on the file (import-linter).
- [ ] **Step 5: Commit** `feat(inbox): render notification emails without message text`.

---

### Task 4: Staff-target email delivery (worker loop)

**Files:**
- Create: `csa/notify/mail_sender.py`, `db/sql/notify/claim_email.sql`
- Modify: `db/sql/notify/claim.sql` (Telegram only: join the target and require `channel = 'telegram'`), `csa/notify/sender.py` (remove nothing; `unsupported_target_channel` stays for whatsapp/webhook), `apps/worker/main.py` (start the loop; one `Mailer` already exists there)
- Test: `tests/test_notify_mail_sender.py`

**Interfaces:**
- Consumes: `csa.notify.sender.quiet_until`, `MAX_NOTIFICATION_ATTEMPTS`, `NOTIFICATION_STALE_SECONDS`, `notify/defer.sql`, `mark_delivered.sql`, `mark_failed.sql`, `reap_stale.sql` (the reaper is shared; make `reap_stale.sql` channel-agnostic as it is), `csa.notify.email_render`, `csa.console.mailer.Mailer`/`OutgoingMail`.
- Produces: `class NotificationMailer` with `__init__(owner_pool, app_pool, mailer: Mailer, base_url: str, *, still_overdue: OverdueCheck | None = None)`, `async def do_work() -> bool` (claims and sends one staff-email row, then (T5) one person row; reaps and expires on the cadence of `StaffNotificationSender.do_work`), and `async def notification_mail_loop(...)` started as a background task of the worker. `OverdueCheck = Callable[[AsyncConnection, UUID, dict[str, Any]], Awaitable[bool]]`. A mail outcome other than `sent` maps to `NotificationFailure(error_code)` with the `MailOutcome` string as the code; `smtp_disabled` is not retried 5 times: it fails with the same retry so a later-enabled relay still delivers (note in a comment).

- [ ] **Step 1: Write failing tests** modelled on `tests/test_staff_path.py` (`_sender` helper, `owner_pool` fixture; use a fake `Transport` for the `Mailer`): `test_email_target_row_is_sent_and_marked_delivered` (subject and body from `render_target_email`, `to` is the target address, no message text), `test_unverified_email_target_is_not_sent` (row stays or is failed `target_unverified`, no mail), `test_disabled_target_is_not_sent`, `test_failure_retries_with_backoff_then_dead_letters` (5 failures: `attempts` 1..5, `next_attempt_at` delays 1,2,4,8 s, then `dead`), `test_replay_does_not_duplicate` (same idempotency key enqueued twice: one row, one mail), `test_quiet_hours_defers_without_consuming_attempts` (inside the window, normal priority: `pending`, `next_attempt_at` at the window end, `attempts` unchanged; urgent: sent now), `test_telegram_sender_never_claims_email_rows` (a `StaffNotificationSender.process_once` with only an email row pending returns False and leaves it `pending`), `test_stale_claim_is_reaped`.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.** `claim_email.sql` mirrors `claim.sql` for email targets only. Run the claim on the owner pool, the send inside `tx(app_pool, client_id)` as the Telegram sender does; mark delivered/failed through the same SQL files. Wire in `apps/worker/main.py`: `background.append(asyncio.create_task(notification_mail_loop(...)))` after the mailer is built, with `base_url=str(settings.PUBLIC_BASE_URL)`; when `mailer.enabled` is false the loop still runs (rows retry) but logs once at start.
- [ ] **Step 4: Run the new file, `tests/test_staff_path.py` and `tests/test_notify_people.py`; expect PASS.** Run `tests/test_worker*` if present.
- [ ] **Step 5: Commit** `feat(inbox): send staff-target notifications by email from the worker`.

---

### Task 5: Person email delivery

**Files:**
- Create: `db/sql/notify/email_outbox_claim.sql`, `email_outbox_mark_delivered.sql`, `email_outbox_mark_failed.sql`, `email_outbox_reap.sql`, `member_emails.sql` (calls `console_member_emails`)
- Modify: `csa/notify/mail_sender.py` (person path in `do_work`)
- Test: `tests/test_notify_mail_sender.py` (extend)

**Interfaces:**
- Consumes: T1 function `console_member_emails`, T3 `render_person_email`.
- Produces: person path of `NotificationMailer.do_work`; claims one `console_email_outbox` row (same shape of lease as staff: `delivering`, `locked_at`, `attempts + 1`), resolves the address through `console_member_emails` on the app pool inside `tx(app_pool, client_id)`, renders, sends, marks. No address is stored anywhere.

- [ ] **Step 1: Write failing tests:** `test_person_row_is_emailed_to_sign_in_address` (the fake transport saw the member's `console_users.email`; outbox row `delivered`; message text absent), `test_recipient_who_left_ends_dead_unavailable` (membership disabled after enqueue: row `dead`, `last_error='recipient_unavailable'`, no mail, no bell row), `test_person_retry_backoff_then_dead` (as T4), `test_reaper_for_person_outbox`, `test_person_mail_not_sent_when_smtp_disabled_retries`, `test_two_rows_two_mails_not_batched`.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement** the SQL files and the person branch; reaper runs for both outboxes on the existing cadence.
- [ ] **Step 4: Run the file; expect PASS.**
- [ ] **Step 5: Commit** `feat(inbox): deliver personal notification emails with retries`.

---

### Task 6: Dead-letter bell notices and flag handling

**Files:**
- Create: `csa/notify/failures.py`, `db/sql/notify/target_failure_mark.sql`, `target_failure_clear.sql`, `person_failure_mark.sql`, `person_failure_clear.sql`
- Modify: `csa/notify/sender.py` (`_fail` when dead, `reap` for rows that become dead, `mark_delivered` path clears the flag), `csa/notify/mail_sender.py` (same hooks for both email paths), `db/sql/notify/mark_delivered.sql` untouched (clear in Python in the same transaction)
- Test: `tests/test_notify_failures.py`

**Interfaces:**
- Produces: `async def report_dead_target(conn, client_id: UUID, target_id: UUID) -> bool` (atomically sets `failure_notified_at` only if null; when it set it, writes one `notify.target_failed` console notification to every tenant admin via `notify_people`, priority `high`, params `{target_id, channel, attempts}`, `message_key='notify.target_failed'`, link `{"route": "notifications"}`, dedupe `notify.target_failed:{target_id}:{epoch}` where epoch is the flag timestamp; returns whether it notified); `async def report_dead_person_email(conn, client_id, user_id) -> bool` (same, one `notify.email_failed` row to that person only, bell only, never enqueues email); `async def clear_target_failure(conn, client_id, target_id)`, `clear_person_failure(conn, client_id, user_id)`.
- Consumes: `csa.notify.people.admin_user_ids`, `notify_people`.
- The two failure kinds are not among the six person-email kinds and never enter an outbox.

- [ ] **Step 1: Write failing tests:** `test_first_dead_notifies_every_admin_once` (two admins, one staff: two rows, staff none; a second dead row for the same target: no new rows), `test_success_clears_flag_so_next_failure_notifies_again`, `test_test_send_and_edit_clear_flag` (call the clear function), `test_dead_telegram_target_also_notifies`, `test_reaped_dead_row_notifies`, `test_dead_person_email_notifies_only_that_person_and_no_email_row`, `test_save_prefs_clears_person_flag` (uses T2 `save_prefs`), `test_failure_kinds_are_not_emailable` (`prefs.KINDS` excludes both).
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement** and hook the five call sites; every hook runs in the transaction that records the dead status where possible, otherwise immediately after, tolerant of a retry (the flag makes it idempotent).
- [ ] **Step 4: Run the file and `tests/test_staff_path.py`; expect PASS.**
- [ ] **Step 5: Commit** `feat(inbox): tell tenant admins when a notification target stops working`.

---

### Task 7: Held overdue messages are re-checked

**Files:**
- Create: `csa/inbox_queue/overdue_check.py`, `db/sql/inbox/overdue_still_open.sql`
- Modify: `csa/inbox_queue/reminders.py` (`handle_overdue` staff payload gains `conversation_id` and `cycle` = the item's `needs_staff_since` ISO string, ids and timestamps only), `csa/notify/sender.py` (`StaffNotificationSender.__init__` gains `still_overdue: OverdueCheck | None = None`; the check runs only for a row that was held and is released), `csa/notify/mail_sender.py` (same rule), `apps/delivery/main.py` and `apps/worker/main.py` (inject `still_overdue`), `db/sql/notify/defer.sql` (also sets `held_at = now()`), a new `db/sql/notify/cancel.sql` (status `cancelled`), and `db/sql/notify/claim.sql`/`claim_email.sql` (RETURN `held_at`)
- Test: `tests/test_overdue_recheck.py`

**Interfaces:**
- Produces: `async def still_overdue(conn, client_id: UUID, payload: dict[str, Any]) -> bool` in `csa/inbox_queue/overdue_check.py`: true only when the conversation is `needs_staff` and its `needs_staff_since` equals `payload["cycle"]`; false for missing keys. A row is "released" when its claim returns a non-null `held_at`; only those are re-checked.

- [ ] **Step 1: Write failing tests:** `test_released_overdue_is_sent_when_still_open`, `test_released_overdue_is_cancelled_when_resolved`, `test_cancelled_when_requeued_new_cycle`, `test_cancelled_when_snoozed`, `test_row_never_held_is_not_rechecked` (check callable raises if called), `test_other_events_never_rechecked`, `test_urgent_bypass_not_rechecked`, `test_both_senders_cancel_the_same_way` (parametrize Telegram and email).
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.** Update `tests/test_inbox_reminders.py` expectations only where the staff payload shape is pinned.
- [ ] **Step 4: Run the new file, `tests/test_inbox_reminders.py`, `tests/test_staff_path.py`, `tests/test_notify_mail_sender.py`; expect PASS.** `make lint` (import-linter: `csa/notify` must not import the Inbox).
- [ ] **Step 5: Commit** `feat(inbox): drop held overdue alerts whose item has moved on`.

---

### Task 8: Notifications API

**Files:**
- Create: `csa/console/notification_targets.py` (router `build_notification_targets_router()` under prefix `/notifications`, mounted next to `build_notifications_router()` in `csa/console/router.py`), `db/sql/console/notification_targets_*.sql` (list, get, update, insert_email, delete, count_email, last_failure)
- Modify: `csa/console/router.py`, `csa/console/notifications.py` only if route order needs it (`/notifications/targets` and `/preferences` must not be shadowed)
- Test: `tests/test_notification_targets_api.py`

**Interfaces:**
- Consumes: `csa.notify.prefs` (T2), `csa.notify.failures.clear_target_failure` (T6), `csa.notify.email_render.render_test_email` (T3), `Mailer` from app state (as the gateway builds it), `inbox_admin`-style dependency for tenant admin (extract or reuse), audit helper `kb_uploads.audit`.
- Produces (JSON, snake_case): `GET /notifications/targets` → `{targets: [{id, channel, address_masked, events, min_priority, quiet_hours, language, is_enabled, verified, version, last_failure: {at, attempts} | null}], email_available: bool, events_offered: [str]}`; `PUT /notifications/targets/{id}` body `{events, min_priority, quiet_hours|null, language, is_enabled, version}` (events validated against `events_offered`, quiet hours `{start:"HH:MM", end:"HH:MM", tz}` with a valid IANA tz, start != end); `POST /notifications/targets/email` body `{address, events?, min_priority?, quiet_hours?, language?}` (sends a test email first; a relay refusal answers 502 `email_test_failed` and saves nothing; success saves enabled and verified with the Telegram chat's default events including `inbox.overdue` and `booking_pending`; 409 `duplicate_address`; 409 `too_many_email_targets` at 10); `POST /notifications/targets/{id}/test` (sets `verified_at`, clears the failure flag, Telegram targets: answers 400 `test_not_supported`); `DELETE /notifications/targets/{id}` (email only, 400 for Telegram); `GET/PUT /notifications/preferences` ↔ `{email_enabled, kinds, min_priority}` (`kinds` subset of the six). Every email route answers 503 `email_not_configured` when `Mailer.enabled` is false; `GET` still works with `email_available: false`. `version` is the T1 column: an update with a stale version answers 409 `version_conflict` and a good one bumps it. Masking: `a***@domain` for email, last 4 characters for a Telegram chat id.

- [ ] **Step 1: Write failing tests** in the style of `tests/test_inbox_queue_api.py:167-211` (`w.mutate`, `w.admin`, staff, read-only fixtures): list shape and masking; `test_put_target_stale_version_409`; `test_staff_put_403_but_get_ok`; `test_other_company_target_404`; `test_invalid_quiet_hours_and_events_422`; `test_add_email_target_sends_test_first_and_saves_verified`; `test_add_email_relay_failure_saves_nothing_502`; `test_duplicate_address_409`; `test_eleventh_email_target_409`; `test_test_endpoint_verifies_and_clears_failure`; `test_delete_email_ok_telegram_400`; `test_email_routes_503_when_mailer_disabled_but_list_works`; `test_preferences_roundtrip_read_only_member_allowed`; `test_preferences_platform_admin_404`; `test_audit_rows_written_for_each_write`; `test_target_edit_clears_failure_flag`.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement** the router and SQL. Platform admin passes `client_id` the way `/inbox` routes resolve it; writes by a platform admin are allowed (as `inbox_admin` allows them).
- [ ] **Step 4: Run the file plus `tests/test_console_notifications_api.py` and the OpenAPI/contract tests (`grep -l openapi tests`); expect PASS.**
- [ ] **Step 5: Commit** `feat(inbox): notification targets and preferences API`.

---

### Task 9: Web client, view logic and nav

**Files:**
- Create: `CSA_WebConsole/csa-console/web/lib/notifications-settings-api.ts`, `lib/notifications-settings-view.ts`, `tests/notifications-settings-view.test.mjs` (beside `tests/notifications-view.test.mjs`)
- Modify: `lib/navigation.ts` (`NavKey` gains `"notifications"`; entry `{ key: "notifications", label: "Notifications", href: to("notifications") }`, shown to every role including platform admin (the page hides the personal section for them), placed beside Team and Password), the navigation tests that enumerate labels (`tests/test_nav_groups_browser.py`, `tests/test_workspace_contracts.py` contract regex; update, do not weaken)

**Interfaces:**
- Produces: types `NotificationTarget`, `TargetsResponse`, `Preferences`; functions `getTargets`, `saveTarget`, `addEmailTarget`, `testTarget`, `deleteTarget`, `getPreferences`, `savePreferences` (all through `lib/api.ts` conventions, `client_id` passthrough for platform admin); pure view functions `eventLabel(event: string): string` (plain-language labels, e.g. `inbox.overdue` → "Overdue customer replies"), `kindLabel(kind)`, `priorityLabel`, `failureLine(t: NotificationTarget): string | null` ("Last delivery failed, 5 attempts, 18 Nov 14:02"), `quietHoursText(q)`, `validateQuietHours(start,end,tz)`, `validateEmailAddress(s)`, `timingFields` (the nine Inbox settings with label, unit, min, max, "0 turns it off" flag) and `validateTiming(values)` returning field errors.

- [ ] **Step 1: Write failing node tests:** labels for every event and kind (unknown event returns the raw key, never throws); `failureLine` null when no failure; quiet hours same start and end invalid; email address validator rejects `a@b`, spaces and over-254 characters; `validateTiming` enforces the ranges of spec 5.3 exactly (urgent..low 5 to 10080, repeat 15 to 1440, max_repeats 0 to 10, follow_up 0 to 720, stale_claim 0 to 168, handback 0 to 720).
- [ ] **Step 2: Run; expect FAIL:** `bash <wrapper> node --test tests/notifications-settings-view.test.mjs` from the web dir.
- [ ] **Step 3: Implement** the files; navigation entry gets a bell-free icon-less row like its siblings.
- [ ] **Step 4: Run node tests, `tsc --noEmit` and eslint via the wrapper; expect PASS.**
- [ ] **Step 5: Commit** `feat(inbox): notification settings client and view logic`.

---

### Task 10: Notifications page

**Files:**
- Create: `app/portal/notifications/page.tsx`, `app/(admin)/notifications/page.tsx` (twins; server components using `requireIdentity()` like `app/portal/password/page.tsx`), `components/console/notifications/NotificationsWorkspace.tsx`, `MyNotificationsPanel.tsx`, `TargetsPanel.tsx`, `InboxTimingPanel.tsx`, `tests/test_notifications_settings_browser.py`
- Modify: `scripts/check-isolated.mjs` (register the new node test and the browser test), the nav registration done in T9

**Interfaces:**
- Consumes: T9 client and view functions; `components/console/ui.tsx` (`Heading`, `Panel`, `Notice`, `action`, `field`), `components/console/kit`.
- Produces: the page. Section 1 "My notifications" (hidden for platform admin): email switch, six kind checkboxes, priority floor select (default High), save; copy: "Emails name the customer. They never contain the customer's message." Section 2 "Where alerts go": one card per target: channel, masked address, events as checkboxes, minimum priority, quiet hours (start, end, timezone), language (English, Malay), enabled, verified chip, "Last delivery failed" line; admins edit/save, "Send test" and "Remove" on email targets, "Add an email address" form; staff and read-only see read-only values; a note for the Telegram chat: "Connect or change the chat on Channels." Section 3 "Inbox timing": the nine settings, read-only for non-admins, `0 turns it off` helper text where it applies, 409 reloads. Email controls show "Email is not set up on this deployment" and are disabled when `email_available` is false.

- [ ] **Step 1: Write the failing browser test** (copy the stub style of `tests/test_bookings_browser.py` and the 2b helper `tests/shell_counts_stub.py`; one module owns its routes): staff sees read-only and no Save; admin edits a target (assert the PUT body, including `version`) and sees a 409 reload; add email target happy path and relay-failure notice; "Send test" updates the verified chip; remove asks for confirmation; person section saves prefs and floor default is High; read-only member can save prefs but not targets; platform admin sees sections 2 and 3 only; email-unavailable state disables email controls; a failed target shows the failure line; keyboard: every control reachable, Save buttons named; mobile 375 px: no horizontal overflow (wait on animations as in 2b's mobile test); no console errors. Run against `next dev`, so effects mount twice: assert request lists by content, not count.
- [ ] **Step 2: Run; expect FAIL** (`bash <wrapper> uv run pytest tests/test_notifications_settings_browser.py -q`).
- [ ] **Step 3: Implement** the components with `--fs-*` tokens only; the rail row appears for every role.
- [ ] **Step 4: Run the browser test, the theme-literals test, `tests/test_nav_groups_browser.py`, `tests/test_console_shell_browser.py`, tsc and eslint; expect PASS.** Register the tests in `check-isolated.mjs` and confirm the registration lists agree (`node scripts/check-isolated.mjs --list` if available, otherwise read the file's own consistency check).
- [ ] **Step 5: Commit** `feat(inbox): notifications settings page`.

---

### Task 11: Docs and retest rows

**Files:**
- Modify: `docs/PROGRESS.md` (entry for PR 3), `docs/INBOX_RETEST.md` (new section "Delivery and settings rows (PR 3)", rows marked not run: add an email target and receive the test mail; kill the relay and watch 5 attempts then the admin bell notice; personal email on with floor High; held overdue alert cancelled after resolve; Telegram chat edit; Inbox timing save with a 409), `docs/superpowers/specs/2026-11-16-inbox-refinement-design.md` (note under 7.4 and the closed known limit in 7.3), `db/migrations/README.md` already by T1
- No code.

- [ ] **Step 1: Write the entries**, keep them factual: what shipped, the five owner decisions and my calls from the design doc section 6, rollout (migration 0626 stops the four services; no compose or secret change; email works where `INVITE_MAIL_ENABLED` and the SMTP secrets already are).
- [ ] **Step 2: Run `make lint` and the docs link checks if present; expect PASS.**
- [ ] **Step 3: Commit** `docs(inbox): record notification settings and email delivery`.
