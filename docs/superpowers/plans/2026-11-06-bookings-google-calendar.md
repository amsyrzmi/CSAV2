# Bookings stage 2: Google Calendar and Meet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A staff member connects their Google account once, and every confirmed booking they attend appears on a "Bookings" calendar the app creates in their account; online meetings get a Google Meet link.

**Architecture:** A new package `csa/bookings/google/` holds a Calendar API client, a per-person connection service, and a push that makes a person's events match the bookings. The booking's `version` is the only source of truth: a reconcile job every five minutes brings every connected person's events in line with it, and a fast path after staff and assistant bookings pushes before the invitation mail is built, so the mail can leave out the calendar file and the Meet link can be in it. Meet is a second part behind its own flag.

**Tech Stack:** Python 3.12, FastAPI, psycopg3, Postgres 16 with row security (never FORCE), httpx with a fake transport in tests, Next.js with node:test, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-06-bookings-google-calendar-design.md`. Stage 1: `docs/superpowers/specs/2026-11-04-bookings-design.md`.

## Global Constraints

- One Google OAuth client per deployment (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`). Calendar is a separate authorization with its own callback and its own stored grant. `csa/email/gmail.py` and its `SCOPES` do not change, and no connected mailbox is asked to reconnect.
- Scope: `https://www.googleapis.com/auth/calendar.app.created` only, with `include_granted_scopes=false`.
- Tokens are encrypted with `csa.core.crypto` and an AAD from `crypto.credential_aad(table, column, row_id)`, as `email_mailboxes` does. No API returns a token, and logs carry categories only: never a token, an address, a title or an event id.
- Row security enabled, never FORCE; no new database roles; a plain tenant-isolation policy on all three new tables.
- Synthetic fixtures, disposable testcontainers and a fake Google transport only. No test contacts Google, and no secret or real address goes in code, tests, docs or commits (the repository is public).
- No attendees on Google events, so Google sends no invitations of its own. Only `confirmed` bookings are pushed; a cancelled booking's event is deleted; a booking set in the past is never pushed.
- Event content: title, start and end in the company's zone, the place or meeting link, the names of the people and guests attending, and the customer's name. Never a phone number, an email address or a note.
- Both features are off by default: `BOOKINGS_GOOGLE_CALENDAR_ENABLED=false` and `BOOKINGS_GOOGLE_MEET_ENABLED=false`. This PR does not turn either on in `deploy/staging-rollout.env`.
- Network calls never run inside a database transaction: claim and commit, call, then settle in a second short transaction.
- Commit trailers: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_012Xj1GSBskmizHEtGV54ZTA`.

## Review Focus

1. **A booking changed by a path that never calls `notify_after_commit`** (the bot's cancel and move, the lapse job, no-show): the Google event follows within one reconcile run. Tested per path in Task 4.
2. **A grant revoked mid-run** (`invalid_grant`): that person goes to `needs_reconnect`, the other people's events and every booking are unaffected. Task 3 and Task 4.
3. **Google slow or down in the fast path:** the booking has committed, the mail goes out as today with the calendar file, and no request fails. Task 5.
4. **Disconnect then reconnect, or the person deleted the calendar or an event in Google:** no duplicate events; a 404 or 410 on a patch recreates once, on a delete counts as deleted; a deleted calendar is recreated once. Task 3 and Task 4.
5. **Customer details leaking into an event,** including on a private meeting and a bot booking: the body holds the customer's name and nothing else about them. Task 4.

---

### Task 1: Schema and settings

**Files:**
- Create: `db/migrations/0513_bookings_google.sql`, `tests/test_bookings_google_schema.py`
- Modify: `csa/core/settings.py`, `db/migrations/README.md`, `.env.example`, `tests/test_settings.py`

**Interfaces:**
- Produces `booking_calendar_connections(client_id, id, user_id, person_id, status, calendar_id, access_token, refresh_token, token_expires_at, scopes text[], created_at, updated_at)`, `status IN ('pending','connected','needs_reconnect')`, unique `(client_id, user_id)` and `(client_id, person_id)`, foreign keys `(user_id, client_id)` to `console_memberships` and `(client_id, person_id)` to `booking_people`.
- Produces `booking_calendar_oauth_states(state_hash PRIMARY KEY, client_id, user_id, connection_id, code_verifier, created_at, expires_at)`, as `email_oauth_states`.
- Produces `booking_calendar_events(client_id, booking_id, person_id, google_event_id, status, synced_version, attempts, last_error, updated_at)`, `status IN ('pending','synced','failed','deleted','dropped')`, unique `(client_id, booking_id, person_id)`, an index for the reconcile sweep on `(client_id, status)`, foreign keys to `bookings` and `booking_people`.
- Produces `Settings.BOOKINGS_GOOGLE_CALENDAR_ENABLED: bool`, `Settings.BOOKINGS_GOOGLE_MEET_ENABLED: bool`, and a validator: either flag requires the Google client id, secret and `GOOGLE_OAUTH_REDIRECT_URI` (whose console origin the calendar callback `.../console/v1/bookings/calendar/callback` is derived from); Meet requires Calendar.

- [ ] **Step 1: Write the failing tests:** each new table is tenant isolated, with RLS enabled and not forced; grants (`csa_app` select, insert, update, delete on the three tables; `csa_ro` nothing); the status checks; unique keys; the migration applies on a fresh database and on one already at 0531; `EXPECTED_SCHEMA_VERSION` stays "0531" and the schema-version check accepts a migration numbered below it (0512 already did); settings: flags default false, Meet without Calendar refused, either flag without the Google client refused.
- [ ] **Step 2: Run them to see them fail:** `bash ci.sh .venv/bin/python -m pytest tests/test_bookings_google_schema.py tests/test_settings.py -q`. Expected: failures naming the missing tables and settings.
- [ ] **Step 3: Write the migration and settings.** The three tables follow `0430_email_assistant.sql` for tokens and states, but the policy is plain tenant isolation on `app.client_id` (the push runs as the system for other people). Add the README line for 0513 and the `.env.example` entries, commented.
- [ ] **Step 4: Run the same command.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(bookings): tables and settings for Google Calendar`.

