# Customer bot: conversation memory, register and handoff wording (PR A of 2)

Status: approved by the owner 2026-11-10 (handoff decision: option a; window 20 messages, 200 characters each). Items marked **(call)** are decisions I took; the owner can overrule any of them. Scope: the CUSTOMER support bot (the graph in `csa/graph`, the turn in `csa/turn/deps.py`, the prompts in `csa/decide/llm.py`), not the staff workspace assistant.

## Intent

The owner reported two problems with the customer bot: it escalates to a human too fast and repeats itself, and its Malay is too formal and rigid (the owner wants informal, "street" Malay that is still professional). A ~25-turn staging conversation (a Malay-speaking customer of a telco tenant) and the staging database show why:

1. **The bot has no memory of the conversation for most companies.** History (`RecentMessage`, 6 messages) exists only inside `BookingContext`, is filled only when the company has the booking agent and Customer support on and the channel is not web (`csa/turn/deps.py` `_booking_context`, `csa/bookings/turn_context.py`), and is shown only to classify and plan. The composer (`LLMProvider.compose`) never sees any history for any company. So the bot repeats plan lists, asks a buyer for an order number, reads "I travel a lot, I watch YouTube" (the answer to its own recommendation question) as chit-chat, and repeats the same handoff line.
2. **The handoff and fallback texts are fixed English strings** (`HANDOFF_TEXT`, `FALLBACK_TEXT` in `csa/turn/deps.py`): a Malay customer received them several times.
3. **Some of the "escalations" were model-provider errors, not decisions**: staging holds `fail_safe:signals:GroqError` and `fail_safe:verify:GroqError` rows at the transcript's times; a classify or verify error goes straight to `fail_safe` with the English `FALLBACK_TEXT` and an escalation row, with no retry.
4. **No register is set anywhere**: the composer is told only "Reply language: Malay" plus the persona tone, so the model's default formal Malay ("anda", "sila", "pihak kami", the Indonesian-ism "menemukan") comes out.

The escalation rules themselves (one clarifying question, then escalate; `angry` as an unguided model label; route reasons discarded; what staff receive) are a second piece of work (PR B), designed after this one lands, because they need the memory this PR adds.

## Goals

- **G1 Conversation memory for every company and channel** (including the web sandbox): the last messages reach classify, plan and compose.
- **G2 No repetition:** the composer is told what was already said, whether a team member was already notified, and not to repeat it; long option lists are shown in small pieces.
- **G3 Register:** Malay replies are informal and warm but professional by default, mirroring the customer's level, with the persona tone able to override.
- **G4 Handoff wording:** the handoff reply is written for the customer's language, register and situation, with localized fixed templates as the fallback; handoff delivery no longer depends on an exact English string.
- **G5 A provider error is not presented as a handoff:** classify, plan and next step retry once, the semantic review degrades to the deterministic check, and a remaining failure shows a localized holding message that promises no human follow-up (the `fail_safe` record for staff stays).
- **G6 Safety and measurement:** a per-company kill switch for memory, multi-turn English and Malay scenario tests on the real graph, and a staging replay script.

## Non-goals

- The escalation ladder, its thresholds, the `angry` wording, preserving route reasons, and what staff receive on an escalation (PR B).
- Giving Jev history (the Jev classifier keeps its single-message state and redaction).
- Changing the 24-hour mute that an escalation applies, the Telegram notification content, the approval or booking flows, or the staff assistant.
- Writing the company's knowledge-base content (how to buy a SIM, which plan for whom, follow-up expectations): that is the owner's content, and the bot can only state what is in the knowledge base.
- Populating or using `Context.summary` (dead today).

## Anti-goals

- The bot acting on, or revealing, instructions found in earlier messages: they stay untrusted data.
- Invented facts: history must not become a way to state figures nobody verified; grounding sources are widened only to messages already verified or written by the customer.
- A handoff reply that is silently dropped: delivery of the handoff message must not depend on its wording.
- Malay that is rude, mirrors the customer's profanity or misspellings, or sounds unprofessional.
- A noticeable rise in latency or cost per turn.

## Constraints, from the code

