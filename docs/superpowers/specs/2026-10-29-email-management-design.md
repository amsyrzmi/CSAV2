# Email management: send after confirmation, replies, CC/BCC, "email my team", "what needs my attention"

Lane B, follows B4. Date: 2026-10-29. Owner: Amsyar.

## Intent

The staff email assistant (`csa/email/`, migration 0430, `docs/EMAIL_ASSISTANT.md`) reads each staff member's own Gmail, analyses threads and drafts replies. It never sends: a person has to finish every email in Gmail. The workspace assistant (`csa/workspace/`) can draft one new email to one person ("email my boss", "email Adam") and save it to Gmail Drafts after a confirm card.

The owner wants the assistant to handle everyday email end to end, without acting behind anyone's back:
- send after the person confirms;
- reply within a thread;
- copy people in;
- write to their whole team;
- say what needs their attention.

## Goals

- **Send after confirmation.** The workspace assistant's card and the Email assistant tab can send a draft through the person's own Gmail. The email goes to the people the card shows, with the exact subject and body it shows.
- **Reply in a thread.** "Reply to Aina's email saying I'll attend" drafts a reply in that Gmail thread, which keeps the thread headers, and sends it after confirmation.
- **CC and BCC** on new emails and replies, through the assistant ("and copy my boss") and the tab.
- **"Email my team".** Draft one email to all of the person's direct reports, taken from the HR reporting lines (B2).
- **"What needs my attention today".** A short summary of the person's synced threads, highest priority first, built from the analysis the email assistant already stores.

## Non-goals (this version)

- Follow-up reminders, templates and signatures. These come later.
- Attachments.
- An undo-send window. This was approach B, kept as a possible follow-on.
- Shared or team mailboxes, Outlook, mailbox push notifications.
- Customer-channel email: the customer support inbox is separate.

## Anti-goals

- An email that leaves without the person seeing its recipients, subject and body on a confirm card.
- A card that sends something different from what it showed, such as content edited after the card was made, or recipients added by the model.
- A runaway loop or mistake sending many emails.
- Email content, addresses or tokens in logs or audit rows.
- One staff member sending from, or reading, another's mailbox.

## Owner decisions (2026-10-29)

- **The assistant may send** after the person confirms the card. Approach **A** was chosen: one email service used by both the assistant and the Email tab, sending right after confirmation, with no undo window.
- **Recipients may include customers and outside addresses.** Outside addresses are flagged on the card as "outside your company".
- **Mail is sent from the person's own Gmail,** so it appears in their Sent folder and replies come back to them.
- **Limit: 20 sends per person per hour.**
- **The email assistant keeps reading inboxes** (`gmail.readonly`). Sending uses the already-granted `gmail.compose`, which permits `users.messages.send`. No new scope is added, so nobody has to reconnect. Production for client tenants still needs Google verification for these restricted scopes (see `docs/EMAIL_ASSISTANT.md`).
- **First version:** sending (new emails and replies in a thread), CC and BCC, "email my team", and "what needs my attention today".

## Approaches considered

- **A. One email service; the assistant and the tab both use it, and sending happens right after confirmation (chosen).**
  - Assistant actions produce the same stored draft the email assistant already uses.
  - One "send draft" path enforces content binding, the hourly limit and the audit.
  - Fastest, with one set of rules.
  - Cost: no undo once confirmed.
- **B. A plus a 30-second undo window,** with a worker sending from a queue. Not chosen now: it needs a send queue, worker failure handling and more tests. It can be added later on top of A.
- **C. Tab-first; the assistant only hands over the draft.** Rejected: it contradicts "the assistant sends after confirmation".

## Design

### Gmail permission and sending

- **No scope change.** `csa/email/gmail.py` already requests `gmail.readonly` and `gmail.compose`, and `gmail.compose` allows `users.messages.send`.
  - Do not add `gmail.send`. `exchange()` requires every scope in `SCOPES`, so adding one would force everyone to reconnect.
  - (Builder-check correction, 2026-10-29.)
