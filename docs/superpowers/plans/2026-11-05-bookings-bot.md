# Bookings bot, reminders and assistant (stage 1, PR 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A customer on Telegram or WhatsApp can ask what's free, book, change or cancel, and the booking is real when the bot says so. Customers and staff get reminders, and staff can book through the assistant at the side of the screen.

**Architecture:** A new `csa/bookings/bot.py` holds the customer rules on top of the PR 1 core, which stops assuming a console user. The agent obeys the reply mode itself, because agents run before the gate. A background job queues reminders, and customer messages go through the existing delivery path so they appear in the Inbox.

**Tech Stack:** Python 3.12, FastAPI, psycopg3, Postgres 16 with RLS, Next.js with node:test, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-04-bookings-design.md`

## Global Constraints

- Migrations stay in 0510-0519: this plan uses 0512. `EXPECTED_SCHEMA_VERSION` stays "0530". It must work on a fresh database, where 0512 runs before 0520.
- Every new table has `client_id`, row security with the usual `tenant_isolation` policy, and grants to `csa_app` only as needed. Never `FORCE`. No new roles. SQL lives in `db/sql/bookings/`, parameterised.
- Reply modes, exactly: `shadow` (Off) writes nothing and sends nothing automatic. `suggest` (Staff approve each reply) holds the slot as `pending`. `auto` is `confirmed`, or `pending` if the type needs confirmation.
- The bot overrides nothing: every finding refuses, and a time beyond `max_advance_days` is refused. Double bookings are refused for everyone.
- The server checks every time and slot. The model can't invent one. "Confirmed" appears in a fact only when a `confirmed` row exists.
- Leave: read only the person and the dates. No response, fact, message or mail says why someone is away, and the words "leave" and "on_leave" never reach a customer or a screen.
- Private meetings never reach the bot. A customer sees only their own contact's bookings.
- A staff member's own action may tell a customer, even with replies Off. Nothing automatic reaches a customer while replies are Off. A company with no Customer support package has no replies setting, so email reminders to its customers still go (see Task 5).
- Telegram gets proactive messages. WhatsApp gets none in this PR: Meta templates wait for business verification. WhatsApp customers get email.
- The Overview preview keeps blocking bookings: `csa/agents/runner.py:115` stays as it is.
- Copy, exactly: "That time was just taken.", "Not available that day.", "Customer wasn't told.". The role is "Admin", not "tenant admin".
- Don't touch the Google OAuth client.
- New screens use `components/console/kit/` and theme tokens, and work in all three themes and at 320 px.
- Synthetic fixtures only. No real address: the repository is public. Tests never reach a real Telegram or SMTP server.
- CI is the merge gate. Locally run each changed test file on its own with `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q <file>`, plus `make lint` (never piped) and the web checks. Never run the full suite. Before changing wording, search tests for the old text.
- Commit messages end with both lines: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_012Xj1GSBskmizHEtGV54ZTA`.

## Review Focus

1. **A retried or resumed turn.** The same customer message never books twice, and the replay answers with the booking it already made, rather than "That time was just taken." (Task 2)
2. **The reply stating a time.** The composed reply passes the grounding check: each date and time in the reply appears in a fact in one fixed format, in the company's zone. (Task 3)
3. **A booking moved or cancelled between queuing and sending a reminder.** No stale reminder goes out, and a moved booking gets a new one for its new time. (Task 5)
4. **Replies switched Off, or Customer support suspended, after a booking exists.** No automatic message reaches the customer. Staff still get theirs. (Task 3 and Task 5)
5. **A customer naming someone else's booking id.** Nothing is read or changed, and the reply doesn't reveal it exists. (Task 2)

---

### Task 1: Schema and settings

**Files:**
- Create: `db/migrations/0512_bookings_bot.sql`, `db/sql/bookings/settings_*.sql`, `tests/test_bookings_bot_schema.py`
- Modify: `csa/bookings/api.py`, `csa/bookings/contracts.py`, `csa/bookings/setup.py`, `tests/test_bookings_setup.py`, `db/migrations/README.md`, `docs/CONSOLE_API.md`, `docs/console_openapi.json`