### Task 2: The Google Calendar client

**Files:**
- Create: `csa/bookings/google/__init__.py`, `csa/bookings/google/client.py`, `tests/test_bookings_google_client.py`

**Interfaces:**
- Produces in `client.py`: `CALENDAR_SCOPE`; `CalendarError(Exception)` with `.code` in `provider_unreachable`, `provider_rate_limited`, `provider_unauthorized`, `invalid_grant`, `provider_not_found`, `provider_rejected`, `provider_invalid_response`, `scopes_not_granted`, `meet_unavailable`; `TokenSet(access_token: SecretStr, refresh_token: SecretStr | None, expires_in: int, scopes: tuple[str, ...])`; `EventResult(event_id: str, meet_link: str | None)`; and
  `CalendarClient(client_id: str, client_secret: SecretStr, redirect_uri: str, http: httpx.AsyncClient)` with `authorization_url(*, state, code_challenge) -> str`, `exchange(*, code, code_verifier) -> TokenSet`, `refresh(refresh_token) -> TokenSet`, `revoke(token) -> None`, `create_calendar(access, *, summary, time_zone) -> str`, `calendar_exists(access, calendar_id) -> bool`, `insert_event(access, calendar_id, body, *, meet=False) -> EventResult`, `patch_event(access, calendar_id, event_id, body) -> None`, `delete_event(access, calendar_id, event_id) -> None`.

