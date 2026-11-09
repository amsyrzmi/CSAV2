# Assistant engine: plans, batch email and Jev as the decision layer (PR 2 of 3)

Status: design for the owner's approval, 2026-11-09 (revised after the builder check). Programme: PR 1 prompts and persona (#85, #86), **PR 2 this**, PR 3 surfaces (Overview as the assistant, interactive dock, alive avatar). Items marked **(call)** are decisions I took; the owner can overrule any of them.

## Intent

The workspace assistant should do what staff do, faster than staff do it. Today a turn gives one action, one card and one confirm: email has one To address (plus cc and bcc), and bookings, people changes, training requests and inbox decisions are one item each. Staff who need ten of something make ten requests and ten confirms. This PR lets one prompt produce one card that holds several steps, confirmed once and run with a result per step. Email is the first and deepest use: several people in one prompt, drafts written from real facts (subscription, knowledge base) where the message needs them. The same PR builds the one shared Jev setup the rest of the system will use for quick typed decisions.

## Goals

- A `plan` proposal: an ordered list of steps, each a kind from a named list, previewed on one card, confirmed once.
- Batch email: several recipients from one prompt; the same message to all, or one personalised draft per recipient, decided per request.
- Several bookings from one prompt.
- Email drafts may use facts from Subscriptions and the Knowledge base, only what the user's role may read.
- One shared Jev client and decision interface, used in `shadow` for: the email decisions, the staff assistant's routing (which facts reach the planner), the customer-message classifier (already built, not wired), and an injection check on inbound email before a reply is drafted. Nothing changes behaviour until a switch is flipped per area (owner: "leverage Jev appropriately for the entire system").
- Per-step results, size caps, and the existing safety rules unchanged: session-bound, single use, expiry, version-bound, idempotent, re-gated at confirm.

## Non-goals

