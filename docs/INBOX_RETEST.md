# Inbox queue retest checklist

Run this on staging after the rollout that brings the Inbox queue backend (migrations `0620`, `0621`
and `0622`, branch `feat/inbox-refinement`, part 1a). Use the staging test company and synthetic
customers only. Spec: [the design](superpowers/specs/2026-11-16-inbox-refinement-design.md). The
routes and fields are in [CONSOLE_API.md](CONSOLE_API.md#inbox-work-queue). Every row is "manual, not
yet run by the owner": only a person can check them, and none has been run.

**There is no screen for the queue yet.** The queue tabs, filters, claim, snooze and resolve buttons
come with plan 1b. Until then these are API-level checks: the Inbox page you know still lists and
answers conversations, and it keeps working as before. To call the queue routes, sign in to the staging
console in a browser, then use the console API as the signed-in person (for example `fetch` from the
browser's developer console on the staging console tab): `GET /console/v1/auth/csrf` first, then send
the `X-CSRF-Token` header and a JSON body on every POST and PUT, and add `?client_id=<company id>` as a
platform admin. A read looks like `GET /console/v1/inbox/conversations?state=needs_staff&order=due`.
An action looks like `POST /console/v1/inbox/conversations/<id>/queue/resolve` with
`{"request_key": "<a new UUID>", "expected_version": <inbox_version from the row you just read>}`. Use a
new `request_key` for every action you want to run.

You need, in the test company: a tenant admin, a plain `tenant_staff` member, a `read_only` member, and
a Telegram customer you control (a second Telegram account, or the test chat). Nothing here sends a
reminder or a notification: those arrive in a later change.

## Rows

| Check | Setup | Expected | Kind |
| --- | --- | --- | --- |
| An escalation appears in Needs us with a due time | As the Telegram customer, write something that makes the assistant escalate to a person (for example "I want a refund and I want to speak to a person"). Then `GET /inbox/conversations?state=needs_staff&order=due` and `GET /inbox/queue/counts` | The conversation is in the list with `queue_status` `needs_staff`, `queue_reason` `escalation`, a `needs_staff_since`, a `due_at` equal to the clock plus the company's target for its priority (default 240 minutes for normal, 60 for high), no `assignee`, and `counts.needs_us` and `counts.unassigned` include it | manual, not yet run by the owner |
| Takeover creates a manual item claimed by the person | As the tenant admin, take over a conversation the assistant is handling (Inbox page, Take over). Read the conversation | `queue_status` `needs_staff`, `queue_reason` `manual`, `assignee` is you with your display name, a `due_at`; it counts in `counts.mine` | manual, not yet run by the owner |
| A reply moves it to waiting on customer | In the same conversation send a reply from the Inbox page. Read it again | `queue_status` `waiting_customer`, no reason and no due time, `last_staff_reply_at` set, the claim kept (or set to you if nobody held it). `counts.needs_us` dropped by one and `counts.waiting_customer` rose by one | manual, not yet run by the owner |
| A customer message returns it | As the Telegram customer, write again in that conversation while it is still taken over | `queue_status` `needs_staff`, `queue_reason` `customer_message`, a new `needs_staff_since` and a new `due_at`, the assignee unchanged | manual, not yet run by the owner |
| Resolve hands the conversation back to the assistant | `POST .../queue/resolve` on that item with `seen_customer_at` set to its `last_customer_at`. Then write once more as the customer | The result is `applied`; the item is `resolved` (listed under `state=resolved`, not in Needs us) and the Inbox page shows the assistant owns the chat. The customer's next message is answered by the assistant and the item returns to `bot`. A resolve that sends an older `seen_customer_at` than the newest customer message is 409 `{"code":"customer_wrote"}` and changes nothing | manual, not yet run by the owner |
| Snooze returns the item at the chosen time | On a `needs_staff` item `POST .../queue/snooze` with `until` about three minutes ahead (a UTC time with an offset). Read it at once, then again a few minutes after the time | At once `queue_status` `snoozed` with `snoozed_until`, it is out of Needs us and in `counts.snoozed`. Within about a minute after `until` (the `inbox_snooze_wake` job runs every minute) it is `needs_staff` again with a fresh `needs_staff_since` and `due_at`, the claim kept. An `until` in the past is 422 `until_in_past`; more than 60 days ahead is 422 `until_too_far` | manual, not yet run by the owner |
| A held reply makes the item needs us, and approving it moves it to waiting | Make the assistant hold a reply for review (the replies setup for the test company must hold, for example an order that needs a person). Read the conversation, then approve the held reply on the Inbox page | While held: `queue_status` `needs_staff`, `queue_reason` `held_reply`, `pending_approvals` 1. Resolving it now is 409 `{"code":"held_reply_pending"}`. After Approve: `waiting_customer`. Repeat and Reject instead: the item stays `needs_staff` with reason `customer_message` | manual, not yet run by the owner |
| Claim, and assign (admin only) | As `tenant_staff`, `POST .../queue/claim` on an unclaimed item. Then, still as staff, `POST .../queue/assign` with a member's id. Then as the tenant admin, `GET /inbox/assignees` and assign the item to the staff member | Claim: `applied`, you are the `assignee`. Staff assign: 403 `assigning needs an administrator`. Admin: the assignees list names the enabled admins and staff (no addresses); assign is `applied`; assigning to a user who is not a member of the company is 422. A `tenant_staff` member releasing a claim someone else holds (`POST .../queue/unclaim`) is 403; an administrator may release it | manual, not yet run by the owner |
| Roles | As the `read_only` member call `GET /inbox/conversations`, `GET /inbox/queue/counts`, `GET /inbox/settings` and any queue action. As a platform admin call the list without `client_id` | Read-only: 403 on every one. Platform admin without `client_id`: 400; with a company selected it can read, claim and resolve | manual, not yet run by the owner |
| Bulk resolve with one refused item | Have three `needs_staff` items, one with a pending held reply. `POST /inbox/bulk` with `action` `resolve`, the three ids and `seen_at` set to now | HTTP 200 with a result per item in the request's order: two `done`, one `held_reply_pending`. The refused item is unchanged; the other two are `resolved`. Sending the same `request_key` and body again returns the same results; the same key with different ids is 409 | manual, not yet run by the owner |
| Saved views | `POST /inbox/views` with `{"name":"Mine","filters":{"assignee":"me","state":["needs_staff","waiting_customer"],"order":"due"}}`, then `GET /inbox/views`, rename it with `PUT`, and sign in as another member and list theirs | Created (201) with the filters as sent; it appears in your list; the rename keeps the filters; the other member sees none of yours (and `PUT`/`DELETE` of your id is 404 for them); a second view named `mine` is 409; a view with `order` `priority` and no `state` of exactly `needs_staff` is 422; `DELETE` is 204 | manual, not yet run by the owner |
| A settings change affects only new items | `PUT /inbox/settings` as the tenant admin (read it first, send every field and its `version`), lowering `target_normal_min` to 30. Read an item already in Needs us, then make a new escalation or takeover | The item that was already waiting keeps its old `due_at`; the new item's `due_at` is its clock plus 30 minutes (for normal priority). A `PUT` with an old `version` is 409 `version_conflict`; as `tenant_staff` the `PUT` is 403 and the `GET` works. Put the target back to 240 afterwards | manual, not yet run by the owner |

## Notes

- The history of every move is stored (who, from, to), but no route reads it yet: it is for the later
  history line and reminders.
- Contact search (`q`) reads the company's conversations until a page fills; with the test company's
  few conversations it is instant. The scale limit is in CONSOLE_API.md.
- Rows that need the assistant to escalate or hold a reply depend on the test company's replies setup
  and on the model: if it does not escalate, try another message rather than treating it as a failure.
