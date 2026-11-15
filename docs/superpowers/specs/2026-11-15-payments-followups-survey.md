# Payments follow-ups survey (A reject claim, B sign-up data note, C Malay write facts, D QR image)

Surveyed main checkout at `2c411bc2` (origin/main), read only. Paths are repo-relative.

## Shared facts

- Migrations on main: highest file `db/migrations/0601_training_video_tables.sql`; 0590 and 0591 are payments; 0592-0599
  are unused (reserved for Payments 2 per `docs/superpowers/plans/2026-11-13-payments-1.md:15`); 0600-0609 is the
  training-video range (`db/migrations/README.md:60-74`). `EXPECTED_SCHEMA_VERSION = "0601"` (`apps/gateway/main.py:74`),
  checked as `max(applied) == EXPECTED` (`apps/gateway/main.py:130`). `scripts/migrate.py:137-150` applies every
  unrecorded file in filename order, so a gap file (e.g. 0592) would apply on a database already at 0601 and leave the
  max (and `EXPECTED_SCHEMA_VERSION`) at 0601, as 0513 did (`db/migrations/README.md:21-22`). A file at 0610+ would need a
  new README range and an `EXPECTED_SCHEMA_VERSION` bump. Using 0592-0599 takes Payments 2 numbers: owner/plan decision.
- Console contract snapshot is enforced: `tests/test_console_contract_snapshot.py:10-15` (regenerate with
  `python -m scripts.export_console_openapi`, writes `docs/console_openapi.json`, `scripts/export_console_openapi.py:20`).
  `docs/CONSOLE_API.md` is hand-written (Payments section starts at :1406; route table :1418-1427).
- Payments console routes: `csa/payments/api.py` (prefix `/console/v1/payments`, human session + `subscriptions`
  package gate, :86-91). `STAFF_ROLES` = platform admin, tenant admin, tenant staff (:44); `read_only` gets 403 everywhere.

## A. "Reject claim" button

**Status model.** `invoices.status` CHECK `invoices_status_chk` = draft, issued, proof_submitted, paid, overdue, void,
cash_pending (`db/migrations/0590_payments_core.sql:122-124`); Python mirror `InvoiceStatus`
(`csa/payments/contracts.py:25-33`, `OPEN_STATUSES` :33). Claim columns: `claim_note` (<=300, check :133) and
`claimed_at` (0590:97-99). There is no rejected_at/by/reason column and no claim history table.

**Claim path.** Bot: `csa/agents/payments/agent.py:196-229` calls `record_claim` (`csa/payments/invoices.py:560-590`):
locks subscription then invoice (`_lock_invoice` :154-168), only the contact's own `issued|overdue` invoice, writes
`db/sql/payments/invoice_claim.sql` (status `proof_submitted`, note, `claimed_at = now()`). No `payments` row, no audit
row, no subscription event. A second claim overwrites `claim_note`/`claimed_at` (same SQL).

**What a claim freezes (and a reject re-enables).**
- Overdue marking: only `status = 'issued'` (`db/sql/jobs/payments_due_overdue.sql:1-23`).
- Unpaid pause: only `overdue` invoices with `due_at < now - suspend_after_days`, once per invoice
  (`db/sql/jobs/payments_due_suspend_lock.sql:1-29`). Consequence: rejecting a long-past-due claim makes it `issued`,
  the next `payments_due` run (15 min) marks it `overdue` and the same/next run can pause the subscription at once.
  Decide whether that is wanted or whether reject gives grace (no grace mechanism exists).
- Reminders: `REMINDABLE = {"issued","overdue"}` (`csa/jobs/payment_reminders.py:86-88`); `reminders_due.sql:1-10`
  sends only the newest owed step and records older owed steps `skipped`, so after a reject one reminder goes at the next
  run (subject to quiet hours and one-per-contact-per-day), not a burst.
- Pending expiry: a pending subscription with a `proof_submitted` invoice is never expired
  (`db/sql/jobs/subscriptions_due_expire_pending_lock.sql:5,26`, `subscriptions_due_expire_pending.sql:3,20`). After a
  reject an overdue-pending one expires at the next run and its opening invoice is voided. Worth stating in the UI copy.
