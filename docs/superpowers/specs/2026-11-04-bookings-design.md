# Bookings stage 1: design

Status: draft for the owner's approval, 2026-11-04. Branch `feat/bookings`. Migrations 0510-0519.

## Why

A company buying Bookings today gets a bot that files a request nobody reads, and no staff page. `BookingAgent` writes to `agent_preparation_requests`, which has no reader for bookings and no way to act. Nothing checks availability, and nothing writes to the `bookings` table.

The owner wants a real booking system that serves two groups:

- **Customers** book through the bot, straight into a free slot.
- **Staff** book for a customer, and book meetings, rooms and other shared things with each other, in companies big or small.

## Goals

- A customer on Telegram or WhatsApp can ask what's free, book, change or cancel, and the booking is real when the bot says so.
- Staff can do all of that from a Bookings tab and from the assistant at the side of the screen.
- Staff can book an online meeting, a room, or anything the company has named as bookable, with colleagues.
- Nothing is double-booked, and nobody is booked while on approved leave.
- People get an email with a calendar file, and customers get reminders on Telegram or by email.
- A company with no customers, such as Syarikat Cekap, can use Bookings for internal meetings.

## Non-goals, for this stage

- Google Calendar and Meet (stage 2). Microsoft 365 and Teams (stage 3).
- WhatsApp reminders. They need Meta-approved templates, and the owner's Meta business verification is still in review.
- Employees booking a service from the company, such as an HR or IT appointment.
- Zoom, a public booking page, deposits and payments.
- Recurring meetings, group classes, waiting lists and booking reports.
- Different time zones within one company.

## Anti-goals

These would make it a failure even if it technically works.

- The bot says a booking is confirmed when no booking exists.
- Two people hold the same person, room or thing at the same time.
- The bot or a calendar reveals why someone is away, or shows one customer another's booking.
- A company with replies Off has the bot booking slots or messaging customers.
- A customer gets a reminder for a booking that was cancelled or moved.

## Approaches

- **A. Our own booking system, with calendars connected later (chosen by the owner).** Bookings, availability and rooms live in our database. It works for every company today and needs no outside approval. It's the most work up front.
- **B. Google Calendar as the master.** Less to build, but it needs Google for every company, waits on Google verification, and handles rooms and "any free person" poorly.
- **C. A booking product such as Cal.com.** Fast public pages, but it adds a cost or another service, keeps customer data elsewhere, and fits poorly with HR leave and per-company access.

## What a company sets up

Admins set these up on the tab. Anyone in the company can read them, subject to the access rules below.

- **People who can be booked.** Bookings keeps its own list, because a company without the HR package has no HR records. Each entry has a name, an optional link to a console account, and an optional link to an HR person. Staff with accounts can be added in one click.
- **Rooms and things.** Anything the company names, such as "Meeting Room A", "Delivery van" or "Projector", with a capacity if it matters.
- **Booking types.** One model covers appointments and meetings. Each type has:
  - a name and length, and a gap kept free afterwards;
  - who it's for: customers, staff, or both;
  - who delivers it: a named person, any free person from a list, or several people at once;
  - any room or thing it needs: a specific one, or any free one from a list;
  - where: in person (with an address), by phone, or online (with a fixed meeting link);
  - a "needs staff confirmation" switch, off by default;
  - how far ahead it can be booked, the shortest notice, and how late a customer may change it.
- **Hours.** Company working hours by weekday, which a person or room can override. A list of closed days, and one-off blocks for a person or room, such as a room being repaired.

An internal meeting doesn't need a booking type: staff pick the people, the time and optionally a room or link. Starter types can be offered, so a small company isn't faced with an empty page.

## How availability works

A slot is free when all of this is true:

- it's inside working hours and not on a closed day, in the company's time zone (`clients.timezone`);
- it meets the type's notice and how-far-ahead rules;
- every required person and thing has no active booking or block overlapping it, including the gap after each booking;
- no required person is on approved leave.

For "any free person", the system picks whoever has the fewest bookings that day, so work spreads evenly. The bot offers a few slots, not a long list.

**The database has the final say.** A booking writes one row for each person and thing it holds. The database refuses an overlap, for everyone, so two simultaneous requests can't both win, and the loser gets "that time was just taken" with fresh options.

