# Staff email assistant

Status: implemented as a staff-only, draft-only module (migration `0430`,
`csa/email/`, console tab "Email assistant"). It is never part of the customer
planner, and nothing in it sends email. Each staff member connects their own
Gmail mailbox; nobody else in the tenant, including administrators, can read it.

## What it does

| Capability | Behaviour |
| --- | --- |
| Tenant switch | Off by default. A tenant administrator switches it on or off (`PUT /console/v1/email/settings`), recorded with a receipt. |
| Connect mailbox | OAuth 2.0 with PKCE against Google, scopes `gmail.readonly` and `gmail.compose` only. The callback must come back to the same console session that started it; the state is single-use and expires after ten minutes. Tokens are AES-GCM encrypted per mailbox and readable only through the connecting user's own session (row-level security on `app.hr_user_id`). |
| Sync | Pulls up to 20 threads from the last 14 days: subject, sender, snippet, labels and the latest message's text body (plain text preferred, HTML stripped, 20,000 characters max). Re-syncing a thread whose latest message changed clears its previous analysis. |
| Analyse | Sends the thread text to the configured model as untrusted data and stores a strict result: category, priority, language, a bounded summary and up to five action items. No tool calls exist for the model to invoke. |
| Draft a reply | The model proposes a plain-text reply in the tenant's persona and tone, in English, Malay or the customer's language. Missing facts become `[STAFF: …]` placeholders. The proposal is validated by the same header-injection and recipient rules as the staged email contract before it is stored. |
| Edit, delete, export | Staff edit drafts locally. "Save to my Gmail drafts" creates a Gmail draft in the user's own mailbox through the compose scope; sending still happens in Gmail by a human. Exported drafts are frozen here. |
| Compose a new email | `POST /console/v1/email/compose` takes exactly one of `recipient_employee_id` (resolved through the company directory; 422 `recipient_unresolved` when the person has no work email) or `recipient_address`, plus a required `subject` and required `instructions` (optional `language`, default `auto`), and writes a model-drafted internal email with `thread_id` null and `source="model"`. It needs the same `staff_email` package and tenant switch, is saved only as a local draft and is exported through the existing export flow, which the workspace assistant wraps in an `email_export` proposal the user confirms. Nothing is sent. |
| Disconnect | Revokes and erases both tokens; cached threads stay readable until the mailbox is reconnected. |

Every write carries an `Idempotency-Key`; receipts are immutable
(`email_receipts`) and a replay with a different payload is refused.

## Authority model

- Session validation reuses `public.hr_lock_session`, so console roles, CSRF,
  idle limits and membership state are enforced identically to HR, and the
  database binds the verified user for row-level security.
- Read-only members can see the context page only. Platform administrators
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
client (web application type), and request only the two scopes listed.

- **Staging** uses an **Internal** client in the fourstacks.my Google Cloud
  project. Only fourstacks.my accounts can connect, so there are no test users
  and no seven-day refresh-token expiry.
- **Production for client tenants** needs an **External** client with Google
  verification, because `gmail.readonly` is a restricted scope. The owner
  decided on 2026-10-29 to keep inbox reading. Planned email management will
  also add `gmail.send`, which changes the verification scope.
- In a client in Testing mode, refresh tokens expire after seven days; the
  module reports `mailbox_reauthorization_required` and the user reconnects.

## Not included

Mailbox push notifications (Pub/Sub), label changes, sending, attachments,
shared or team mailboxes, Outlook. Those are later phases.

## Validation

- `tests/test_email_contracts.py`: parsing, PKCE, MIME, strict model outputs.
- `tests/test_email_service.py`: disposable database, fake Gmail transport and
  scripted model; covers switch authority, PKCE/state binding, encrypted
  tokens, sync replay, triage with an injected instruction, persona drafting,
  validation failures, export without send, cross-user and cross-tenant
  isolation, immutable receipts, token refresh and disconnect.
- `tests/email-transport.test.mjs`: client idempotency, tenant binding, headers.
