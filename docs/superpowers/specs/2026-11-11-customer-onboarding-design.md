# Customer onboarding: from a chat contact to a customer with a subscription

Status: design for the owner's approval, 2026-11-11. PR 2b of the business surfaces programme
(`2026-11-10-business-surfaces-programme.md`), after PR 2 (`2026-11-10-customer-bot-business-answers-design.md`).
Migrations 0580 to 0589. Payments are a separate design (`2026-11-11-payments-design.md`) that this one
only prepares for.

## 1. Intent

A person who writes to a company's bot is a contact and nothing more. Staff turn them into a customer by hand:
a console user must create the subscription for an existing contact, and the same human on Telegram and WhatsApp
is two contacts. With thousands of customers that does not scale. The owner wants a customer to sign up from the
chat, to be recognised across channels, and then to pay (the payments design).

## 2. What the code does today (survey of 4d4d808)

- `contacts` (name, phone, email, `telegram_id`, `contact_kind`, no whatsapp column); channel ids live in
  `contact_identities`. There is no customer or lead status. The inbox calls a sender "known" when they have an
  order, a subscription, an email or an address.
- Telegram creates a contact on the first message with only the display name; no phone or email. WhatsApp links to
  an existing contact when exactly one contact in the tenant has the same phone digits, else creates one
  (`resolve_whatsapp_contact.sql`).
- No merge or link across channels exists. `contact_verifications` is a table nothing uses.
- Subscriptions are created only by staff (`POST /subscriptions`, a required existing `contact_id` and an active
  plan). The bot's `SubscriptionAgent` can request pause, cancel and change for an existing subscription; there is
  no "new subscription" request. The staff queue (`agent_preparation_requests`, decided in the console by
  `decide_request`) already exists.
- Subscription status is `active`, `paused` or `cancelled`.

## 3. Goals, non-goals, anti-goals

Goals

- A customer can ask to sign up in the chat: the bot collects the person's name, phone and the plan they want and
  files one **sign-up request**. At scale, a plan can be set to **auto-approve** (default off) so a sign-up creates
  the subscription without staff; otherwise staff approve in the Subscriptions tab with one click.
- A new subscription starts as **pending** and becomes **active** when its first invoice is paid (the payments
  design), or when staff activate it.
- The same human on Telegram and WhatsApp becomes one customer when the phone number is **verified by the channel**:
  WhatsApp's own number, or a number the person shares with Telegram's "share my number" button. Staff can also link
  two contacts by hand from the console, with an audit row.
- Staff see where a customer came from and can fix a wrong link.

Non-goals

- Taking payment in this design; ID or KYC documents; email verification; a customer self-service portal; WhatsApp
  media; merging order history of two linked contacts beyond re-pointing subscriptions, bookings and invoices.

Anti-goals

- **Never link, or reveal a subscription, on a phone number the person typed.** Only a number the channel itself
  verified links contacts, otherwise anyone could claim another person's subscription.
- A sign-up never charges, never activates a subscription before the rule says so, and never promises approval.
- Personal data collected is the minimum (name, phone), said to the customer in one sentence, kept on the contact.

## 4. Approaches

A. **Staff-only onboarding with a nicer console form.** No bot change; does not scale.
B. **Bot sign-up request through the existing staff queue, optional per-plan auto-approve, verified-phone linking
   (recommended).** Reuses the request queue, the subscription service and the grounding gate; the approval policy
   is the one switch that decides how much staff work remains.
C. **Full self-service portal with login.** Largest build and a new auth surface; not needed to reach thousands.

Chosen: B.

## 5. Design

Data (migration 0580 onward, additive)

- `contacts.phone_verified_at timestamptz`, set when a channel verifies the phone (WhatsApp ingress; Telegram
  contact-share). A unique partial index per tenant on the normalised digits for verified phones.
- Subscription status gains `pending` (extend the check constraint by reading the live list, as 0531 does);
  `subscriptions.activated_at`.
- `plans.signup_mode text` (`staff` default | `auto`) and `plans.is_signup_open boolean` (default true).
- A contact link record `contact_links(client_id, from_contact_id, to_contact_id, reason, moved jsonb, created_by,
  created_at)` (reasons `verified_phone`, `staff`) and `contacts.merged_into`; what a link moves is defined in
  section 9.

Bot

- New `SubscriptionAgent` action `request_signup` (draft risk, like the other requests): inputs plan code, full name,
  phone (E.164). The planner rule asks only for what is missing and asks one thing at a time; the customer confirms
  with the existing confirm button (typed reply on WhatsApp). The reply says the request was received and what
  happens next, never that the subscription exists.
- Telegram: when a phone is needed the bot sends the "share my number" keyboard button; the shared number is
  stored on the contact with `phone_verified_at`.