- [ ] **Step 1: Write the failing tests** with an `httpx.MockTransport`: the authorization URL has the one scope, `access_type=offline`, `prompt=consent`, `include_granted_scopes=false`, PKCE S256; `exchange` refuses a grant lacking the scope (`scopes_not_granted`); `refresh` with `invalid_grant` raises `CalendarError("invalid_grant")`; 401 and 403 give `provider_unauthorized`, 429 and a rate-limit 403 give `provider_rate_limited`, 404 and 410 give `provider_not_found`, a timeout gives `provider_unreachable`; `insert_event(meet=True)` sends `conferenceData.createRequest` with `conferenceDataVersion=1` and returns the hangout link, and a response without one raises `meet_unavailable`; no request carries `attendees` or a `sendUpdates` other than `none`; no error message holds a token.
- [ ] **Step 2: Run to see them fail:** `bash ci.sh .venv/bin/python -m pytest tests/test_bookings_google_client.py -q`.
- [ ] **Step 3: Implement `CalendarClient`,** following the transport conventions of `GmailClient` in `csa/email/gmail.py` (the `_json` helper with size limit, timeouts, `follow_redirects=False`), mapping statuses to the error codes above.
- [ ] **Step 4: Run the same command.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(bookings): Google Calendar API client`.

### Task 3: The connection service and API

**Files:**
- Create: `csa/bookings/google/connections.py`, `csa/bookings/google/api.py`, `db/sql/bookings/google_*.sql`, `tests/test_bookings_google_connections.py`
- Modify: `apps/gateway/main.py`, `docs/CONSOLE_API.md`, `docs/console_openapi.json`

**Interfaces:**
- Consumes `CalendarClient` and `CalendarError` (Task 2), `csa.core.crypto`, the tables of Task 1, and `booking_people.user_id`.
- Produces `CalendarConnections(pool, client: CalendarClient | None, *, enabled: bool)` with `status(conn, client_id, user_id) -> Literal["not_connected","connected","needs_reconnect"]`, `start(client_id, user_id) -> str` (the authorization URL), `finish(client_id, user_id, *, code, state) -> None`, `disconnect(client_id, user_id) -> None`, and `access(conn, connection) -> SecretStr` (refreshes when due; on `invalid_grant` marks `needs_reconnect` and re-raises).
- Produces routes under `/console/v1/bookings/calendar`: `GET /status` (`CalendarStatus {available: bool, status, people: list[{person_id, name, status}] | None}`; `people` only for an Admin), `POST /connect -> {url}`, `GET /callback` (cookie session, redirects to the Bookings page with `?calendar=connected` or `?calendar_error=<code>`, as `csa/email/api.py` does, including the issuer check), `POST /disconnect`. `available` is false when the flag is off or the client is not configured.

- [ ] **Step 1: Write the failing tests** with a fake `CalendarClient`: only a member linked to a booking person can start a connection, and only for themselves (an Admin cannot connect for another, a platform admin has no panel, read-only members cannot connect); the callback stores encrypted tokens and creates the Bookings calendar named for the company; a reconnect reuses the stored calendar when `calendar_exists` is true and creates a new one once when not; a bad or expired state refuses; disconnect revokes at Google, deletes the stored tokens, sets the person's non-`deleted` event rows to `dropped`, and leaves the calendar; `invalid_grant` on refresh sets `needs_reconnect`; no response body, log line or exception text holds a token; the stored columns are not plaintext and the AAD is bound to the row; the other tenant cannot see the rows; `status` lists people only for an Admin; with the flag off every route answers `available: false`.
- [ ] **Step 2: Run to see them fail:** `bash ci.sh .venv/bin/python -m pytest tests/test_bookings_google_connections.py -q`.
- [ ] **Step 3: Implement the service and router,** following `EmailService.connect_start`, `connect_callback` and `disconnect` in `csa/email/service.py` and the callback in `csa/email/api.py`. Wire `app.state.calendar_connections` in `apps/gateway/main.py` next to the mailer, and include the router. Regenerate `docs/console_openapi.json` and document the routes in `docs/CONSOLE_API.md`.
- [ ] **Step 4: Run the same command, then `tests/test_console_contract_snapshot.py`.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(bookings): connect a Google Calendar from the Bookings page`.