- Dependent steps (a step that uses an earlier step's result: "add Hana, email her, book her Monday"): follow-up.
- Bulk inbox approvals (**call**, a cut from the idea list): the planner sees held replies only for the open conversation, so "approve everything held" needs a new cross-inbox fact, trigger words and a cap, and edges towards arbitrary audiences. Later.
- A different planner model. A different drafter model only as an opt-in setting (below).
- Arbitrary audience queries. Only the groups listed below; anything else gets a clarification.
- Marketing mail (**call**): batch mail to customers is for service messages to people who already have a subscription (renewal, billing, plan changes, answers from the knowledge base).
- Overview and dock redesign, avatar, removing the sandbox chat (PR 3). PR 2 only teaches the existing card to show a plan.
- Scheduled sends, recurring bookings, undo.

## Anti-goals

- A plan that sends mail or changes records the user did not see on the card.
- Jev or the planner ever creating a send card. The send rule stays the owner's server rule (below).
- A figure, link or date in a draft that is not in the supplied facts, and a claim about a plan or policy the facts do not support reaching a recipient unreviewed.
- Recipients seeing each other's addresses by accident.
- Customer or employee names or addresses on a tenant-readable row.
- Jev becoming a required dependency: with no key or on an error everything runs on the LLM and the server rules alone.

## Constraints, from the code

- **Send rule.** A send card comes only from `rules.asks_to_send` on the user's own words (`_asked_to_send`); the model cannot create one. This is the owner's server-enforced rule. The decision layer may only **downgrade** send to draft, never upgrade; Jev's "is this a send request" answer is logged in shadow against the rule and never decides.
- **Cards and turns hold counts only.** `console_proposals` and `console_workspace_turns` are readable by the whole tenant, so email cards store no address, subject or body (migration 0485). A plan card holds step titles and counts; drafts are opened by their owner from their own draft rows, as the email card does today with its single Draft field. Recipients and the facts each draft used live on the draft side, readable only by the draft's owner (new draft columns, builder to place). "Not included" lines give counts and reasons, never names.
- **Foreign key.** `subscription_request_decisions.proposal_id` references `console_proposals(id)`, and a plan step has no row of its own, so `subscription_action` is not an allowed step.
- **Grounding.** `csa/decide/grounding.py` finds numbers and URLs only and is not used by the email assistant today; its `[STAFF: ...]` placeholders come only from the model's prompt. See the email flow for what the design does about invented claims.
- **Send quota.** `SEND_LIMIT_PER_HOUR = 20` per mailbox; each Gmail send may take up to 25 s (`SEND_DEADLINE_SECONDS`); the claim lease is fixed at 2 minutes (`proposal_get.sql`).
- **Placeholders block sending.** `send_draft` refuses a draft that still holds a `[STAFF: ...]` placeholder (`draft_needs_staff_input`).
- **Planner calls** take 300 to 1100 ms; a batch must not multiply that, so drafts and fact reads run in parallel (limit 4) and the planner is called once.
- **Jev is not live anywhere**: `JevClient` is created only in `csa/evals/decisions.py`; the customer path accepts a `JevProvider` but nothing passes one. The key (`TYPESAFE_API_KEY`) exists (owner), goes in as a staging secret file like the others, and only redacted text may be sent.
- **Customer path cascade is per tenant** (`policy.decide_mode`, one confidence value); the staff assistant has no per-tenant policy, so its switches are global settings with a confidence floor per question.
- A migration is needed: `console_proposals_kind_check` and `console_workspace_turns_action_check` must allow the new kinds (as 0531 did for `booking_action`), plus the new draft columns and the staff-assistant decision log. It stops gateway, frontend, worker and delivery during rollout.
- The repo is public: no real names or addresses in code, tests, fixtures or docs.

## Approaches considered

**Engine shape.** A: one generic `plan` proposal holding steps of existing kinds (chosen; owner: "I would want A", read as A with dependent bundles later). B: multi-item fields on each existing proposal: quicker per feature, but three list-on-a-card shapes, no mixed plans, three renderings in PR 3. C: loop over several confirmed turns: one tap per step, fails the goal.

**Decision layer.** LLM only: free, works today, one model deciding and writing. Jev only: answers typed questions with probabilities and never writes, suits decisions, not live today. Both behind one interface (chosen; owner: "I'll follow your recommended flow", key available): the LLM decider is default and the fallback; Jev runs in `shadow` (both answer, disagreements logged) and `cascade` (Jev decides at or above the question's floor, else the LLM). **Jev live from day one vs the data deciding per area is an open question to the owner; until answered the design assumes shadow first.**

**Model.** Keep the planner and drafter model: this week's failures were prompt fragility, not capability, and a larger model adds latency to every call of a batch. `ASSISTANT_DRAFT_MODEL` (default: the same model) selects the drafter for assistant batch drafting only, through its own client instance; the Email tab's drafts are unchanged (**call**).

## Design

### The plan proposal

- New `ProposalKind` `plan` and a `PlanExecutor` registered beside the others, holding the registry's executors by kind. Allowed step kinds, a named list (**call**): `email_send`, `email_export`, `booking_action`, `inbox_decision`, `training_submit`, `hr_person`; never `plan` or `subscription_action`. The builder checks each for foreign keys to `console_proposals`.
- Payload `{"steps": [{"kind", "payload", "version"}]}`, 1 to 20 steps. `card()` runs each step's `card()` (its gate, its record as the user), stores each step's version, and returns a plan version that is the hash of the **stored** step versions. The card shows a summary and one row per step (title and counts, no addresses). Only the assistant creates a plan: `PlanExecutor` refuses origin `tab`, and the direct create route rejects kind `plan`.
- **Confirm is per step (call).** `execute()` re-checks each step on its own: re-runs its gate and compares its current version with the stored one. A step that drifted (a draft edited, a slot taken, a held reply decided by a colleague) is skipped and reported "changed since you reviewed it", never failing the whole plan. A plan goes stale as a whole only when every step drifted.
- **Placeholders (call).** An email step whose draft still has a `[STAFF: ...]` placeholder is made an `email_export` step at plan time (saved to Gmail drafts, not sent), and the card says so.
- Step `i` runs under proposal id `uuid5(plan_id, "step:i")`, so each step is idempotent and an interrupted run replays finished steps. A failing step is recorded and the rest still run.
- **Time (call).** The plan runs inside the confirm request with concurrency 4 and a plan deadline of 90 seconds, under the 2-minute lease. Steps not started by the deadline are recorded `not_run` and are not claimed or lost. Receipt: `{"steps": [{"index", "kind", "status": "done"|"failed"|"skipped"|"not_run", "code"?, "receipt"?}]}`. If every step failed with the same refusal (for example the package was switched off) `execute` raises it so the registry releases the claim.
- **Retry (call).** The card of an executed plan with failed or `not_run` steps offers "Retry these", through an assistant-origin route owned by the session (`POST /workspace/proposals/{id}/retry`) that prepares a new plan from those steps' payloads. A tab cannot create or retry a plan, so no assistant-only check is dropped.
- Planner output: a `plan` action with `steps` (existing mutation actions from the allowed list) and an `email_batch` action the server expands into steps. One short, tested prompt section; the **full** smoke runs after every prompt change (the lesson of #86).
- Unresolved steps (ambiguous person, closed slot) are left off the card and counted under "Not included" with the reason; the rest can proceed.
- Caps: 20 steps per plan; 10 distinct non-email steps; email batches up to the mailbox's remaining hourly sends.

### Email flow

1. The planner returns `email_batch`: recipients (named people, typed addresses, named customers, or one fixed group), subject and instructions, language, and the user's `send` wish as the planner reads it (advisory, see send rule).
2. The decision layer answers: personalised or shared; which sources are needed (subscription, knowledge base, none). Jev asks them as typed questions in one request (2 second timeout) at a floor per question; below the floor or on an error the LLM's answer is used. Low confidence on personalised versus shared defaults to personalised. The send decision stays with `asks_to_send`; the decider can only lower a send to a draft.
3. The server gathers facts with its existing checks: subscription facts for a customer recipient (plan, status, renewal date, price) only when the user's role may read Subscriptions and its package is active; knowledge base passages for the topic only when the Knowledge base package is active, through a read-only search that does not go through `KbAgent._query` (that records unmatched queries as knowledge gaps and would put staff instruction text on the admins' list). Facts are plain data with ids.
4. The drafter writes from the facts only, in parallel (limit 4). Checks: the existing number and link check against the facts, always; a model review pass for drafts that used subscription or knowledge base facts, reusing the customer path's verifier if it fits (**call**; this adds one parallel call per such draft). Anything unsupported becomes a `[STAFF: ...]` placeholder listed in `needs_facts_from_staff`. The wording that remains is for the user's review on the card, which opens each draft.
5. One plan card as above. Shared mode sends separate messages with the same body, so recipients never see each other. `email_team` (one email to the team, all in To) is unchanged and not part of the batch groups (**call**).
6. **Send quota (call).** The card checks the mailbox's remaining hourly sends. Steps beyond it become `email_export` steps (saved to Gmail drafts) and the card says "sending 12, saving 8 as drafts". Groups larger than the cap: the assistant says how many match, proposes the first 20 in a fixed order and answers "the rest" with the next 20.
- Recipients: employees (directory), typed addresses, and a new source, subscription customers that have an email. Fixed groups (**call**; the builder checks each against the data): active subscribers, subscribers renewing within N days.

### Jev, shared setup

- One `JevClient` created at startup when `TYPESAFE_API_KEY` is set and handed to both the customer path (its existing `jev` parameter) and the new staff decider. No key: no client, everything on the LLM, no error.
- `EmailDecider` / staff `RoutingDecider` interface with `LlmDecider` (default) and `JevDecider`. Settings: global `ASSISTANT_DECIDER` = `llm` (default) | `shadow` | `cascade`, per area (`email`, `routing`, `inbound_injection`), startup refuses a Jev mode without a key. Customer messages keep their per-tenant `policy.decide_mode`.
- Questions: routing as `noul` for each fact group (people, training, bookings, mail, team, manager, stock) plus language `choice` (en, ms, other); email as `choice` for mode and `noul` per source; inbound email as the existing `injection` question. Each question has its own confidence floor.
- Logging: typed answers, the LLM or rule answer beside them, and which differed; never message text. Only redacted text is sent to Jev (the redaction the customer path uses; staff text can contain names and addresses).
- Jev never extracts names, dates or times, never writes text, never decides permissions or a send.

### Frontend

`ProposalCard` renders a `plan`: step rows (title and counts), each email step opening its draft through the existing preview, a result badge per step after execution, and the retry action. `AssistantDock` needs no layout change; PR 3 reuses the card. Every browser test that stubs the console API is checked for any new call (lessons of #84 and #85).

## Left to the builder

Exact schema and column names; where the draft-side columns and the decision log go; each group's definition against the data; reuse of the customer verifier; concurrency tuning; Malay and English card text; the migration number (next free after 0541).

## Verification

- Plan executor: order, per-step receipt, idempotent replay, a failing step, all-fail raise, a drifted step skipped, deadline `not_run`, cap, kinds allowlist, tab cannot create or retry, no names on any card or turn row.
- Planner pins and new smoke cases (several-person email shared and personalised, bookings batch, English and Malay); the full smoke after every prompt change.
- Drafting: a draft with subscription facts states only supplied figures; a missing fact becomes a placeholder; a placeholder draft becomes an export step; quota split.
- Deciders: no key, Jev error, low confidence, shadow logging with no message text, the decider can never produce a send card.
- Measured, not promised: time from prompt to card for 1, 5 and 10 recipients on staging.
