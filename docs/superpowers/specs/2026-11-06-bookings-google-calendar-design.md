# Bookings stage 2: Google Calendar and Meet, design

Date: 2026-11-06. Follows stage 1 (PR #72 core, PR #75 bot, reminders and assistant), which is live on
staging. Spec for stage 1: `docs/superpowers/specs/2026-11-04-bookings-design.md`.

## Intent

A person who uses Google Calendar sees their bookings there without adding anything by hand, and an
online meeting booked by staff gets a Google Meet link. Our own database stays the master for
availability, holds and rules; Google is a place the booking is shown, not a second source of truth.

The owner chose, on 2026-11-06: build "bookings appear on people's Google calendars" and "Meet links
on online meetings" first, and "a person's Google busy times block their availability" later (stage 2b,
after Google approves the stricter scope). He chose approach B below.

## Goals

- A staff member connects their own Google account once. From then on, every confirmed booking they
  attend appears on a secondary calendar the app creates in their account, and a move or a cancel is
  reflected there.
- An online booking with no meeting link gets a Google Meet link, and that link goes into the booking,
  the invitation email, the customer's messages and the reminders like any other link.
- The booking still works, with today's behaviour, for anyone who hasn't connected, and when Google
  fails.

## Non-goals, for this stage

- Reading anyone's Google calendar, so availability ignores Google busy time (stage 2b, scope
  `calendar.freebusy` or similar, a separate grant and a separate verification step).
- Two-way sync. An event moved or deleted in Google does not change the booking.
- Rooms and shared things as Google resource calendars; customers' and guests' own calendars (they keep
  the calendar file in our email); Microsoft 365 (stage 3); a company-wide connection by a Workspace
  admin.
- Writing to a person's primary calendar.

## Anti-goals

- The app can read or change anything in a person's Google account outside its own Bookings calendar.
- A booking, a hold or a reminder fails or waits because Google is slow, down or revoked.
- Customer contact details (phone, email, notes) land in Google events.
- A person's tokens are readable in any log, API response or by other members of the company.
- A mailbox already connected for the email assistant is asked to reconnect.

## Constraints

