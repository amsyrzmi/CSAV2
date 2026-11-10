# Customer bot: answers about subscriptions and products, then the escalation ladder

Status: design for the owner's approval, 2026-11-10. Pull request 2 of the business surfaces programme
(`2026-11-10-business-surfaces-programme.md`), and PR B of `2026-11-10-customer-bot-conversation-design.md`.
Migrations 0560 to 0569 reserved, probably none needed.

## 1. Intent

Customers write on Telegram, and soon WhatsApp. They ask about the plan they are on, what plans exist,
whether a product is in stock, where their order is. Today the bot answers some of this by accident and
otherwise asks a clarifying question and then hands off. After PR A (#92) it remembers the conversation;
this pull request teaches it the business it serves and makes it hand off only when it must.

The owner confirmed (2026-11-10) that these questions come from customers on the chat. Questions that
only staff should ask (how many orders did we get, what is low on stock, sales figures) stay with the
staff assistant; the customer bot never answers them.

## 2. What the code does today (survey of 2e7ca4e)

- Intents (`csa/contracts/plan.py`) include `subscription`, `product_info`, `order`; there is none for
  business operations, deliberately.
- `SubscriptionAgent` (`csa/agents/staged/service.py`) reads `plans`, `list` and `status`, and drafts
  `request_pause`, `request_cancel`, `request_change` for staff to decide. `status` and every request
  require a `subscription_id` UUID (`staged/models.py` `SubscriptionInput`). The turn context carries no
  subscription data, so the planner cannot fill the UUID and falls back to a clarifying question. A
  read-only loop (`coordinator.py`) means a draft request can only arrive on the next turn.
- The plans read does not select `plans.features`. Read results go to the composer as one raw JSON fact.
- There is no planner or classifier rule for subscriptions (compare the booking rules in
  `csa/decide/llm.py`).
- `subscriptions.contact_id` links a subscription to the customer's contact, which is exactly what the
  chat already identifies. Nothing uses it for the bot.
- Products: `OrderAgent` searches the local catalogue (`products`, with `available_qty`);
  `EcommerceAgent.stock_check` reads TikTok listings only as a fallback after a local search finds
  nothing (`plan_rules.py` `local_stock_first`, `tiktok_stock_fallback`); the model cannot plan it.
  Order status by TikTok order number works and shows status and tracking only.
- Escalation (`plan_rules.py`, `routing.py`, `handoff.py`): a second clarifying question becomes an
  escalation; any plan validation error escalates; the handoff carries no note for staff.

## 3. Goals, non-goals, anti-goals

Goals

- A customer can ask, in English or Malay, what plans exist (with features), which plan they are on,
  when it renews, and whether a change is pending; the bot answers from the customer's own
  subscription, found from the chat's contact, with no id typed.
- A customer can ask to pause, cancel or change a plan; the bot says it has passed the request to staff
  to decide (existing draft flow), never that it is done.
- A product question is answered from the local catalogue and the marketplace listings together when
  both exist, with the source of each number named in plain words ("in our shop", "on TikTok Shop").
- The bot hands off only for a real reason, after guiding the customer: up to two contextual
  clarifying questions, then a partial answer plus one targeted question, then a handoff that carries
  a short context note for the staff who pick it up.
- When the bot lacks a fact ("how do I pay"), it says so, offers the handoff, and the gap is recorded
  in `kb_gaps` for the Admin to fill.

Non-goals

- Business-operations questions for staff, payments, refunds executed by the bot, any write to TikTok
  Shop, TikTok chat as a channel, the Jev customer classifier.

Anti-goals

- A customer must never see another customer's subscription or order. No subscription is shown unless
  the chat's contact owns it.
- No price, stock or status claim without a fact behind it; the grounding gate and the semantic review
  stay as they are.
- The bot never says an action is done when it only created a request.

## 4. Approaches

A. **Prompt-only:** teach the planner about subscriptions and leave the data plumbing as it is.
   Cannot work: the UUID is not in the context.

B. **Resolve in code, answer from facts, rules in the prompt (recommended).** The subscription agent
   gets a `mine` read that takes no id and resolves by the chat's contact (server-side, from the turn
   envelope, never from model output). Product availability becomes one plannable read that merges
   local and marketplace facts. Planner and classifier get explicit rules and examples. The ladder is
   deterministic code in `plan_rules.py`, with the model only writing the words.

