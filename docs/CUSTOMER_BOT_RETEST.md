# Customer bot conversation retest checklist

Run this after every staging rollout that touches the customer bot's conversation handling (memory,
no-repeat, Malay register, handoff and holding texts). Use the staging test company and synthetic
customers only. Rows marked "manual" depend on staging state or on the console, so only a person can
check them. Tick each row when the result matches.

## The replay

The replay sends a synthetic chat through the real classify and compose model calls, with the
conversation memory a real turn would carry. It uses synthetic facts only, touches no database and
needs only the model key (see the docstring of `scripts/customer_bot_replay.py`):

```bash
docker exec csa-staging-gateway-1 python -m scripts.customer_bot_replay --help
docker exec csa-staging-gateway-1 python -m scripts.customer_bot_replay --dry-run
docker exec csa-staging-gateway-1 python -m scripts.customer_bot_replay --language ms
```

- `--script PATH` replays another script file (default `scripts/customer_bot_replay_cases.json`),
  `--language en|ms|zh|ta` and `--conversation ID` pick conversations, `--dry-run` only checks the
  script file and calls no model.
- The shipped script has three conversations: `ms-plans` (Malay), `ms-owner-style` (Malay, written the way real customers type: "korg ada jual apa", "nak bayar cenne eh") and `en-plans` (English). The last Malay message asks how to pay, which no fact answers: expect an honest "no exact information yet" with a team follow-up, not an invented method.
- It prints each customer message and reply (synthetic text, on purpose), the classifier's intent,
  language and wants_human, the brand tone used, then per conversation the counts of the formal
  Malay markers (`anda`, `sila`, `pihak kami`, `menemukan`, `dimaklumkan`) and of sentences repeated
  across replies. The key is never printed.
- Exit 0: every message answered, no formal marker in a Malay conversation and no repeated
  sentence. 1: a failure. 2: a setup error (no model key, the model unavailable on the first call,
  a bad script file or an unknown conversation id).
- The replay does not run the graph (routing, agents, gate, delivery); the golden cases cover that.
  The counts are a floor, not a verdict: read every reply yourself.

## Rows

