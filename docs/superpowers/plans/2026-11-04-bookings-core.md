# Bookings core and staff (stage 1, PR 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Companies can set up people, rooms and booking types, and staff can book customers and internal meetings from a Bookings tab, with no double booking, HR leave respected, and email invitations with a calendar file.

**Architecture:** A new `csa/bookings` package owns the rules. Pure functions decide availability and what each role may override, and the database refuses overlaps through one hold row per person or thing, so simultaneous requests can't both win. The tab follows the Subscriptions pattern. The customer bot, reminders and the assistant's actions are PR 2, so the bot behaves as it does today.

**Tech Stack:** Python 3.12, FastAPI, psycopg3, Postgres 16 with RLS, Next.js with node:test, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-04-bookings-design.md`

## Global Constraints

- Migrations stay in 0510-0519. Add the range to `db/migrations/README.md`. `EXPECTED_SCHEMA_VERSION` stays "0530". It must work on a fresh database, where 0510 runs before 0520.
- Every new table has `client_id`, row security with the usual `tenant_isolation` policy, and grants to `csa_app` only as needed. Never `FORCE`. No new roles. SQL lives in `db/sql/bookings/`, parameterised.
- Human console sessions only, through `package_gate("bookings")`, as `csa/subscriptions/api.py` does. The machine token never reaches bookings.
- Times are stored in UTC and shown and reasoned about in `clients.timezone`.
- No code path sends a customer anything automatically in this PR. A staff member's own action may email them, even with replies Off.
- Leave: read only the person and the dates. Never store, return or show the type of leave or the reason.
- Private meetings show "Busy" to everyone except attendees and the creator, including admins and platform admins, who can still move or cancel them.
- Read-only members see the calendar with no customer names or contact details.
- Copy, exactly: "That time was just taken.", "Not available that day.", "Outside working hours.", "This is a closed day.", "Busy". The role is "Admin", not "tenant admin".
- Don't touch the Google OAuth client, `BookingAgent`, `agent_preparation_requests`, or the turn path.
- New screens use `components/console/kit/` and theme tokens, and work in all three themes and at 320 px.
- Synthetic fixtures only. No real address: the repository is public.
- CI is the merge gate. Locally run each changed test file on its own with `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q <file>`, plus `make lint` (never piped) and the web checks. Never run the full suite. Before changing wording, search tests for the old text.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Moving a booking when the new time fails.** The original booking and its holds stay exactly as they were. (Task 4)
2. **A company outside Malaysia, and bookings near midnight.** Hours, closed days and leave are judged in the company's own time zone, not UTC. (Task 2)
3. **A person or room deactivated with future bookings.** Existing bookings stay and are flagged, and new ones are refused. (Task 4)
4. **Awkward text in a guest name, title or address:** commas, semicolons, new lines. The calendar file stays valid, and no mail header can be injected. (Task 5)
5. **A private meeting's details leaking** through the calendar, search, filters, a refusal message, or an email to someone not attending. (Task 4 and Task 5)

---

### Task 1: Schema

**Files:**
- Create: `db/migrations/0510_bookings_core.sql`, `tests/test_bookings_schema.py`
- Modify: `db/migrations/README.md`

**Interfaces:**
- Produces these tables:
  - `booking_people(client_id, id, display_name, user_id NULL, employee_id NULL, email NULL, is_active, version)`, with `(client_id, user_id)` unique when set.
  - `booking_resources(client_id, id, name, capacity NULL, is_active, version)`.
  - `booking_types(client_id, id, name, duration_minutes, buffer_minutes, audience, delivery, resource_rule, location_kind, address NULL, meeting_link NULL, needs_confirmation DEFAULT false, min_notice_minutes, max_advance_days, change_cutoff_minutes, is_active, version)`, with `audience IN ('customers','staff','both')`, `delivery IN ('named','any','several')`, `resource_rule IN ('none','specific','any')`, `location_kind IN ('in_person','phone','online')`.
  - `booking_type_people(client_id, type_id, person_id)` and `booking_type_resources(client_id, type_id, resource_id)`.
  - `booking_hours(client_id, id, subject_kind, subject_id NULL, weekday, opens, closes)`, with `subject_kind IN ('company','person','resource')` and `weekday` 0 (Monday) to 6.
  - `booking_closed_days(client_id, day, note NULL)`.
  - `booking_blocks(client_id, id, subject_kind, subject_id, starts_at, ends_at, note NULL, created_by)`.
  - `booking_attendees(client_id, booking_id, id, person_id NULL, guest_name NULL, guest_email NULL)`, with exactly one of person or guest email.
  - `booking_holds(client_id, id, booking_id NULL, block_id NULL, subject_kind, subject_id, during tstzrange)`, with an exclusion constraint on `(client_id, subject_kind, subject_id, during)`, and `subject_kind IN ('person','resource')`.
- Extends `bookings` with `type_id NULL, title NULL, kind DEFAULT 'appointment', created_by NULL, source DEFAULT 'staff', private DEFAULT false, location_kind NULL, address NULL, meeting_link NULL, version DEFAULT 1`. Adds `bookings_status_chk` with `status IN ('pending','confirmed','cancelled','no_show')`, and `ends_at > starts_at`. `kind IN ('appointment','meeting')`, `source IN ('staff','assistant','bot')`.
- Produces `public.booking_leave_days(p_client uuid, p_employees uuid[], p_from date, p_to date) RETURNS TABLE(employee_id uuid, starts_on date, ends_on date)`, `SECURITY DEFINER` in the style of `0502_console_last_change.sql`. It returns approved `hr_cases` of domain `leave` only, and refuses a client other than `app.client_id`.

- [ ] **Step 1: Write the failing tests** in `tests/test_bookings_schema.py` against a testcontainer:
  - `test_the_database_refuses_an_overlapping_hold`: two holds on one person with overlapping ranges raise `ExclusionViolation`; touching ranges and a different person pass.
  - `test_two_simultaneous_holds_only_one_wins`: two connections insert the same range at once; exactly one succeeds.
  - `test_each_new_table_is_tenant_isolated`: for every new table, a row in company A can't be read under `app.client_id` of company B as `csa_app`.
  - `test_leave_days_returns_only_approved_leave_and_no_reason`: pending and rejected leave are absent, the result's columns are exactly `employee_id, starts_on, ends_on`, and another company's `p_client` raises.
  - `test_leave_days_works_for_a_non_hr_member`: a session with no HR grant gets the dates.
  - `test_legacy_statuses_are_normalised`: a `bookings` row with an old status such as `completed` becomes `cancelled` with a NOTICE, as 0480 does; `pending` and `confirmed` are kept. Confirm `db/seed/sandbox_scenarios.sql` still loads.
- [ ] **Step 2: Run them.** Expected: FAIL, tables missing.
- [ ] **Step 3: Write the migration.** Follow 0480's header (pre-check and rollback) and grant style. Use `CREATE EXTENSION IF NOT EXISTS btree_gist`. If the owner role in CI or staging can't create it, say so in your report and stop, because the spec's overlap method depends on it. Add the range to the README. Don't backfill holds for old rows, which name no person.
- [ ] **Step 4: Run** `tests/test_bookings_schema.py`, and `tests/test_migrations*.py` if present. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(bookings): tables, row security and the overlap rule`.