**Interfaces:**
- Produces on `bookings`: `turn_key text NULL` with a partial unique index on `(client_id, turn_key)`; `replaces_booking_id uuid NULL`, a foreign key to `(client_id, id)`; `conversation_id uuid NULL`; `expires_at timestamptz NULL`.
- Produces `booking_settings(client_id PRIMARY KEY, reminder_minutes integer DEFAULT 1440, pending_hours integer DEFAULT 24, customer_limit integer DEFAULT 5, version)`.
- Produces `booking_reminders(client_id, id, booking_id, starts_at, audience, channel, address NULL, status, created_at, sent_at NULL)`, unique on `(client_id, booking_id, starts_at, audience, address)`, with `audience IN ('customer','staff')`, `channel IN ('telegram','email')`, `status IN ('queued','sending','sent','failed','dropped')`.
- Rebuilds `console_proposals_kind_check` and `console_workspace_turns_action_check` to add `booking_action`, copying the lists from their latest definition in 0521.
- Adds `booking_pending` to `staff_notification_targets.events`: to the column default, and to existing targets that already have `approval_needed`.
- Produces `GET, PUT /console/v1/bookings/settings`, returning `BookingSettingsOut {reminder_minutes, pending_hours, customer_limit, version, replies_off: bool}`. `replies_off` is true only when the company has Customer support active or suspended, and its mode is `shadow`. Writes are Admin or platform admin. All members can read.