**The other rules are checked by the server on every write,** whether from the tab, the assistant or the bot, and depend on who is booking (owner's decision):

| Situation | The bot | Staff | Admin |
|---|---|---|---|
| Outside working hours or on a closed day | Never | Warning, then allowed | Warning, then allowed |
| Shorter notice than the type allows | Never | Allowed | Allowed |
| Person on approved leave | Never | Refused | Warning, then allowed |

The warning about leave says only "not available that day", never why.

## HR leave

- Approved leave (`hr_cases`, domain `leave`, `starts_on` to `ends_on`) blocks a person whose entry is linked to an HR person, for those whole days.
- Bookings reads only the person and the dates. It never shows or stores the type of leave or the reason. On screen and in the bot, the person is just not available.
- If leave is approved after a booking exists, the booking stays and the tab flags the conflict for staff. Nothing is cancelled automatically.

## The customer's path through the bot

`BookingAgent` keeps its name and replaces its actions: `availability`, `book`, `reschedule`, `cancel`, `list`, `status`. It works for the customer's own contact only. The server checks every time and slot, so the model can't invent one, and the reply is built from what was really written.

**Reply modes decide what the bot may do.** Agents run before the gate, so the agent itself must obey this. Without it, a company with replies Off would be booking slots silently.

| Company's mode | What a `book` does |
|---|---|
| Off | Writes nothing. Reads are still allowed, and the turn ends as it does today, with no reply. |
| Staff approve each reply | Holds the slot as `pending`, for staff to confirm in the tab. |
| Bot replies by itself | `confirmed`, or `pending` if the type needs staff confirmation. |

- **Pending bookings** hold the slot. If staff don't act before the expiry, the booking is released and the customer is told. The customer is always told it isn't confirmed yet.
- **"Staff approve each reply":** the bot's reply about a pending booking is a draft like any other. Rejecting the draft doesn't release the slot: staff confirm or decline the booking in the tab, or it expires. The expiry notice goes straight out, as handoffs and expired-draft notices already do in this mode.
- **Changing a booking that needs confirmation:** the old slot stays confirmed, and the new one is held as `pending`. If staff confirm, the booking moves and the old slot frees up. If staff decline or it expires, the old booking stands and the customer is told.
- **Cancelling asks first** (owner's decision, 2026-11-06): the bot replies naming the booking, for example "Cancel your Haircut on Tuesday 10 November 2026, 3:00 pm (2026-11-10 15:00)? Reply yes to cancel.", and cancels only when the customer's next message is a clear yes (in any language) within 30 minutes and the booking hasn't changed since. A stale or late yes gets the question again; any other message drops it. Moves still go straight through. In "Staff approve each reply" the question is a draft like any other reply, and it counts only once the customer is sent it as drafted: a held, rejected or edited draft is no question, and the 30 minutes run from when it was sent.
- **A retried turn** can't book twice: a booking is tied to the turn that made it.
- **Limits:** a customer can hold a limited number of upcoming bookings, so nobody can fill a calendar by chatting.
- **Past the change deadline,** the bot hands the customer over to staff.
- **Details:** the bot asks for a name if it doesn't have one. On WhatsApp, it asks for an email address, because that's how the customer gets a reminder. Saying no is fine, and the staff page shows that this booking has no reminder.
- **Who they'll see:** for "any free person", the customer learns the name only once it's booked.
- **Preview:** the Overview chat keeps blocking bookings, as it does now.

## Staff

**The tab** is a new rail entry, "Bookings", with sub-pages:

- **Calendar:** a day and week view with filters by person and room, and a simple agenda list at phone width.
- **Booking types.**
- **People & rooms,** with hours and blocks.

From the calendar, staff can create, move and cancel bookings, confirm pending ones, and mark no-shows.

**Who can be on a booking besides staff (owner's decision):**

- **Customers:** pick an existing contact, or enter a walk-in with just a name and a phone number or email. This must work from an empty list, because a company without Customer support may have no contacts.
- **Outside guests on internal meetings:** allowed by email address, such as a supplier. They get the invitation and calendar file, but no console access.
- **Reminders** go to whichever address the booking has. Someone with no address gets no reminder, and the tab shows that.

**The assistant at the side of the screen** can find a free time and book, move or cancel, as a card to confirm like its other actions, in English and Malay. For example: "book Room A tomorrow at 3 with Aina".

**Access:**

| Role | Can do |
|---|---|
| Admin, or a platform admin who has chosen the company | Everything, including setup. |
| Staff | See the calendar; create bookings; change or cancel their own, or those they attend. Customer details follow the same rule as the Inbox. |
| View only | See the calendar. |

An internal meeting can be marked private, so that everyone except its attendees and creator sees only "Busy", on every read including admins and platform admins. An admin can still move or cancel it without seeing the details. Every setup change and every booking change writes an audit row.

## Messages

- **Email with a calendar file:** customers with an email address, staff attendees and outside guests get an `.ics` invitation. A change updates it, and a cancellation removes it from their calendar. This goes through the existing SMTP mail, which needs attachment support added. If mail is off, the tab says so and the booking still works.
- **Maps and Waze links** appear for in-person types. The fixed meeting link appears for online types.
- **Reminders:** a background job sends them at a company-set time, 24 hours before by default. Telegram customers get a message in their conversation, which staff can see in the Inbox. Others get email. Staff attendees get email.
- **Each reminder goes once,** and never after the booking is cancelled or moved.
- **A late booking gets no reminder** (owner's decision, 2026-11-06): a booking made, or moved to its current start, after its reminder would be due (the start minus the company's reminder time at that moment) gets none, for the customer, the staff or the guests: its confirmation is enough. The same goes for a booking the bot asked for that staff confirm after then (nobody on it heard of it before); a staff-made booking keeps its reminder, because its people were invited when it was made. One made before then keeps its reminder even if the reminder time changes later.
- **A suspended Customer support package** stops everything automatic to customers too, so the tab shows the Replies Off banner then, without the link (Services can't turn replies on for a suspended company). A company with no active or suspended Customer support package reminds its customers by email only, never on Telegram.
- **Replies Off:** nothing automatic goes to customers while replies are Off: no reminders and no expiry notices. The tab shows this with a banner. A staff member's own action still sends: if staff book, move or cancel for a customer, that customer gets the email. This follows Go live, where a staff approval still sends while replies are Off, because it's a person's explicit choice, not the bot's.
- **A WhatsApp customer with no email** gets no reminder, and if staff change their booking the tab says "Customer wasn't told", so staff can follow up.

## Package changes

- `bookings` no longer requires Customer support, so an employee-only company can buy it. The tab and assistant work there, and the customer bot has nothing to do.
- **Activating Bookings must also move an `onboarding` company to `pilot`,** as Customer support does. Background jobs skip `onboarding` companies, so otherwise reminders and expiries would silently never run.
- The package summary and the agent's descriptions in `csa/console/packages.py`, `csa/console/agents.py` and `csa/turn/deps.py` change to say what's now true.

## Data

- The existing `bookings` table is extended, not replaced. `calendar_event_id` stays for stage 2. It gains the type, who created it, where it came from, and a real status: `pending`, `confirmed`, `cancelled`, `no_show`.
- Old rows in `agent_preparation_requests` stay where they are, unused.
- Every new table has `client_id` and the usual tenant policy. No new roles, and never `FORCE`.
- Migrations stay in 0510-0519, added to `db/migrations/README.md`. `EXPECTED_SCHEMA_VERSION` stays at the highest applied version.

## Delivery

Stage 1 ships as two PRs, each with its own CI and rollout:

1. **Core and staff:** tables, availability, HR leave, the tab, internal meetings, staff booking for customers, email with a calendar file, and the package changes.
2. **Bot and reminders:** the new `BookingAgent`, reply-mode rules, reminders and the assistant's actions.

Until the second PR ships, the customer bot behaves as it does today.

## What must be true when it's done

- Two simultaneous requests for the last slot: exactly one succeeds, and the other is offered new times.
- A customer who hears "confirmed" has a confirmed booking in the tab.
- A company with replies Off: a booking request writes nothing and sends nothing.
- A person on approved leave is never offered, and no screen or reply says why.
- A cancelled booking gets no reminder, and the attendee's calendar file is cancelled.
- Syarikat Cekap can activate Bookings without Customer support, book a room and meeting, and get email invitations and reminders.
- One customer can never see another's booking, and one company can't see another's.
- All screens work in all three themes and at phone width.

## Constraints

- Synthetic fixtures and disposable testcontainers only. No real address: the repository is public.
- Never touch the single Google OAuth client in this stage.
- CI is the merge gate. Locally, run the changed files' tests one at a time, plus `make lint` and the web checks.
- Other sessions are building TikTok Shop and HR attendance, which also touch `packages.py` and the navigation. Whichever merges second resolves that.

## My calls, for the owner to check

These weren't discussed. Each is my recommendation.

1. Bookings keeps its own list of bookable people, optionally linked to accounts and HR.
2. "Any free person" means the person with the fewest bookings that day.
3. Default reminder is 24 hours before, set per company.
4. Replies Off also stops customer reminders from the booking system.
5. With "Staff approve each reply", the bot's bookings are `pending` until staff confirm in the tab.
6. Internal meetings can be marked private, showing others only "Busy".
7. Leave approved after a booking flags the conflict and doesn't cancel it.
8. Whole-day leave only, because that's how leave is stored today.
9. The bot asks WhatsApp customers for an email address, and accepts no.
10. The Overview preview keeps blocking bookings.
11. Replies Off stops what's automatic, but a staff member's own booking action still emails the customer.
12. Rejecting a draft reply doesn't release a pending booking.
13. A change awaiting confirmation keeps the old slot until staff decide.
14. A private meeting shows "Busy" even to admins and platform admins, who can still move or cancel it.

## Left to the builder

- How the database refuses overlaps. `btree_gist` isn't installed today, so check whether the migration can add it to the staging and CI images. Otherwise use another method that passes the simultaneous-request test.
- How long a pending booking waits, how many upcoming bookings a customer may hold, and how often slots start, as sensible defaults.
- The calendar's layout, using the kit in `components/console/kit/`.
- How a proactive Telegram reminder fits the delivery path, so it shows in the conversation and retries safely.
- Whether HR's public holidays can fill the closed days list.
- How non-HR staff and the bot read leave dates, which sit behind HR's row security. It must return only the person and the dates.
- Table names and API shape, following Subscriptions as the pattern.
