# Payments follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three follow-ups to Payments 1 (#117): a staff "Reject claim" action, a sign-up data note that mentions an email the customer gives, and Malay wording for every customer-facing fact that can share a reply with a payment answer.

**Architecture:** Three independent pull requests. A is a backend action plus a console form and reuses existing columns and the audit log (no migration). B changes one fact text, conditional on the email ask. C adds an optional Malay text to the `Fact` contract and writes Malay copy for the write facts, so a Malay payment turn that also commits a write stops mixing languages.

**Tech Stack:** Python 3 / FastAPI / psycopg (`csa/payments`, `csa/agents`, `csa/turn`), Next.js console (`CSA_WebConsole/csa-console/web`), pytest, node test files.

**Spec:** `docs/superpowers/specs/2026-11-11-payments-design.md` (section 11 is binding), the code survey `docs/superpowers/specs/2026-11-15-payments-followups-survey.md` (file:line facts for every task; read the part for your task), and the owner decisions below.

## Decisions (owner chat 2026-11-14/15, and rulings by the assistant that the owner can overrule)

- Owner: build the Reject-claim button; add the email mention to the sign-up note; fix the Malay language mix; confirm design section 11 (done); the QR image is its own design round after these (it needs outbound media, a tenant image store and delivery changes; see the survey, section D).
- Ruling R1: Reject needs **no migration**. A claim sets `claim_note`/`claimed_at` and status `proof_submitted`; a reject sets the status back to `issued` and keeps the claim fields. An `issued` or `overdue` invoice that still has `claimed_at` is therefore a rejected claim; the drawer labels it so by status. The trail (who, when, why) is the `payments.invoice.claim_rejected` audit row, the action name the RUNBOOK SQL already uses. *Cost if wrong:* a later migration adds `claim_rejected_*` columns to show "rejected by X on D" in the drawer.
- Ruling R2: Rejecting a claim on a long-past-due invoice lets the unpaid pause run at the next check; there is no grace mechanism, and the dialog says so. *Cost if wrong:* a grace field later.
- Ruling R3: No automatic message to the customer on reject; the dialog tells staff to reply from the Inbox (as the RUNBOOK does today). *Cost if wrong:* one notice using `csa/payments/notices.py` and new copy.
- Ruling R4 (B): survey wording option 2: the base note is unchanged and an email clause is appended only on a turn that asks for the email (WhatsApp, payments on, contact has no address). Telegram text and every Telegram golden stay unchanged. No receipts are promised (none exist). *Cost if wrong:* wording only; the owner approves the exact text in the PR.
- Ruling R5 (C): survey option 2: `Fact` gains an optional `text_ms`; `_facts_draft` uses it on Malay turns; the composer's input is unchanged (so recorded `compose_sees` goldens do not move). *Cost if wrong:* a wider change (survey option 1).

## Global Constraints