- [ ] **Step 1: Write the failing tests:** each new table is tenant isolated; `turn_key` is unique per company and repeats across companies; `test_settings_defaults_when_none_stored`; `test_only_admins_change_settings`; `test_a_stale_settings_version_gets_409`; `replies_off` for Off, approve, auto, suspended and Bookings-only companies; ranges refused with 422: `reminder_minutes` 15 to 10080, `pending_hours` 1 to 168, `customer_limit` 1 to 50.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Follow 0510's header and grants. Add the range note to the README. Run `tests/test_console_contract_snapshot.py`, regenerating the OpenAPI file if asked.
- [ ] **Step 4: Run** the two test files, `tests/test_migrate.py` and `make lint`. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(bookings): settings, and the columns the bot and reminders need`.

### Task 2: The customer rules

**Files:**
- Create: `csa/bookings/bot.py`, `tests/test_bookings_bot.py`
- Modify: `csa/bookings/service.py`, `db/sql/bookings/booking_insert.sql`, `csa/console/kb_uploads.py` only if the audit needs a null actor, `tests/test_bookings_api.py`

**Interfaces:**
- Consumes `free_slots`, `pick_person`, `findings_for`, `decide("bot", ...)`, and the holds and lock path in `service.py`.
- Produces in `service.py`: a dataclass `Author(acting: Actor, user_id: UUID | None, source: Literal["staff","assistant","bot"])`. `create_booking` and `move_booking` keep their signatures for the routes and call internal functions taking `Author`. The internal create takes `force_pending: bool`, `turn_key: str | None`, `conversation_id: UUID | None`, `expires_at: datetime | None`. Add `max_advance_days` to the bot's checks only, as a refusal.
- Produces in `csa/bookings/bot.py`, each on the caller's connection:
  - `reply_mode(conn, client_id) -> Literal["shadow","suggest","auto"]`, from `turn/get_client_policy.sql`, with the same default as `Policy`.
  - `customer_service_on(conn, client_id) -> bool`: Customer support is active, using `console/service_package_any_in_status.sql`.
  - `customer_types(conn, client_id) -> list[BookingTypeOut]`: active, audience `customers` or `both`.
  - `offer(conn, client_id, *, type_id, day_from, now, limit=5) -> list[Offer]`, with `Offer(starts_at, ends_at, label)`. It names a person only for a `named` type.
  - `book(conn, client_id, contact_id, *, type_id, starts_at, turn_key, conversation_id, now) -> BotOutcome`.
  - `change(conn, client_id, contact_id, booking_id, *, starts_at, turn_key, now) -> BotOutcome`.
  - `cancel(conn, client_id, contact_id, booking_id, *, turn_key, now) -> BotOutcome`.
  - `BotOutcome(code, booking: BookingRecord | None, offers: list[Offer])`, with `code` in `confirmed, pending, moved, change_pending, cancelled, replay, replies_off, service_off, slot_taken, not_allowed, limit_reached, past_cutoff, not_found, overlaps_itself`.

- [ ] **Step 1: Write the failing tests,** at this level, with no turn:
  - The mode table: `shadow` writes nothing and returns `replies_off`; `suggest` gives `pending` with `expires_at`; `auto` gives `confirmed`, or `pending` for a type needing confirmation.
  - `test_customer_support_suspended_writes_nothing` returns `service_off`.
  - `test_the_same_turn_key_replays_the_booking`: a second `book` with the same key returns `replay` with the same booking, and holds are unchanged, even though the slot is now taken. This covers Review Focus 1.
  - `test_two_customers_race_for_the_last_slot`: one wins, the other gets `slot_taken` with fresh `offers`.
  - The bot is refused outside hours, on a closed day, inside notice, beyond `max_advance_days`, and for a person on approved leave. Each is `not_allowed` with fresh `offers`, and no reason.
  - `test_any_free_person_is_named_only_once_booked`; `test_the_customer_limit_refuses_more`.
  - `change`: `auto` with no confirmation moves in place. Otherwise a second `pending` row with `replaces_booking_id` holds the new slot, and the old one stays confirmed. A new time overlapping the old slot gives `overlaps_itself`. Past the type's `change_cutoff_minutes` gives `past_cutoff`.
  - `cancel` frees the slot in `auto` and `suggest`, and gives `replies_off` in `shadow`.
  - `test_another_customers_booking_is_not_found`, for `change` and `cancel`. A private meeting and a staff `pending` booking also give `not_found`.
  - Bookings from the bot have `source='bot'`, `created_by` null, and an audit row with the id and action only.
  - Existing `tests/test_bookings_api.py` still passes, with staff bookings `source='staff'`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Look up `turn_key` before taking locks, so a replay returns before the time checks. Keep `service.py`'s staff behaviour unchanged.
- [ ] **Step 4: Run** `tests/test_bookings_bot.py`, `tests/test_bookings_api.py` once, `tests/test_booking_rules.py`, and `make lint`. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(bookings): the customer's rules, obeying the reply mode`.

### Task 3: The agent

**Files:**
- Modify: `csa/agents/staged/service.py`, `csa/agents/staged/models.py`, `csa/turn/deps.py`, `csa/console/agents.py`, `csa/console/packages.py`, `db/sql/agents/booking_list.sql`, `db/sql/agents/booking_get.sql`, `tests/test_staged_agents.py`, `tests/test_coordinator_acceptance.py`
- Create: `db/sql/bookings/contact_details_fill.sql`

