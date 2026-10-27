# Writing guide

How we write the words that staff see in the console: labels, buttons, descriptions, errors and empty states. The appendix at the end is for developers only.

## Who we write for

- Write for tenant staff first. They run a small business in Malaysia, they are busy, and they are not technical.
  - Good: "Your customers can message you on WhatsApp."
  - Bad: "Inbound webhook routing is enabled."
- Write for platform admins second. They see the same screens, so never write something only they would understand.
  - Good: "This company has no active package."
  - Bad: "tenant_id has no row in package_state."

## Rules

- Use sentence case everywhere: headings, buttons, menus and table headers.
  - Good: "Team members"
  - Bad: "Team Members"
- Start every button with a verb.
  - Good: "Pause subscription"
  - Bad: "Pause"
- Say what happens next.
  - Good: "We will email the link to your colleague."
  - Bad: "Invite sent."
- Keep internal codes, table names, acronyms and UUIDs off the screen. Keep the raw code on `ApiError.code` so support can still find it.
  - Good: "We could not reach the assistant. Try again in a minute."
  - Bad: "planner_unavailable (HTTP 503)"
- Put one idea in each sentence.
  - Good: "WhatsApp only allows replies within 24 hours of the customer's last message. Wait for the customer to message again."
  - Bad: "Replies are permitted within 24 hours of the customer's last message, after which the conversation window closes unless the customer writes again."
- Use Malaysian English spelling.
  - Good: "organisation", "colour", "cancelled"
  - Bad: "organization", "color", "canceled"
- Write dates as day, short month, year, then 24-hour time.
  - Good: "27 Oct 2026, 09:15"
  - Bad: "10/27/26 9:15 AM"
- Write money with "RM", a space and two decimals.
  - Good: "RM 59.90"
  - Bad: "MYR59.9"
- Use the same word for the same thing: people on your team are "members", the people you serve are "customers", and the AI is "the assistant".
  - Good: "Ask the assistant to reply to this customer."
  - Bad: "Ask the bot to reply to this user."

## Errors

- Lead with what the person can do, then say why if it helps.
  - Good: "Ask your administrator to switch on customer support."
  - Bad: "Request failed with status 403."
- Name the thing, not the code.
  - Good: "Customer support is not switched on for this company. Ask your administrator."
  - Bad: "package_inactive"
- Never blame the person. Say what is missing or what to try.
  - Good: "That password is too short. Choose a longer one."
  - Bad: "You entered an invalid password."

Copy for the codes staff can meet:

| Code (never shown) | What staff see |
|---|---|
| `package_inactive` | Customer support is not switched on for this company. Ask your administrator. |
| `planner_unavailable` | We could not reach the assistant. Wait a minute and try again. |
| `proposal_consumed` | A colleague has already confirmed or cancelled this card. Refresh the page to see what happened. |
| `outside_service_window` | WhatsApp only allows replies within 24 hours of the customer's last message. Wait for the customer to message again. |
| `recipient_unresolved` | We have no work email for this colleague. Add one to their profile, then try again. |
| `hr_owner_required` | Only your company's HR owner can change HR access. Ask them to make this change. |
| `password_change_required` | Change your password to continue. Choose a new one on the next screen. |

## Empty states

- Say what will appear here and how to make it appear.
  - Good: "Conversations from your customers will appear here. Connect WhatsApp to get started."
  - Bad: "No data."
- Offer one clear action when there is one, as a button that starts with a verb.
  - Good: "Connect WhatsApp"
  - Bad: "Click here"
- Do not apologise or joke. An empty page is normal on day one.
  - Good: "No members yet. Invite your first colleague."
  - Bad: "Oops! Nothing to see here."

## Appendix for developers: working in two lanes

Lane A builds the customer-facing WhatsApp work. Lane B builds the console and subscriptions. These rules keep the two lanes from colliding.

- Take migrations only from your own number range. Ranges and the rules for adding one are in [db/migrations/README.md](../db/migrations/README.md).
  - Good: lane B adds a file inside its range.
  - Bad: lane B takes the next free number from the whole folder.
- Register your router only inside the "lane registrations" block in `apps/gateway/main.py`. Add your one line directly under your lane's comment; no other edits here.
  - Good: `app.include_router(build_subscriptions_router())` under the lane B comment.
  - Bad: reordering imports or middleware in the same change.
- Regenerate `docs/console_openapi.json` on your lane branch as the last commit before merge, with `python -m scripts.export_console_openapi`. A new console router must also be registered in `scripts/export_console_openapi.py` (`contract()`), or the snapshot test fails. If the other lane merged first, merge `main` and regenerate; never hand-merge the JSON.
  - Good: regenerate on your lane branch as the last commit before merge, and register the new router in `scripts/export_console_openapi.py`.
  - Bad: hand-edit or hand-merge `docs/console_openapi.json`.
- Add exactly one entry per lane to `docs/PROGRESS.md`, at the top, and update that entry as you go.
  - Good: one "Lane B: subscriptions" entry that grows.
  - Bad: a new entry for every commit.
- Leave `csa/console/packages.py` untouched in both lanes. Package definitions change only through the shared foundations work.
  - Good: ask for the change in a foundations task.
  - Bad: add your package inline while building your feature.
- Run focused tests locally and leave the full suites to CI. The host is shared and short on memory.
  - Good: `pytest tests/test_console_subscriptions.py`
  - Bad: `make test` before every push.
