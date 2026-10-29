# Invitation and Reset Emails, and HR Access Chosen on the Invitation: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Email invitation and reset links from `no-reply@fourstacks.my`, falling back to today's copy-the-link flow. Let the inviting admin choose the new person's HR access, and apply it the moment they accept.

**Architecture:**
- **Migration 0483** adds `hr_preset` and `hr_preset_chosen_by` to `console_tokens`, plus four narrow `SECURITY DEFINER` HR functions: read the invitation preset, apply it, list pending ones for the owner, and resolve actor names.
- **Mail:** a small mailer (`csa/console/mailer.py`) plus templates (`csa/console/mail_templates.py`) send after the identity transaction commits.
  - The route layer (`csa/console/auth.py`) gathers the business name, the time zone and the inviter's HR-owner status from the app pool, because `csa_identity` stays isolated.
- **HR:** a new HR service path (`csa/hr/invitation_access.py`) applies the preset right after acceptance. The B2 "Who can do what" page gains a "not applied" list, plus a Recent-changes variant.

**Tech Stack:** PostgreSQL 16, psycopg 3 async, FastAPI, Pydantic v2, `smtplib` (stdlib) in a thread, pytest with Testcontainers, Next.js with TypeScript, `node --test`, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-29-invite-mail-and-access-design.md` (owner-approved 2026-10-29).

## Global Constraints

**Migration and schema version**
- The migration file is `db/migrations/0483_invitation_hr_access.sql`.
- `EXPECTED_SCHEMA_VERSION` in `apps/gateway/main.py` stays `"0500"`, the highest applied. 0483 sorts below it and `scripts/migrate.py` applies it.
- Tests import the constant and never hardcode it.
- Never FORCE RLS. No new tables. No new roles. No new grants to `csa_identity` on any non-`console_*` table or `SECURITY DEFINER` function: `db/sql/console/identity/readiness.sql` must still pass.
- New definer functions use `SET search_path=pg_catalog,public,pg_temp`. `EXECUTE` goes to `csa_app` only, revoked from `PUBLIC`, `csa_ro` and `csa_identity`.

**Presets**
- The preset ids are exactly `staff`, `manager`, `hr_team`, `payroll_preparer` and `payroll_reviewer`, the B2 `PresetId` in `csa/hr/access_switches.py`. Rows always come from `rows(PRESETS[p], EMPTY)` there.
- Any inviting admin may choose none or `staff`. The other presets are allowed only when the chooser is the tenant's HR owner, `hr_client_owners` with `PRIMARY KEY(client_id)`. This is checked at invite time and again at apply time.
- The form default is `staff`, and `none` for a `read_only` invitee. The select is shown only when the tenant has the `hr` package.

**Tokens and secrecy**
- Raw tokens are never stored, logged, audited or put in exception text.
- When `delivery == "email"`, the response carries no raw token.
- Links keep the existing form `<console origin>/invite#token=<raw>`.

**Mail**
- The relay is `smtp-relay.gmail.com:587`, STARTTLS required, with SMTP auth.
- The sender is `FourStacks <no-reply@fourstacks.my>`.
- Each send runs in `asyncio.to_thread` with a 10 s timeout.
- Failures are logged by category only: `smtp_disabled`, `smtp_auth_failed`, `smtp_refused`, `smtp_timeout` or `smtp_error`. Logs never carry the address, link or body.

**Configuration**
- Settings are read from `/run/secrets/<FIELD>` (`csa/core/settings.py`).
- New fields:
  - `INVITE_MAIL_ENABLED: bool = False`
  - `INVITE_MAIL_FROM: str = "FourStacks <no-reply@fourstacks.my>"`
  - `SMTP_HOST: str = "smtp-relay.gmail.com"`
  - `SMTP_PORT: int = 587`
  - `SMTP_USERNAME: SecretStr | None = None`
  - `SMTP_PASSWORD: SecretStr | None = None`
- Mail is active only when the flag is true and both secrets are present.