**Interfaces:**
- Consumes Task 2's `bot` functions.
- Produces `BookingInput.action` as `Literal["availability","book","reschedule","cancel","list","status"]`, with `type_id`, `day`, `starts_at`, `booking_id`, `customer_name`, `customer_email` (using the shared `Email` check). `ACTION_RISK`: `availability`, `list` and `status` are `read`; the others are `commit`. `side_effects="irreversible"`, `max_retries=0`, `timeout_ms=25000`.
- Produces `format_slot(starts_at, zone) -> str` in `csa/bookings/bot.py`, for example "Tuesday 10 November 2026, 3:00 pm". Every fact stating a time uses it.
- Produces `BookingAgent(mailer: Mailer | None = None)`. With no mailer, mail reports `"off"`. Task 5 builds the worker's mailer and passes it through `csa/turn/deps.py`.

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_staged_agents.py`: each `BotOutcome` code maps to a fact the reply can use. `confirmed` says confirmed. `pending` says "not confirmed yet". `replies_off` and `service_off` give no booking claim. `slot_taken` carries "That time was just taken." and fresh times. `past_cutoff` and `overlaps_itself` set `requires_human` with a `handoff_reason`.
  - `test_availability_with_no_type_lists_what_can_be_booked`.
  - `test_a_whatsapp_booking_asks_for_an_email`: on `whatsapp` with no contact email, the fact asks for one, and booking still succeeds. `test_the_bot_asks_for_a_name_it_does_not_have`: with no contact name, `book` returns a fact asking for it before booking. `customer_name` and `customer_email` fill only empty contact fields.
  - `list` and `status`: the customer's own bookings by `contact_id`, excluding `private`, and excluding `pending` unless `source='bot'`. Staff-made confirmed bookings now appear. The earlier `created_by IS NULL` tests change: this is the planned change.
  - `test_a_confirmed_booking_sends_the_invitation` with a fake transport: mail goes after commit, and a mail failure doesn't change the result.
  - `tests/test_coordinator_acceptance.py`: a full turn with a fake model books a slot, and the reply passes `ungrounded()` with the exact time from the fact. A full turn in `shadow` writes nothing and sends nothing.
  - `tests/test_workspace_planner.py` or `tests/test_console_chat.py`: a preview still can't book.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Pass `ctx.idempotency_key` as `turn_key`. Commit the transaction, then call `notify_after_commit` for a change that needs mail. Rewrite `CAPABILITIES["booking_agent"]`, `csa/console/agents.py:35-39` and the package summary to say what's now true, searching tests for the old text first. `db/sql/agents/preparation_enabled.sql` stays the enablement check.
- [ ] **Step 4: Run** `tests/test_staged_agents.py`, `tests/test_coordinator_acceptance.py`, `tests/test_console_chat.py`, and `make lint`. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(bookings): the bot books for real, and says only what's true`.

### Task 4: Pending bookings and customer messages

**Files:**
- Create: `csa/bookings/customer_messages.py`, `csa/jobs/bookings.py`, `db/sql/bookings/pending_expired.sql`, `db/sql/bookings/customer_route.sql`, `tests/test_bookings_customer_messages.py`
- Modify: `csa/bookings/service.py`, `csa/bookings/contracts.py`, `csa/bookings/notify.py`, `csa/bookings/api.py`, `csa/jobs/runner.py`, `csa/notify/sender.py`, `tests/test_jobs.py`, `tests/test_bookings_mail.py`, `docs/CONSOLE_API.md`, `docs/console_openapi.json`

**Interfaces:**
- Produces in `customer_messages.py`: `async telegram_message(conn, client_id, booking: BookingRecord, *, key: str, body: str, authority: Literal["reviewed","human"]) -> bool`. It writes `worker/insert_message.sql` and `worker/insert_delivery.sql`, using the route shape of `inbox/get_reply_route.sql` for the booking's `conversation_id`, or the contact's latest Telegram conversation, reusing that conversation's latest trace. Idempotent on `key`. Returns false when there's no Telegram route.
- Produces in `csa/jobs/bookings.py`: `run_pending_expiry(conn, client_id, clock, *, batch_size) -> dict`, registered every 5 minutes, per client.
- Extends `confirm_booking`: confirming a row with `replaces_booking_id` moves the original to the new time, keeping its id, and removes the pending row, in one transaction.
- Staff are told: a new pending bot booking calls `csa.notify.notifier.notify` with `event="booking_pending"`, and `_HEADLINES` gains a line with no customer details.