| Check | Setup | Expected | Kind |
| --- | --- | --- | --- |
| Malay replay read-through (run both Malay conversations) | `--conversation ms-plans`, then `--conversation ms-owner-style` on the shipped script | Exit 0. Read every reply: casual, natural Malay; no `anda`, `sila`, `pihak kami`, `menemukan` or `dimaklumkan`; no sentence said twice; a list is never longer than the cap; asking for a person is acknowledged once, in one or two short sentences | manual |
| English twin | `--conversation en-plans` | Exit 0. Read every reply: the same facts as the Malay run, warm and plain, no repeated sentence, no list over the cap | manual |
| Register with a professional tone | Console, Customer replies, Tone set to `professional` for the staging test company; then the replay (the script's brand tone can be changed in a copy passed with `--script`) or a real Telegram chat in Malay | The Malay stays casual: `professional` is not a request for formal Malay | manual |
| Register with an explicit formal tone | The same, with Tone set to `formal Malay` | The Malay goes formal (`anda`, `sila` and so on are now allowed); setting the Tone back to empty returns it to casual | manual |
| Handoff after "AI resumed" | A Telegram chat on the staging test company: ask for a person; take over in the console (or note the staff alert) and hand back with Resume AI ("AI resumed."); ask for a person again | The first ask gets a short acknowledgement and the staff alert. After the hand back, the second ask is acknowledged once, in different words from the first, with no repeated sentence | manual |
| Provider-failure holding text | The golden cases only (`fail_safe` expects the localized holding text, and a review error degrades to grounding). Do not break a staging key to test it | In a real outage the customer gets the short holding text in their own language; a handoff reply never fails the turn | golden only |
| Memory kill switch | Staging test company; the runbook SQL under "Customer bot conversation memory" in [RUNBOOK.md](RUNBOOK.md). In a chat, say your name and a plan you want, then switch memory off, then ask "what did I ask for?" | With memory off the bot does not know the earlier turn and asks again, without an error. Switch it back on (or clear the key): the next message uses the earlier turns again. Read the setting back with the runbook SELECT each time | manual |

## Business answers and the escalation ladder

Run this after the rollout that carries migration 0560 (the customer bot answers subscription and stock
questions and hands off through a ladder with a staff note). The staging test company needs a contact with
a subscription (made through the console), a TikTok Shop connection for the stock rows, and Telegram or
the web chat to write from. Use synthetic customers only. The replay needs the model key and no database:

```bash
docker exec csa-staging-gateway-1 python -m scripts.customer_bot_replay --dry-run
docker exec csa-staging-gateway-1 python -m scripts.customer_bot_replay --conversation ms-subscription
docker exec csa-staging-gateway-1 python -m scripts.customer_bot_replay --conversation ms-stock
```

The replay does not run the ladder, the agents or the inbox; it checks the wording the model gives
against synthetic facts. The golden cases cover the rest and cannot show the real model's choices.

| Check | Setup | Expected | Kind |
| --- | --- | --- | --- |
| Subscription replay read-through | `--conversation ms-subscription` | Exit 0. Read every reply: casual Malay, the plan name, price and renew date exactly as the fact writes them, the cancel request described as passed to staff with nothing changed (never "dah stop"), and the cancel request not mentioned on turn 1 | manual |
| Stock replay read-through | `--conversation ms-stock` | Exit 0. Read every reply: each number names its source (our shop or TikTok Shop), "out of stock" for the pack with none, no invented stock or price, and no TikTok price ever (only our shop's price is quoted) | manual |
| "plan apa saya?" with one plan | A contact with one active subscription; ask which plan they are on and when it renews | The plan, price and interval, and the renew date, from the subscription row | manual |
| The same with two plans | A contact with two active subscriptions | One sentence naming both plans and asking which one; no date or price guessed | manual |
| The same with none | A contact with no subscription | "I can't find a subscription linked to this chat." (or its Malay), no plan name | manual |
| The same with a plan ending at period end | An active subscription set to cancel at period end | "It ends on <date> and will not renew", never "renews" | manual |
| "nak cancel plan" | A contact with one active plan; ask to cancel | The reply says the request was passed to staff and nothing has changed; the request appears in the console under Subscriptions as waiting for a decision; the subscription itself is unchanged | manual |
| Cancel with two plans | A contact with two plans; "cancel my plan", then name one of them | The first message prepares nothing and asks which; the second prepares exactly one request, on the plan named | manual |
| Stock for a product in both | A product in our shop and as a TikTok Shop listing | Both numbers, each named by source, and only our price (owner decision 2026-11-11: a TikTok price is never quoted) | manual |
| Stock for a TikTok-only product | A listing that is not in the shop catalogue | The answer comes from TikTok and says so, with the count but no price (the bot may say it can't quote a price here); an unpublished or deactivated listing is never called available | manual |
| Stock with TikTok switched off | Switch the TikTok Shop (Marketplaces) service off for the company, then ask about a shop product and a TikTok-only product | The shop product is answered from the shop; the TikTok-only one gets "I couldn't find that item in our shop."; the model does not mention TikTok | manual |
| Three vague messages, then a fourth | Write a vague message ("tolong"), a second one that adds nothing, a third, then a fourth | The first two get a clarifying question each, none repeated; the third gets a partial answer with one targeted question; the fourth hands off. In the inbox thread the line "Note from the assistant (...)" is there, in English, one or two sentences, with no figure the customer never wrote; the customer never sees it | manual |
| Asking for a person on the second vague message | "nak cakap dengan orang" after one vague message | Handoff at once with the "asked for a person" reply (reason `human_requested`); the ladder does not run | manual |
| A question with no fact ("how do I pay") | A company whose knowledge base has no payment answer; ask how to pay, then say "ya" or "boleh" to the offer | The bot says it does not have that information and offers to pass it to a team member; a gap row with a short topic appears in `kb_gaps` (read it with the runbook SELECT); "ya" hands off, and the thread note names the topic | manual |
| "ok" after a normal answer must not hand off | After a reply that did not offer a handoff (a plan answer, a stock answer), send a plain "ok" and "ya" | The bot closes politely or asks if there is more; no handoff, no staff alert, no note | manual |
| Memory switched off | The memory kill switch (see [RUNBOOK.md](RUNBOOK.md)), then three vague messages and a fourth | The ladder still hands off on the fourth message | manual |
| Another contact's subscription id | Golden only (`cs_sub_other_contacts_id_finds_nothing`): another contact's id typed into the message | The no-subscription sentence, no other plan name, no request row. Do not try it with real ids | golden only |
| An unsellable listing | Golden only (`cs_stock_tiktok_only_*` and the availability tests): a listing that is not on sale | Never described as available | golden only |

Malay calibration of the new copy. The new answers use the casual register of the PR A examples, and the
Malay lines of the new cases ("kat", "nak", "tu", "bagitahu") are the builder's own wording. After the
two replay conversations the owner (1) lists the phrases that sound wrong or stiff in the plan, renew,
stock and handoff-offer replies, and (2) approves or rewrites the exact copy: the subscription and product
facts, the fixed staff notes and the inbox line. A change to wording edits only the text in
`csa/agents/staged/subscription_facts.py`, `csa/agents/orders/availability.py`, `csa/turn/texts.py` or the
Malay examples, then re-runs `tests/test_golden.py` and `tests/test_llm_provider.py` in full.

## Customer onboarding

Run this after the rollout that carries migrations 0580 and 0581 (a customer signs up for a plan in the
chat, and one person on WhatsApp and Telegram becomes one customer). The staging test company needs the
Subscriptions package with the subscription agent switched on, two plans (one set to "Staff approve", one
to "Sign up automatically"), a plan closed to new sign-ups, and a Telegram bot and a WhatsApp test number
to write from. Use synthetic customers and made-up phone numbers only. What staff do with a sign-up, a
link or a review is in [RUNBOOK.md](RUNBOOK.md), section "Customer onboarding: sign-ups and linked
contacts". The replay checks only the wording the model gives for the sign-up facts (it needs the model key
and no database):

```bash
docker exec csa-staging-gateway-1 python -m scripts.customer_bot_replay --conversation en-signup
docker exec csa-staging-gateway-1 python -m scripts.customer_bot_replay --conversation ms-signup
```

| Check | Setup | Expected | Kind |
| --- | --- | --- | --- |
| Sign-up replay read-through | `--conversation en-signup`, then `--conversation ms-signup` | Exit 0. Read every reply: it asks for one missing thing at a time, reads the name and phone back before anything is filed, and says nothing is active yet once it is filed (and never that anything was or was not charged); casual Malay in the Malay run | manual |
| Sign up in English on Telegram | A new Telegram chat: "I want to sign up for <plan>"; give a full name when asked; tap the share-my-number button | One question per message (plan, name, then the phone). The share button appears once, when the phone is asked for. After the share the bot does not ask for the phone again; it reads back the plan and name and refers to the number already on the chat. A "yes" files the sign-up: it appears in the console under Subscriptions, Sign-ups, with the phone marked "Verified" | manual |
| Sign up in Malay on Telegram | The same in Malay ("nak daftar plan <plan>") with a new chat | The same steps in casual Malay, no `anda` or `sila`; the read-back and the filed confirmation are in Malay; the Sign-ups row is the same | manual |
| Sign up in English on WhatsApp | A new WhatsApp chat from a synthetic number: sign up for the plan set to "Staff approve" | The bot never asks for the phone, because WhatsApp gave it; the read-back refers to the number already on the chat. A "yes" files the sign-up, marked "Verified"; the reply says the team will review it and nothing is active yet | manual |
| Sign up in Malay on WhatsApp | The same in Malay | The same steps in casual Malay; the sign-up row is the same | manual |
| A sign-up that needs no approval | Sign up for the plan set to "Sign up automatically" | The sign-up shows under "Decided sign-ups" as "Approved automatically" at once; a subscription "Waiting to start" appears under Customers; the customer is told it is waiting to be started, never that it is active or paid | manual |
| An "ok" that does not answer the read-back files nothing | During a sign-up, when the bot has read the sign-up back, ask a question first (for example "what does the plan include?"), then reply "ok" to that answer | No sign-up is filed and nothing appears in the Sign-ups tab; the bot reads the sign-up back again. A yes (an "ok" included) files it only when it answers the read-back itself, as the latest reply; an "ok" with no read-back just before it files nothing (covered by the golden case `cs_signup_bare_ok_not_confirm_en`) | manual |
| Another customer's number typed | In a Telegram chat that has not shared a number, type the phone number of an existing synthetic customer (one with a subscription) when asked | The sign-up is filed (or read back) with the number "Not verified"; the two contacts are not linked, no "Linked contacts" panel appears for either, and nothing about the other customer (name, plan) is said in the chat | manual |
| Telegram share, then a WhatsApp message | An existing customer who has a WhatsApp chat with a subscription: from a new Telegram chat share the same number with the share button; then send a message from the WhatsApp number | One customer, not two: the subscription's "Linked contacts" panel lists both chats ("Matched by a verified phone number"), and both chats' messages appear on the one customer. A message from either chat reaches the bot without an error. In the console, Unlink (within 30 days) separates them again | manual |
| A forwarded contact card is refused | In Telegram send a contact card that someone else shared (forward a contact card, or share the contact of another person) instead of using the share button | The bot answers "Please use the button to share your own number."; nothing is stored, no contact is created or changed and no phone is marked verified | manual |
| A closed plan | Ask to sign up for the plan marked "Closed to new sign-ups" | The bot says at once that the plan is not taking new sign-ups and asks for no name or phone; no sign-up is filed and nothing appears in the Sign-ups tab | manual |
| A company without subscriptions | A staging company with only the TikTok Shop service: "how do I sign up" and "what plan am I on" | An honest "I don't have that information" with an offer to pass it to a person; no sign-up wording and no plan names. Covered by `cs_signup_not_offered_tiktok_only_*` in the golden cases | manual |
| Pending subscription expiry | Approve a sign-up, then (staging only, with the owner's agreement) lower "Pending sign-ups expire after" to 1 day and wait, or back-date `subscriptions.started_at` of the waiting subscription by two days | Within about five minutes of the job the subscription shows Cancelled, the sign-up shows "Expired", and the timeline has "Expired before it started, so it was cancelled."; nobody is messaged. Put the setting back to 14 | manual |
| Callback taps and contact shares | Golden only: golden cases cannot send a button tap or a contact share. The ingress tests (`test_ingress.py`, `test_telegram.py`, `test_delivery_buttons.py`, `test_staged_agents.py`) cover them | The rows above are the only end-to-end check | golden only |

Owner decision still open: the sign-up's data sentence ("We keep your name and phone number on your
contact record so the team can set up your plan.") says "on your contact record", but a number the
customer types stays on the sign-up request and is not written to the contact (only a number the channel
verified is). Read it in the Telegram and WhatsApp runs above and decide whether the wording should say "on
your sign-up request" or leave the promise as it is.

## Payments

Run this after the rollout that carries migrations 0590 and 0591 (invoices with references, payment
settings and methods, reminders, and the bot answering about payments). The staging test company needs
the Subscriptions package with the subscription agent and the payment agent switched on, a priced plan
(for example MYR 35.00 a month, "Staff approve"), a second plan set to "Sign up automatically", a made-up
bank transfer method under Payments, Settings (a made-up bank and an account number such as
5120000000xx; the switch "Send invoices and reminders" on), a Telegram bot and a WhatsApp test number to
write from, and mail switched on for the email rows. Use synthetic customers only, and an email address
you control for the email rows. What staff do with an invoice (Mark paid, Void, Needs attention, the
reminder rules) is in [RUNBOOK.md](RUNBOOK.md), section "Payments". The replay checks only the wording
the model gives for synthetic payment facts (it needs the model key and no database):

```bash
docker exec csa-staging-gateway-1 python -m scripts.customer_bot_replay --conversation en-payment
docker exec csa-staging-gateway-1 python -m scripts.customer_bot_replay --conversation ms-payment
```

The payment answers themselves are not model text. A customer's payment question gets the facts as
`csa/payments/copy.py` writes them, with the reference, amount and account number copied from the
database, in English or casual Malay (the language of the customer's message). The replay does not run
the planner's payment rules or the classifier's, so the rows below are the only check of those against
the real model: the golden cases (`cs_pay_*`) use a scripted model.

| Check | Setup | Expected | Kind |
| --- | --- | --- | --- |
| Payment replay read-through | `--conversation en-payment`, then `--conversation ms-payment` | Exit 0. Read every reply: the invoice, bank line, reference and "after paying" line (and, after the claim, the "noted" and "not confirmed yet" lines) match the facts exactly; no invented bank, amount or account; casual Malay in the Malay run | manual |
| Instructions in English | A contact with one open invoice (approve a sign-up with payments on). In the chat: "how do I pay for my plan? which account do I transfer to?" | Four lines: "Invoice <reference>: MYR 35.00 for <plan>, due <day month year>." then "Bank transfer: <bank>, account <number>, name <account name>." (staff's instructions after it, if any), then "Please put <reference> as the payment reference so we can match your payment." and "After paying, tell me when and from which bank, and we'll confirm once it shows in our account." The reference, amount and account number are exactly the console's | manual |
| Instructions in Malay | The same in Malay: "nak bayar plan macam mana eh? transfer ke akaun mana?" | "Invois <reference>: MYR 35.00 untuk <plan>, kena bayar sebelum <hari bulan tahun>." (the month in Malay, for example "20 Disember 2026"), "Pindahan bank: <bank>, akaun <number>, nama <account name>.", "Letak <reference> sebagai rujukan bayaran ya, senang kami padankan." and "Lepas bayar, bagitahu bila dan dari bank mana, nanti kami sahkan bila duit dah masuk akaun." Casual, no `anda` or `sila`. The owner reads the wording and approves or rewrites it (see below) | manual |
| "Did you receive my payment?" before any claim | The same open invoice. Ask "did you receive my payment?" and, in Malay, "dah masuk ke?" | "Invoice <reference> (MYR 35.00) is not paid yet. No payment has been recorded for it." / "Invois <reference> (MYR 35.00) belum dibayar. Belum ada bayaran direkodkan untuknya." The reply never says "received" or "confirmed" | manual |
| A claim | "ok I've transferred it just now from Maybank" (Malay: "dah transfer pagi tadi dari Maybank") | "Thanks, I've noted that you paid invoice <reference>. We'll confirm once it shows in our account; until then it isn't marked as paid." / "Terima kasih, saya dah catat awak bayar invois <reference>. Kami sahkan bila duit dah masuk akaun; sebelum tu ia belum ditanda dibayar." In the console the invoice is "Customer says paid", it is under "Customer says paid" in Needs attention, and the drawer shows what the customer said as text. It is not marked paid, and the subscription has not started. A second claim, or the same question again, answers "Invoice <reference> (MYR 35.00): you told us you paid. It isn't confirmed yet; we confirm it once it shows in our account." (Malay: "Invois <reference> (MYR 35.00): awak dah bagitahu dah bayar. Belum disahkan lagi; kami sahkan bila duit dah masuk akaun.") | manual |
| "Did you receive my payment?" after a claim, then after Mark paid | Ask once after the claim (the "told us you paid" line above), then Mark paid the invoice in the console and ask again | After Mark paid: "Invoice <reference> (MYR 35.00) was paid on <day month year>." (Malay: "Invois <reference> (MYR 35.00) dah dibayar pada <hari bulan tahun>."). The waiting subscription now shows Active in the console. The bot says "paid" only after this | manual |
| Two open invoices and "I paid" | A contact with two open invoices (two plans, or an unpaid invoice and the next renewal's); say "I've paid" | "You have more than one invoice waiting: <reference>, <reference>. Which one did you pay?" and nothing is noted until the customer names one | manual |
| Another customer's reference | Type the reference of an invoice that belongs to another synthetic customer, with "did you get payment for <reference>?" | "I can't find invoice <the reference as typed> on this chat." (Malay: "Saya tak jumpa invois <reference> dalam chat ni."). Nothing about the other invoice (amount, plan, name) is said, and nothing is noted on it. Covered by `cs_pay_other_contacts_reference_en` | manual |
| A dispute is handed to a person | "you charged me twice" or "the amount is wrong" | No amount, account or reference in the reply and no payment is noted; the chat is handed off with the usual holding reply and the staff alert, and the Inbox shows the escalation. Needs the real planner: the replay and golden cases cannot show that it chooses `escalate` and does not use it for other account questions ("how do I pay" must still answer, "plan apa saya" must still answer) | manual |
| A TikTok-only company | A staging company with only the TikTok Shop service: "how do I pay for my plan" | No payment wording and no payment answers: an honest "I don't have that information" with an offer to pass it to a person. Covered by `cs_pay_not_offered_tiktok_only_en` | manual |
| Payments switched off | Switch "Send invoices and reminders" off for the test company (the invoices stay), then "how do I pay" | "Payment details aren't set up in this chat yet. I can pass your question to the team." (Malay: "Butiran bayaran belum disediakan kat sini lagi. Saya boleh hantar soalan ni kepada team."). Switch it back on afterwards | manual |
| WhatsApp sign-up without an email | A new WhatsApp chat on the plan set to "Staff approve", payments on: sign up, give the name, and answer the email question with "skip" | After the name (WhatsApp already gave the phone) the bot asks once: "If you'd like payment reminders by email, tell me your email address, or say skip. We keep it on your contact record to send you messages about your plan and bookings." After "skip" the read-back says "no email" ("Sign-up for <plan>: name <name>, phone the number already on this chat, no email. Is that right? ..."). A "yes" files it: "Your sign-up for <plan> was received. The team will review it; once it is approved you'll get an invoice with how to pay, and the plan starts when the payment is confirmed. Nothing is active yet." The Sign-ups row shows no email | manual |
| WhatsApp sign-up with an email | The same, answering with an address you control. Then try once with "ali@" in a fresh chat | The read-back ends "..., email <the address in lower case>". A bad address gets once "That email address does not look right. Please type it again, or say skip."; a second bad one is left out. After approval the Sign-ups row shows the address. A Telegram sign-up is never asked for an email (`cs_signup_no_email_ask_telegram_en`), and a WhatsApp sign-up with payments off is not asked either | manual |
| Sign-up on a plan that approves itself | Sign up on the "Sign up automatically" plan with payments on | "Your sign-up for <plan> is registered. Invoice <reference> is ready: ask me how to pay. The plan starts when the payment is confirmed." The invoice is in the console. With payments off, or on a plan priced at zero, the reply is "Your sign-up for <plan> is registered and waiting to be started by the team." and no invoice is made | manual |
| A pending subscription asks about its plan | A waiting subscription with an open invoice: "plan apa saya?" | "Your plan: <plan>, MYR 35.00 a month, starts when your first payment is confirmed. Invoice <reference> is waiting for payment." After a claim: "... starts when your payment is confirmed. You told us you paid; it isn't confirmed yet." (golden case `cs_sub_pending_waiting_payment_en`) | manual |
| A reminder on Telegram | A customer with a Telegram chat whose sign-up you approve with payments on. Wait for the next payment reminder run (every 15 minutes), outside 21:00 to 09:00 in the company's time zone | One message, the first of the steps ("7 days before" with the default settings, owed at once for a new invoice): "<Brand>: invoice <reference> for MYR 35.00 is due on <date>." (or "is due today", or "was due on <date> and is still unpaid"), the bank line, "Please put <reference> as the payment reference so we can match your payment." and "If you've already paid, reply here and tell us when." In Malay (the contact's language) "<Brand>: invois <reference> untuk MYR 35.00 kena bayar sebelum <tarikh>." ... "Kalau dah bayar, balas sini dan bagitahu bila ya." The drawer's Reminders shows "Telegram · sent <day>". No second reminder that day for the same customer | manual |
| A reminder in an open WhatsApp chat | A WhatsApp customer who wrote within the last 23 hours, with no Telegram chat; approve a sign-up with payments on | The same reminder text arrives in that WhatsApp chat (Reminders: "WhatsApp · sent <day>"). Proactive WhatsApp delivery through the real delivery worker is not verified yet: record whether the message arrived | manual |
| A reminder by email, and no channel | A WhatsApp customer whose last message is more than 23 hours old: once with an email on the contact, once without | With an email: the drawer shows "Email · waiting to send"; the mail goes out within five minutes of the next permitted run (never between 21:00 and 09:00), built with that day's date, and its closing line is "If you've already paid, please let us know when." (no "reply here"). Without an email: "No channel: message them yourself", and the invoice appears under "Needs a nudge" | manual |

Owner decisions and calibration for this section:

- Read the Malay wording of the payment facts and of the reminder (`csa/payments/copy.py`: "kena bayar
  sebelum", "dah lepas", "Letak ... sebagai rujukan bayaran ya", "Kalau dah bayar, balas sini dan bagitahu
  bila ya", the Malay dates "20 Disember 2026") and approve or rewrite it. The builder wrote it in the same
  casual register as the earlier Malay copy; no native speaker has read it. Edits go in `copy.py`, then
  `tests/test_payment_copy.py`, `tests/test_golden.py` and `tests/test_customer_bot_replay.py` re-run.
- The sign-up email question discloses where the address is kept ("We keep it on your contact record to send
  you messages about your plan and bookings"). The shared sign-up data sentence under "Customer onboarding"
  does not mention a given email; decide whether it should, together with the open "on your contact record"
  wording question there.
- Not verified before this checklist: the planner and classifier payment rules against the real model (the
  `en-payment` and `ms-payment` replay does not run them), the sign-up email rules against the real model
  (copying `[EMAIL_0]`, "skip" in Malay), and proactive WhatsApp delivery of a reminder.

## Calibration

The Malay register (a glossary and 12 examples in `_MS_GLOSSARY` and `_MS_EXAMPLES`, in
`csa/decide/llm.py`) has not been read by a native speaker against live model output. After the first
staging rollout:

1. The owner runs the replay on the Malay script and reads every reply.
2. The owner lists the phrases that sound wrong, too stiff or over-used (the examples use "ya"
   often, so watch for it in every reply).
3. One follow-up change then edits ONLY `_MS_GLOSSARY` and `_MS_EXAMPLES` in `csa/decide/llm.py` and
   re-runs `pytest tests/test_llm_provider.py -k register`.

The register is not finished until that pass is done.

## Known limits

- The Chinese and Tamil handoff, holding and follow-up texts have not been reviewed by a native
  speaker.
- The approval-outcome follow-up text uses the contact's stored language. It can still be English
  for a Malay customer when the draft is too short for the word-list detector.
- The second-provider fallback for classify and plan (Gemini JSON mode) has not been exercised on a
  live model, and staging has no Google key.
- When the semantic review is down, an answer with no unsupported figures or links is sent
  unreviewed.