### Task 2: Availability and the override rules

Pure functions with no database, so the rules can be tested thoroughly.

**Files:**
- Create: `csa/bookings/__init__.py`, `csa/bookings/availability.py`, `csa/bookings/rules.py`, `tests/test_booking_availability.py`, `tests/test_booking_rules.py`

**Interfaces:**
- Produces in `csa/bookings/availability.py`:
  - `@dataclass(frozen=True) class Busy(subject_kind: str, subject_id: UUID, start: datetime, end: datetime)`.
  - `@dataclass(frozen=True) class Calendar(timezone: ZoneInfo, company_hours: Mapping[int, list[tuple[time, time]]], overrides: Mapping[UUID, Mapping[int, list[tuple[time, time]]]], closed_days: frozenset[date], leave: Mapping[UUID, list[tuple[date, date]]], busy: list[Busy])`. `overrides` and `leave` are keyed by the booking person's or thing's id; the service translates HR employees to people when it loads leave.
  - `free_slots(calendar: Calendar, *, required_people: Sequence[UUID], any_of_people: Sequence[UUID], required_resources: Sequence[UUID], any_of_resources: Sequence[UUID], duration: timedelta, buffer: timedelta, start_from: datetime, until: datetime, step: timedelta = timedelta(minutes=15)) -> list[Slot]`, where `Slot(start, end, people: tuple[UUID, ...], resources: tuple[UUID, ...])`.
  - `pick_person(candidates: Sequence[UUID], bookings_that_day: Mapping[UUID, int]) -> UUID`: the fewest bookings; ties go by the order given.