- [ ] **Step 1: Write the failing tests.**
  - Confirm a bot pending booking: the customer gets a Telegram message, or email if on WhatsApp, even with replies Off, because it's a person's action. `customer_told` is true, or false if neither is possible.
  - Decline: the same, saying it wasn't confirmed. This replaces PR 1's "no customer mail" for `source='bot'`; a staff-made pending booking still tells the customer nothing.
  - Expiry: past `expires_at`, a pending booking is cancelled with holds freed and the customer told, unless replies are Off or Customer support isn't active, in which case nothing goes.
  - A pending change: confirm moves the original, keeping its UID, with a higher `SEQUENCE`, and frees the old slot. Decline or expiry leaves the original untouched and tells the customer it stands.
  - A cancellation by the bot tells attendees by email. The customer's own reply covers them.
  - `test_a_telegram_message_appears_in_the_inbox_thread`, and is delivered with the reused trace by the real `check_route` and `get_message` SQL.
  - A message with `authority="reviewed"` still sends while a person has taken over the conversation.
  - `test_staff_are_told_of_a_pending_booking`, with no customer details in the payload.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement,** following `csa/approval/service.py::_settle` for the message and delivery writes.
- [ ] **Step 4: Run** the changed test files one at a time and `make lint`. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(bookings): pending bookings confirm, decline or expire, and the customer hears`.

### Task 5: Reminders

**Files:**
- Modify: `csa/jobs/bookings.py`, `csa/jobs/runner.py`, `apps/worker/main.py`, `csa/turn/deps.py`, `csa/console/mail_templates.py`, `deploy/compose.staging-pipeline.yml`, `deploy/STAGING_PIPELINE.md`, `tests/test_staging_pipeline.py`, `tests/test_jobs.py`
- Create: `db/sql/bookings/reminders_*.sql`, `tests/test_bookings_reminders.py`

**Interfaces:**
- Consumes `telegram_message` and `Mailer`.
- Produces in `csa/jobs/bookings.py`:
  - `run_reminder_queue(conn, client_id, clock, *, batch_size) -> dict`, per client, every 5 minutes: for each `confirmed` booking starting within `reminder_minutes`, inserts one `booking_reminders` row per recipient on conflict do nothing, and for a Telegram customer writes the message in the same transaction.
  - `run_reminder_mail(pool, mailer, clock, *, batch_size) -> dict`, `per_client=False`, every 5 minutes: claims `queued` email rows as `sending` in one transaction, commits, re-checks that the booking is still `confirmed` with the same `starts_at`, sends outside any transaction, and records `sent`, `failed` or `dropped`. A row is never sent twice, even if the worker dies mid-send.
- `build_default_registry` takes `mailer: Mailer | None`, and with no mailer `run_reminder_mail` leaves rows `queued`.

- [ ] **Step 1: Write the failing tests.**
  - A Telegram customer gets one message, in the Inbox thread. A customer with only an email gets one email. Staff attendees get email. Outside guests get email.
  - `test_each_reminder_goes_once`: running the job twice sends once.
  - `test_a_moved_booking_gets_a_new_reminder_and_no_stale_one`; `test_a_cancelled_booking_gets_none`, including between queuing and sending. This covers Review Focus 3.
  - `test_a_pending_booking_gets_no_reminder`.
  - Replies Off, and Customer support suspended: no customer reminder, and staff still get theirs. This covers Review Focus 4.
  - `test_a_bookings_only_company_reminds_its_customers_by_email`, since it has no replies setting.
  - A WhatsApp customer with no email gets no reminder and no WhatsApp message. A private meeting's reminder goes to its attendees only. A company in `onboarding` is skipped by the runner.
  - Reminder text states the local time with `format_slot`, and the place or link. A mail failure records `failed` and isn't retried.
  - `tests/test_staging_pipeline.py`: the worker has `INVITE_MAIL_ENABLED`, `SMTP_USERNAME` and `SMTP_PASSWORD`, as the gateway does, and a network that reaches the relay.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Build the worker's `Mailer` with `Mailer.from_settings`. Add the compose lines and a note in `deploy/STAGING_PIPELINE.md`.
- [ ] **Step 4: Run** the changed test files one at a time and `make lint`. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(bookings): reminders on Telegram and by email`.

### Task 6: The tab

**Files:**
- Modify: `lib/bookings-api.ts`, `lib/bookings-view.ts`, `components/console/bookings/BookingsCalendar.tsx`, `components/console/bookings/BookingDetails.tsx`, `components/console/bookings/BookingsWorkspace.tsx`, `tests/bookings-api.test.mjs`, `tests/bookings-view.test.mjs` (all under `CSA_WebConsole/csa-console/web/`), `tests/test_bookings_browser.py`
- Create: `components/console/bookings/BookingSettingsPanel.tsx`