- One Google OAuth client per deployment (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`). Calendar is a
  separate authorization on that client with its own scope, its own callback path and its own stored
  grant. `csa/email/gmail.py` and its `SCOPES` do not change.
- Scope: `https://www.googleapis.com/auth/calendar.app.created` only, with `include_granted_scopes`
  off. The owner checks in the Cloud Console's scope picker whether Google marks it sensitive; if it
  does not, production needs no verification for this stage. Staging uses the Internal client, so only
  accounts in the owner's Workspace organization can connect there.
- Tokens are encrypted at rest with `csa.core.crypto` and an AAD that binds the row and column, the
  same way `email_mailboxes` does.
- Row security with no FORCE, no new database roles, synthetic data and disposable containers in tests,
  never a call to Google from a test, no secrets or real addresses in the repository (it is public).
- The release gate is off by default: `BOOKINGS_GOOGLE_CALENDAR_ENABLED=false`. Staging turns it on
  through `deploy/staging-rollout.env`, which is a pull request.

## Approaches

- **A. Per-person connection, events on the primary calendar (`calendar.events`).** What people expect,
  but the broadest scope: access to all their calendars, and probably the heavier review.
- **B. Per-person connection, events on a secondary "Bookings" calendar the app creates
  (`calendar.app.created`). Chosen.** The app can touch only that calendar. Review is probably lighter,
  and it may need no verification. The cost is a separate calendar in their account, which has to be
  visible in their Google Calendar app.
- **C. Company-wide through Workspace domain-wide delegation.** No per-person consent, but Workspace
  companies only, per-company setup, and more than this stage needs.

## Design

### 1. Who connects, and where

Only a member linked to a booking person (`booking_people.user_id`) can connect, and only for
themselves. A "Google Calendar" panel on the Bookings page shows their status (not connected,
connected, needs reconnecting), a Connect button and a Disconnect button. An Admin sees on People & rooms
which people are connected, as a status only, and cannot connect for someone else. A platform admin has
no booking person and sees no panel.

### 2. The connection

New table `booking_calendar_connections` (migration, next free number above 0531):
`client_id`, `user_id`, `id`, `person_id`, `status` (`pending`, `connected`, `needs_reconnect`),
`calendar_id`, encrypted `access_token` and `refresh_token`, `token_expires_at`, `scopes`,
`created_at`, `updated_at`; unique `(client_id, user_id)`, foreign keys to the membership and to
`booking_people`. A short-lived `booking_calendar_oauth_states` table holds the PKCE state, as
`email_oauth_states` does.

Row security: unlike `email_mailboxes`, which only the user can read, the push runs as the system for
other people's bookings, so the policy is the plain tenant isolation. Access control for tokens is in
the application: no API returns a token, and only the sync code decrypts one.

Connect: `POST /console/v1/bookings/calendar/connect` returns the Google authorization URL (PKCE, state,
`access_type=offline`, `prompt=consent`); the callback exchanges the code, stores the encrypted grant,
and creates the secondary calendar (named after the company and "Bookings"), storing its id. A reconnect
reuses the stored calendar if it still exists. Disconnect revokes the grant at Google, deletes the
stored tokens, marks the person's pending sync rows `dropped`, and leaves the calendar and its existing
events in the person's account. `invalid_grant` on any call marks the connection `needs_reconnect`.

Redirect URI: derived from the console origin plus `/console/v1/bookings/calendar/callback`, and it
must be registered on the Google client by the owner.

### 3. Pushing a booking

New table `booking_calendar_events`: `client_id`, `booking_id`, `person_id`, `google_event_id`,
`status` (`pending`, `synced`, `failed`, `deleted`, `dropped`), `synced_version` (the booking's
`version` when the event was last made to match), `attempts`, `last_error` (a category, never text
from Google), `updated_at`; unique `(client_id, booking_id, person_id)`.

The table is a ledger of what Google holds, and the booking is the only source of what it should hold.
A row is `pending` when it has no event yet or the booking's `version` is ahead of `synced_version`,
`deleted` once the event has been removed for a cancelled booking, and `dropped` when the person
disconnected. Every path that changes a booking's time, status or attendees bumps `version` already or
must be made to (staff, the assistant, the bot's book, move and cancel, the lapse job, no-show);
finding one that does not, and fixing it, is part of this work.

One event per connected staff attendee, in that person's own Bookings calendar. We do not add attendees
to the Google event, so Google sends no invitations of its own. Only `confirmed` bookings are pushed: a
pending booking has no event, a confirmed one gets it, a cancelled one has it deleted, and a moved one
has it patched to the new time. The desired state is read from the booking at send time, so a late
retry cannot restore a stale time.

Event content: the booking's title, start and end in the company's zone, the place or meeting link, the
names of the people and guests attending, and the customer's name when there is one. Never a phone
number, email address or note. A private meeting's event shows the same to its own attendees, who are
the only people who get one.

Two ways a row gets pushed, and one rule between them: a push always reads the booking and the
connection just before the call, and records `synced_version` only for the version it read.

- **Fast path, for the on-time Meet link.** After a staff or assistant booking commits, in the
  background path that already sends its mail (`notify_after_commit`), the push runs with a short
  timeout (about 8 seconds) before the mail is built. For an online booking with no meeting link, the
  push to the organizer's calendar asks Google for a Meet link (`conferenceData` create request), and
  the link is written to `bookings.meeting_link` only if that field is still empty (a link staff typed
  always wins), bumping `version`. The mail that follows has the link. If Google is slow or refuses,
  the mail goes without one.
- **Reconcile job, for everything else.** A job every five minutes, per company, finds every upcoming
  booking (starting within 60 days, and any cancelled one whose event still exists) of a connected
  person where no row exists, the row is `pending` or `failed`, or `synced_version` is behind the
  booking's `version`, and brings the event in line: create, patch or delete. One transaction per row,
  at most five attempts per version. This covers the bot's cancel and moves, the lapse job, no-show,
  a process that died after commit, and a person who connects or reconnects with bookings already on
  the calendar: they are backfilled 60 days ahead without any extra path. A booking set in the past is
  never pushed.

The organizer is the person who created the booking if connected, else the first connected attendee;
other connected attendees' events carry the same link in their location.

No late "meeting link added" mail or customer message in this stage. If a Meet link could not be made
on time, the booking's details show "No Meet link yet" with a retry and the existing field to paste
a link; a link the reconcile job creates later appears there and in the Google events, and in any
reminder sent after it exists.

### 4. Mail for connected people

A connected person's event is on their calendar, so the email to them for a new booking or a move goes
without the calendar file (no duplicate event), still stating what, when and where, once their event row
is `synced` at that version. If it is not, they get the file as today. A cancellation always carries
the cancel file for anyone who was ever sent a file for that booking, because that person may hold a
mail-created event Google cannot remove; this is how stage 1 already decides it. Customers, guests and
people who haven't connected are unchanged.

### 5. Failure and visibility

- Google down or slow: bookings, holds, mail and reminders behave as today; rows retry.
- A revoked or expired grant: `needs_reconnect`, a banner on that person's panel, and no further
  attempts until they reconnect. The other people's events are unaffected.
- Quota (403 or 429): the row stays `failed`, with a category, and retries later.
- Logs carry categories only: never a token, an address, a title or an event id.

## Testing

Synthetic fixtures and a fake Google transport only; no test reaches Google. Unit and database tests for
the state machine (connect, callback, reconnect, disconnect, `invalid_grant`), the push (create, move,
cancel, one per connected attendee, none for pending, none in the past, retry limits), the reconcile
job (a bot cancel, a lapse, a no-show, a crash after commit, a reconnect backfill, a stale `synced`
row, and every status or time change bumping `version`), mail without a
file for synced people and with it otherwise, the Meet link flowing into the booking and its mail,
tenant isolation of both new tables, token encryption, no token in any response or log. A browser test
for the panel and the Admin's status column. A manual retest row for the real thing: connect a
real Internal-org account on staging, book, move, cancel, and book an online meeting.

## Rollout

Two new tables in one migration, additive, so the usual migration stop applies. The owner enables the
Calendar API in the Cloud project, adds the scope to the consent screen, and registers the redirect URI;
then turns on `BOOKINGS_GOOGLE_CALENDAR_ENABLED` for staging. Existing mailboxes are untouched.

## Left to the builder

- Naming and placement of modules (suggested: `csa/bookings/google/` with the client, the connection
  service and the push), and the exact table and index names.
- The Google client's HTTP details, using the existing transport conventions of `csa/email/gmail.py`.
- A spike, the first task, that confirms against the real API (staging, Internal client) that
  `conferenceData` creation works on an event in a secondary calendar made under
  `calendar.app.created`. The work is built in two parts: events first (connection, push, reconcile,
  mail), Meet second. If the spike fails, part one still ships, and the builder stops and tells the owner
  before part two: the fallback decision is his.

## Calls I made (to check while reading)

- Disconnect leaves the calendar and its events in the person's account.
- No attendees on Google events, so Google sends nothing itself.
- The customer's name appears on staff events; no other customer detail does.
- The push runs inline after commit with a short timeout for staff and assistant bookings, because the
  Meet link has to exist before the invitation email is built; a reconcile job covers every other
  change and every failure, by comparing the booking's `version` with what Google last got.
- Connecting or reconnecting backfills upcoming confirmed bookings 60 days ahead.
- No late "meeting link added" note; a missing link shows on the booking with a retry and the paste
  field, and a link staff typed is never overwritten.
- Moves and new bookings go to a connected person's email without the file once synced; a cancel still
  carries the cancel file for anyone ever sent one.
- Build order: events first, Meet second, with the Meet spike as task one.
- A connected person's email loses the calendar file only when their event is already `synced`.
- Tokens use the plain tenant policy, not the per-user policy the mailbox table has, because the system
  pushes on other people's behalf.