- Produces in `csa/bookings/rules.py`:
  - `Actor = Literal["bot", "staff", "admin"]`.
  - `Finding = Literal["outside_hours", "closed_day", "short_notice", "on_leave"]`.
  - `findings_for(calendar, start, end, people, resources, min_notice, now) -> set[Finding]`.
  - `decide(actor: Actor, findings: set[Finding], acknowledged: set[Finding]) -> Decision`, where `Decision(allowed: bool, needs_acknowledging: set[Finding], refused: set[Finding])`.

- [ ] **Step 1: Write the failing tests.**
  - `decide`, parametrised on the spec's table: bot refuses all four. Staff: `outside_hours` and `closed_day` need acknowledging, `short_notice` is allowed with nothing, `on_leave` is refused. Admin: `outside_hours`, `closed_day` and `on_leave` need acknowledging, and `short_notice` is allowed with nothing.
  - `test_slots_respect_hours_closed_days_and_busy`, `test_the_buffer_keeps_the_gap_free`, `test_a_person_on_leave_is_not_offered`.
  - `test_any_free_person_needs_only_one_free`, `test_several_people_need_all_free`, `test_a_required_room_must_be_free`, `pick_person` fewest-first.
  - `test_override_hours_replace_company_hours` for a person with their own hours.
  - `test_hours_and_closed_days_use_the_company_time_zone`: with `Asia/Kuala_Lumpur` and with `America/New_York`, a slot at 23:30 UTC falls on the correct local weekday and closed day. A daylight-saving change day doesn't crash or produce a slot of the wrong length.
  - `test_leave_covers_whole_local_days`: leave on a date blocks 00:00 to 24:00 local, including across UTC midnight.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** `free_slots` keeps a slot only if every required subject is free for the slot plus the buffer, and at least one of each `any_of` group is.