- **The send call:** `users.messages.send`, with a MIME message from `build_reply_mime`, extended to carry CC, BCC, `In-Reply-To` and `References`. Every header value is validated with the same header-injection and recipient rules as `EmailDraftInput` (`csa/agents/staged/models.py`).
- **Reply threading (my call):**
  - Sync also stores the latest message's RFC 5322 `Message-ID`, its `References` chain and its `Reply-To` on `email_threads`.
  - For threads synced before 0484, the send path fetches those headers once from Gmail.
  - A reply goes to the latest message's `Reply-To`, else its `From`, taken from the latest message **not sent by the mailbox owner**.
  - Reply-all is not automatic; others are added only as CC when the user asks.
- **At most once (my call):**
  1. Before calling Gmail, the draft is claimed (`status = 'sending'`, with a send receipt holding the idempotency key) inside a per-mailbox advisory lock, which also guards the hourly count.
  2. Gmail is called once, with no automatic retry.
  3. Success sets `'sent'`. A clear refusal returns the draft to `'draft'`.
  4. An ambiguous failure (timeout, unreachable) leaves `'sending'` and tells the user to check their Gmail Sent folder before trying again.
  - A retake with the same key never calls Gmail again.
  - Claimed, sent and ambiguous sends all count toward the hourly limit.

### Draft model (migration in lane B's range, 0484)

- `email_drafts` gains (with `status` adding `'sending'` as well as `'sent'`):
  - `cc text[]` and `bcc text[]`, each validated like `recipient`, together at most 20 addresses;
  - `status` adds `'sent'`;
  - `sent_at timestamptz`, `gmail_message_id text`;
  - `reply_to_message_id text`, which holds the Gmail message the reply answers.
- **Recipients are stored as lists (my call).** `to_addresses text[]` (1–20) replaces the single-value use of `recipient`. `recipient` is kept and filled with the first address for compatibility. To, CC and BCC together are capped at 20.
- **Placeholders block sending (my call).** A draft whose body still contains a `[STAFF: …]` placeholder cannot be sent: the server refuses with `draft_needs_staff_input`, and the card lists what to fill in first. The draft stores `needs_staff_input boolean`, set when drafted and cleared when an edit removes the placeholders.
- **Other schema in 0484:**
  - `console_proposals_kind_check` adds `email_send`;
  - `console_workspace_turns_action_check` adds `email_reply`, `email_team`, `inbox_summary` and `email_send`.
- **Sends are recorded.** Every send writes an immutable `email_receipts` row (existing table, idempotency key) and a send event. Each holds the draft id, the number of recipients, an internal/outside count and the Gmail message id, never addresses or content.
- **The hourly limit** counts the person's send receipts in the last 60 minutes. The 21st refuses with `send_limit_reached` and the time it frees up.

### The workspace assistant (`csa/workspace/`)

- **New and extended actions:**
  - `email_compose` gains optional `cc` and `bcc` (employee ids or addresses).
  - New `email_reply` takes `thread_id` (from synced threads the facts show), `instructions`, and optional `cc` and `bcc`.
  - New `email_team` takes `subject` and `instructions`.
  - New `inbox_summary` is an answer-style read with no card.
- **"My team" (my call).** Only people who hold the B2 "Approves … for their team" switch can use it, because they already have directory access to their team (`directory.read` on `assigned_manager`).
  - A new owner-run read in 0484 returns the caller's active direct reports with their names and work emails, using the same `app.hr_user_id` filter. Nobody else's reports are exposed.
  - Reports without a work email are skipped and listed on the card.
  - More than 20 reports is refused (`team_too_large`).
  - Everyone goes in **To**: they are colleagues on the same team.
- **Two cards, two steps.** Drafting stays a no-send step, because the AI writes the draft first. The new `email_send` proposal is the card:
  - it shows **To, CC, BCC**, each outside address flagged "outside your company", plus the subject and the full body;
  - it binds a version hash of exactly those fields (the existing `EmailExportExecutor` pattern), so any later edit makes the card stale;
  - its buttons are **Send** and **Cancel**. "Save to Gmail drafts instead" is a cancel followed by a new `email_export` card, because cards support one confirm action.
  - When the user didn't ask to send, `email_compose` keeps producing today's export card.