- "Needs attention" group `proof_submitted` (`db/sql/payments/attention_list.sql:3,10-13`) clears automatically.

**Operator SQL today** (`docs/RUNBOOK.md:934-954`): in one tx, `UPDATE invoices SET status='issued' WHERE ... AND
status='proof_submitted'` (claim note/time kept "for the record"), audit row `payments.invoice.claim_rejected` with
`{"by":"operator","why":...}`; "Tell the customer from the Inbox". Also referenced at RUNBOOK :816-821.

**What a console reject should do (proposal, mirrors `void_invoice` at `csa/payments/invoices.py:489-526`).**
`reject_claim(conn, client_id, invoice_id, *, actor_user_id, reason: str | None)`: `_lock_invoice`; 404
`invoice_not_found`; status not `proof_submitted` -> 409 (new code, e.g. `claim_not_open`; paid -> existing
`invoice_already_paid`/`invoice_paid` semantics, void -> `invoice_void`); new SQL `db/sql/payments/invoice_reject_claim.sql`
(`SET status='issued' WHERE ... AND status='proof_submitted' RETURNING id`); audit via `db/sql/console/audit.sql`
(`actor_id, client_id, action, target_id, details`) with action `payments.invoice.claim_rejected` (same name the RUNBOOK
uses) and details e.g. `{"reason": ...}` or `{}` like void (:518). Route `POST /invoices/{id}/reject-claim` beside
`void` (`csa/payments/api.py:205-221`), `staff(...)` guard, body model like `VoidIn` (`contracts.py:234-235`), returns
`InvoiceDetail`. Optionally set `overdue` directly when `due_at < now` (the job does it within 15 min either way).
- Audit schema allows any action string 1-100 chars (`db/migrations/0270_console_identity.sql:66-74`): no migration.
- **No migration is strictly needed** (`issued` already allowed). Decision: if the drawer must show "claim rejected
  by X on D (reason)", add nullable `claim_rejected_at/by/reason` columns (0590-style NOT VALID + VALIDATE checks) in a
  new migration; otherwise the only trail is `console_audit`, which no tenant page reads (only
  `db/sql/console/identity/audit_list.sql`, platform side). Without columns the drawer keeps showing "What the customer
  said" (`PaymentsWorkspace.tsx:463-470`) on an `issued` invoice with no sign it was rejected; either clear the claim
  fields on reject (contradicts RUNBOOK wording) or relabel the section by status.
- Notify the customer? Today nothing does (RUNBOOK says message from Inbox). An automatic message would reuse
  `csa/payments/notices.py:37-100` (`chat_message`, Telegram or WhatsApp-in-23h) plus new en/ms copy in
  `csa/payments/copy.py`. Owner decision; default suggestion: no auto message in this follow-up.
- Workspace assistant has no payment actions (`grep` of `csa/workspace` finds none): nothing to change there.

**Web.** One component serves both admin and portal: `app/(admin)/payments/page.tsx` and `app/portal/payments/page.tsx`
both render `components/console/PaymentsWorkspace.tsx` (all under `CSA_WebConsole/csa-console/web/`). Changes:
- `lib/payments-api.ts`: add `rejectClaim()` beside `voidInvoice` (:211-213), body type beside `VoidIn` (:91),
  new error codes in `PAYMENT_ERRORS` (:286-300).
- `lib/payments-view.ts`: `canRejectClaim(status) = status === "proof_submitted"` beside `canVoid` (:161-163); body helper
  beside `voidBody` (:148-151).
- `PaymentsWorkspace.tsx`: drawer action row (:496-507, `form` state `"pay" | "void"` at :410/:437) gets a third form;
  new `RejectClaimForm` modelled on `VoidForm` (:617-660); the claim section (:463-470).
