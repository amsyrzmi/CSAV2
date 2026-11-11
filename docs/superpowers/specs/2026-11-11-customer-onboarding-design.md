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
