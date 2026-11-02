# Invitation and reset emails, and HR access chosen on the invitation

Lane B, item B4 of the client growth program
(`docs/superpowers/specs/2026-10-27-client-growth-program-design.md`, section B4). It is combined
with the follow-on the owner deferred from B2: default HR access for new invitees. Date: 2026-10-29.
Owner: Amsyar.

## Intent

**Today:**
- When an admin invites someone or resets their password, the console shows a link that the admin must copy and send by hand.
- New members start with no HR access until the HR owner sets it on "Who can do what" (B2).

**The owner wants:**
- the link emailed from `no-reply@fourstacks.my`;
- the inviting admin to choose the new person's HR access on the invitation, so the person is ready the first time they log in.

**Relay status:** the Google Workspace SMTP relay was set up and tested from the staging host on 2026-10-29.
- **Sender:** `no-reply@fourstacks.my`, with 2-Step Verification and an app password.
- **Relay rule:** allowed *senders* are only addresses in fourstacks.my; recipients can be anyone. It also has an IP allow-list plus SMTP authentication, and requires TLS.
- **Mail authentication:** SPF and DKIM are published; DMARC is `p=none`.

## Goals

- **Invitations:** an invitation, and a renewed invitation, email the accept link to the invitee.
- **Resets:** an admin-issued password reset emails the reset link to the user.
- **Fallback:** if sending fails or mail is not configured, the console falls back to today's copy-the-link flow and says so plainly.
- **HR access:** the invite form has an **HR access** choice, applied when the person accepts, and recorded as set on invitation by the inviting admin.

## Non-goals

- **No self-service "Forgot password".** The login page keeps saying "ask your administrator". This is a separate follow-on, because it needs rate limits and must not reveal which addresses have accounts.
- **No other email kinds:** no notification or marketing emails.
- **No stored mail:** no outbox table, no retry queue, no stored raw tokens.
- **Nothing beyond these routes:** no new console roles, and no change to who can invite, renew or reset.

## Anti-goals

- A raw link stored in the database, or written to logs, audit rows or error messages.
- A screen that says "emailed" when the email did not go out.
- An admin who is not the HR owner able to give a new person approver, HR-team or payroll access.
- An invitation whose HR access is applied twice, applied to the wrong person, or applied after the invitation was revoked.

## Owner decisions (2026-10-29)

- **The inviting admin chooses** the new person's HR access, on the invite form.
- **What each inviter may choose** (my recommendation, which the owner accepted):
  - Any inviting admin may choose **No HR access** or **Staff**. Staff is the B2 preset: "See everyone's basic details" plus "Can request their own training, leave and attendance". The default is **Staff**.
  - When the inviter is the client's **HR owner**, they may also choose Manager, HR team, Payroll preparer or Payroll reviewer.
- **Approach A** (applied the moment the person accepts) was picked over B (a worker job) and C (the HR owner applies a suggestion).
- **Self-service "Forgot password"** stays out of B4.

## Approaches considered

- **A. Apply on acceptance (chosen).** The invitation stores the chosen preset. Right after the acceptance transaction commits, the gateway makes one narrow HR call, and the database checks everything before writing the preset's grant rows. The person is ready at first login.
- **B. A worker job applies accepted invitations' presets.** Rejected: there is a delay before access arrives, and the worker would need HR write rights it lacks today.
- **C. The HR owner applies a suggested preset.** Rejected: it contradicts the owner's decision that the inviting admin decides, and leaves new people without access.
- **Mail transport.** The program design's choice stands: in-process SMTP through the Google Workspace relay, sent after the token transaction commits, with the manual fallback. The Gmail API and a mail outbox were rejected there.

## Design

### Email

- **Module:** a small mail module (for example `csa/console/mailer.py`) used only by the identity service.
- **Connection:** it connects to `smtp-relay.gmail.com:587`, requires STARTTLS, and authenticates with the relay account.
  - It runs in a thread (`asyncio.to_thread`) with a short timeout, about 10 s, so a slow relay never blocks the event loop for long.
- **Configuration** (all off by default):
  - `INVITE_MAIL_ENABLED` (true or false);
  - `INVITE_MAIL_FROM` (`FourStacks <no-reply@fourstacks.my>`);
  - `SMTP_HOST`, `SMTP_PORT`;
  - `SMTP_USERNAME_FILE`, `SMTP_PASSWORD_FILE` (secret files);
  - the console origin, already configured, for building links.
- **When it sends:**
  - `invite` sends after the transaction that creates the user and the token commits. The program design notes `invite` returns `IssuedTokenOut`.
  - `renew_invitation` sends the same way.
  - `issue_reset` sends to the user's email after its transaction commits. Reset is platform-admin only today (`require_platform`), so only platform admins trigger reset mail.