**Tests to extend.** `tests/test_payment_invoices.py` (claim :972, void from every status :909, no-deadlock :777,
cross-tenant :1045); `tests/test_console_payments.py` (void route :553, audit order :548-582, read_only/package gate
:249); `tests/test_payments_browser.py` (void flow :673, attention drawer :727, roles :758); job interplay in
`tests/test_jobs_payments.py`, `tests/test_jobs_payment_reminders.py`, `tests/test_jobs_subscriptions.py`; web
`tests/payments-api.test.mjs` (:42-80, error map :120-172) and `tests/payments-view.test.mjs` (:97-117);
`tests/test_console_contract_snapshot.py` (regenerate). Docs: `docs/CONSOLE_API.md:1418-1427` (+ audit list),
`docs/RUNBOOK.md:816-821,934-954`, `docs/CUSTOMER_BOT_RETEST.md` "Payments" section, `docs/PROGRESS.md:35` item (1).

## B. Sign-up data note mentions a given email

**Constant and uses.** `SIGNUP_DATA_NOTE` = "We keep your name and phone number on your contact record so the team can
set up your plan." (`csa/agents/staged/subscription_facts.py:171-173`), used only as `texts["data_note"]` (:224) in
`signup_fact`. Emitted once per sign-up in the first asking turn by `_ask` (`csa/agents/staged/signup.py:308-321`,
`draft.noted`). `asks_email` is already computed before any ask (`signup.py:128`, `_asks_email` :172-177: WhatsApp only,
payments on, contact has no address), but `_ask` does not receive it today: threading it through is a small change.
The email ask already discloses use: "...We keep it on your contact record to send you messages about your plan and
bookings." (`subscription_facts.py:236-239`; ruling `docs/superpowers/specs/2026-11-11-payments-design.md:307-310`).
The given email is stored on the request and fills `contacts.customer_given_email` (`csa/subscriptions/signup.py:7-9,
119-120,168`) and is used for payment reminder email (RUNBOOK :848-850) and booking mail.
- **Receipts: none exist.** No receipt or payment-confirmation message/mail is sent on Mark paid (`grep -i receipt csa`
  finds only HR). Wording must not promise receipts unless that is built.
- **Malay version: there is none in code.** Signup facts are English only; the composer model rewrites them on normal
  turns (comment `subscription_facts.py:166-170`). The Malay sentences in golden files are recorded composer output, not
  copy: "Kami simpan nama dan nombor phone kat rekod contact supaya team boleh setup plan."
  (`tests/golden/cases/cs_signup_collect_ms.json:60`, `cs_signup_confirm_ms.json:63`, `cs_signup_verified_phone_ms.json:65`).
  On a payment turn the note would be sent verbatim in English (see C).