- **History:** `Context` has no `recent` outside `BookingContext`; `_recent_block`/`_RECENT_RULE` appear only when `ctx.booking is not None`; `RECENT_MESSAGES = 6`, `RECENT_CHARS = 300` (`csa/contracts/state.py`); the SQL `db/sql/turn/booking_recent_messages.sql` returns both directions, redacted bodies only, oldest first, excluding the current trace. `compose` receives only facts, failures, strategy and the latest message.
- **Grounding:** `verify` runs `ungrounded` (numbers and links must appear in the message or the facts) and a model `review`; both use sources of the message and facts only. A reply that repeats a figure from an earlier turn would fail unless the sources widen.
- **Handoff by exact text:** `csa/turn/deps.py` ~538 (`verify` skips checks when `draft.text == HANDOFF_TEXT`) and ~681 (`emit` sets `handoff=` only when `message.body == HANDOFF_TEXT`). `automation_allowed` then lets the message through even though the escalation agent has muted the conversation; a different text would be dropped silently with no error.
- **Languages:** `signals.answers["language"]` (en, ms, zh, ta, other) is the only language signal; Telegram and WhatsApp envelopes carry `und`.
- **Policy:** `Policy` (`csa/graph/policy/gate.py`) is `extra="forbid"`: a field must ship in the model before any stored value; stored in `clients.config.policy`, read per turn.
- **Tests:** the golden harness (`tests/test_golden.py`, `tests/golden/`) runs the real worker, graph and agents on a real database with a cassette model, multi-turn; it has no Malay case and its `Turn` has no language field.
- Privacy: only redacted bodies; the repo is public, so scenarios and fixtures are synthetic (modelled on, not copied from, the owner's transcript).

## Approaches considered

**A (chosen by the owner): memory first, escalation later.** PR A (this document): memory for every company, the no-repeat and register rules, the handoff and fallback wording, provider-error handling. PR B: the escalation ladder and a staff context note.
**B: prompt-only fixes** (register, no-repeat, handoff text): cheapest, but the no-repeat instruction has nothing to work from without history.
**C: a server-side conversation state machine** (browsing, recommending, buying, handoff pending): most robust and largest; not justified before memory exists.

## Design (PR A)

### 1. Conversation memory

- New `Context.recent: list[RecentMessage]` (same model, max 20). `BookingContext.recent` stays as it is for the booking planner; when both exist the prompts show ONE block, taken from `Context.recent`.
- New read in `load_context`: the same SQL (renamed or reused), run for every channel including web, under its own 1.0 second budget; any error or timeout leaves `recent` empty and logs `turn_context_degraded` (a failed read never fails the turn).
- Window and caps (owner's decision): the last 20 messages (both directions), each capped at 200 characters, so at most 4000 characters of history. This replaces `RECENT_MESSAGES = 6` and `RECENT_CHARS = 300` for the generalised block; the booking planner's own `BookingContext.recent` keeps its current constants. A long outbound message (a plan list) is cut at 200 characters, which still shows what was listed and what was asked last only if the cut keeps the end: keep the first 120 and the last 80 characters of a longer message **(call)**.
- Prompts: `_recent_block` and the rule read `ctx.recent` (falling back to `ctx.booking.recent`). Classify and plan get the block with the generalised rule ("a short answer continues the topic in the earlier messages"); **compose gets the block for the first time**, after the facts. Messages stay escaped (`<`, `>`) and untrusted.
- Grounding widens in two different ways **(call)**. The deterministic check (`ungrounded`, `_sources` in `csa/decide/llm.py`) accepts numbers and links that appear in the recent messages of either side (the customer's own words, and earlier outbound messages). The semantic `review` (which judges business claims against facts only) is given the recent OUTBOUND messages as "already told to this customer" and accepts a claim they support, so repeating "RM50" from the previous reply passes both checks. Customer messages never support a business claim in `review`: "you said the refund is RM200" does not make a refund of RM200 true. Outbound messages are safe to trust because each was grounded by this same check when sent, written or approved by staff, or built from booking facts; the old fallback line carries no figures.
- Kill switch: `Policy.conversation_memory: bool = True`. When false, `Context.recent` is empty. The model field ships in this PR; setting it is an ops step (a runbook SQL like `force_decide_mode`), with no console control **(call)**.

### 2. No repetition

- Compose rule: do not repeat what an earlier assistant message already said; acknowledge new information; if the customer asks the same thing again, answer shorter or differently.
- `Context.flags["handoff_recent"]`: true when an `escalations` row exists for this conversation in the last 24 hours **whose reason is a real handoff** (a small new read; the `escalations` table already exists). Rows with reason `fail_safe:*` and `approval_expired` do NOT count: they notify and mute no one, so the composer must not be told "a team member was already notified" after them **(call)**. When set, the composer is told a team member was already notified and must not say so again unless the customer asks for a person.
- A real handoff mutes the bot for 24 hours (`ai_muted_until`, `owned_by='human_support'`), so the flag only matters when the conversation can be answered again: after staff press "AI resumed", in shadow mode (no mute), or when the mute expired. In the owner's transcript the bot kept answering after handoffs because the owner handed the conversation back to the assistant each time (the "AI resumed" path), so this flag is exactly the case that happened; the golden scenario sets up a released conversation.
- Lists **(call, from the transcript)**: when offering options (plans, products), at most three at a time and one short question to narrow, never the whole catalogue in one message.

### 3. Register

- A per-language register block appended to the compose system prompt, Malay first: everyday Malaysian Malay, friendly and professional; "saya/kami"; prefer "boleh", "tak", "dah", "nak", "sikit", "tolong", "sebab"; avoid "anda", "sila", "pihak kami", "dimaklumkan", "kerana" where a natural phrase works; avoid Indonesian words ("menemukan" becomes "jumpa" or "cari"); particles ("lah", "kan", "ya") sparingly; write full words even when the customer abbreviates ("x", "sy", "nk"); never mirror profanity, answer it with one calm clause; match the customer's length. The block carries a 30-pair formal-to-casual glossary and 12 example replies, one per common situation (greeting, one option at a time, usage follow-up, price, in stock, out of stock, order status, missing detail, no fact, upset customer, thanks, goodbye), which the owner approves or rewrites; the owner's own replies, if supplied, are added to them. Casual, not street: no "bro", "weh", "lu/gua", heavy "lah" or regional dialect. Five examples were judged too few (owner discussion 2026-11-10); a calibration pass on the staging replay follows the first rollout.
- Precedence **(call)**: `brand_tone` is free text each company sets. An ordinary tone ("professional", "friendly", "polite", "helpful") does NOT switch the register back to formal: the tone adjusts warmth and word choice inside the colloquial register. Only an explicit request for formal language ("formal Malay", "bahasa baku", "no slang") switches it off. The register block comes after the tone in the prompt and states this rule. The register never overrides the support guide or the facts-only rule. The replay reports each test tenant's tone beside its replies, so an over-formal result can be traced to the tone.
- English keeps today's guide; zh and ta unchanged (no register block) **(call)**. `other` and mixed Malay-English: "reply in the customer's own mix".

### 4. Handoff and fallback wording

- `_compose` stops returning `HANDOFF_TEXT` for a handoff. It composes a short handoff reply (at most two sentences) in the customer's language and register, using the recent messages and `handoff_recent`: acknowledge (one clause if the customer is upset), say a team member will reply in this chat, refer to what the customer asked only in general terms taken from the conversation, and promise no time, refund or outcome unless a knowledge-base fact states it.
- A light check replaces the grounding skip: no digits or links that are not in the recent messages or facts, at most 300 characters. If generation fails the check, or the model errors, use a **localized template** (en, ms, zh, ta; the Malay wording approved by the owner). A handoff never goes to `fail_safe` because of its wording.
- `Draft.handoff: bool`, set in `_compose` when the handoff is required or the plan escalates. `verify` skips grounding and review on `draft.handoff` (running the light check), and `emit` passes `handoff=` from the draft (still false on web), not from string equality. A test proves a handoff draft with a different text is delivered with `send_authority='handoff'` while the conversation is muted.
- Gate and hold. The escalation agent has already muted the conversation by the time the gate runs, and `hold` calls `automation_allowed` without `handoff=True`, so a handoff reply the gate sends to approval is dropped silently today (it needs a policy that sets `suggest`, `always_review_if: requires_human` or an intent threshold; the defaults do not). Two changes: `hold` passes `handoff=` from the draft, so a held handoff reply is kept for staff instead of vanishing; and the gate treats any required handoff as a `system_reply` (send), as it already does for `service_off` and `escalate` plans, except in shadow mode (which still sends nothing). Recommended, owner to confirm: with a model-written text the second change widens what a `suggest`-mode company sends without approval to a two-sentence acknowledgement that passed the light check or is a fixed template (decision 5).
- `FALLBACK_TEXT` (fail-safe and approval outcomes) becomes the localized template for the known language; when the language is unknown (a classify failure) a small deterministic Malay or English detector picks it **(call)**.
- `SANDBOX_SCOPE_TEXT` keeps its English text (staff-facing preview).

### 5. Provider errors

- The model-call nodes classify, plan and next step each retry once after a short backoff on a model error (and use the fallback client if one is configured) before the node fails **(call)**. The semantic review (`review`) degrades to the deterministic check for that turn when it errors, as with `semantic_verify=false` today **(call)**: it is advisory and the deterministic grounding check has already run.
- If a node still fails, the turn ends in `fail_safe` as today, so `fail_safe` still writes its `escalations` row (a record for staff; it notifies and mutes no one), but the customer gets a localized holding message that promises nothing a person will do: in Malay along the lines of "Maaf, ada gangguan sekejap. Boleh hantar semula mesej tu sekejap lagi?" (exact wording is decision 2). Today's English line says "A team member will follow up", which nothing keeps. The new `fail_safe` rows do not feed `handoff_recent` (section 2).

### 6. Measurement and rollout

- Golden scenarios (real graph, scripted model): a synthetic Malay conversation modelled on the owner's transcript, English twin included, asserting that `compose_sees` carries the recent block, the second handoff in a conversation is not a repeat of the first, a handoff with a localized or generated text is delivered, and a provider error on classify retries and shows a localized message. The golden `Turn` gains an optional language and the booking-free tenant needs no booking seed.
- Prompt tests in `tests/test_llm_provider.py` pin the new blocks and rules, and that every other prompt is unchanged when `conversation_memory` is false.
- `scripts/customer_bot_replay.py`: replays a synthetic conversation through the real graph and real model on a staging test tenant (never real customer data), prints the replies and counts formal markers (`anda`, `sila`, `pihak kami`, `menemukan`) and repeated sentences; the owner reads the replies. Not a CI gate.
- Rollout: no migration (a JSON policy field and one new read of an existing table). Staging first; the owner replays and reads; production (the interim Lightsail deployment) is the owner's later decision.

## PR B (designed separately after PR A)

The escalation ladder: split "must escalate now" (asked for a human, abusive, a legal word, a guard) from "could not answer" (low confidence, nothing found); up to two contextual clarifications that each ask something new, then a partial answer plus one targeted question, then a handoff; the angry label gets threshold wording and treats colloquial exclamations as frustration; route reasons survive into the escalation record; staff receive a context note.

## Decisions for the owner

1. Malay register: approve or rewrite the glossary and the 12 example replies (drafts are in the plan), optionally add your own replies; the drafts address the customer with no pronoun (not "awak", not "kak/abang").
2. The localized handoff templates' Malay wording.
3. The three-option cap on lists.
4. Knowledge-base content (the owner's, per company; the SIM sign-up facts are specific to the Team Jo tenant): "how to buy / sign up", "which plan suits whom", and what a customer can expect after a handoff (who replies, where, when).
5. Handoff reply and approval (owner chose a, 2026-11-10): a required handoff always sends its acknowledgement without staff approval, even for a company in `suggest` mode; shadow mode still sends nothing.

## Left to the builder

Names and file layout; whether the recent read reuses the booking SQL file or a renamed one; exact constants; the Malay detector; the retry backoff; where `Draft.handoff` lives.