- WhatsApp: the contact's own number is already verified; no phone is asked.
- Linking: when a verified phone matches exactly one other contact of the tenant that holds no identity on the same
  channel, the two are linked automatically (`verified_phone`); several matches or a conflict leave both contacts and
  raise a staff review item. Never any link from a typed number.

Console (Subscriptions tab)

- Requests list shows sign-ups with the person's details, plan and source channel; Approve creates the subscription
  as `pending` and, with the payments design, the first invoice in one transaction (section 9 says how); Decline
  asks for a reason shown to staff only.
- A "Link contacts" action on a customer with a picker and an audit row; "Unlink" for a wrong link within 30 days.
- Plan editor gets the sign-up switch (staff approves / sign up automatically) and "closed to new sign-ups".

Scale

- Request creation and the approval queue are indexed by (client_id, status, created_at); the linking function runs
  per verified-phone event, not in a sweep; auto-approve removes staff from the common case.

## 6. Tests (named for the builder)

- Agent: request_signup collects missing fields one at a time, rejects a plan that is closed or unknown, files one
  request per customer per plan (idempotent), never says "approved"; Malay and English golden cases.
- Linking: verified WhatsApp phone links a staff-created contact; a typed number never links (explicit test); two
  matches raise a review and link nothing; Telegram share links; unlink restores identities; RLS isolation.
- Approval: staff approve creates a pending subscription and (with the payments design) the invoice atomically;
  auto-approve plan creates it without staff; decline records the reason.
- Console: requests list, link and unlink, plan switch; portal twin.

## 7. Left to the builder

The exact planner rule wording, phone normalisation library choice within the existing dependencies, where the
review item for ambiguous links sits in the console.

## 8. Owner decisions to confirm

1. A sign-up starts as `pending` and activates when paid (or on staff action), not before.
2. Sign-ups need staff approval by default; a plan can be switched to automatic approval.
3. Only channel-verified phone numbers link contacts; ambiguous cases go to staff.
4. The customer is told in one sentence what is stored (name, phone) when asked for it.

## 9. Builder check (2026-11-11): calls made by the designer

These settle what the builder check found and supersede any earlier sentence they contradict.

- **Sign-up is its own service path, not the existing request queue.** The existing queue (`agent_preparation_requests`,
  `decide_request`, the `subscription_action` proposal) only handles pause, cancel and change on an existing
  subscription, and its rows cannot be updated. A new table `signup_requests(client_id, contact_id, plan_code,
  full_name, phone, status pending|approved|declined|expired, decided_via staff|auto, decided_by nullable,
  decline_reason, created_at, decided_at)` with a unique open request per contact and plan (a decline allows a new
  one) and a service `csa/subscriptions/signup.py` (`approve_signup`, `decline_signup`, `auto_approve`). Approve
  runs in one transaction: the subscription, the first invoice, the request update, a `subscription_events` row.
  An automatic approval has no staff actor: `decided_via='auto'`, `decided_by` null, and the audit/event row says
  `auto`. The Subscriptions tab lists sign-ups in their own "Sign-ups" section beside the existing requests.
- **Dates while pending.** `subscription_insert` and the check `renews_at > started_at` are relaxed for `pending`
  rows: `started_at` is the approval time, `renews_at` and `renewal_anchor` stay null. Activation (first payment or
  staff) sets `started_at`, `renews_at` (one interval later) and the anchor in the same transaction.
- **`pending` is patched everywhere** (the check constraint, the `Status` literal, `ALLOWED_FROM`, `plan_in_use.sql`
  so a plan with pending sign-ups cannot be archived or have its interval changed, the customer lists, the bot's
  `status` read). Staff can cancel a pending subscription. An unpaid pending subscription expires to `cancelled`
  after 14 days (a company setting, default 14, `0` = never) with an event row. PR 2's `mine` read includes `pending`
  and says "waiting for payment" (the PR 2 design is amended).
- **What a link moves.** Nothing is re-pointed except subscriptions, bookings and invoices of the losing contact;
  conversations, orders, tickets and the rest stay on the old contact row, which gets `merged_into` so the console's
  customer card and inbox follow it to the survivor. The survivor is the contact that already holds a subscription,
  else the older contact. The new channel's identity row points at the survivor, and the survivor takes the loser's
  `telegram_id` only when it has none (otherwise two Telegram accounts belong to one person: the link is refused
  and goes to review). `contact_links.moved` lists the moved row ids, which is what "Unlink within 30 days" restores.
  Staff can unlink after 30 days by hand only (a console action that re-checks the rows).