**Interfaces:**
- Consumes `GET, PUT /settings`, and `BookingOut.source` and `replaces_booking_id`, which Task 4 adds to the response.

- [ ] **Step 1: Write the failing tests.**
  - A banner on the tab when `replies_off` is true, saying customers get no automatic messages, linking to Services for those who can change it. No banner for a Bookings-only company.
  - A pending change shows "Asked to move from … to …", and confirm or decline acts on it. A bot booking shows it came from the customer.
  - Admin changes reminder time, how long a pending booking waits, and the customer limit. Staff and read-only see no controls.
  - No overflow at 320, 390 and 768 px, in all three themes.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement,** on the kit and theme tokens. Add `docs/ASSISTANT_RETEST.md` rows ("Bookings") for a real customer booking, a pending confirmation, a reminder, and Replies Off.
- [ ] **Step 4: Run** the node tests, `tests/test_bookings_browser.py` alone, web lint and typecheck, and `make lint`. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(console): the Bookings tab shows customers' bookings, and the reminder settings`.

### Task 7: The assistant

**Files:**
- Create: `csa/bookings/executor.py`, `tests/test_bookings_executor.py`
- Modify: `csa/workspace/contracts.py`, `csa/workspace/planner.py`, `csa/workspace/service.py`, `csa/workspace/executors.py`, `csa/workspace/smoke_cases.json`, `scripts/assistant_smoke.py`, `docs/ASSISTANT_RETEST.md`, `tests/test_workspace_planner.py`, `tests/test_workspace_registry.py`, `tests/test_workspace_contracts.py`, `tests/test_assistant_smoke.py`, `lib/workspace-api.ts`, `lib/assistant-view.ts`, `lib/assistant-copy.ts`, and their node tests

**Interfaces:**
- Consumes the staff paths in `service.py`, with `Author(source="assistant")`.
- Produces `ProposalKind` value `booking_action`, `BookingActionExecutor` with `kind`, `gate`, `card` and `execute`, following `ProposalExecutor` in `csa/workspace/registry.py:91`, and resolving names in the style of `hr_people.resolve`, which `_resolve` in `csa/workspace/service.py` calls. Planner proposals: book, move, cancel. A new read `booking_availability` on `AnswerProposal.reads` answers "when is Aina free?".

- [ ] **Step 1: Write the failing tests.**
  - "Book Room A tomorrow at 3 with Aina" makes a card in English and Malay, and confirming books through the same rules as the tab, with `source='assistant'`.
  - Missing or ambiguous details give a clarification.
  - A warning shows on the card, and confirming counts as acknowledging it. A refusal says so with no reason. `slot_taken` at confirm says "That time was just taken."
  - Read-only members get no card, and staff limits match the tab. Private meetings stay "Busy" in answers.
  - A replayed proposal doesn't book twice.
  - The assistant only offers this when the `bookings` tab gate is open.
  - Smoke cases and checklist rows are added, and `tests/test_assistant_smoke.py` passes.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Card text goes in `_CARD_TEXT` for both languages. Add refusal codes to `MS_ERRORS`.
- [ ] **Step 4: Run** the changed test files one at a time, node tests, web lint and typecheck, and `make lint`. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(assistant): book, move and cancel from the assistant`.

### Task 8: CI triage

- [ ] **Step 1: Merge `origin/main`, push and read `gate`'s failures.** Merge only, no rebase. The owner approves the push.
- [ ] **Step 2: Fix each failure at its cause,** running that one test file locally. A failure showing a real product bug gets its fix and a line in the PR. Repeat until `gate` passes.
- [ ] **Step 3: Write the PR description:** what changed, the rulings, deferred minors, what wasn't tested, and the rollout: migration 0512 stops all four services, and the worker gains mail settings through a compose change.