**Copy, testing and tooling**
- Copy follows `docs/WRITING_GUIDE.md`, in Malaysian English. Reset expiry times show in `Asia/Kuala_Lumpur`; invitation expiry times show in the tenant's `clients.timezone`.
- Synthetic fixtures and disposable test databases only. Tests use a fake SMTP transport and never contact the real relay.
- **Tooling:**
  - **Python tests:** run one test file per invocation, from the worktree root:
    `bash /tmp/claude-1000/-home-amsyrzmi-CSAV2/7d09c8ff-ee80-4525-b21a-195503f98d9c/scratchpad/ci.sh /home/amsyrzmi/CSAV2/.venv/bin/pytest -q -p no:cacheprovider <file>`
    Never run the full suite; the PR's CI is the merge gate.
  - **Python edits:** an editor hook reflows `.py` files to 120 columns. Edit through a script or heredoc, then run `ruff format` and check `git diff`.
  - **Web checks:** run from `CSA_WebConsole/csa-console/web` with `PATH=/tmp/fourstacks-toolbin:$PATH` and `CSA_CONSOLE_API_ORIGIN` set to a dummy value: `npm run lint`, `npm run typecheck` and `npm test`. New `lib/` and test files go in `scripts/check-isolated.mjs`'s allowlist.
  - **OpenAPI:** after a route or contract change, run `python -m scripts.export_console_openapi`, then `tests/test_console_contract_snapshot.py`.
  - `make lint` before each task's last commit.
- **Keep out of these files:** do not touch `ServicesWorkspace.tsx`, `RuntimeWorkspace.tsx`, `components/console/ui.tsx`, the avatar components (console redesign phase 2), or `.github/workflows/ci.yml` (chores).

## Review Focus

1. **A non-owner admin renews an HR-owner's Manager invitation.** The renewed token keeps `hr_preset = 'manager'` and `hr_preset_chosen_by` = the HR owner, and acceptance applies Manager. Pinned in Task 1 by `test_renewal_keeps_preset_and_chooser`, and in Task 3 by `test_renewed_manager_invitation_applies_for_the_original_owner`.
2. **The HR owner changes between invite and acceptance.** A non-Staff preset is not applied and shows as pending. The new owner's Apply works; Dismiss clears it. Pinned in Task 3 by `test_chooser_no_longer_owner_leaves_it_pending_and_owner_apply_works`.
3. **A platform admin invites into a tenant.** Only none or Staff are accepted. Acceptance applies Staff, with `subject_id` = the invitee, and Recent changes names the chooser "FourStacks admin". Pinned in Task 1 by `test_platform_admin_may_choose_only_staff`, and in Task 3 by `test_platform_admin_staff_invitation_applies_and_is_named`.
4. **The relay times out or rejects auth.** The invite still succeeds with `delivery = "manual"` and the token, nothing is logged beyond the category, and the user and token exist once (no retry duplicates). Pinned in Task 2 by `test_mail_failure_falls_back_to_manual_without_leaking`.
5. **A tenant without the `hr` package.** A preset in the body is refused with 422 `hr_not_available`, and the form hides the select. Pinned in Task 2 by `test_preset_refused_without_hr_package`, and in Task 4 by the node test `hrAccessOptions hides presets without the hr package`.

---

### Task 1: Migration 0483 (token columns) and the identity side of presets

**Files:**
- Create: `db/migrations/0483_invitation_hr_access.sql`. This task adds only the columns; Task 3 appends the HR functions to the same file.
- Modify: `csa/console/records.py` (`TokenRecord` gains `hr_preset: PresetId | None` and `hr_preset_chosen_by: UUID | None`).
- Modify: `csa/console/contracts.py` (`InviteIn` gains `hr_preset: Literal["staff","manager","hr_team","payroll_preparer","payroll_reviewer"] | None = None`; `InvitationOut` gains `hr_preset`).
- Modify: `db/sql/console/identity/token_create.sql` (it stores both new columns), and `token_list.sql` (it returns `hr_preset`).
- Modify: `csa/console/identity.py` (`invite`, `renew_invitation`).
- Test: `tests/test_console_identity.py`.