- **The WhatsApp resolver is kept and tightened.** WhatsApp's own number is channel-verified; a staff-entered contact
  phone is trusted (the anti-goal is about numbers a customer types in the chat). The resolver uses one shared
  Malaysian normaliser (a leading 0 becomes 60, spaces and dashes dropped) for both sides and sets
  `phone_verified_at` on the matched contact. The unique verified-phone index needs no backfill (the column starts
  null); two contacts with the same digits are never auto-linked (already the rule) and go to review.
- **Telegram share-my-number.** Delivery gains an outbound `Ui.request_phone` rendered as a reply keyboard with
  `request_contact` and removed after use. Inbound, the Telegram parser reads `message.contact` and ingress
  intercepts it before the graph (as `_StaffEdit` does): it accepts it only when `contact.user_id` equals the sender,
  stores it with `phone_verified_at`, removes the keyboard, then passes the graph a synthetic inbound
  "[shared phone number]" so the sign-up continues from the conversation memory. A forwarded contact card is refused
  with a plain sentence.
- **Ambiguous links** go to a new table `contact_link_reviews(client_id, contact_a, contact_b, reason, status
  open|resolved, resolved_by)` listed under "Needs review" in the Subscriptions customers view; not to
  `escalations` (conversation-based).
- The `subscription_agent` capability is seeded disabled per company, so sign-up inherits that switch; the company
  enables it with the Subscriptions package.

## 10. Builder check 2 (2026-11-12): the settled design

The build (one pull request, twelve tasks, migrations 0580 and 0581) departs from sections 5, 8 and 9 in the
places below. Each is the design now; where a sentence above says otherwise, this section wins. Each ruling
says what it costs if it was the wrong call.

### 10.1 Deviations from the design and the survey

1. **A link re-points every contact-owned row; there are no `merged_into` followers.** Section 9 re-points
   only subscriptions, bookings and invoices and makes the console and inbox follow `merged_into`. The survey
   found about thirty readers, and the Telegram ingress returns 409 when a conversation's contact differs from
   the resolved contact, so that design breaks the next message after a link. Re-pointing conversations,
   orders, tickets and the rest to the survivor keeps every reader and the ingress unchanged. The loser keeps
   `merged_into` only to hide it from live lists. *If wrong:* a bigger link transaction and a heavier unlink
   (ids per table in `moved`); an unlink after new activity leaves the new rows with the survivor.
2. **Staff-entered phones count as a match; typed phones never do.** The Telegram share links to exactly one
   contact with the same digits, whether that contact's number was verified or entered by staff (the WhatsApp
   resolver already trusts staff numbers). *If wrong:* a mistyped staff number links two people; staff can
   unlink within 30 days.
3. **Unlink after 30 days is refused (409 `unlink_window_closed`), not offered "by hand".** Section 9 says by
   hand only; no safe manual path exists once rows have moved. *If wrong:* an old wrong link can only be fixed
   by linking the other way.
4. **A typed read-back and a typed "yes" replace the confirm button.** No confirm button exists for this agent
   and WhatsApp ignores `ui`; the booking cancel already confirms by a typed yes. The server requires the
   "yes" to answer the chat's latest assistant message, and that message to be the read-back of the same plan,
   name and phone. *If wrong:* a model that mistakes "ok" for a yes files an unwanted request; staff approval is
   the default and a pending subscription never charges.
5. **Staff activate until payments exist.** Nothing creates subscription invoices yet; the payments project
   will. An automatically approved sign-up creates a pending subscription that staff still start, or that
   expires after the company's days. *If wrong:* auto-approve saves less staff time than designed until the
   payments project lands.
6. **Archiving a plan stays unblocked.** `plan_in_use` runs only when the interval changes (and pending
   subscriptions now count toward it); blocking archives would also change active and paused plans.
   `is_signup_open` is how a company stops sign-ups.
7. **No staff push notification for a new sign-up in this PR.** The tab shows an open count. If filing a
   pause or cancel request notifies staff elsewhere, mirror it as a follow-up.
8. **The "Link contacts" anchor is the subscription drawer plus a "Needs review" section in the Sign-ups
   tab**, because the Customers view lists subscriptions, not contacts, and a contact with no subscription
   never appears there.
9. **A typed sign-up phone is never copied to `contacts.phone`.** Found by the Task 2 review: the copy plus
   trusting staff-entered phones would let a typed number link its owner's contact to the typer. The typed
   number lives on `signup_requests.phone` and is marked unverified unless the contact's own phone is
   verified. *If wrong:* staff retype the number onto the contact when they want it there.
10. **Corrections from the survey.** `renews_at > started_at` is a `WHERE` in `subscription_insert.sql`, not a
    table check; the portal "twin" is the same `SubscriptionsWorkspace`; PR 2's `mine` read already handled
    `pending`; no subscription settings table existed; golden cases cannot send a callback tap or a contact
    share (those are covered in `test_ingress.py`, `test_telegram.py`, `test_delivery_buttons.py` and
    `test_staged_agents.py`); `contact_verifications` is unused and unsuitable and stays untouched; the
    baseline `subscription_requests` table is unused and left alone; a phone normaliser needs an IMMUTABLE
    SQL function behind a generated column, because the unique index cannot call Python.