- [ ] **Step 4: Run both files.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(bookings): availability and who may override which rule`.

### Task 3: Setup API and the package change

**Files:**
- Create: `csa/bookings/contracts.py`, `csa/bookings/setup.py`, `csa/bookings/api.py`, `db/sql/bookings/*.sql`, `tests/test_bookings_setup.py`
- Modify: `csa/console/packages.py`, `csa/console/services.py`, `tests/test_console_packages.py`, `tests/test_console_services.py`, `apps/gateway/main.py`, `csa/workspace/contracts.py`, `csa/workspace/rules.py`, `tests/test_workspace_planner.py`, `docs/CONSOLE_API.md`, `docs/console_openapi.json`

**Interfaces:**
- Produces `build_bookings_router() -> APIRouter`, prefix `/console/v1/bookings`, with:
  - `GET, POST /people`; `PATCH /people/{id}`; `POST /people/from-team`, which adds each console member of the company not yet listed, linking the account and any HR person.
  - `GET, POST /resources`; `PATCH /resources/{id}`.
  - `GET, POST /types`; `PATCH /types/{id}`; `POST /types/starters`, which creates "Appointment" (30 minutes) and "Consultation" (60 minutes) for customers, delivered by any free person, once.
  - `GET, PUT /hours`; `GET, POST, DELETE /closed-days`; `GET, POST /blocks`; `DELETE /blocks/{id}`.
- Setup writes require Admin or a platform admin. Reads need company membership. `PATCH` uses `version`, and a stale one gets 409.
- A block writes its hold in the same transaction. A block over an existing booking gets 409 `slot_taken`.

- [ ] **Step 1: Write the failing tests** in `tests/test_bookings_setup.py`:
  - Route tests for each resource: Admin writes, staff get 403, read-only reads, another company gets 403, an inactive package gets the package gate's refusal.
  - `test_from_team_adds_members_once_and_links_hr`, `test_starter_types_are_created_once`, `test_a_stale_version_gets_409`, `test_each_setup_change_writes_an_audit_row`.
  - `test_a_block_over_a_booking_is_refused`.
  - In `tests/test_console_packages.py`: `bookings` has `requires == ()` and isn't an add-on. Update the existing rule so the check still holds.
  - In `tests/test_console_services.py`: `test_bookings_activates_without_customer_support`, and `test_activating_bookings_moves_an_onboarding_company_to_pilot` with one `tenant.status_changed` row.
  - In `tests/test_workspace_planner.py`: a `bookings` tab is offered only with the package active, matching the rail.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** In `packages.py`, set `kind="standalone"` (confirm the name against the file's existing kinds), `requires=()`, and summary "A calendar for booking people, rooms and shared things, for your team and customers." In `activate_package`, promote for `customer_support` and `bookings`. Add `bookings` to `TabKey`, `TAB_GATES` (active only) and `TAB_LABELS`. Include the router in `apps/gateway/main.py` beside Subscriptions, and audit with `csa.console.kb_uploads.audit`. Leave `csa/console/agents.py` and `csa/turn/deps.py` alone: the bot doesn't change until PR 2.
- [ ] **Step 4: Run** the three changed test files one at a time, then `tests/test_console_contract_snapshot.py`, regenerating `docs/console_openapi.json` if it asks. Add the routes to `docs/CONSOLE_API.md`. Run `make lint`. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(bookings): set up people, rooms, types and hours, and sell it without Customer support`.

### Task 4: Booking and the calendar

**Files:**
- Create: `csa/bookings/service.py`, `db/sql/bookings/*.sql`, `tests/test_bookings_api.py`
- Modify: `csa/bookings/contracts.py`, `csa/bookings/api.py`, `docs/CONSOLE_API.md`, `docs/console_openapi.json`

**Interfaces:**
- Consumes `free_slots`, `pick_person`, `findings_for`, `decide` and `booking_leave_days`.
- Produces these routes, each returning `BookingOut`, and `service.create_booking(conn, client_id, actor: ConsoleActor, data: BookingIn) -> BookingRecord` for Task 5 and PR 2:
  - `GET /availability?type_id=&from=&to=`, or with `people=&resources=&minutes=`, returning slots.
  - `GET /calendar?from=&to=&person=&resource=`.
  - `GET /customers?q=`, reusing the Subscriptions customer search SQL.
  - `POST /bookings`, with `type_id` or a meeting's `title` and `minutes`; `people`; `resources`; `contact_id` or `walk_in {name, phone, email}`; `guests [{name, email}]`; `private`; `acknowledged: Finding[]`.
  - `PATCH /bookings/{id}` to move it, with `version` and `acknowledged`.
  - `POST /bookings/{id}/cancel`, `/confirm`, `/decline`, `/no-show`.
- Refusals: 409 `slot_taken` with detail "That time was just taken."; 409 `needs_acknowledging` with `findings`; 409 `refused` with `findings` and the sentence "Not available that day."; 403 for role limits; 409 `stale`.

- [ ] **Step 1: Write the failing tests** in `tests/test_bookings_api.py`:
  - `test_two_simultaneous_requests_for_the_last_slot`: two `POST /bookings` run with `asyncio.gather`; exactly one returns 201, the other 409 `slot_taken`.
  - `test_any_free_person_gets_the_least_busy`, `test_several_people_all_hold_the_time`, `test_a_room_is_held`.
  - `test_staff_outside_hours_needs_acknowledging_then_books`, `test_staff_cannot_book_a_person_on_leave`, `test_an_admin_can_book_a_person_on_leave_after_acknowledging`, and in both leave cases the response contains no leave type or reason.
  - `test_short_notice_is_allowed_for_staff`.
  - `test_a_failed_move_leaves_the_booking_and_holds_unchanged` (Review Focus 1).
  - `test_cancelling_frees_the_slot`, `test_no_show_keeps_the_record`, `test_confirm_and_decline_a_pending_booking`.
  - `test_a_deactivated_person_keeps_bookings_but_gets_no_new_ones` (Review Focus 3): existing ones are returned flagged, and a new one is refused.
  - `test_leave_approved_after_a_booking_flags_the_conflict`: the booking stays, and `BookingOut.conflict` is `"unavailable"` with no reason.
  - `test_a_walk_in_creates_a_contact_from_an_empty_list`, `test_an_outside_guest_is_kept_with_the_meeting`.
  - `test_staff_change_their_own_or_attended_bookings_only`; Admin changes any; read-only gets 403.
  - `test_a_private_meeting_shows_busy_to_everyone_but_attendees` (Review Focus 5): Admin, platform admin and other staff see only "Busy", with no title, people, guests or link, and the `person=` filter doesn't reveal more. An Admin can still move or cancel it, and the response stays redacted.
  - `test_read_only_sees_no_customer_details`.
  - `test_another_company_cannot_read_or_change_a_booking`.
  - `test_every_booking_change_writes_an_audit_row`, with no customer details in the row.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Writes run in one transaction: load the calendar, `findings_for`, `decide`, then insert the booking and its holds, catching the exclusion violation as `slot_taken`. The hold covers the booking plus the buffer. Moves replace holds in the same transaction, and cancel and decline delete them. A type's location, address and link are copied onto the booking. Redact in one function that every read uses, so a new read can't forget it.
- [ ] **Step 4: Run** `tests/test_bookings_api.py`, `tests/test_console_contract_snapshot.py` and `make lint`. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(bookings): book, move and cancel, with no double booking and private meetings kept private`.

### Task 5: Email with a calendar file

**Files:**
- Create: `csa/bookings/ics.py`, `csa/bookings/notify.py`, `tests/test_booking_ics.py`, `tests/test_bookings_mail.py`
- Modify: `csa/console/mailer.py`, `tests/test_console_mailer.py` (or the existing mailer test file), `csa/console/mail_templates.py`, `csa/bookings/service.py`, `csa/bookings/api.py`, `csa/bookings/contracts.py`

**Interfaces:**
- Produces in `csa/console/mailer.py`: `@dataclass(frozen=True) class CalendarFile(method: Literal["REQUEST","CANCEL"], content: str)`, and `OutgoingMail.calendar: CalendarFile | None = None`. Existing callers don't change.
- Produces in `csa/bookings/ics.py`: `invitation(booking: BookingRecord, organizer: str, method: Literal["REQUEST","CANCEL"]) -> str`, with `UID` `<booking id>@fourstacks.my`, `SEQUENCE` from `version`, and times in UTC. `map_links(address: str) -> tuple[str, str]` gives the Google Maps and Waze search URLs.
- Produces in `csa/bookings/notify.py`: `async notify(mailer: Mailer, booking: BookingRecord, change: Literal["created","moved","cancelled"]) -> EmailSummary`, called after the transaction commits. `BookingOut.email` is `"sent" | "off" | "failed" | "none"`.

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_booking_ics.py`: a request parses with CRLF line ends and folded lines at 75 octets. `test_awkward_text_stays_valid` (Review Focus 4): commas, semicolons, backslashes and new lines in the title, address and guest name are escaped and can't add a property. A cancel keeps the UID and has a higher `SEQUENCE`. An online type carries the link, and an in-person type the address.
  - Mailer test: with `calendar` set, the message has a `text/calendar; method=REQUEST` part and an `invite.ics` attachment, and without it the message is unchanged. `test_a_new_line_in_an_address_is_refused`.
  - `tests/test_bookings_mail.py`, using a fake `Transport`: a customer with an email, staff attendees and outside guests each get one. Nobody without an address gets one, and `BookingOut.email` says `"none"`.
  - `test_replies_off_still_emails_on_a_staff_action`.
  - `test_a_failed_send_keeps_the_booking` and `test_mail_off_keeps_the_booking` report `"failed"` and `"off"`.
  - `test_moving_updates_and_cancelling_removes_the_invitation`.
  - `test_a_private_meeting_mails_only_its_attendees` (Review Focus 5).
  - `test_an_in_person_mail_has_map_links`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Reuse the existing `Mailer` the console builds at startup, as invitation mail does. Validate a guest email as one line with one `@` at the API. Logs carry the category only, never an address or title.
- [ ] **Step 4: Run** the three test files and `make lint`. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(bookings): email invitations with a calendar file`.

### Task 6: The tab, setup pages

**Files:**
- Create: `lib/bookings-api.ts`, `lib/bookings-view.ts`, `components/console/bookings/BookingTypes.tsx`, `components/console/bookings/PeopleAndRooms.tsx`, `app/(admin)/bookings/`, `app/portal/bookings/` pages, `tests/bookings-api.test.mjs`, `tests/bookings-view.test.mjs` (all under `CSA_WebConsole/csa-console/web/`), `tests/test_bookings_browser.py`
- Modify: `lib/navigation.ts`, `lib/shell-state.ts`, `lib/assistant-view.ts` if it lists tabs, `proxy.ts`, `scripts/check-isolated.mjs`, `tests/nav-visibility.test.mjs`

**Interfaces:**
- Consumes the routes from Tasks 3 and 4.
- Produces rail entry `bookings`, label "Bookings", icon `CalendarDays`, gated on the `bookings` package while active, with sub-pages Calendar, Booking types, and People & rooms, built like `businessPages`. Routes: `/bookings`, `/bookings/types`, `/bookings/people-and-rooms`, and the same under `/portal`.

- [ ] **Step 1: Write the failing tests.**
  - `tests/nav-visibility.test.mjs`: Bookings shows with the package active, including at a company with no Customer support, and not when it's off or suspended; a platform admin keeps it.
  - `tests/bookings-api.test.mjs`: requests follow the transport pattern of `subscriptions-api`, including `client_id` for platform admins.
  - `tests/bookings-view.test.mjs`: hours and type labels, and a type's form values round-trip.
  - `tests/test_bookings_browser.py`, with stubs: an Admin adds people from the team, adds a room and uses starter types; staff see the pages with no edit controls; an empty company sees a helpful first step; no horizontal overflow at 320, 390 and 768 px in all three themes.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Follow `SubscriptionsWorkspace` and the Business pages' sub-page pattern. Add each new lib, test and public file to `check-isolated.mjs`. Add the routes to the `proxy.ts` allowlist.
- [ ] **Step 4: Run** the node tests, `tests/test_bookings_browser.py` alone with Node on `PATH`, web lint and typecheck (with `CSA_CONSOLE_API_ORIGIN` set), and `make lint`. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(console): the Bookings tab, with booking types, people and rooms`.

### Task 7: The tab, calendar and booking

**Files:**
- Create: `components/console/bookings/BookingsCalendar.tsx`, `components/console/bookings/BookingForm.tsx`, `components/console/bookings/BookingDetails.tsx` (under `CSA_WebConsole/csa-console/web/`)
- Modify: `lib/bookings-view.ts`, `tests/bookings-view.test.mjs`, `tests/test_bookings_browser.py`, `scripts/check-isolated.mjs`, `docs/ASSISTANT_RETEST.md`, `docs/PROGRESS.md`

**Interfaces:**
- Consumes the routes from Task 4, and `BookingOut.email` and `BookingOut.conflict`.
- Produces in `lib/bookings-view.ts`: `weekDays(anchor: Date, timezone: string)`, `layoutDay(bookings, timezone)`, `findingCopy(finding)`, which returns "Outside working hours.", "This is a closed day." or "Not available that day.", and `emailNote(status)`.

- [ ] **Step 1: Write the failing tests.**
  - `tests/bookings-view.test.mjs`: days and times follow the company's time zone, not the browser's; overlapping bookings across different people lay out side by side; the finding copy is exact.
  - `tests/test_bookings_browser.py`:
    - staff create an internal meeting with a room, colleagues and an online link;
    - staff book a walk-in customer from an empty list;
    - a warning shows and "Book anyway" sends `acknowledged`;
    - `slot_taken` shows "That time was just taken." and keeps the form filled in;
    - a private meeting from the stub renders "Busy" with no details;
    - a conflict flag shows on a booking;
    - booking details show Maps and Waze links for an in-person booking, and the meeting link for an online one;
    - "Mail is off" shows when `email` is `"off"`;
    - a read-only member has no create button and no customer names;
    - a platform admin switching companies follows the choice;
    - at 390 px the calendar becomes an agenda list with no overflow, in all three themes.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Use the kit's `DateRangeFilter` and `StatusChips` where they fit, and `fs/Dialog` for the form. Add `docs/ASSISTANT_RETEST.md` rows ("Bookings"), marked manual, for a real invitation arriving in a real calendar, including an update and a cancel. Add a `docs/PROGRESS.md` entry stating that the bot, reminders and the assistant's actions are PR 2.
- [ ] **Step 4: Run** the node tests, `tests/test_bookings_browser.py`, web lint and typecheck, and `make lint`. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(console): the Bookings calendar, with booking, moving and cancelling`.

### Task 8: CI triage

- [ ] **Step 1: Merge `origin/main`, push the branch and read `gate`'s failures.** Merge only, no rebase. Expect to resolve `packages.py` and navigation with TikTok and HR attendance if they've merged. The owner approves the push.
- [ ] **Step 2: Fix each failure at its cause,** running that one test file locally. A failure showing a real product bug gets its fix and a line in the PR. Repeat until `gate` passes.
- [ ] **Step 3: Write the PR description:** what changed, the rulings, deferred minors, what wasn't tested, and the rollout (migration 0510, so all four services stop).