### Task 4: Event content, push and reconcile

**Files:**
- Create: `csa/bookings/google/push.py`, `db/sql/bookings/google_reconcile_*.sql`, `tests/test_bookings_google_push.py`
- Modify: `csa/jobs/runner.py`, `apps/worker/main.py`, and any booking path found to change time, status or attendees without bumping `version`

**Interfaces:**
- Consumes `CalendarClient` (Task 2), `CalendarConnections.access` (Task 3), `BookingRecord` (`csa/bookings/service.py`), `read_records`.
- Produces `event_body(record: BookingRecord, *, zone: ZoneInfo, brand: str) -> dict[str, object]`, pure, holding exactly the content in Global Constraints.
- Produces `push_booking(pool, calendar: CalendarConnections, client: CalendarClient, booking: BookingRecord, *, timeout: float) -> None`: for each connected staff attendee, claims the person's row, calls Google outside any transaction, settles; create when no event, patch when `synced_version` is behind, delete when the booking is cancelled; records `synced_version` only for the version it read; never raises.
- Produces `run_calendar_reconcile(...)`, a job every five minutes registered in `csa/jobs/runner.py`, following the reminder mail job's shape (the owner connection only lists companies; everything else per company through `app_pool`, one transaction per row, send slot first, then claim and re-read, commit, call, settle): it finds every upcoming booking (starting within 60 days, and any cancelled one whose event exists) of a connected person with no row, a `pending` or `failed` row, or `synced_version` behind the booking's `version`, at most five attempts per version.

- [ ] **Step 1: Write the failing tests:**
  - `event_body` holds the title, times in the company's zone, the place or link, names of attending people and guests and the customer's name, and never a phone, email or note, including on a private meeting and a bot booking (Review Focus 5);
  - create, patch on a move, delete on a cancel; none for `pending`; none for a booking in the past; one event per connected attendee and none for an unconnected one;
  - a bot cancel, a bot move, a lapse and a no-show each change the Google event within one reconcile run (Review Focus 1);
  - a crash after commit (no row yet) is picked up by reconcile; a reconnect backfills the next 60 days with no duplicate (Review Focus 4); a 404 or 410 on patch recreates once, on delete counts as `deleted`; a deleted calendar is recreated once;
  - `invalid_grant` on one person sets `needs_reconnect` for them and leaves the others and the booking alone (Review Focus 2); rate limits leave `failed` with a category and retry; the sixth attempt at one version does not happen;
  - a late retry never writes a stale time (the version it read is the one recorded);
  - every path that changes status, times or attendees bumps `bookings.version`: a test that drives each of `create_for`, `move_to`, `cancel_for`, `expire_for`, `confirm_booking`, `decline_booking`, `mark_no_show`, `_apply_change` and the bot's cancel and checks `version` increased; fix any that does not.
- [ ] **Step 2: Run to see them fail:** `bash ci.sh .venv/bin/python -m pytest tests/test_bookings_google_push.py -q`.
- [ ] **Step 3: Implement `event_body`, `push_booking` and `run_calendar_reconcile`;** pass the calendar objects to the registry from the worker (`build_default_registry` takes them as optional parameters, and without them the job leaves rows alone). Fix any version-bump gaps the test finds.
- [ ] **Step 4: Run the same command, then tests/test_jobs.py, tests/test_worker_coordinator.py, tests/test_bookings_customer_messages.py and tests/test_bookings_api.py one at a time.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(bookings): events on a person's Bookings calendar, kept in line by a reconcile job`.

### Task 5: Mail for connected people, and the fast path

**Files:**
- Modify: `csa/bookings/notify.py`, `csa/bookings/api.py`, `tests/test_bookings_mail.py`, `tests/test_bookings_google_push.py`