### 10.2 Rulings made during the build

- **Ruling A: `merged_into` is set before the survivor takes the phone.** The verified-phone unique index
  ignores merged rows, so `link_contacts` marks the loser merged first and only then writes the survivor's
  phone and `phone_verified_at`. `moved["contacts"]` records both contacts' phone, stamp and Telegram id
  before and after the link, so an unlink restores them in reverse order.
- **Ruling B: the re-point SQL is built from the `LINKED_TABLES` constant.** Table names come from that
  constant only, never from input, and there is no `repoint_table.sql`. The constant has twelve tables
  (eleven planned plus `signup_requests`); the 0581 trigger and grants cover `agent_preparation_requests`
  (`UPDATE (contact_id)` for `csa_app`).
- **Ruling C: the link and review routes live in `csa/contacts/api.py`.** `build_links_router()` is included
  by `build_subscriptions_router()` ahead of the `/{subscription_id}` routes, so the prefix, the guards and
  the static-before-dynamic order hold.
- **Ruling D: see deviation 9.**
- **Ruling E: the subscription and sign-up prompt rules are per company.** The planner and classifier rules
  for subscriptions and sign-up (and the capability list, schemas and look-ahead) are included only for a
  company with `subscription_agent` switched on. A company that sells only through TikTok Shop gets no
  sign-up wording, and "how do I sign up" gets the honest no-information answer with an offer of a person.
  A TikTok purchase never creates a subscriber or links a contact in this PR. *If wrong:* a company with the
  capability off sees a sign-up offer it cannot honour.
- **Ruling F: an unlink sticks.** An unlinked pair is never linked automatically again: when the pair has an
  earlier undone link, `verify_phone` opens a review (reason `previously_unlinked`) and sets nothing. Staff
  can still link that pair. `unlink_contacts` returns `UnlinkOutcome(skipped=[...])`, entries of the form
  `role.column` for a phone, stamp or Telegram id it left alone because it changed since the link or another
  contact now holds it; the console and the audit row show them. *If wrong:* a pair that really was re-merged
  needs staff to link it by hand once.
- **Review reasons.** `several_matches`, `telegram_conflict`, `same_channel_identity`, `table_conflict` (one
  of the loser's rows would collide with a unique key of the survivor's, such as a waiting sign-up for the
  same plan) and `previously_unlinked`. Staff cannot link a pair refused for `telegram_conflict`,
  `same_channel_identity` or `table_conflict` (409 `link_refused:<reason>`) until the clash is gone.
- **The WhatsApp ingress is race-safe.** Resolve and `verify_phone` run in a savepoint with one retry on
  `contact_merged`, so a link racing the first message cannot strand the new identity on a merged shell. A
  second merge returns a retryable 503 (`IngressError`) and is logged.
- **A contact card is judged before the contact is resolved.** A foreign, missing-`user_id` or forwarded
  card is refused before contact resolution: nothing is stored and the update is recorded as handled. The
  stored payload hash of a share has `phone_number` and `vcard` scrubbed. The reply keyboard is dormant until
  the bot asks (the sign-up agent emits `Ui(request_phone=True)`).
- **The sign-up draft lives in `pending_actions`.** The planner only sees `[PHONE_n]` and inbound bodies are
  stored redacted, so a typed number cannot be recovered on the "yes" turn. The bot keeps a one-hour per-chat
  draft (action `signup_draft`, no migration), updated in place (one row per chat) and with its payload
  blanked when it is closed or expires. The server requires the read-back to match plan, name and phone.
  *If wrong:* typed name and phone sit in `pending_actions` for up to an hour.
- **Pending subscriptions expire.** A fifth rule in the subscriptions-due job cancels a pending subscription
  older than the company's `pending_expiry_days` (default 14, 0 never, 0 to 90) and marks the approved
  sign-up it came from `expired`; each gets a `pending_expired` event. A company with no active
  Subscriptions package is skipped, as for the other rules.

### 10.3 Open owner decision

The sign-up's data sentence ("We keep your name and phone number on your contact record so the team can set
up your plan.", `SIGNUP_DATA_NOTE`) says "on your contact record", but by deviation 9 a typed phone stays on
the sign-up request and only a channel-verified number reaches the contact. The builder did not decide
whether the sentence should say "on your sign-up request" or keep the broader promise; the owner decides
after reading it in the retest (`docs/CUSTOMER_BOT_RETEST.md`, "Customer onboarding").