- **Rule "facts sent verbatim are written to the customer":** `subscription_facts.py:1-10,166-170` ("each is said to the
  customer, never to the composer"), design 11.1 item 3 (`payments-design.md:236-243`) and ruling (j) in
  `docs/PROGRESS.md:31`; payment copy module docstring `csa/payments/copy.py:1-14`. Style: second person, plain, no
  "Please note"; Malay copy uses casual "awak/dah/bagitahu/kat", and goldens exclude " anda ", "sila", "pihak kami"
  (`tests/golden/cases/cs_pay_instructions_ms.json` `reply_excludes`).
- **Channel-specific?** The note is shared by Telegram and WhatsApp. Recommendation: keep kind `data_note` (fact id
  `signup_data_note_N` is pinned in ~10 asserts, `tests/test_staged_agents.py:1206-1948`) and vary the text with a flag
  (`with_email=asks_email`), so Telegram text and all Telegram goldens stay unchanged. An unconditional "if you give us
  an email" also works but shows on Telegram where no email is ever asked.
- Related open decision: "on your contact record" is inaccurate for a typed phone (stays on the request), `docs/PROGRESS.md:45`,
  `docs/CUSTOMER_BOT_RETEST.md:124-128`. Fold into the same wording pass.

**Wording options** (EN as the fact; MS for the copy if C adds Malay facts):
1. Conditional, WhatsApp-with-email only:
   EN "We keep your name, phone number and any email you give us on your contact record, so the team can set up your
   plan and send you payment reminders."
   MS "Kami simpan nama, nombor telefon dan emel (kalau awak bagi) dalam rekod awak, supaya team boleh setup plan dan
   hantar peringatan bayaran."
2. Two sentences, email clause appended only when asked:
   EN base unchanged + " If you give us your email, we use it for payment reminders and booking messages."
   MS "Kami simpan nama dan nombor telefon awak dalam rekod kami supaya team boleh setup plan. Kalau awak bagi emel,
   kami guna untuk hantar peringatan bayaran dan info tempahan."
3. Unconditional (both channels): EN "We keep your name and phone number, and your email if you give one, so the team
   can set up your plan and remind you about payments." MS "Kami simpan nama, nombor telefon dan emel awak (kalau ada)
   supaya team boleh setup plan dan ingatkan awak pasal bayaran."
(Options 1-2 drop or keep "on your contact record" per the open decision; "receipts" deliberately left out.)

**Pins to update.** `tests/test_subscription_facts.py:275-279`; `tests/test_staged_agents.py` WhatsApp email cases
(:1798-1948, `_kinds` asserts unaffected if the kind stays); `tests/test_customer_bot_replay.py:243` and
`scripts/customer_bot_replay_cases.json:116,140` (only if base EN text changes); golden EN cases that embed the sentence in
recorded replies/`compose_sees`: `cs_signup_confirm_en.json:62,82`, `cs_signup_collect_en.json:59,79`,
`cs_signup_bare_ok_not_confirm_en.json:62`, `cs_signup_no_email_ask_telegram_en.json:65`, `cs_signup_collect_ms.json:80`
(all Telegram; unchanged under option 1/2). Docs: `docs/CUSTOMER_BOT_RETEST.md:124-128`, `docs/PROGRESS.md:33` item (3).

## C. Mixed languages on Malay payment turns

**Assembly.** `csa/turn/deps.py:687-698` `compose()`: if `payment_facts(state)` (:209-221) and not handed off ->
`_facts_draft([*write_facts(state), *paid], _turn_language(state.envelope))` (:226-250), no model. `write_facts`
(:181-206) = ok results of planned non-payment tasks whose action risk is `draft|commit`. The same `_facts_draft` is the
booking fallback when compose fails (:709-716), also English on Malay turns.
**Locale.** `AgentContext.locale` (`csa/agents/base.py:38`) is set for every task from `_turn_language`
(`deps.py:656-669`, `:1191-1193`: channel language else word detector `csa/turn/texts.py:103-110`) but only the payment
agent reads it (`csa/agents/payments/agent.py:101`, `copy.locale_of` `csa/payments/copy.py:49-51`). Minor: language
comes from the message, not the classifier (`docs/PROGRESS.md:35` item 8).
**Write facts that can join a payment reply, all English only:**
- Booking (`booking_agent` book/reschedule/cancel/save_details, `csa/agents/staged/service.py:227-235`):
  `csa/agents/staged/booking_facts.py` (265 lines: `booking_line` :127-153, `refusal` :160, `withdrawn` :168,
  `cancel_question` :178-190, `saved_line` :218-248, `no_email` :45-50, `EMAIL_NOT_SEEN`/`LIMIT` :36-42) and dates from
  `csa/bookings/bot.py:145-154` `format_slot` (`%A`/`%B` English day/month names).
- Ticket (`create|add_note|cancel|reopen`, `csa/agents/tickets/agent.py:100-107`): texts at :199, :231-232, :277, :295,
  :319-320 (the cancel question), :347, :362, :378.
- Order (`create_draft|confirm|cancel`, `csa/agents/orders/agent.py:89-97`): draft :372-373, confirm :521, already/cancelled
  :473, :544, :590. Telegram only (order agent refuses WhatsApp).
- Subscription requests and sign-up (`request_*` = draft, `service.py:584-593`): `request_fact` texts
  `subscription_facts.py:127-163`, `signup_fact` texts :219-256 (readback, phone_invalid, email_ask/invalid, filed*,
  exists, has_plan, closed, which, try_later, data_note).
- Already Malay: only `csa/payments/copy.py` (payment lines and reminders) and `csa/turn/texts.py:16-63` (holding/handoff).
**Coverage today.** `tests/test_payment_reply.py:203` (`keeps_what_the_turn_wrote`, English), :428 (`malay message gets
the malay facts`, payment only). Golden: `cs_pay_instructions_ms.json`, `cs_pay_claim_ms.json`, `cs_pay_status_paid_ms.json`
are payment-only; no golden mixes payment with a write. Runner seeds tickets, plans/subscriptions, invoices, orders but
not bookings (`tests/golden/runner.py:181-199`) and Telegram chats only. Replay (`scripts/customer_bot_replay.py:242-251`)
calls `llm.compose` directly with scripted facts, so `en-payment`/`ms-payment`
(`scripts/customer_bot_replay_cases.json:158-209`) never exercise the facts-only path: replay cannot cover C.
**Smallest change options.**
1. Locale-aware copy per agent, keyed on `ctx.locale` (already plumbed): add `ms` variants to signup/request facts,
   ticket facts, order draft/confirm/cancel, booking facts (+ a Malay `format_slot`: Malay day names are new; month
   names exist in `copy.py:37-46`). Roughly 50-60 strings. Side effect: Malay facts also feed the composer on normal
   Malay turns (should be harmless; goldens replay recorded model output, but `compose_sees` asserts in `_ms` goldens,
   e.g. `cs_signup_collect_ms.json:80`, pin English facts and would change).
2. Narrower: render Malay only where facts go out verbatim. E.g. agents return the English fact plus a Malay text
   (new optional field on `Fact`, `csa/contracts/facts.py:15-21`, additive) and `_facts_draft` picks by language; composer
   input unchanged. Same copy volume, smaller blast radius, but a contract change.
3. Cheapest, lossy: on a Malay payment turn with write facts, run the composer over the write facts only and append the
   payment facts verbatim. Brings a model back into a payment turn (grounding/review path), contrary to design 11.1.3.
Recommend 1 or 2, scoped to the facts in `write_facts` (reads are dropped on payment turns anyway).
**Files/tests to change.** `csa/agents/staged/{subscription_facts,signup,booking_facts,service}.py`, `csa/bookings/bot.py`
(or a new Malay formatter), `csa/agents/tickets/agent.py`, `csa/agents/orders/agent.py`, `csa/turn/deps.py` (if option 2);
tests `tests/test_payment_reply.py`, `tests/test_subscription_facts.py`, `tests/test_staged_agents.py`,
`tests/test_booking_agent.py`, `tests/test_bookings_bot.py`, `tests/test_order_agent.py`, ticket tests (`tests/golden/cases/
ticket_*`, UNVERIFIED which unit file pins ticket text), new goldens e.g. `cs_pay_with_ticket_ms`, `cs_pay_with_signup_ms`.

## D. DuitNow QR image in replies

**Outbound today is text only.** Delivery `apps/delivery/main.py:105-180`: WhatsApp `WhatsAppAdapter.send_text`
(`csa/channels/whatsapp.py:157-193`, `type: "text"`), Telegram `TelegramAdapter.send_message` (`csa/channels/telegram.py:303-360`,
`sendMessage`, inline buttons / share-phone keyboard). No `sendPhoto`, no WhatsApp media upload or `image` message
anywhere; media code only parses inbound (`whatsapp.py:30-35,397-405`, `telegram.py:605`). Design confirms
(`payments-design.md:229-231`).
**Outbox schema.** `messages` has `body text NOT NULL`, `attachments jsonb DEFAULT '[]'`, `has_attachment`
(`db/schema_baseline.sql:952-965`) and `ui jsonb` (`0180_message_ui.sql`); attachments are written for inbound only
(`db/sql/worker/insert_message.sql`, `apps/worker/main.py:303-314`). `outbound_deliveries` rows come from trigger
`trg_webhook_delivery` on outbound inserts (`schema_baseline.sql:3050`); the delivery read `db/sql/delivery/get_message.sql`
selects `body, ui` only. `OutboundMessage` (`csa/contracts/state.py:130-137`) has `body` (1-8000) and `ui`; `Ui`
(`csa/contracts/facts.py:56-60`) has options/confirmation/request_phone. `state.outbound` is a list and `emit` inserts
each (`csa/turn/deps.py:1029-1042`; coordinator builds one at `csa/graph/coordinator.py:400-415`, key `:0`), so a turn
can emit an image message plus the text message (keys `:0`, `:1`) without schema change; claim order is per conversation
by `created_at` (`db/sql/delivery/claim.sql`). One provider message per delivery row should be kept (retry safety).
**Where methods live.** `payment_methods` (`0590_payments_core.sql:192-205`: kind, label, `details jsonb`, instructions,
is_active, sort_order, version); `QrDetails {duitnow_id, account_name}` (`csa/payments/contracts.py:50-54`); PATCH replaces
the whole `details` object (`docs/CONSOLE_API.md:1422`), so an image key is better a column than a details key (needs a
migration; see Shared facts on numbering). Customer text `method_line` (`csa/payments/copy.py:94-115`); methods offered
`db/sql/payments/agent_methods.sql` (first 4 active qr/transfer). Settings UI: method form in
`PaymentsWorkspace.tsx:881-1060` (`Methods`, `MethodForm` :957), drafts in `lib/payments-view.ts:331-477`.
**Upload/storage that exists.** `csa/storage` (`base.py` ObjectStore put/open/read/delete; `local.py`; `s3.py` with
presign `media_source` :254-255). Stores built per area `kb`, `hr`, `training` with S3 prefixes of the same names
(`csa/storage/factory.py:18-49`); local backend needs `*_UPLOAD_ROOT` (`csa/core/settings.py:127-142`). Gateway builds
them (`apps/gateway/main.py:146,527-535`); worker gets KB only (`apps/worker/main.py:716`); **the delivery process has
no store**. Best template for a small image upload: HR documents (`csa/hr/documents.py:28-190`: `upload_form`
from `csa/console/multipart.py:14,83` with `MAX_FILE_BYTES` 10 MiB, extension allow-list, magic-byte sniff for JPEG/PNG
`_MAGIC` :66). Design 9 notes a new `payments` prefix needs stores, IAM prefixes and grants (`payments-design.md:166-170`).
**Limits to enforce (proposal).** PNG or JPEG only (magic sniff), at most ~1-2 MB, one image per QR method, replace on
re-upload. Provider limits are NOT documented in the repo (UNVERIFIED, general API knowledge): WhatsApp Cloud API images
JPEG/PNG up to 5 MB, sent by uploaded media id or public `link`, caption up to 1024 chars; Telegram `sendPhoto` up to
10 MB multipart or a URL, caption up to 1024 chars. The longest payment reply is ~3,950 chars (`docs/PROGRESS.md:31` (u)),
so the image must be its own message with a short caption, not the text as caption.
**WhatsApp constraints as documented here.** 24-hour service window enforced in delivery (`whatsapp.py:22,238-242`;
`apps/delivery/main.py:150-151` fails terminal `outside_service_window`); proactive reminders only in chats with a
customer message in the last 23 h (`csa/payments/notices.py:8-9`, RUNBOOK :846-847); templates, media download and
interactive messages are "phase 2" (`docs/superpowers/specs/2026-10-27-client-growth-program-design.md:40-41,245`). The
repo does not say whether media is allowed in session messages (UNVERIFIED; Meta allows it inside the window).
**Minimal vertical slice.** (1) Migration: nullable `payment_methods.qr_image` (storage name, media type, size, sha256)
or a small `payment_method_images` table. (2) Store: new `payments` area in `Stores` + S3 prefix + IAM + compose env for
gateway and delivery. (3) Console: `PUT/DELETE /methods/{id}/qr-image` (admin), `GET` for preview; contract regenerate;
Settings form file input + preview. (4) Reply: payment agent marks which method has an image (e.g. `Ui.image` or a typed
attachment on `OutboundMessage`); emit inserts an image message (caption "DuitNow QR: <label>") before the text.
(5) Delivery: read the image (from the store, or a presigned URL on S3 only: local backend has no presign), Telegram
`sendPhoto` multipart, WhatsApp media upload then `image` message (or `image.link` with a presigned URL); cache provider
ids optional. (6) Web sandbox delivery (`_deliver_web`, `apps/delivery/main.py:244`) and the Inbox message view must show
or skip it. (7) Optional: reminders (`notices.chat_message`) and reminder mail (`csa/console/mail_templates.py:369-392`).
**New infrastructure:** outbound media in contracts/outbox/delivery for both providers, a tenant image store reachable
from delivery (S3 prefix, IAM, secrets on staging/prod), upload route. Telegram-only first is about the "one task" the
design estimated only for the send; with storage, contract and UI it is several tasks. Treat as its own design round.