**Interfaces:**
- Consumes `push_booking` (Task 4).
- Produces `notify_after_commit(..., calendar: CalendarPush | None = None)` where `CalendarPush` is a small protocol or callable bundle from `push.py` that pushes one booking within a timeout; when given, it runs first for staff and assistant bookings and moves, before the mail is built (about 8 seconds). `api.after_commit` passes it from `app.state`. `Recipient` gains `synced: bool = False`.

- [ ] **Step 1: Write the failing tests:** a connected person whose row is `synced` at this booking's version gets the new-booking or moved mail without the calendar file, with what, when and where still stated; one whose row is not synced gets the file as today; a cancellation carries the cancel file for anyone ever sent a file for that booking, and not for a connected person who never was; customers, guests and unconnected people are unchanged; Google slow (past the timeout) or down: the booking has committed, the mail goes as today with the file, and nothing raises (Review Focus 3); the assistant's booking goes through the same path.
- [ ] **Step 2: Run to see them fail:** `bash ci.sh .venv/bin/python -m pytest tests/test_bookings_mail.py -q`.
- [ ] **Step 3: Implement.** Decide "ever sent a file" from what stage 1 already tracks for a cancel (`original` and `invite`); add a stored fact only if nothing existing says it. Keep `notify` and `recipients` pure.
- [ ] **Step 4: Run the same command, then tests/test_bookings_google_push.py, tests/test_bookings_customer_messages.py and tests/test_bookings_executor.py one at a time.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(bookings): no calendar file for a person whose Google event is already there`.

### Task 6: The Bookings page panel

**Files:**
- Create: `CSA_WebConsole/csa-console/web/components/console/bookings/GoogleCalendarPanel.tsx`
- Modify: `CSA_WebConsole/csa-console/web/components/console/BookingsWorkspace.tsx`, `.../bookings/PeopleAndRooms.tsx`, `lib/bookings-api.ts`, `lib/bookings-view.ts`, `tests/test_bookings_browser.py`, node tests under `web/tests/`, `docs/ASSISTANT_RETEST.md`

**Interfaces:**
- Consumes the routes of Task 3. Read `CSA_WebConsole/csa-console/web/AGENTS.md` and the Next.js docs in `node_modules/next/dist/docs/` before web code.
- Produces a "Google Calendar" panel for a member with a booking person (hidden when `available` is false or the member has none): status line, Connect, Disconnect, and for `needs_reconnect` a plain banner and a Reconnect button; a status column on People & rooms for an Admin; the `?calendar=connected` and `?calendar_error=<code>` results shown once and then cleared from the URL.

- [ ] **Step 1: Write the failing tests:** node tests for the view helpers (status words, which controls show for Admin, staff, read-only, platform admin, `available` false); browser tests for Connect leaving for the returned URL (stubbed), the connected and error banners, `needs_reconnect`, Disconnect with its confirmation, and the Admin's status column; the panel absent for a member with no person.
- [ ] **Step 2: Run to see them fail:** the node suite, then `bash ci.sh .venv/bin/python -m pytest tests/test_bookings_browser.py -q`.
- [ ] **Step 3: Implement** on the kit and theme tokens, matching `BookingSettingsPanel.tsx`. Add `docs/ASSISTANT_RETEST.md` rows for connect, book, move, cancel on a real Internal-organisation account.
- [ ] **Step 4: Run node tests, web lint and typecheck, and the browser file.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(console): connect a Google Calendar from the Bookings page`.

### Task 7: Rollout wiring and runbook

**Files:**
- Modify: `deploy/compose.staging-pipeline.yml`, `deploy/STAGING_PIPELINE.md`, `tests/test_staging_pipeline.py`, `apps/worker/main.py`

**Interfaces:**
- Produces compose entries `BOOKINGS_GOOGLE_CALENDAR_ENABLED` and `BOOKINGS_GOOGLE_MEET_ENABLED` on the gateway and worker, as `${CSA_STAGING_BOOKINGS_GOOGLE_CALENDAR:-false}` and `${CSA_STAGING_BOOKINGS_GOOGLE_MEET:-false}`; the worker also gets the Google client id and secret secrets the gateway already has. `deploy/staging-rollout.env` is not changed.