C. **A new "business" agent that can answer anything from raw tables.** Highest reach, highest risk:
   wide read access for a customer-facing model. Rejected.

Chosen: B.

## 5. Design

Subscriptions

- New action `mine` on `SubscriptionAgent`: reads the contact's subscriptions (not cancelled, plus a
  just-cancelled one for the current period), joined to `plans` for name, price, interval and
  `features`; returns one fact per subscription with exact figures and dates (renew date, pending plan
  and effective date, cancel-at-period-end). The contact comes from the turn context, not from the
  plan arguments. `plans` selects `features`; plan facts are one per plan, not raw JSON.
- `request_pause`, `request_cancel`, `request_change` accept the contact's own subscription without an
  id when the contact has exactly one active subscription; with more than one the bot asks which, by
  plan name. The request stays a draft decided by staff; the reply says it was passed on and what
  happens next.
- If no subscription is linked to the chat's contact, the bot says it cannot find one for this chat
  and offers a handoff with a note; it asks for no personal data in chat.
- Planner and classifier get a subscription rule (like `_BOOKING_PLAN_RULE`): which action for which
  question, never invent ids, pause/cancel/change are requests.

Products

- One plannable `product_availability` read (the model may plan it, unlike `stock_check` today) that
  runs the local catalogue search and the marketplace listing read and returns separate facts, each
  naming its source. A product only on TikTok is answered from TikTok (live read, existing fence and
  rate limit); one in both shows both numbers. Only products counted as sellable are described as
  available (the existing allowlist).
- Customer-scope order status stays as it is.

Escalation ladder (`plan_rules.py`, `handoff.py`, `llm.py`)

- Separate "must escalate now" (angry, wants a person, legal or police words, injection, unsupported
  language) from "could not answer". Only the second goes through the ladder.
- Ladder: attempt 1 and 2, a clarifying question that uses the conversation memory (never repeats a
  question already asked); then a partial answer from what is known plus one targeted question; then
  handoff. The count persists in the existing `clarify_count`.
- The handoff carries a context note for staff (one or two sentences: what the customer wanted, what
  was answered, what is missing), stored with the escalation, shown in the inbox thread; never sent to
  the customer. Generated by the model under the same digits-and-links grounding as the handoff text.
- Missing facts are recorded as `kb_gaps` rows (existing table), de-duplicated per question.

Prompt and rule changes follow the PR A pattern: English rules with Malay examples (glossary and
register unchanged), full golden run, replay conversations extended.

## 6. Tests (named for the builder)

- Agents: `mine` for zero, one and several subscriptions; another contact's subscription is never
  returned (RLS and contact scoping); requests without an id; `features` shown; pending change and
  cancel-at-period-end wording facts.
- Products: local only, TikTok only, both, unsellable status, live read failure falls back to the
  stored listing with the age said.
- Ladder: each rung, no repeated question, partial answer, handoff note present and grounded, must-
  escalate-now cases skip the ladder, kill switch unaffected.
- Golden cases (new, English and Malay): "what plan am I on", "when does it renew", "cancel my plan",
  "do you have <product> in stock", a product on TikTok only, a customer with no linked subscription,
  an escalation after two clarifications with a note.
- Replay (`scripts/customer_bot_replay_cases.json`): two new conversations, subscription and stock.
- Safety review: a reviewer reads the contact-scoping and the grounding of every new fact type.

## 7. Left to the builder

Fact wording and ids (constant, never carrying an order number or contact data); the exact number of
clarifications if the golden runs show a better fit; where the staff note appears in the inbox UI
(the existing thread, one line).

## 8. Owner decisions to confirm

1. Two clarifying questions, then a partial answer and one targeted question, then handoff.
2. A pause, cancel or change request from a customer stays a draft that staff decide.
3. The bot names the source of a product number ("in our shop" / "on TikTok Shop").
4. The staff context note lives in the inbox thread only.

The owner's own task for this pull request: the telco example company's knowledge base needs the facts
for how to sign up, how to pay, which plan suits whom, and what follows a handoff.