- The repository is PUBLIC: synthetic data only, no real hostnames, ids, tokens or addresses.
- No migration in any of these three pull requests; `EXPECTED_SCHEMA_VERSION` stays as on `main`.
- Three pull requests from `origin/main`, each on its own branch, each merged by the owner after green CI: **A** (Tasks 1 to 2) branch `feat/payments-reject-claim`, **B** (Task 3) `feat/payments-signup-email-note`, **C** (Tasks 4 to 9) `feat/malay-write-facts`. Merge commits only; a hook blocks rebase and force-push.
- Customer copy: written to the customer in the second person, plain, no "Please note". Malay copy is casual ("awak", "dah", "kat", "bagitahu"); never "anda", "sila", "pihak kami" (the Malay payment goldens exclude them). Facts that can be sent word for word are written to the customer, never to the model. The owner reads the Malay.
- Console copy follows `docs/CONSOLE_VOICE.md` and uses the shared building blocks in `components/console/ui.tsx`. The admin and portal payments pages share one component; change it once.
- After any change to a console route regenerate `docs/console_openapi.json` (`python -m scripts.export_console_openapi`); `docs/CONSOLE_API.md` is hand-written and must be updated.
- Tests one file at a time through `bash /tmp/claude-1000/-home-amsyrzmi-CSAV2/7d09c8ff-ee80-4525-b21a-195503f98d9c/scratchpad/ci.sh /home/amsyrzmi/CSAV2/.venv/bin/python -m pytest <file> -q`; browser files need more than 3000 MB free (`free -m`), otherwise leave them to CI; node test files one at a time from `CSA_WebConsole/csa-console/web` (node is `/tmp/fourstacks-toolbin/node`, not on PATH; the web folder needs a `node_modules` symlink to the main checkout's). `make lint` as ONE command redirected to a file with its exit code echoed in the same command.
- A PostToolUse formatter hook mass-reformats after the Edit tool: use Write or python3-heredoc replace scripts, check `git diff --stat`, run `.venv/bin/ruff format` on touched Python.
- Commit trailers on every commit: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_012Xj1GSXSjsM1gcTVMBiZJt`.
- Never run a staging rollout, touch production or the home server; the owner does those.

## Review Focus

- Reject on an invoice that is not `proof_submitted` (issued, overdue, paid, void, another company's, a replay of the same click): exactly one outcome per case and no state change; two staff clicking at once changes it once.
- A rejected invoice that is long past due: the next `payments_due` run marks it overdue and may pause the plan; one reminder (the newest owed step) goes, not a burst; a pending subscription with it can expire. The dialog says this before the click.
- A customer claims again after a reject: the claim path still works (`issued`/`overdue` to `proof_submitted`) and the drawer label follows the status.
- Malay payment turn with a booking, a ticket, an order draft and a sign-up request in the same message: no English sentence from the facts in the reply; Malay day and month names in the booking line; an English turn is unchanged.
- The optional email clause appears only when the email is asked (WhatsApp) and never on Telegram.

---

## Pull request A: Reject claim

### Task 1: Backend `reject_claim` (route, SQL, audit, jobs interplay)

**Files:**
- Create: `db/sql/payments/invoice_reject_claim.sql`
- Modify: `csa/payments/invoices.py` (new function beside `void_invoice`), `csa/payments/contracts.py` (new `RejectClaimIn`), `csa/payments/api.py` (route beside `void`), `docs/CONSOLE_API.md` (route table in the Payments section and the audit list), `docs/RUNBOOK.md` (the "Payments" claim sections: the button is the normal way, the SQL stays as the operator fallback), `docs/console_openapi.json` (regenerate)
- Test: `tests/test_payment_invoices.py`, `tests/test_console_payments.py`, `tests/test_jobs_payments.py`, `tests/test_jobs_payment_reminders.py`, `tests/test_jobs_subscriptions.py`, `tests/test_console_contract_snapshot.py`

**Interfaces:**
- Produces: `async def reject_claim(conn: Conn, client_id: UUID, invoice_id: str, *, actor_user_id: UUID, reason: str | None) -> InvoiceRecord` in `csa/payments/invoices.py`; `class RejectClaimIn(Contract)` with `reason: str | None` (stripped, 1 to 300 characters when given; an empty or whitespace string is `None`); route `POST /console/v1/payments/invoices/{invoice_id}/reject-claim` returning `InvoiceDetail`, guarded by `staff(...)` (platform admin, tenant admin, tenant staff; `read_only` gets 403) exactly like `void`; audit action `payments.invoice.claim_rejected` with details `{"reason": <cleaned>}` (or `{}` when none).
- Errors (`HTTPException` codes): 404 `invoice_not_found` (also another company's); 409 `invoice_paid`, 409 `invoice_void`; 409 `claim_not_open` for `issued`, `overdue`, `draft`, `cash_pending`; 422 `invalid_reason` for a reason that fails `_clean_text`.

- [ ] **Step 1: Write the failing tests.** `tests/test_payment_invoices.py`: `test_reject_claim_returns_a_claimed_invoice_to_issued_and_keeps_the_claim` (claim fields kept, audit row with the reason, actor recorded), `test_reject_claim_refuses_every_status_but_proof_submitted` parametrized over issued, overdue, draft, cash_pending (409 `claim_not_open`), paid (`invoice_paid`), void (`invoice_void`) with the row unchanged, `test_reject_claim_of_another_company_is_not_found`, `test_two_rejects_change_the_invoice_once` (second call 409 `claim_not_open`, one audit row), `test_a_second_claim_after_a_reject_works` (back to `proof_submitted`), and the lock-order no-deadlock case modelled on the existing claim/void concurrency test (~line 777). `tests/test_console_payments.py`: the route for tenant staff and tenant admin returns the detail with status `issued`; `read_only` 403; the package gate; audit row order like the void tests (~line 548 to 582). `tests/test_jobs_payments.py`: a rejected invoice past `due_at` is marked `overdue` by the next `payments_due` run. `tests/test_jobs_payment_reminders.py`: a rejected overdue invoice gets exactly one reminder, the newest owed step, older steps recorded `skipped`. `tests/test_jobs_subscriptions.py`: a pending subscription whose invoice was rejected is no longer protected from the pending expiry.
- [ ] **Step 2: Run each test file to see them fail.**
- [ ] **Step 3: Implement.** `invoice_reject_claim.sql`: `UPDATE public.invoices SET status = 'issued' WHERE client_id = %(client_id)s AND id = %(invoice_id)s AND status = 'proof_submitted' RETURNING id`. `reject_claim` mirrors `void_invoice` (clean the reason, `_lock_invoice` with the subscription-then-invoice order, status checks in the order paid, void, not `proof_submitted`, the update, the audit row through `console/audit.sql`, return `_record(final)`). The route mirrors `void`. Update `docs/CONSOLE_API.md` and `docs/RUNBOOK.md`; regenerate the OpenAPI snapshot.
- [ ] **Step 4: Run the six test files, one at a time, and `make lint`.** Expected: all pass.
- [ ] **Step 5: Commit** `feat(payments): staff can reject a customer's payment claim`.

### Task 2: Console form, drawer label and tests

**Files:**
- Modify: `CSA_WebConsole/csa-console/web/lib/payments-api.ts` (`rejectClaim()` beside `voidInvoice`, body type beside `VoidIn`, `claim_not_open` in `PAYMENT_ERRORS`), `lib/payments-view.ts` (`canRejectClaim(status)` true only for `proof_submitted`; body helper beside `voidBody`), `components/console/PaymentsWorkspace.tsx` (a third drawer form `"reject"` beside `"pay"` and `"void"`, a `RejectClaimForm` modelled on `VoidForm`, the claim section label by status), `docs/CUSTOMER_BOT_RETEST.md` (a retest row)
- Test: `tests/payments-api.test.mjs`, `tests/payments-view.test.mjs`, `tests/test_payments_browser.py`

**Interfaces:**
- Consumes: Task 1's route and error codes.
- Produces: the drawer shows a "Reject claim" action only for `proof_submitted`, for roles that may void; the form has an optional reason (max 300) and a confirm that states the consequence: "The invoice goes back to unpaid. Reminders and the unpaid pause run again, and if it is long overdue the plan can pause at the next check. This does not message the customer: reply from the Inbox." (plain words per `CONSOLE_VOICE.md`; adjust phrasing to match the page's voice). The claim section heading is "What the customer said" while `proof_submitted` and "Claim rejected: what the customer said" for `issued` or `overdue` with a claim time.

- [ ] **Step 1: Write the failing tests.** `payments-api.test.mjs`: `rejectClaim` posts to the right path with `{reason}` and maps `claim_not_open`, `invoice_paid`, `invoice_void`, `invoice_not_found` through `PAYMENT_ERRORS`. `payments-view.test.mjs`: `canRejectClaim` is true only for `proof_submitted`; the claim-section label for each status. `test_payments_browser.py`: reject flow on the attention drawer for staff (the invoice leaves the proof-submitted group and the drawer shows the rejected label), `read_only` does not see the action, the portal page shows the same action.
- [ ] **Step 2: Run the two node files and the browser file (if memory allows) to see them fail.**
- [ ] **Step 3: Implement** as described. Grep every browser stub of the payments calls before changing signatures (PR CI runs only changed test files).
- [ ] **Step 4: Run the three files, then `make lint`.** Expected: pass (the browser file runs in CI if the host is short of memory).
- [ ] **Step 5: Commit** `feat(console): Reject claim on the Payments drawer`.

---

## Pull request B: the sign-up note mentions a given email

### Task 3: Conditional email clause in the sign-up data note

**Files:**
- Modify: `csa/agents/staged/subscription_facts.py` (new constant `SIGNUP_EMAIL_NOTE` and the `signup_fact` text for `data_note`), `csa/agents/staged/signup.py` (`_ask` receives the already computed `asks_email` flag, ~lines 128 and 308), `docs/CUSTOMER_BOT_RETEST.md`, `docs/PROGRESS.md` (close item 3)
- Test: `tests/test_subscription_facts.py`, `tests/test_staged_agents.py`

**Interfaces:**
- Produces: `SIGNUP_EMAIL_NOTE = " If you give us your email, we use it for payment reminders and booking messages."`; the `data_note` text is `SIGNUP_DATA_NOTE + SIGNUP_EMAIL_NOTE` when the turn asks for the email and `SIGNUP_DATA_NOTE` otherwise; the fact kind and id (`signup_data_note_N`) are unchanged (about ten assertions pin them).

- [ ] **Step 1: Write the failing tests:** the WhatsApp email-ask cases in `tests/test_staged_agents.py` (~lines 1798 to 1948) assert the clause is in the `data_note` text; the Telegram cases and the no-email-ask WhatsApp cases assert it is absent; `tests/test_subscription_facts.py` (~line 275) covers both texts.
- [ ] **Step 2: Run both files to see them fail.**
- [ ] **Step 3: Implement** by threading `asks_email` into `_ask` and `signup_fact`.
- [ ] **Step 4: Run both files, `tests/test_customer_bot_replay.py` and the golden files that embed the sentence (`tests/golden/cases/cs_signup_*_en.json` are Telegram and must not change), then `make lint`.**
- [ ] **Step 5: Commit** `feat(signup): the data note says what an email is used for`. The PR description quotes the exact English text for the owner's approval and notes that the Malay version arrives with Task 6.

---

## Pull request C: Malay write facts

### Task 4: `Fact.text_ms` and the facts-only reply

**Files:**
- Modify: `csa/contracts/facts.py` (`Fact`), `csa/turn/deps.py` (`_facts_draft` at ~226 to 250 and its two callers: the payment reply ~687 and the booking fallback ~709)
- Test: `tests/test_payment_reply.py`, `tests/test_contracts_facts.py` (create if there is no contract test file for `Fact`)

**Interfaces:**
- Produces: `Fact.text_ms: str | None = None` (additive; `min_length=1` when given); `_facts_draft(facts: Sequence[Fact], language: str) -> ...` uses `fact.text_ms` when `language` is Malay (`ms`) and `text_ms` is set, else `fact.text`. The composer path (`compose`) never reads `text_ms`.

- [ ] **Step 1: Write the failing tests:** `test_facts_draft_uses_the_malay_text_on_a_malay_turn`, `test_facts_draft_falls_back_to_english_without_a_malay_text`, `test_english_turns_ignore_text_ms`, `test_the_composer_input_does_not_carry_text_ms` (the facts sent to the model equal today's); a payment turn that commits a write (reuse the helpers of `test_payment_reply.py` ~lines 203 and 428) in Malay returns the Malay write text first, then the payment facts.
- [ ] **Step 2 to 5:** run red, implement, run `tests/test_payment_reply.py`, `tests/test_staged_agents.py`, `tests/test_turn_deps.py` (or the file that covers `compose`; grep for `_facts_draft`), `make lint`, commit `feat(turn): facts can carry a Malay text for turns where they go out word for word`.

### Task 5 to 8: Malay text on the write facts

One task per area, each with the same shape: write the Malay text next to the English at the place the fact is built (`text_ms=...`), a test per fact that the Malay text is set and contains no English sentence of that fact, and a mixed-turn test through `compose`. Malay copy follows the Global Constraints; the English text and the composer input stay untouched.

- **Task 5, sign-up and subscription requests:** `csa/agents/staged/subscription_facts.py` (`request_fact` texts ~127 to 163, `signup_fact` texts ~219 to 256: readback, phone_invalid, email_ask, email_invalid, filed, exists, has_plan, closed, which, try_later, `data_note` including the email clause from Task 3). Tests: `tests/test_subscription_facts.py`, `tests/test_staged_agents.py`.
- **Task 6, tickets:** `csa/agents/tickets/agent.py` (create, add_note, cancel question, cancelled, reopen; the texts at ~199, 231, 277, 295, 319, 347, 362, 378). Tests: the ticket agent unit file (grep `tests/` for the ticket facts) and `tests/golden/cases/ticket_*` must not change.
- **Task 7, orders (Telegram only):** `csa/agents/orders/agent.py` (draft ~372, confirm ~521, already/cancelled ~473, 544, 590). Test: `tests/test_order_agent.py`.
- **Task 8, bookings:** `csa/agents/staged/booking_facts.py` (`booking_line`, `refusal`, `withdrawn`, `cancel_question`, `saved_line`, `no_email`, the limits) and a Malay slot formatter beside `format_slot` in `csa/bookings/bot.py` (Malay day names are new; the Malay month names exist in `csa/payments/copy.py` ~lines 37 to 46: reuse them, do not copy). The booking fallback in `deps.py` (~709) then uses it too. Tests: `tests/test_booking_agent.py`, `tests/test_bookings_bot.py`.

Each task commits as `feat(<area>): Malay text for the facts that can go out word for word` after `make lint`.

### Task 9: Mixed-turn goldens and the end-to-end check

**Files:**
- Create: `tests/golden/cases/cs_pay_with_ticket_ms.json`, `tests/golden/cases/cs_pay_with_signup_ms.json` (follow the shape of `cs_pay_instructions_ms.json`, including `reply_excludes` for " anda ", "sila", "pihak kami" and the English fact sentences)
- Modify: `docs/CUSTOMER_BOT_RETEST.md` (a Malay mixed-turn row), `docs/PROGRESS.md` (close the follow-up)
- Test: the golden runner file (`tests/test_golden_cases.py` or the file that loads `tests/golden/cases`) one case file at a time

- [ ] Write the two goldens; the runner cannot seed bookings, so cover tickets and sign-up only and cover bookings in the unit test of Task 8. Run the golden runner for the new cases and the existing `cs_pay_*` and `cs_signup_*` cases, then `make lint`. Commit `test(golden): Malay payment turns that also commit a write`.
- [ ] The replay script calls the model directly and cannot cover this path: the owner reads the Malay strings in the PR before merge.

## After the three pull requests

The QR image gets its own design round (brainstorm, design doc, plan): its migration, a `payments` store with its S3 prefix and IAM, an upload route modelled on HR documents, and outbound media in delivery for Telegram `sendPhoto` and WhatsApp media. Provider media limits are not in the repo and are UNVERIFIED until checked against the providers' current documentation.