**Interfaces:**
- Produces:
  - `console_tokens.hr_preset text NULL CHECK (hr_preset IN (…five ids…))`;
  - `console_tokens.hr_preset_chosen_by uuid NULL REFERENCES console_users(id)`;
  - `CHECK (kind = 'invite' OR (hr_preset IS NULL AND hr_preset_chosen_by IS NULL))`;
  - `CHECK ((hr_preset IS NULL) = (hr_preset_chosen_by IS NULL))`.
- Produces `IdentityService.invite(actor, data: InviteIn, *, may_grant_hr: bool = False) -> IssuedTokenOut`. It raises `HTTPException(403, "hr_preset_requires_hr_owner")` when `data.hr_preset not in (None, "staff")` and not `may_grant_hr`. It stores `hr_preset` and sets `hr_preset_chosen_by = actor.user_id` when a preset is given.
- `renew_invitation` copies `hr_preset` and `hr_preset_chosen_by` from the old token unchanged, whoever renews.
- `IssuedTokenOut` stays as today in this task.

**Steps**

- [ ] **Step 1: Write the failing tests** in `tests/test_console_identity.py`, following its existing fixtures:
  - `test_invite_stores_preset_and_chooser`: a tenant admin invites with `hr_preset="staff"`. The token row has `hr_preset='staff'` and `hr_preset_chosen_by` = that admin.
  - `test_non_owner_cannot_choose_manager`: `hr_preset="manager"` with `may_grant_hr=False` → 403 `hr_preset_requires_hr_owner`, and no user or token is created.
  - `test_hr_owner_may_choose_manager`: with `may_grant_hr=True`, it is stored.
  - `test_platform_admin_may_choose_only_staff` (Review Focus 3): through `/admin/invites`, a platform admin gets 403 for `manager`, and `staff` succeeds.
  - `test_renewal_keeps_preset_and_chooser` (Review Focus 1): renewing by a different tenant admin keeps `hr_preset` and `hr_preset_chosen_by`, while `created_by` is the renewer.
  - `test_omitted_preset_stores_none`: an invite without the field stores NULLs, so the existing behaviour is unchanged.
  - `test_reset_tokens_cannot_carry_a_preset`: inserting a reset token with a preset through the owner pool violates the CHECK.
  - `test_identity_isolation_still_holds`: `IdentityService.check_ready()` passes after 0483, so `readiness.sql` is unchanged.
- [ ] **Step 2: Run the file and see the new tests fail.**
- [ ] **Step 3: Implement.**
  - The migration header follows the 0480–0482 style: purpose, pre-check, rollback (drop the columns and constraints).
  - Use `ADD COLUMN IF NOT EXISTS`.
  - The route wiring that computes `may_grant_hr` is Task 2. Here the service takes it as a parameter, and both routes pass `False` until then.
- [ ] **Step 4: Run `tests/test_console_identity.py`, `tests/test_console_identity_gateway.py`, `tests/test_migrate.py` and `tests/test_health.py`, one per run. Then regenerate OpenAPI and run the contract snapshot.**
- [ ] **Step 5: Commit.** Message: `feat(console): invitations carry the chosen HR preset and who chose it`

### Task 2: The mailer, the templates, delivery, and the route wiring

**Files:**
- Create: `csa/console/mailer.py`, `csa/console/mail_templates.py`
- Modify:
  - `csa/core/settings.py` (the six fields in Global Constraints);
  - `apps/gateway/main.py` (build the `Mailer` and set `app.state.mailer`);
  - `csa/console/contracts.py` (`IssuedTokenOut.token: str | None`, and `delivery: Literal["manual","email"] = "manual"`);
  - `csa/console/identity.py` (`invite`, `renew_invitation` and `issue_reset` take a send callback);
  - `csa/console/auth.py` (it gathers the mail facts and `may_grant_hr`, and checks the `hr` package);
  - the identity audit calls, which record `delivery`.
- Test: `tests/test_console_mail.py` (new), `tests/test_console_identity_gateway.py`.