- **Where the email facts come from (my call):** `csa_identity` must stay isolated. Its readiness checks fail if it can read `clients` or execute any `SECURITY DEFINER` function (`db/sql/console/identity/readiness.sql`), so the identity service never looks these facts up itself.
  - The route layer (`csa/console/auth.py`) gathers the business name, the tenant time zone and the inviter's HR-owner status from the app pool before it calls identity, and passes them in, together with a send callback.
  - Reset mail has no tenant, so its expiry time uses Asia/Kuala_Lumpur, as the console already does.
- **Links:** they use the same form the console builds today, `<console origin>/invite#token=<raw>`. The token stays in the URL fragment, so it never reaches server logs.
- **Response:** `IssuedTokenOut.delivery` becomes `"email" | "manual"`.
  - On a successful send: `delivery = "email"`, and the response carries no raw token (the field is omitted or null).
  - On failure or when disabled: `delivery = "manual"` with the token, exactly as today. The failure is logged by category only (for example `smtp_auth_failed`, `smtp_timeout`, `smtp_refused`), never with the address, link or message body.
- **Content:** plain text plus a minimal HTML part, in Malaysian English per `docs/WRITING_GUIDE.md`.
  - **Invitation:** who invited them (the admin's display name), which business (`clients.name`), the link, the expiry time in the tenant's time zone, and what to do if it was not expected.
  - **Reset:** the link, the expiry time, and a note that ignoring it changes nothing.
  - **Subjects:** "You're invited to <business> on FourStacks" and "Reset your FourStacks password".
- **Audit:** the existing `identity.invited`, `identity.invitation_renewed` and reset audit rows record `delivery` (email or manual). They never record the address beyond what is already stored, and never the link.

### HR access on the invitation

- **Migration `0483`** (lane B range) adds `console_tokens.hr_preset text NULL`. The value is one of `staff`, `manager`, `hr_team`, `payroll_preparer` or `payroll_reviewer`, and is allowed only on `kind = 'invite'`. `EXPECTED_SCHEMA_VERSION` stays the highest applied migration, which is `0500` today. The migration only adds 0483 below it.
- **Invite form:**
  - It gets an "HR access" select: No HR access, or Staff (the default). Manager, HR team, Payroll preparer and Payroll reviewer appear only when the current user is the client's HR owner.
  - Each option has a one-line explanation from the B2 copy.
  - Renewing an invitation keeps its preset.
- **My calls on the select:**
  - **No HR package:** shown only when the tenant has the `hr` package. Otherwise no preset is stored.
  - **Read-only invitees:** the default is No HR access, because HR changes are blocked for `read_only`.
  - **Knowing who the HR owner is:** the form learns it from the server, either through a flag on the invite page's data or the existing HR context. A platform admin is never the tenant's HR owner, so they see No HR access and Staff only.
  - **API default:** when `hr_preset` is omitted, `InviteIn` stores no preset, so existing callers and tests are unchanged. The form always sends the choice explicitly.
- **Who chose it (my call):** migration 0483 also adds `console_tokens.hr_preset_chosen_by uuid NULL`, the admin who chose the preset.
  - A renewal copies both the preset and the chooser, so renewing never changes who sponsored the access. A non-owner may renew a non-Staff invitation, but cannot change its preset.
  - The apply rules check the chooser, not the token's `created_by`.
- **On invite (identity side):**
  - The service rejects a non-Staff preset unless the inviter is the HR owner of `client_id`. This is checked through an existing HR read or a narrow security-definer check, with no new grants to `csa_identity` on HR tables.
  - It stores the preset on the token. The check is repeated on apply.
- **On acceptance (approach A):**
  1. The acceptance transaction commits as today (membership, password, session).
  2. Then the gateway calls one new HR path, for example `HrService.apply_invitation_access(token_id)`, which uses a new `SECURITY DEFINER` function, for example `public.hr_apply_invitation_access(p_client uuid, p_token uuid, p_rows jsonb)`.
  3. Before writing, the function checks all of these:
     - the token is `kind = 'invite'`, consumed, not revoked, and for `p_client`;
     - it has a non-null `hr_preset`;
     - its user is now an enabled member of `p_client`;
     - it has not been applied already. Idempotency comes from an `hr_operations` and `hr_authority_events` record keyed by the token id. **My call:** `subject_id` is the invitee, who is now a member. `hr_operations` requires its subject to be a tenant member, and a platform-admin inviter is not one. The chooser's id goes in the event `details` as `chosen_by`.
     - for any preset other than `staff`, the chooser is still the HR client owner of `p_client`. There is exactly one HR owner per tenant, a primary key on `hr_client_owners`.
     - the rows match the preset. One source of truth: the rows come from `csa/hr/access_switches.py` (`PRESETS`, `SWITCHES`). The function at least confines a non-owner inviter's rows to the Staff preset's (action, scope) pairs. The builder chooses how the SQL enforces this without duplicating the switch table by hand.
  4. Then it writes the grant, workflow and area rows with the same semantics as `access.save` and `private_access.save`, and records the authority event as `invitation_access`.
- **Failure:** if the apply fails, acceptance is not undone. The person is a member with no HR access, the failure is logged by category, and it can be retried.
  - "Who can do what" shows such people with "Access chosen on the invitation was not applied (<preset>)", and an **Apply** button for the HR owner.
  - **My call:** when the current HR owner presses Apply, their own authority stands in, so the preset applies even if the chooser has since lost owner status. The owner can grant any preset anyway.
  - A **Dismiss** action clears it without applying.
  - The list comes from a new owner-only definer read in 0483, a separate function. The return type of 0482's `hr_owner_access_summary` is unchanged.
- **Visibility:** Recent changes (B2) shows these events as "Set on invitation by <admin>".
  - **Event shape:** the `invitation_access` event `details` carry `user_id`, `preset`, the resulting `on` switches and `chosen_by`.
  - **Baseline:** the next `access.save` diff for that person uses this event as its baseline.
  - **Contract:** `AccessEvent` gains an `invitation` variant.
  - **Names:** the chooser's name is resolved by a definer read that includes platform admins (shown as "FourStacks admin") and disabled members ("Former member").
- **Platform admins** inviting into a tenant follow the same rules. They are not HR owners of the tenant, so they get No HR access or Staff only.

### Staging and secrets

- **Secret files:** they belong in the staging secret layout checked by `scripts/staging_secrets.py`. The files the owner created on 2026-10-29 at the top level of `/srv/csa-staging/secrets/` (`smtp_username`, `smtp_password`) move into that layout, for example `runtime/smtp_username` and `runtime/smtp_password`, with the ownership and permissions the checker expects.
  - Add a `staging_secrets.py` action (like `whatsapp-defaults`), or give the owner copy-ready `install` commands.
  - Wire both into the gateway's secrets in `deploy/compose.staging-pipeline.yml`, beside the WhatsApp secrets. That overlay gives the gateway its outbound `provider-egress` network, which the base file does not.
  - Secrets are read from `/run/secrets/<FIELD>` (`csa/core/settings.py`). Follow that convention rather than `*_FILE` variables.
- **Flag:** `INVITE_MAIL_ENABLED` stays false on staging until the owner switches it on after rollout, through a `CSA_STAGING_INVITE_MAIL` compose variable like the workspace assistant's.
- **Relay allow-list:** this host's IP is allow-listed. A future AWS deployment (program item O2) must add its outbound IP to the relay rule.

## Constraints

- Migration `0483` only, in lane B's range. `EXPECTED_SCHEMA_VERSION` stays an exact match to the highest applied migration.
  - Never enable FORCE RLS.
  - No new roles, and no new tables.
  - No new grants to `csa_identity` on HR tables, or to HR roles on identity tables, beyond the narrow security-definer functions.
- Raw tokens are never stored, logged or audited. `csa_identity` isolation checks stay as they are.
- **Tests:**
  - a fake SMTP transport, never the real relay;
  - snapshots of both email templates;
  - the manual fallback on each failure category;
  - no raw token in a response when `delivery = "email"`;
  - the HR-preset rules (Staff for any admin, others only for the HR owner, rechecked on apply);
  - apply idempotency and the revoked or unconsumed token refusals;
  - cross-tenant denial;
  - the "not applied" retry path;
  - the contract snapshot;
  - a browser check of the invite form's HR access select.
- **Local testing:** the changed files' tests one file at a time, the web checks and `make lint`. The PR's CI is the merge gate.

## Left to the builder

- Exact template wording, within the writing guide, and the HTML styling (plain, FourStacks blue accent, no images).
- How the SQL confines a non-owner inviter's rows without hand-duplicating the switch table.
- The exact config and secret file names and the compose wiring.
- Test names.
- **Docs:** `docs/CONSOLE_API.md` (the token responses and `delivery`), `docs/console_openapi.json`, and `docs/PROGRESS.md`. PROGRESS also gets the B2 (0482) and console redesign (0500) staging rollout records from 2026-10-28 and 2026-10-29, which are still missing.