- [ ] **Step 1: Write the failing tests** in `tests/test_staging_pipeline.py`: both flags default false in the rendered compose for gateway and worker; the worker has the Google client secrets; the rollout env file does not set either flag.
- [ ] **Step 2: Run to see them fail:** `bash ci.sh .venv/bin/python -m pytest tests/test_staging_pipeline.py -q`.
- [ ] **Step 3: Implement,** and add a runbook section: enable the Calendar API in the Cloud project; add the scope `calendar.app.created` to the consent screen and note whether Google marks it sensitive; register `<console origin>/console/v1/bookings/calendar/callback` on the client; then a separate pull request turns the flag on in `deploy/staging-rollout.env`. Say that only accounts in the Internal client's organisation can connect on staging.
- [ ] **Step 4: Run the same command.** Expected: PASS.
- [ ] **Step 5: Commit** `chore(deploy): Google Calendar flags and runbook`.

### Task 8: Meet links (part two)

**Files:**
- Modify: `csa/bookings/google/push.py`, `csa/bookings/google/client.py`, `csa/bookings/service.py`, `db/sql/bookings/`, `csa/bookings/api.py`, the Bookings details component and `lib/bookings-api.ts`, `tests/test_bookings_google_push.py`, `tests/test_bookings_api.py`, `tests/test_bookings_browser.py`

**Interfaces:**
- Consumes Tasks 2 to 7 and `Settings.BOOKINGS_GOOGLE_MEET_ENABLED`.
- Produces: when the flag is on, a confirmed booking with `location_kind = 'online'` and no `meeting_link` whose organizer (the creator if connected, else the first connected attendee) is connected has the organizer's event inserted with `meet=True`; the returned link is written to `bookings.meeting_link` only if still empty (compare and set, bumping `version`); other connected attendees' events carry the link in their location. `POST /console/v1/bookings/{id}/meet` retries for staff who can change the booking. `BookingOut` gains `meet_missing: bool`, true for such a booking whose link is empty (so the details can show "No Meet link yet" with a retry and the existing field to paste a link).

- [ ] **Step 1: Write the failing tests:** the link is created on time in the fast path and is in the mail; a link staff typed is never overwritten; `meet_unavailable` from Google leaves the event without a link, `failed` with that category, the booking and mail untouched, and `meet_missing` true; the reconcile job fills a missing link later and patches the other attendees' events; no late "link added" mail or customer message is sent; the retry route is refused for someone who cannot change the booking; an offline or phone booking never asks for Meet; with the flag off nothing changes.
- [ ] **Step 2: Run to see them fail:** `bash ci.sh .venv/bin/python -m pytest tests/test_bookings_google_push.py -q`, then `tests/test_bookings_api.py`.
- [ ] **Step 3: Implement.** Regenerate `docs/console_openapi.json`. Add the "No Meet link yet" line and retry to the details, with a browser test. Add `docs/ASSISTANT_RETEST.md` rows: the owner books an online meeting on staging with both flags on and says whether a Meet link came back. If Google refuses Meet in a secondary calendar, the flag stays off and the owner decides what next; nothing else is affected.
- [ ] **Step 4: Run the same files, the browser file, node tests, web lint and typecheck, and `make lint`.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(bookings): a Meet link for online meetings`.

### Task 9: CI triage

- [ ] **Step 1: Merge `origin/main`, push and read `gate`'s failures.** Merge only, no rebase. The owner approves the push.
- [ ] **Step 2: Fix each failure at its cause,** running that one test file locally. A failure showing a real product bug gets its fix and a line in the PR. Repeat until `gate` passes.
- [ ] **Step 3: Write the PR description:** what changed, the rulings, deferred minors, what wasn't tested (the real Google calls), and the rollout: migration 0513 (a table-only migration, still a stop of all four services under the staging tool), the two flags off by default, the Cloud Console steps for the owner, and the Internal-client limit.