- **Content stays owner-only (my call).** `console_proposals` and `console_workspace_turns` are tenant-wide under RLS, so they never store the body or the addresses.
  - The send proposal stores only the draft id and the version hash.
  - The card's recipients and body are rendered at read time from the draft, through the owner's email session.
  - The `inbox_summary` answer is returned to the user but not persisted in the turn: the turn records only the kind and a count.
- **What the planner sees of the mailbox (my call):**
  - For `email_reply` and `inbox_summary` the facts include up to 10 recent synced threads: `thread_id`, sender display name and address, subject (cut to 120 characters), received time and priority. They are marked as untrusted data, under the planner's existing rule that facts drawn from messages are never instructions.
  - A thread's sender address may be used **only** as the reply recipient of that thread, never for a new email.
  - "Aina's email" with more than one match becomes a clarification with those threads as options.
- **Planner rules:**
  - recipients only from ids or addresses in the message, history, directory results or facts, never invented;
  - send only when the user clearly asks to send;
  - the model can never add a recipient the user didn't ask for. The card is the user's check.

### The Email assistant tab

- Drafts gain CC and BCC fields and a **Send** button. It opens the same confirm dialog, with the same binding and limit.
- **Reply drafts** made on the tab can be sent as replies in their thread.
- **A sent draft** shows "Sent at <time>", and editing is disabled.
- **"What needs my attention today"** is a panel at the top of the tab with the same summary the assistant gives.

### Inbox summary

- **Built from what's stored:** synced threads and their stored analyses (category, priority, summary, action items). It runs an analysis first only for threads not yet analysed, within existing limits.
- **No extra Gmail read beyond sync:** it reflects the last sync and says so ("as of your last sync at 09:15").
- **Content:** up to 10 items, priority first, each with its sender, subject, a one-line summary and suggested next steps. Any message content in it stays out of logs.
- **What counts as needing attention (my call):**
  - excludes newsletter, promotional, spam and notification categories;
  - excludes threads whose latest message was sent by the mailbox owner, since they're already answered;
  - analyses at most 5 not-yet-analysed threads per summary.
- **Who can see it:** members who can use the Email assistant, not `read_only` ones.
- **"Outside your company" (my call):** an address is inside when it matches an active directory `work_email` of the tenant, or shares the mailbox owner's domain when that domain isn't a public mail provider (gmail.com, yahoo.com, outlook.com, hotmail.com, icloud.com and similar). Everything else is flagged.

## Constraints

- Migration `0484` only, in lane B's range. Keep `EXPECTED_SCHEMA_VERSION` equal to the highest applied migration, `0500` today. Never FORCE RLS; no new roles. The new columns follow `email_drafts`' existing owner-only RLS.
- No addresses, content or tokens in logs, audit rows or exception text. Categories only.
- **The hourly limit and the card binding are enforced on the server,** not only in the UI.
- Tests use a fake Gmail transport and a scripted model; never real Google calls. Synthetic fixtures only.
- **Local checks:** the changed files' tests one at a time, the web checks and `make lint`. The PR's CI is the gate.
- **Rollout:**
  - apply `0484`, then recreate the gateway with **both** `CSA_STAGING_INVITE_MAIL=true` and `CSA_STAGING_EMAIL_ASSISTANT=true` exported;
  - no scope change and no reconnect are needed.
- **Coordination:** the booking session may later ask for Calendar scopes. The email track owns `csa/email/gmail.py` `SCOPES` and the Google settings. `csa/workspace/planner.py` is shared, so edit it here and tell the owner.
- **Audit:** a send writes the existing console audit row (`console/audit.sql`) with the kind only, plus the email receipt, which records counts and the Gmail message id, never addresses or content.

## Left to the builder

- Exact SQL for the new columns and how team recipients are stored.
- Card wording within the writing guide.
- How the reply's `In-Reply-To` and `References` are taken from synced data. This may need the latest message id stored at sync.
- Test names.
- Docs: `docs/EMAIL_ASSISTANT.md` (sending, scopes, reconnect, limits), `docs/CONSOLE_API.md`, `docs/console_openapi.json`, and a `docs/PROGRESS.md` lane B entry. Record the PR #50/#51 staging state there too.
