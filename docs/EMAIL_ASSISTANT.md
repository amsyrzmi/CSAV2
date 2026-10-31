# Staff email assistant

Status: implemented as a staff-only module (migration `0430`, with sending and
paging added by `0484`; `csa/email/`, console tab "Email assistant"). It is never
part of the customer planner. Email leaves only from the staff member's own
Gmail, only after they confirm it, and at most once. Each staff member connects
their own Gmail mailbox; nobody else in the tenant, including administrators, can
read it.

## What it does

| Capability | Behaviour |
| --- | --- |
| Tenant switch | Off by default. A tenant administrator switches it on or off (`PUT /console/v1/email/settings`), recorded with a receipt. |
| Connect mailbox | OAuth 2.0 with PKCE against Google, scopes `gmail.readonly` and `gmail.compose` only. The callback must come back to the same console session that started it; the state is single-use and expires after ten minutes. Tokens are AES-GCM encrypted per mailbox and readable only through the connecting user's own session (row-level security on `app.hr_user_id`). |
| Sync | Pulls threads from the last 90 days (`newer_than:90d -in:chats`), 20 at a time: subject, sender, snippet, labels and the latest message's text body (plain text preferred, HTML stripped, 20,000 characters max). Re-syncing a thread whose latest message changed clears its previous analysis. See [Sync](#sync). |
| Analyse | Sends the thread text to the configured model as untrusted data and stores a strict result: category, priority, language, a bounded summary and up to five action items. No tool calls exist for the model to invoke. |
| Draft a reply | The model proposes a plain-text reply in the tenant's persona and tone, in English, Malay or the customer's language. Missing facts become `[STAFF: …]` placeholders. The proposal is validated by the same header-injection and recipient rules as the staged email contract before it is stored. |
| Edit, delete, export | Staff edit drafts locally, including To, CC and BCC. "Save to my Gmail drafts" creates a Gmail draft in the user's own mailbox through the compose scope and sends nothing. Exported drafts are frozen here. |
| Send | Staff send a draft from their own Gmail after confirming it. See [Sending](#sending). |
| Reply | `POST /console/v1/email/threads/{thread_id}/reply` drafts a reply to a thread, with optional CC and BCC. See [Replies](#replies). |
| What needs my attention | `GET /console/v1/email/attention` lists up to 10 threads that need the user. See [Attention summary](#attention-summary). |
| Compose a new email | `POST /console/v1/email/compose` takes exactly one of `recipient_employee_id` (resolved through the company directory; 422 `recipient_unresolved` when the person has no work email) or `recipient_address`, plus a required `subject` and required `instructions` (optional `language`, default `auto`), and writes a model-drafted internal email with `thread_id` null and `source="model"`. It needs the same `staff_email` package and tenant switch and is saved as a local draft. Staff can then send it, or export it through the existing export flow, which the workspace assistant wraps in an `email_export` proposal the user confirms. Nothing is sent by compose itself. |
| Disconnect | Revokes and erases both tokens; cached threads stay readable until the mailbox is reconnected. |

Every write carries an `Idempotency-Key`; receipts are immutable
(`email_receipts`) and a replay with a different payload is refused.

## Sending

No new Google scope is needed. `gmail.compose` already permits
`users.messages.send`, so nobody has to reconnect their mailbox.

A send comes from a draft, through `POST /console/v1/email/drafts/{draft_id}/send`,
with the draft's `version` and an `Idempotency-Key`. The server computes the
version (`DraftOut.version`); a send whose version no longer matches the stored
draft is refused, so an edit after a confirmation cannot be sent by accident.
Only the draft's owner can send it.

### At most once

- The server claims the draft (`sending`) under a per-mailbox advisory lock
  before Gmail is called. Each claim is bound to its attempt (`send_claim_id`),
  so a late outcome from a superseded attempt never changes the row.
- There is one Gmail call, with a 25-second deadline and no automatic retry.
  A second request for the same draft, or a replay of the same key, never
  calls Gmail again.
- A clear refusal returns the draft to `draft`, so the user can fix it and try
  again:
  - any other 4xx: `send_refused`;
  - 401 or 403: shown as `mailbox_reauthorization_required`;
  - 429, or a 403 whose Gmail reason is a quota limit (`userRateLimitExceeded`,
    `rateLimitExceeded` or `dailyLimitExceeded`): `provider_rate_limited`. The
    same quota 403s count as a rate limit for every other Gmail call, and the
    Gmail error body is never logged;
  - a message Gmail cannot accept: `invalid_message`.
- An ambiguous failure leaves the draft `sending` and returns 502
  `send_outcome_unknown` ("Check your Gmail Sent folder before trying again.").
  These are timeouts, 5xx answers, 408, 409, 499 and a 200 without a message id.
  The user must check Sent first, because the email may have gone.

### Release

`POST /console/v1/email/drafts/{draft_id}/release` is the way out of an unknown
outcome. In the console it is the button "I checked — it wasn't sent". It works
only for the draft's owner, and only when all of these hold: the draft is
`sending`, it has no Gmail message id, and the claim is at least 120 seconds
old. Otherwise it returns 409 `draft_not_releasable`. The 120 seconds is longer
than the 25-second Gmail deadline, so a release never runs while a send is still
in flight. A released draft returns to `draft` and can be sent again.

Replaying the old attempt's key after a release never calls Gmail. It returns 409
`send_released` instead of the stored `send_outcome_unknown`, so a send card
confirmed before the release says "You marked this email as not sent. Ask the
assistant to send it again, or send it from the Email assistant tab."

### Limits and checks

- **Hourly limit:** 20 sends per owner in a rolling 60 minutes, counting
  `sending` and `sent`. Beyond that the answer is 429 `send_limit_reached` with
  `retry_after` (seconds).
- **Recipients:** To, CC and BCC together are 1 to 20 addresses, de-duplicated
  without regard to case, each checked by the same header-injection rules as the
  staged email contract. A BCC address goes only in the raw message, so the To
  recipient's copy shows no Bcc.
- **Placeholders:** a draft that still contains `[STAFF:` cannot be sent
  (`draft_needs_staff_input`).
- **Outside your company:** every address is marked inside or outside. An
  address is inside when it matches an active directory `work_email` of the
  tenant, or when it shares the owner's domain and that domain is not a public
  provider (such as Gmail, Yahoo, Outlook, Hotmail, iCloud or Proton). The send
  dialog and the assistant card show "Outside your company" beside the others.

### Replies

`POST /console/v1/email/threads/{thread_id}/reply` (201) drafts a reply to the
thread, in the thread, with optional `cc` and `bcc`. The reply goes to the
Reply-To, else the From, of the latest message that was not sent by the mailbox
owner. It never goes to the owner (`reply_target_unavailable`). There is no
automatic reply-all.

A message counts as the owner's when Gmail labels it `SENT`, or when its From is
the connected address. The `SENT` label covers send-as aliases, so a reply from
an alias is never the reply target, and a thread whose latest message carries it
counts as answered by the owner (it is left out of the attention summary).

### Email my team

In the workspace assistant, "email my team" drafts one email to the caller's
active direct reports, all in To (the caller is excluded). It needs the HR
access switch "approves team" and the `hr` package.

- Reports without a work email are skipped and named on the card.
- More than 20 reports with a work email is refused (`team_too_large`).
- Without the switch, or with no reports, the assistant answers in plain words
  and drafts nothing.

## Sync

- **Window:** the last 90 days, `newer_than:90d -in:chats`, in pages of 20.
- **Order:** sync picks the 20 conversations with the most recent messages (Gmail `messages.list`, newest first, up to three list pages), so an old conversation with new mail is fetched again.
- **Load more:** `POST /console/v1/email/sync/more` fetches the next page using a
  Gmail page token stored per mailbox. The tab shows "Load older email" until
  there is nothing left in the 90 days. Load more also stops at the cap: when
  the mailbox holds 200 threads after cleanup, or cleanup removed every thread
  the page brought, the token is cleared and the answer is
  `{"threads": 0, "done": true}`, so the tab shows "No older email in the last
  90 days".
- **Caps:** at most 200 cached threads per mailbox, and none older than 90 days
  by their latest message. Cleanup runs after each sync and each load more.
- **Message time:** a thread's time is when Gmail received its latest message
  (`internalDate`). The sender's `Date` header is used only when `internalDate`
  is missing, and a `Date` in the future counts as now, so it cannot rank a
  thread first.
- **Drafts survive cleanup.** Cleanup never deletes a draft. A removed thread's
  drafts lose their thread link and become new (unthreaded) emails, and the
  console does not show them as replies.
- **Sync on open:** when the Email tab opens and the last sync was more than
  5 minutes ago. There is no background sync.
- `GET /console/v1/email/threads` lists every cached thread, up to 200.
- Sync and load more make no model call.

## Attention summary

`GET /console/v1/email/attention` answers "what needs my attention" from stored
data only.

- It runs at most 5 analyses of threads that have none yet, and makes no Gmail call.
- It leaves out threads classed `newsletter_or_promotion` or `spam_or_phishing`,
  and threads whose latest message the owner sent.
- It returns up to 10 items, most urgent first. `as_of` is the time of the last sync.
- A thread it cannot analyse is skipped and counted in `skipped`. The summary
  fails only when nothing can be shown and every thread it tried to analyse
  failed (for example `model_not_configured`). With nothing to show and
  `skipped` above 0, the console says "Some emails couldn't be summarised."
- An analysis is saved only while the thread's latest message is still the one
  analysed. If a sync stored a newer message during the model call, the result
  is dropped and the thread is analysed again later.
- `read_only` members are refused. The console loads the summary when the tab
  opens (after sync on open) and after the Sync button, not after load more,
  because each load can spend up to 5 model calls.
- In the workspace assistant, a failure becomes a plain answer, not an error.
- In the workspace assistant, a connected mailbox that has never synced (its
  last-sync time is null) gets a plain answer instead of an empty summary: "Your
  email hasn't been synced yet. Open the Email assistant tab and press Sync, then
  ask again." (in Malay when the user writes in Malay). A never-synced row that
  is not connected (a pending connect or a revoked mailbox) gets "Connect your
  Gmail on the Email assistant tab first." instead, because Sync needs a
  connected mailbox. Zero threads after a sync is the normal "nothing needs your
  attention" answer.

## Workspace assistant

The assistant can reply to a thread, email the team, summarise the inbox and
send. See [CONSOLE_API.md](CONSOLE_API.md#workspace-assistant). The server picks
the card from the user's own words. Only the parts of the turn the user wrote
count, never a planner question or a clicked option. Text equal to an option
any earlier card offered does not count either, so typing an old card's "send it
now" gives a draft card. The first rule that matches wins:

1. A draft word (draft, drafts, draf, save, simpan) or a negated send (don't
   send, do not send, never send, jangan hantar) gives a card that saves to
   Gmail drafts (`email_export`), so "save it as a draft, don't send" and
   "email Danial but don't send it yet" stay drafts.
2. A send word (send, hantar, hantarkan, kirim, kirimkan) gives a send card
   (`email_send`), unless a write word (write, tulis, karang) is there too and
   the planner does not ask to send: "write a reply to Aina saying we'll send
   the parts Monday" is a draft.
3. An email verb (email, emails, e-mail, e-mel, emel) gives a send card when the
   planner also asks to send, unless the user wrote a write word.
4. Anything else gives a draft card.

The user confirms either card before anything happens.

- Both cards check that the user's mailbox is connected when the card is made,
  so a disconnected mailbox is refused then with 409 `mailbox_not_connected`,
  not at Confirm.
- When the drafting model fails (an invalid answer, a provider error or a
  timeout) on a compose, reply or team email turn, the turn returns 503
  `email_model_unavailable`, never a 500. Nothing is drafted or stored, and the
  user can try again. A failure while planning stays 503 `planner_unavailable`.
- The drafting prompts name the JSON keys (`subject`, `body`,
  `needs_facts_from_staff`). An extra key in the model's answer, such as `to`,
  is ignored: recipients never come from the model. A subject over 200
  characters is cut and facts past 5 are dropped.

Clarifications are checked by the server, not only by the planner prompt:

- An option that only asks the user to type is dropped. That is a label that
  starts with a whole word such as Enter, Type, Provide, Input, Write, Specify,
  Fill in, Masukkan, Taip, Berikan or Isi, or a snake_case value with a word
  such as enter, address, subject, body, email or details in it
  (`enter_address`, `subject_body`). Real choices stay, for example
  "Enterprise plan", `knowledge_base`, a colleague (employee id) or a thread
  (thread id). A question left with no options is answered by typing.
- On an email turn (the user's own words say email, e-mel, reply, balas or
  mail, or hold an address; "send" alone is not enough, and neither are loaded
  mailbox facts or the planner's question), the assistant does not ask for the
  subject or the wording. A question whose options pick a synced thread or a
  colleague (thread id or employee id) is a choice and is not corrected, even
  when it names a subject. When the planner's question mentions the subject,
  body, wording or content (tajuk, isi, kandungan), the server plans once more
  with a fixed correction. If the planner asks again, the user gets one plain
  question with no options: "What should the email say? Type a line or two and
  I'll write it." (in Malay for a Malay message). A turn makes at most two
  planner calls, so a corrected turn can take up to about 40 seconds (two
  20-second planner timeouts) in the worst case. Other clarifications, such as
  "What content should the training cover?", pass through unchanged.
- The user's message cannot open or close the prompt's `<message>` or
  `<correction>` tags: their `<` is escaped as `\u003c`, as in the facts.

- The planner sees up to 10 recent threads (sender, subject cut to 120
  characters, time, priority) as untrusted facts, with `<` and `>` escaped so a
  subject cannot close the prompt wrapper. A thread's sender can only be that
  thread's reply recipient. Any other address must be in the user's own message.
- An employee id in To, CC or BCC must be one the user could have meant: an id
  in the message (a directory result the user picked), an id an earlier
  directory turn offered, or `my_manager` (present only when the message
  mentions a manager). `my_team` ids serve "email my team" only. Any other id
  is refused as invented.
- A refused plan the server can name (a recipient the user did not ask for, a
  thread not in the synced mail, a blank directory search, an over-long option,
  or JSON that breaks the plan contract) is planned once more with a fixed
  server note for that rule, and every gate runs again on the new plan. The
  correction budget is shared with the content correction above: one
  correction per turn, whatever its kind, so a turn makes at most two planner
  calls. If the second plan breaks a rule too, the user gets a plain answer:
  "I couldn't find that conversation in your synced email." for an unknown
  thread, otherwise "I couldn't work out who to send that to. Type their email
  address." (in Malay for a Malay message). Nothing is drafted. Output that is
  not JSON at all stays 502 `planner_invalid_output`.
- When the directory finds nobody for an email request, or HR is not active,
  the assistant asks "I couldn't find that person in the directory. Type their
  email address and I'll continue." with no options. The typed address is
  folded into the first request, so "send" still counts. Outside email a
  search with no match answers "No matching colleague was found." (in Malay
  for a Malay message).
- The save-to-Gmail-drafts card is bound to the whole draft (To, CC, BCC,
  subject and body, the same `draft_version` as a send card), so any edit makes
  it stale. Cards made before this change go stale once.
- A card whose draft is no longer a draft (sent, sending or saved to Gmail)
  offers no Confirm. A send card whose draft still has `[STAFF: …]` parts offers
  no Send and says "Fill in the [STAFF: …] parts on the Email assistant tab,
  then send it there."
- Both email cards store counts only. The console fetches the draft
  (`GET /console/v1/email/drafts/{draft_id}`, owner only) to show the
  recipients and body when the card is open.
- The inbox summary answer is not saved. The stored turn says "Inbox summary: N items."

## Privacy

Addresses, subjects and bodies are kept out of logs, audit rows,
`console_proposals` and `console_workspace_turns`. The cards, turns and audit
rows hold ids, counts and outcomes only.

## Authority model

- Session validation reuses `public.hr_lock_session`, so console roles, CSRF,
  idle limits and membership state are enforced identically to HR, and the
  database binds the verified user for row-level security.
- Read-only members can see the context page only. The attention summary is refused for them. Platform administrators
  must hold a membership in the tenant to use the module there.
- Email bodies and subjects are untrusted input. They are passed to the model
  only inside the JSON `user` payload under an explicit untrusted-data rule,
  never as instructions, and model output is forced into strict schemas.

## Configuration

| Setting | Meaning |
| --- | --- |
| `EMAIL_ASSISTANT_ENABLED` | Gateway flag. When true the three Google values below are required. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | OAuth client (web application type). The secret is a secret file in staging, never an environment literal. |
| `GOOGLE_OAUTH_REDIRECT_URI` | Must be `<console origin>/console/v1/email/oauth/callback`, HTTPS in prod. |
| `GROQ_API_KEY`, `GROQ_MODEL` | Reused for analysis and drafting. |

Google Cloud prerequisites, done once by the owner: enable the Gmail API,
configure the OAuth consent screen, add the redirect URI above to the OAuth
client (web application type), and request only the two scopes listed. Sending
needs no third scope.

- **Staging** uses an **Internal** client in the fourstacks.my Google Cloud
  project. Only fourstacks.my accounts can connect, so there are no test users
  and no seven-day refresh-token expiry.
- **Production for client tenants** needs an **External** client with Google
  verification, because the scopes are restricted (`gmail.readonly` and
  `gmail.compose`). The owner decided on 2026-10-29 to keep inbox reading.
  Email management adds no scope: `gmail.compose` already permits sending, so the
  verification scope does not widen.
- In a client in Testing mode, refresh tokens expire after seven days; the
  module reports `mailbox_reauthorization_required` and the user reconnects.

## Not included

Mailbox push notifications (Pub/Sub), background sync, label changes,
attachments, shared or team mailboxes, Outlook and reply-all. Those are later
phases.

## Validation

- `tests/test_email_contracts.py`: parsing, PKCE, MIME, strict model outputs.
- `tests/test_email_service.py`: disposable database, fake Gmail transport and
  scripted model; covers switch authority, PKCE/state binding, encrypted
  tokens, sync replay, triage with an injected instruction, persona drafting,
  validation failures, export without send, cross-user and cross-tenant
  isolation, immutable receipts, token refresh and disconnect.
- `tests/test_email_schema.py`, `tests/test_workspace_planner.py`,
  `tests/test_workspace_registry.py` and `tests/test_console_workspace.py`: the
  `0484` schema, the assistant's reply, team, summary and send cards, and the
  stale-card and at-most-once rules.
- `tests/email-transport.test.mjs` and `tests/email-recipients.test.mjs`: client
  idempotency, tenant binding, headers, send-key handling, recipient groups and
  error sentences.
- `tests/test_email_management_browser.py`: sync on open, load more, CC and
  send, release, and the send card in the assistant.
