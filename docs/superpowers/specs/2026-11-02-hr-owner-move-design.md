# Moving the HR owner: design

Status: approved 2026-11-02. Builds on PR #63 (People & HR 1b, migration 0521). Migration **0522**.

## Why

Every company has one HR owner, who controls who gets HR access. Today only `scripts/bootstrap_hr_owner.py` can set the first one, and nothing can replace one. So an HR owner who leaves is a dead end: 1b refuses to mark them as leaving and tells the customer to contact support, and support has no tool for it.

The company should be able to fix this itself, without FourStacks and without a database change.

## Goals

- A tenant admin can set the first HR owner and move ownership to another member, from the Team page.
- A platform admin can do the same for any company, from the admin Team page, so "contact support" also works.
- The move happens at once, as one transaction, and the old owner drops straight to their ordinary HR switches.
- The move can't happen quietly: emails to the people affected, and a permanent record.
- The HR owner's refusal in 1b becomes advice the customer can follow.

## Non-goals

- Several HR owners per company. `hr_client_owners` keeps one row per company.
- An accept step for the new owner.
- Making the title itself carry all owner powers (rejected approach C). A later job, if wanted.
- Showing tenant admins any HR data. They learn who the owner is, and nothing else.
- Changing the bootstrap script, which stays for isolated environments and seeding.

## Anti-goals

- The assistant moving ownership. There is no dock action for this and no planner path to it.
- A tenant admin gaining HR access by holding that role. They gain it only by openly making themselves the owner.
- A move that leaves the company without a working owner, or a new owner with the title but not the powers.
- HR data, tokens or addresses in logs, or any HR data in the emails.

## Owner decisions (2026-11-02)

1. Tenant admins may choose the HR owner, **including themselves**, as long as everyone is notified. This deliberately relaxes the rule "console roles create no HR permissions" for this one action. The rule still holds everywhere else.
2. The old owner drops **straight away**, with no accept step. This also works when the old owner has already gone.
3. **Approach A**: one dedicated action.
4. A new owner does **not** get payroll automatically. They turn it on for themselves or someone else on the access page, and that change appears in Recent changes.

## Approaches

- **A. A dedicated "move HR owner" action (chosen).** One new database function does the whole move, and a Team page control calls it. It leaves the existing HR permission checks untouched. The cost is that any new owner-only grant added later must also be moved; a test pins the list.
- **B. A new operation in the HR actions system (`hr_admin_apply`).** Retries and receipts come built in, but that system is for people with HR authority. Letting tenant admins in by console role breaks the rule above and adds risk to every other HR action.
- **C. The title alone carries the powers.** This is the cleanest long term, but it rewrites permission checks in many definer functions to deliver one feature. A mistake could hand out or remove the wrong HR access.

## How it works today

- **Title:** `hr_client_owners` has `client_id` as primary key, plus `user_id`, `approved_by` and `approval_reference`.
- **Powers:** some come from the title (`access.client_owner`: the access page, linking accounts, the leaving guards). Others come from separate rows that only count while the person is the owner: `hr_grants` and `hr_workflow_grants` with `scope='client_owner'`. The checks need both the row and the title, in three places: `hr_case_access` (0400), `hr_resource_visible` (0380) and the training reminders (0410).
- **First owner:** the bootstrap script inserts only the title. `scripts/seed_staging.py` adds the rows separately through `access.save`.
- **Read-only:** `csa/hr/api.py` refuses every HR write from a `read_only` member, so a read-only owner couldn't act.
- **Pools:** the identity pool (`csa_identity`) never touches domain tables. Routes that need them use the app pool (`csa_app`), as `_invite_facts` does in `csa/console/auth.py`.
- **Records:** `hr_authority_events` feeds the access page's Recent changes. It has a foreign key to `hr_operations`, whose `subject_id` must be a member of the company.

## The move

### Who can do it

A tenant admin of that company, or a platform admin. Checked twice: in the route with the existing `require_member_admin` rule, which already lets a platform admin through, and again inside the function against `console_memberships` and `console_users`, using the actor from `app.hr_user_id`. The HR package must be active (`require_package`); otherwise the control doesn't show and the route answers 422 `hr_not_available`.

### Who can be chosen

An enabled member of the company with an enabled account who has changed their password (as bootstrap requires), and who is:

| Refused | Code | Sentence |
|---|---|---|
| Read only | `owner_read_only` | "A read-only member can't be the HR owner. Change their role first." |
| Marked as leaving, or their linked person isn't active | `owner_leaving` | "This person is leaving, so they can't be the HR owner. Choose someone else." |
| Not an active member, or still on their first password | `owner_unavailable` | "This person can't be the HR owner yet. They need to sign in and change their password first." |
| Already the owner | `owner_unchanged` | "This person is already the HR owner." |
| The owner changed since the page loaded | `owner_changed` | "The HR owner changed since you opened this page. Reload to see who it is now." |

An employee record is not required, matching bootstrap. The Team page tells the new owner's admin nothing about HR records beyond these refusals.

### What the function does

`public.hr_owner_move(p_client, p_actor, p_new_owner, p_expected_owner, p_request_key, p_fingerprint)`: SECURITY DEFINER with a pinned `search_path`, execute granted to `csa_app` only. No new roles, and RLS is never FORCEd. In one transaction it:

1. Takes the company's HR owner lock (the same key bootstrap uses), then locks the owner row.
2. Returns the first receipt if this request key already ran with the same fingerprint, and refuses `hr_idempotency_conflict` if the fingerprint differs.
3. Checks the actor, the target and `p_expected_owner` (NULL meaning "no owner yet").
4. Writes the owner row: `user_id` = the new owner, `approved_by` = the actor, `approved_at` = now, `approval_reference` = `console:<request key>`.
5. Deletes the old owner's `scope='client_owner'` rows from `hr_grants` and `hr_workflow_grants`. Their other rows stay, including any directory, HR team, payroll or private-area access, until the new owner turns them off on the access page. The old owner may simply be moving to another HR role, and marking them as leaving removes it all.
6. Gives the new owner the **standard owner set**, keeping any rows they already hold:
   - `hr_grants`, `scope='client_owner'`: `training.decide`
   - `hr_workflow_grants`, `scope='client_owner'`: `recruitment`, `attendance` and `leave`, each with read, prepare and decide
   - `hr_grants`, `scope='hr_operator'`: `directory.read` with the five basic fields, and `training.read`, because the training screens and the directory need them and neither is allowed at `client_owner`
   - `hr_private_grants`: `recruitment` and `attendance`, because candidates and attendance records are visible only through these

   Payroll is left out on purpose (owner decision 4). Payroll rows and the payroll private area are never given or taken by a move.
7. Writes `hr_operations` and `hr_authority_events` with operation `owner.move`, `subject_id` = the new owner (always a member, so a platform admin can act), and details holding the old owner, the new owner and the actor. Writes one `console_audit` row, `hr.owner_moved`.

### After it commits

The route sends one email each to the old owner, the new owner and every enabled tenant admin of the company, without repeats, through the existing `Mailer` and a new template in `csa/console/mail_templates.py`.

- The email names the company, who made the change, the old and new owner, and the time in the company's timezone. A platform admin appears as "FourStacks admin". There is no link and no HR data.
- A failed or switched-off mail never undoes the move. The response reports it, and the page says: "HR owner changed, but we couldn't send the emails. Please tell the people affected."
- Logs carry the failure category only.

### API

- `GET /console/v1/team/hr-owner?client_id=` for the same people, returning `{available, owner: {user_id, display_name, usable} | null}`. `usable` is false when the owner's account or membership is disabled, or they are read only. It reads through a definer function that returns nothing else.
- `POST /console/v1/team/hr-owner` with `Idempotency-Key`, CSRF, and `{client_id, new_owner_id, expected_owner_id}`. Returns the new owner and the mail outcome. Refusals are 409 with the codes above; a caller without the right gets the existing refusal for Team routes.
- OpenAPI and `docs/CONSOLE_API.md` updated.

### Team page

Shared `TeamManagement.tsx`, so both Team pages get it. English only, like the rest of that page.

- An "HR owner" panel names the current owner, or says "No HR owner yet". If `usable` is false it warns: "This HR owner can't sign in or act. Choose a new one."
- **Change** (or **Set HR owner**) opens a confirmation with a list of eligible members. It says: "They get full control of who has HR access and can see sensitive employee details. {Old owner} loses HR owner powers straight away. Payroll stays off until they turn it on. Everyone affected gets an email."
- If the admin picks themselves, the confirmation adds: "You are making yourself the HR owner."

### Copy and docs

- The 1b refusal becomes, on the profile and the dock card: "The HR owner can't be marked as leaving. Ask your tenant admin to move HR ownership to someone else first." The Malay pair: "Pemilik HR tidak boleh ditandakan sebagai akan berhenti. Minta pentadbir syarikat anda memindahkan pemilikan HR kepada orang lain dahulu."
- Recent changes gets a line for `owner.move`. Today it lists only `access.save`, `private_access.save` and `invitation_access`, and treats any other operation as a private-area event, so its query, its event shape and its actor (the admin, not `subject_id`) all change.
- `docs/hr/SETUP.md`, `docs/hr/PROGRESS.md`, `docs/PROGRESS.md` and `docs/ASSISTANT_RETEST.md` are updated. The rule "client ownership requires a separately recorded, client-approved assignment" is reworded: the record is now the move itself, made by the company's admin.

## Constraints

- Synthetic fixtures and disposable testcontainers only.
- `EXPECTED_SCHEMA_VERSION` becomes "0522". No new tables, so the readiness table count stays.
- One scripted staging rollout: migration 0522 with all images. Rebase on main once PR #63 merges.
- `lib/navigation.ts` and `components/AppShell.tsx` stay untouched, because they belong to the console redesign session. `TeamManagement.tsx` must be checked against that branch at merge time, without touching its worktree.

## What must be true when it's done

- A new owner who held no HR switches can, through the screens and without signing in again: open HR Home, People and the access page; list, open and decide a training request; and see recruitment candidates and attendance records. They can't open payroll until it's turned on.
- The old owner can no longer open the access page or decide as owner, in their very next request.
- A test compares the `client_owner` rows in the standard owner set with every `client_owner` combination the `hr_grants` and `hr_workflow_grants` checks allow (10 today), so a new owner-only power can't be silently missed.
- The old owner keeps their rows outside `client_owner` after the move, and a move never adds or removes payroll rows.
- A staff member, a read-only member and a tenant admin of another company are all refused, at both the route and the function.
- Two admins moving at once: one wins, the other gets `owner_changed`. A retry with the same key answers with the current holder and sends no second round of emails.
- After a move, the old owner can be marked as leaving, and the finish run the next day leaves the new owner in place.

## Left to the builder

- The exact definer read used for the eligible list and for `usable`, and how recipient addresses reach the mail step, as long as the identity pool stays out of domain tables.
- The shape of the receipt and fingerprint, following `hr_people_apply`.
- Lock order, as long as it can't deadlock with `hr_is_owner`'s `FOR SHARE` reads and the `person.leave` guard.
- How the access page shows the new owner's rows, as long as they read sensibly and not as unexplained "Custom access".
- The Malay wording may be adjusted to match the existing HR copy; the owner reads it before release.