**Interfaces:**
- **`csa/console/mailer.py`:**
  - `MailFailure = Literal["smtp_disabled","smtp_auth_failed","smtp_refused","smtp_timeout","smtp_error"]`
  - `@dataclass(frozen=True) class OutgoingMail: to: str; subject: str; text: str; html: str`
  - `class Transport(Protocol): def send(self, mail: OutgoingMail, sender: str) -> None`. `SmtpTransport(host, port, username, password, timeout=10)` uses `smtplib.SMTP`, `starttls(ssl.create_default_context())`, `login`, then `send_message`.
  - `class Mailer`:
    - `Mailer(transport: Transport | None, sender: str)` (`None` means disabled);
    - `async send(mail) -> Literal["sent"] | MailFailure`, via `asyncio.to_thread`, mapping exceptions to categories (`SMTPAuthenticationError` → auth_failed, `SMTPRecipientsRefused`/`SMTPSenderRefused`/`SMTPDataError` → refused, `TimeoutError`/`socket.timeout` → timeout, other `OSError`/`SMTPException` → error);
    - `Mailer.from_settings(settings) -> Mailer`.
- **`csa/console/mail_templates.py`:**
  - `invitation_mail(*, to, invitee_name, inviter_name, business_name, link, expires_at, tz) -> OutgoingMail`
  - `reset_mail(*, to, name, link, expires_at) -> OutgoingMail`, with expiry in `Asia/Kuala_Lumpur`.
  - Subjects are exactly `You're invited to {business_name} on FourStacks` and `Reset your FourStacks password`.
  - Dates use the format `27 Oct 2026, 09:15`.
- **Identity:** `IdentityService.invite(actor, data, *, may_grant_hr=False, notify: Callable[[str, datetime], Awaitable[Literal["sent"] | MailFailure]] | None = None)`.
  - After the transaction commits it calls `notify(raw_link, expires_at)`.
  - On `"sent"` it returns `IssuedTokenOut(id, token=None, expires_at, delivery="email")`; otherwise it returns `delivery="manual"` with the token.
  - `renew_invitation` and `issue_reset` follow the same pattern.
  - The audit row records `delivery`.
- **`auth.py` (both invite routes, renew, reset):**
  - It builds `notify` from `request.app.state.mailer` and facts read through `request.app.state.pool`, in a `tx` with the tenant's `app.client_id`:
    - `clients.name` and `clients.timezone`;
    - the inviter's `display_name`, from the actor;
    - `may_grant_hr`, true only if `hr_client_owners.user_id` = the actor for that client. Read it through a small app-pool SQL file, `db/sql/console/invite_facts.sql`.
    - `hr_client_owners` has the RLS policy `hr_owner_self` (0380), so the app role sees the owner row only when `app.hr_user_id` is the actor. Set `app.client_id` and `app.hr_user_id` to the actor for that transaction, the way `HrService._access` does, rather than reading the table without them. A platform admin never matches.
  - A body with `hr_preset` for a tenant without the `hr` package gets 422 `hr_not_available`. Reuse the existing package check (`require_package`, as `csa/hr/api.py:190` uses it).
  - The link uses the identity config's first console origin, in the existing `/invite#token=` form.

**Steps**

- [ ] **Step 1: Write the failing tests:**
  - `tests/test_console_mail.py`, which is pure and needs no database:
    - `test_invitation_template_snapshot` and `test_reset_template_snapshot`: exact subject, text and HTML for fixed synthetic inputs.
    - `test_mailer_maps_exceptions_to_categories`: a fake transport raising each exception gives the matching category.
    - `test_disabled_mailer_reports_smtp_disabled`.
    - `test_from_settings_requires_flag_and_both_secrets`.
  - `tests/test_console_identity_gateway.py`, with a fake `Transport` on `app.state.mailer`:
    - `test_invite_emails_and_hides_the_token`: 201, `delivery == "email"`, `token is None`. The fake received one mail to the invitee whose text contains `/invite#token=`, the business name and the inviter's name.
    - `test_mail_failure_falls_back_to_manual_without_leaking` (Review Focus 4): the fake raises `TimeoutError`. Expect `delivery == "manual"` with a token, exactly one user and one token row, and captured logs containing `smtp_timeout` but neither the email address nor `#token=`.
    - `test_renew_and_reset_email`: both email when sent, and fall back otherwise.
    - `test_owner_inviter_may_choose_manager_through_the_route`: the tenant's HR owner, as a tenant admin, can choose `manager` through `/team/invites`; another tenant admin gets 403.
    - `test_preset_refused_without_hr_package` (Review Focus 5).
    - `test_audit_records_delivery`.
- [ ] **Step 2: Run the two files and see the new tests fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, one file per run:**
  - `tests/test_console_mail.py`, `tests/test_console_identity_gateway.py`, `tests/test_console_identity.py`, `tests/test_console_identity_config.py`;
  - then regenerate OpenAPI and run `tests/test_console_contract_snapshot.py`;
  - then `make lint`.
- [ ] **Step 5: Commit.** Message: `feat(console): email invitation and reset links with a manual fallback`

### Task 3: Apply the preset on acceptance; pending list, apply and dismiss; Recent changes

**Files:**
- Modify: `db/migrations/0483_invitation_hr_access.sql`. Append the four functions below.
- Create: `csa/hr/invitation_access.py`, `db/sql/hr/invitation_preset.sql`, `db/sql/hr/invitation_apply.sql`, `db/sql/hr/invitation_pending.sql` and `db/sql/hr/actor_names.sql`.
- Modify:
  - `csa/console/identity.py` (`accept_token` also returns the consumed token's `id` and `client_id`);
  - `csa/console/auth.py` (`accept_invite` calls the HR apply after acceptance commits, and logs the failure category);
  - `csa/hr/access_contracts.py` (`AccessEvent` gains `source: Literal["page","invitation"]` and `preset: PresetId | None`; new `PendingInvitationAccess` and `PendingInvitationPage`);
  - `csa/hr/access_page.py` (`recent()` includes `invitation_access` events, and `actor_names` replaces the summary-only name map);
  - `db/sql/hr/access_event_page.sql` and `access_event_previous.sql` (include `invitation_access`);
  - `csa/hr/api.py` (three routes).
- Test: `tests/test_hr_invitation_access.py` (new), `tests/test_hr_access_page.py`.

**Interfaces (SQL; all `SECURITY DEFINER`, `EXECUTE` to `csa_app` only):**
- **`public.hr_invitation_preset(p_client uuid, p_token uuid)`** returns `(user_id uuid, preset text, chosen_by uuid, chooser_is_owner boolean, applied boolean)`.
  - It returns nothing unless the token is `kind='invite'`, `client_id = p_client = app.client_id`, consumed, has a preset, and its user is an enabled member.
  - `applied` is true when an `hr_operations` row with `request_key = p_token` and `operation IN ('invitation_access','invitation_access.dismiss')` exists for `p_client`.
- **`public.hr_apply_invitation_access(p_client uuid, p_token uuid, p_rows jsonb, p_owner_override boolean)`** returns `jsonb`, the receipt.
  - It re-checks everything above, inside a lock on the token row.
  - It refuses (`RAISE` with a code that maps to `HrPolicyError`) in these cases:
    - already applied;
    - the preset is not `staff`, the chooser is not the current HR owner, and `p_owner_override` is false;
    - `p_owner_override` is true but `public.hr_is_owner(p_client)` is false;
    - the chooser is not the HR owner and some row in `p_rows` falls outside the Staff preset's (action, scope) pairs.
  - The Staff pairs are hard-coded in this one function, which keeps it the only SQL copy. A Python test asserts they equal `rows(PRESETS["staff"], EMPTY)`.
  - Then it replaces the member's grant, workflow and area rows with the union of their current rows and `p_rows`. It adds, never removes, so it is safe if the HR owner already gave access.
  - It records `hr_operations` and `hr_authority_events` with `subject_id` = the invitee, `request_key = p_token`, operation `invitation_access`, and details `{user_id, preset, on, chosen_by}`.
- **`public.hr_dismiss_invitation_access(p_client uuid, p_token uuid)`** is guarded by `hr_is_owner`. It records operation `invitation_access.dismiss` with `subject_id` = the owner.
- **`public.hr_owner_pending_invitation_access(p_client uuid)`** is guarded by `hr_is_owner`. It returns the consumed invitations with a preset and no applied or dismissed record: `(token_id, user_id, display_name, preset, chosen_by, chooser_name, accepted_at)`.
- **`public.hr_owner_actor_names(p_client uuid, p_ids uuid[])`** is guarded by `hr_is_owner`. It returns `(user_id, display_name, is_platform_admin, is_enabled_member)` for those ids, including platform admins and disabled members.

**Interfaces (Python):**
- `csa/hr/invitation_access.py`: `class InvitationAccess(core: HrService)`
  - `async apply_on_accept(client_id: UUID, token_id: UUID) -> Literal["applied","none","pending","failed"]`. It runs in `tx(pool, client_id)` with no HR session. It reads the preset, computes `rows(PRESETS[preset], EMPTY)` and applies with `p_owner_override=False`. If the chooser is not the owner it returns `"pending"`; `"none"` means no preset.
  - `async pending(session) -> PendingInvitationPage`, owner only.
  - `async apply(session, token_id) -> AccessPersonResult`, owner only, with `p_owner_override=True`.
  - `async dismiss(session, token_id) -> None`, owner only.
- **Routes** (existing `/console/v1/hr` router):
  - `GET /access/invitations` → `PendingInvitationPage`
  - `POST /access/invitations/{token_id}/apply` → `AccessPersonResult`
  - `POST /access/invitations/{token_id}/dismiss`, status 204
- **`AccessEvent`:**
  - Invitation events have `source="invitation"`, `preset` set, and `turned_on` = the preset's switches, with an empty `turned_off`.
  - `actor_name` comes from `hr_owner_actor_names`: a platform admin gives `"FourStacks admin"`, a disabled member gives `None` (the page shows "Former member").
  - The next `access.save` diff for that person uses this event's `on` as its baseline.

**Steps**

- [ ] **Step 1: Write the failing tests** in `tests/test_hr_invitation_access.py`, using `HrWorld` (`tests/test_hr_persistence.py`) plus identity invites through `world.call` or the identity service, as `tests/test_console_identity_gateway.py` does:
  - `test_staff_invitation_applies_on_accept`: after acceptance the member's rows equal `rows(PRESETS["staff"], EMPTY)`. There is one `hr_operations` row (`invitation_access`, subject = the invitee), and `apply_on_accept` returns `"applied"`.
  - `test_apply_is_idempotent_and_refuses_twice`: a second `apply_on_accept` for the same token writes nothing.
  - `test_renewed_manager_invitation_applies_for_the_original_owner` (Review Focus 1).
  - `test_chooser_no_longer_owner_leaves_it_pending_and_owner_apply_works` (Review Focus 2). Change `hr_client_owners` through the owner pool between invite and accept. The result is `"pending"`. `GET /access/invitations` lists it for the new owner. `POST …/apply` gives `saved`, and the person then has Manager rows. A second pending item is dismissed and leaves the list.
  - `test_non_owner_rows_outside_staff_are_refused`: calling `hr_apply_invitation_access` directly with a non-owner chooser and a payroll row raises, and nothing is written.
  - `test_staff_pairs_in_sql_match_python`: read the hard-coded pairs, through a `pg_get_functiondef` parse or a tiny probe, and compare them with `rows(PRESETS["staff"], EMPTY)`.
  - `test_unconsumed_or_other_tenant_token_is_refused`: `hr_invitation_preset` returns nothing for an unaccepted token or for another tenant's.
  - `test_existing_access_is_kept_not_replaced`: the owner gives `payroll_review` before the apply. After the apply, `payroll_review` remains and Staff is added.
  - `test_platform_admin_staff_invitation_applies_and_is_named` (Review Focus 3): Recent changes shows `source == "invitation"` and `actor_name == "FourStacks admin"`.
  - `test_pending_and_actions_are_owner_only`: an admin or staff member gets 404 on all three routes.
  - `test_accept_still_succeeds_when_apply_fails`: monkeypatch the apply to raise. `/invites/accept` returns 200, the member exists, and the log carries the category.
- [ ] **Step 2: Run the file and see it fail.**
- [ ] **Step 3: Implement the SQL functions, the module, the accept wiring, the routes and the Recent-changes changes.**
- [ ] **Step 4: Run, one file per run:**
  - `tests/test_hr_invitation_access.py`, `tests/test_hr_access_page.py`, `tests/test_hr_access_summary.py`, `tests/test_console_identity.py`, `tests/test_console_identity_gateway.py`, `tests/test_hr_persistence.py`;
  - then regenerate OpenAPI and run `tests/test_console_contract_snapshot.py` and `tests/test_hr_contracts.py`;
  - then `make lint`.
- [ ] **Step 5: Commit.** Message: `feat(hr): apply the HR access chosen on an invitation when it is accepted`

### Task 4: The console pages

**Files:**
- Modify: `CSA_WebConsole/csa-console/web/components/console/TeamManagement.tsx`
  - Add an "HR access" select to the invite form.
  - `showToken` handles `delivery === "email"` with no token: "Invitation emailed to <email>". On `manual`, keep today's copy box, with "We couldn't send the email. Copy this link and send it yourself."
- Modify: `components/console/InvitationList.tsx`. Renew shows the same delivery outcomes, and the list shows each invitation's HR access.
- Modify: the admin reset UI (wherever `/admin/users/{id}/reset` is called; grep for it). It handles the same delivery outcomes.
- Modify: `components/console/HrAccessPage.tsx` and/or `HrAccessMore.tsx`.
  - Add a "Waiting for access chosen on the invitation" panel listing `GET /access/invitations`. Each row has Apply and Dismiss buttons, and plain text naming the preset and who chose it.
  - Recent changes renders invitation events as "Set on invitation by <name>: <preset label>".
- Create: `CSA_WebConsole/csa-console/web/lib/invite-access.ts` with:
  - `hrAccessOptions({ hasHrPackage, isHrOwner, role }) → {value, label, explanation}[]`;
  - `defaultHrAccess(role) → "staff" | "none"`;
  - `deliveryMessage(result, email) → string`.
- Modify: `lib/hr-access-api.ts` (types and calls for the three routes), `lib/hr-access-copy.ts` (preset labels and event text), and `scripts/check-isolated.mjs` (add `lib/invite-access.ts` and the new test).
- Test: `CSA_WebConsole/csa-console/web/tests/invite-access.test.mjs` (new), and extend `tests/hr-access-api.test.mjs`.

**Interfaces:**
- Consumes Tasks 1–3: `InviteIn.hr_preset`, `IssuedTokenOut{token|null, delivery}`, `InvitationOut.hr_preset`, the pending, apply and dismiss routes, and `AccessEvent.source`/`preset`.
- **Where the form learns its facts:**
  - `isHrOwner` comes from `/console/v1/hr/context` (`client_owner`) for tenant members. It is always false for platform admins.
  - `hasHrPackage` comes from the existing package list the console already reads for navigation.
- **Option labels:** "No HR access", "Staff", "Manager", "HR team", "Payroll preparer" and "Payroll reviewer". Each explanation reuses `PRESET_COPY` in `lib/hr-access-copy.ts`.

**Steps**

- [ ] **Step 1: Write the failing node tests** in `tests/invite-access.test.mjs`:
  - `hrAccessOptions hides presets without the hr package` (Review Focus 5): it returns `[]`.
  - Non-owners get exactly `["none","staff"]`, and owners get all six.
  - `defaultHrAccess("read_only") === "none"` and `defaultHrAccess("tenant_staff") === "staff"`.
  - `deliveryMessage` gives the two exact sentences above.

  Extend `hr-access-api.test.mjs`: the pending, apply and dismiss URLs carry `?client_id=`, and an invitation event's text names "FourStacks admin".
- [ ] **Step 2: Run `npm test` and see the new tests fail.**
- [ ] **Step 3: Implement.** Use the existing console components. Keep existing `data-testid`s. New ones: `invite-hr-access`, `invite-delivery`, `hr-access-pending`, `hr-access-pending-apply-<token>` and `hr-access-pending-dismiss-<token>`.
- [ ] **Step 4: Run `npm run lint`, `npm run typecheck`, `npm test`, then `make lint`.**
- [ ] **Step 5: Commit.** Message: `feat(console): choose HR access on invitations and show emailed delivery`

### Task 5: Staging wiring, the browser test and docs

**Files:**
- Modify: `deploy/compose.staging-pipeline.yml`. Add the gateway secrets `SMTP_USERNAME` and `SMTP_PASSWORD`, beside the WhatsApp secrets, sourced from `${CSA_STAGING_SECRET_DIR}/runtime/smtp_username` and `runtime/smtp_password`. Set the gateway environment `INVITE_MAIL_ENABLED: ${CSA_STAGING_INVITE_MAIL:-false}`.
- Modify: `scripts/staging_secrets.py`. Add an `smtp-install` action, modelled on `whatsapp-defaults`.
  - It moves `smtp_username` and `smtp_password` from the top of the secret dir into `runtime/`, with the ownership and mode `check` expects, without printing their contents.
  - `check` passes afterwards, and also passes when the files are absent and mail is off.
- Modify: `docs/RUNBOOK.md` or `deploy/STAGING_PIPELINE.md`. Add a short "Invitation mail" section: install the secrets, switch the flag on, and how to verify.
- Create: `tests/test_invite_access_browser.py`, modelled on `tests/test_hr_access_browser.py`. Stubs:
  - the HR owner sees six HR-access options, and a tenant admin sees two;
  - an emailed invite shows "Invitation emailed to …" with no link;
  - a failed send shows the copy box;
  - the HR access page lists one pending invitation, and Apply removes it.
- Modify: `docs/CONSOLE_API.md` (`InviteIn.hr_preset`, `IssuedTokenOut.delivery` and the optional token, the three HR routes, invitation events in Recent changes) and `docs/console_openapi.json` (regenerated).
- Modify: `docs/PROGRESS.md`. Add three entries at the top.
  1. **B4 (this branch):** what was built, migration 0483, the owner decisions of 2026-10-29, and the focused test counts from Tasks 1–4.
  2. **The B2 staging rollout, 2026-10-28:**
     - main `3ec6963`, PR #44; CI 40719373802, publish 40724856740;
     - `csa-python@sha256:5f047e4f4e5615b58c260b7188843f982bc0cb97a09be78647e31ba53c4fed87` and `csa-frontend@sha256:f1bae9faa6fb2b6ab29a20171d783862eeaad999d57e50b2ec22a7ea1c6c0691`;
     - 0482 applied through the `migrate` one-shot; gateway, worker, delivery and frontend recreated and healthy;
     - `/readyz` ok, and `/console/v1/hr/access/people` returned 401 unauthenticated.
  3. **The console redesign phase 1 staging rollout, 2026-10-29:**
     - main `b4fc900`, PR #45; CI 40803720107, publish 40806120782;
     - `csa-python@sha256:a7f6c53aa310ba9a9127a67c8a0f9d6c500c8e65de91cb707063e3f3f62354b9` and `csa-frontend@sha256:d9019024db366eeb7e016b6671432644f47d4d4b023dfea511f5be64dfc7ad68`;
     - 0500 applied, with 0 of 13 contacts having a Telegram username yet;
     - the gateway first reported unhealthy, because its 3 s health checks timed out while host swap was full. It then went healthy, and the loop was re-run.
     - All four app services healthy; `/readyz` ok; `/console/v1/inbox/conversations` returned 401 unauthenticated.

**Steps**

- [ ] **Step 1: Write `tests/test_staging_secrets.py` cases (extend the existing file if there is one):**
  - `smtp-install` moves the files with the expected mode and owner;
  - it is a no-op when they are already installed;
  - it refuses a symlink;
  - it never prints the file contents.

  Write the browser test.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement the compose, script and docs changes.** Validate the compose file with `docker compose … config --quiet` using the staging environment variables, in the same form as the rollout commands.
- [ ] **Step 4: Run, one per run:** `tests/test_staging_secrets.py`, then `tests/test_invite_access_browser.py` (once, if memory allows; otherwise say so, because CI runs it). Then `make lint`.
- [ ] **Step 5: Commit.** Message: `chore(staging): wire invitation mail secrets; browser test; record B2 and redesign rollouts`
