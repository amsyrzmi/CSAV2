# Moving the HR owner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tenant admin or platform admin can set or move a company's HR owner from the Team page, at once, with emails and a permanent record.

**Architecture:** One SECURITY DEFINER function, `hr_owner_move`, does the whole move in one transaction on the app pool. Two Team routes call it and send the emails after commit. A small panel on the shared Team page is the only way in; the assistant has no path to it.

**Tech Stack:** Python 3.12, FastAPI, psycopg3, Postgres 16 testcontainers, Next.js with node:test, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-02-hr-owner-move-design.md`

## Global Constraints

- In this plan, `web/` means `CSA_WebConsole/csa-console/web/`.
- Migration `db/migrations/0522_hr_owner_move.sql`; `EXPECTED_SCHEMA_VERSION` becomes "0522". No new tables, so the readiness table count stays.
- Never FORCE RLS. No new roles. Definer functions pin `search_path=pg_catalog,public,pg_temp` and grant EXECUTE to `csa_app` only.
- The identity pool (`csa_identity`) never touches domain tables; HR reads and the move use `request.app.state.pool`.
- A new owner gets no payroll: no payroll workflow rows and no `payroll` private area, given or taken.
- No dock action and no planner path for this. No HR data in emails. Logs carry a failure category only, never an address.
- Do not edit `lib/navigation.ts` or `components/AppShell.tsx`. Never read or touch the `feat-console-redesign-phase-2` worktree. In `TeamManagement.tsx`, change only one prop and one rendered line, so the redesign can merge.
- Synthetic fixtures and disposable testcontainers only. Never delete Testcontainers you didn't start. Leave alone any process running `tests/test_services_redesign_browser.py`.
- CI is the merge gate. Locally run the changed files' tests one at a time with `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q <file>`, plus `make lint`, and for web files `npm test`, `npm run lint`, `npm run typecheck` (needs `CSA_CONSOLE_API_ORIGIN=http://127.0.0.1:9`) and `npm run check:isolated`. Run `uv run ruff format` on changed `.py` files.
- A permission refusal on HR routes reads as a content-free 404; Team routes keep their own 403 "tenant administrator required". This feature uses the Team one.

Exact copy, used verbatim:

| Code | Sentence |
|---|---|
| `owner_read_only` | A read-only member can't be the HR owner. Change their role first. |
| `owner_leaving` | This person is leaving, so they can't be the HR owner. Choose someone else. |
| `owner_unavailable` | This person can't be the HR owner yet. They need to sign in and change their password first. |
| `owner_unchanged` | This person is already the HR owner. |
| `owner_changed` | The HR owner changed since you opened this page. Reload to see who it is now. |

## Review Focus

1. The new owner saves their own switches on the access page after the move: their owner-only rows must survive, or the title loses its powers without anyone noticing. Pinned in Task 3.
2. A retried request reuses a key with a *different* person: it must refuse, not move ownership twice. `hr_operations` keys on the subject, so a lookup by subject alone would miss it. Pinned in Task 1.
3. The old owner's open tab or an assistant card made before the move: the next action is refused, not honoured. Pinned in Task 3.
4. The old owner's account is already disabled or read only, which is the case this feature exists for: the move still works and they are still emailed. Pinned in Tasks 1 and 2.
5. A tenant admin moves ownership to themselves: each address gets exactly one email. A bad company timezone must not block the move. Pinned in Task 2.

---

### Task 1: The database function

**Files:**
- Create: `db/migrations/0522_hr_owner_move.sql`, `tests/test_hr_owner_move.py`
- Modify: the `EXPECTED_SCHEMA_VERSION` constant, and regenerate `docs/console_openapi.json` if the contract test asks

**Interfaces:**
- Produces, EXECUTE to `csa_app` only:
  - `public.hr_owner_move(p_client uuid, p_actor uuid, p_new_owner uuid, p_expected_owner uuid, p_request_key uuid, p_fingerprint text) RETURNS TABLE(old_owner uuid, new_owner uuid, duplicate boolean)`
  - `public.hr_owner_read(p_client uuid, p_actor uuid) RETURNS TABLE(user_id uuid, display_name text, usable boolean)`: no row when there is no owner.
- Exceptions, `USING ERRCODE='23514'`: `hr_owner_read_only`, `hr_owner_leaving`, `hr_owner_unavailable`, `hr_owner_unchanged`, `hr_owner_changed`, `hr_idempotency_conflict`. Not allowed: `hr_owner_move_denied`, `USING ERRCODE='42501'`.
- Session: both functions refuse unless `p_client` equals `app.client_id` and `p_actor` equals `app.hr_user_id`. Allowed actors: an enabled `console_users` row with `is_platform_admin`, or an enabled user with an enabled `console_memberships` row in `p_client` with role `tenant_admin`.

The function, in this order:

1. `pg_advisory_xact_lock(hashtextextended(p_client::text||':hr-owner-bootstrap',0))`, the key `scripts/bootstrap_hr_owner.py` takes.
2. Lock the owner row `FOR UPDATE`. Other readers take the owner row `FOR SHARE` before grant rows, so this order can't deadlock with them. Read employee and account rows without a lock: `person.leave` takes the owner row first, so it waits for this move and then sees the new owner.
3. Check the actor.
4. Look for a receipt by `client_id`, `request_key` and `operation='owner.move'`, **not** by subject. Same fingerprint: return it with `duplicate=true`, before the owner and target checks, whose answers have changed since the first run. Other fingerprint: `hr_idempotency_conflict`.
5. Check `p_expected_owner IS NOT DISTINCT FROM` the current owner (`hr_owner_changed`); then `hr_owner_unchanged`; then the target: enabled user, enabled membership, `must_change_password` false (`hr_owner_unavailable`), role not `read_only` (`hr_owner_read_only`), linked `hr_employees` row, if any, active with `last_day IS NULL` (`hr_owner_leaving`).
6. Upsert the owner row with `approved_by=p_actor`, `approved_at=clock_timestamp()`, `approval_reference='console:'||p_request_key`.
7. Delete the old owner's `scope='client_owner'` rows from `hr_grants` and `hr_workflow_grants`. Touch nothing else of theirs.
8. Give the new owner the standard set, `ON CONFLICT DO NOTHING`, except the directory row, which is `DO UPDATE SET fields=EXCLUDED.fields`:
   - `hr_grants`: `training.decide` at `client_owner`; `directory.read` at `hr_operator` with fields `assigned_manager, department, display_name, job_title, work_email`; `training.read` at `hr_operator`
   - `hr_workflow_grants` at `client_owner`: `recruitment`, `attendance`, `leave` × `read`, `prepare`, `decide` (9 rows)
   - `hr_private_grants`: `recruitment`, `attendance`, `approved_by=p_actor`
9. Insert `hr_operations` (`subject_id=p_new_owner`, `operation='owner.move'`, the fingerprint, receipt `{old_owner, new_owner}`), `hr_authority_events` (same key; details `{user_id: new owner, old_owner_id, actor_id}`), and `console_audit` (`action='hr.owner_moved'`, `target_id` = new owner, details `{old_owner_id, new_owner_id}`).

`hr_owner_read`'s `usable` is false when the owner's user or membership is disabled or their role is `read_only`.

- [ ] **Step 1: Write the failing tests** in `tests/test_hr_owner_move.py`, reusing `HrWorld` from `tests/test_hr_people.py`:

```python
OWNER_ONLY_EXPECTED = {("hr_grants", "training.decide")} | {
    ("hr_workflow_grants", d, a) for d in ("recruitment", "attendance", "leave") for a in ("read", "prepare", "decide")
}  # 10 combinations today


async def test_standard_set_covers_every_owner_only_combination(world):
    ...
    # In a rolled-back savepoint, try each known action and each domain×action at
    # scope='client_owner'. The accepted ones must equal the client_owner rows a move gives.


async def test_tenant_admin_moves_owner_and_powers_follow(world):
    ...
    # hr_is_owner true for the new owner and false for the old, on their next transaction;
    # hr_case_access true for new, false for old; the old owner's rows outside client_owner stay.


async def test_no_owner_yet_sets_the_first_owner(world): ...
async def test_platform_admin_can_move_and_is_recorded_as_actor(world): ...
async def test_refused_actors(world):
    ...
    # tenant_staff, read_only, a tenant admin of the other company, and p_actor != app.hr_user_id.


async def test_refused_targets(world):
    ...
    # read_only, disabled member, must_change_password, last_day set, inactive person, same person.


async def test_stale_expected_owner_is_refused(world): ...
async def test_same_key_returns_first_result_without_a_second_write(world): ...
async def test_same_key_with_another_person_is_a_conflict(world): ...
async def test_two_moves_at_once_one_gets_owner_changed(world): ...
async def test_moves_away_from_a_disabled_owner(world): ...
async def test_no_payroll_row_or_area_is_given_or_taken(world): ...
async def test_old_owner_can_leave_and_the_finish_keeps_the_new_owner(world):
    ...
    # Move, mark the old owner as leaving with today's last day, run hr_finish_leaver the next day.


async def test_owner_read_returns_usable_and_refuses_other_actors(world): ...
```

- [ ] **Step 2: Run them.** `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_hr_owner_move.py`. Expected: FAIL, the function doesn't exist.
- [ ] **Step 3: Write the migration**, with the `Adds`, `Pre-check` and `Rollback` header the other migrations carry, and bump the version.
- [ ] **Step 4: Run them again,** plus `tests/test_hr_people_records.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(hr): a definer function moves the HR owner and the owner-only rows`.

### Task 2: Routes and emails

**Files:**
- Create: `csa/console/hr_owner.py`, `tests/test_hr_owner_routes.py`
- Modify: `csa/console/auth.py` (two routes only), `csa/console/contracts.py`, `csa/console/mail_templates.py`, `tests/test_console_mail.py`, `docs/console_openapi.json`, `docs/CONSOLE_API.md`

**Interfaces:**
- Consumes: `hr_owner_move`, `hr_owner_read` from Task 1; `_invite_facts`, `_mailer`, `Mutation`, `Actor`, `Service`, `require_package` in `csa/console/auth.py`.
- Produces:
  - `GET /console/v1/team/hr-owner?client_id=` → `HrOwnerOut {available: bool, owner: {user_id, display_name, usable} | null}`. Package inactive: 200 with `available=false`, `owner=null`.
  - `POST /console/v1/team/hr-owner`, `Idempotency-Key` header required, body `HrOwnerMoveIn {client_id, new_owner_id, expected_owner_id: UUID | null}` → `HrOwnerMoveOut {owner: {user_id, display_name, usable}, mail: Literal["sent", "not_sent", "repeat"]}`.
  - `owner_moved_mail(*, to, name, business_name, actor_name, old_owner_name: str | None, new_owner_name, moved_at, tz) -> OutgoingMail` with subject `HR owner changed for {business_name}`.
- Refusals: `hr_owner_*` → 409 with the code minus `hr_`; `hr_idempotency_conflict` → 409 `idempotency_conflict`; `hr_owner_move_denied` → 403 "tenant administrator required"; package inactive on POST → 422 `hr_not_available`.

Flow in `csa/console/hr_owner.py`: `service.require_member_admin(actor, client_id)` first; `require_package`; in `tx(pool, str(client_id))` run `console/invite_facts_context.sql` for every actor (the function reads `app.hr_user_id`), then the function. The fingerprint is `sha256(f"{new_owner_id}:{expected_owner_id or ''}")` hex.

After commit, unless `duplicate` (then `mail="repeat"`): names and addresses come from `service.members(actor, client_id)`, business name and zone from `_invite_facts(..., mail_on=False)`, so a bad timezone falls back and never blocks. Recipients: old owner, new owner, each enabled `tenant_admin`, unique by address, old owner included even when disabled. `actor_name` is "FourStacks admin" for a platform admin. Send with `asyncio.gather` over `mailer.send`. Any outcome other than `"sent"`, or no mailer, gives `mail="not_sent"`; an exception in the mail step is logged by category and gives the same, so the committed move always answers 200.

Email text: "{actor} made {new} the HR owner of {business} on {when}, replacing {old}." With no old owner, the sentence ends at the date. Then: "The HR owner controls who has HR access and can see sensitive employee details." No link.

- [ ] **Step 1: Write the failing tests:** `test_owner_moved_mail_has_no_link_and_names_everyone` and `test_owner_moved_mail_first_owner` in `tests/test_console_mail.py`; in `tests/test_hr_owner_routes.py`, reusing the `mail_world` pattern from `tests/test_console_identity_gateway.py`: `test_get_for_admin_and_refused_for_staff_and_other_company`, `test_get_when_package_off_says_unavailable`, `test_post_needs_csrf_and_idempotency_key`, `test_tenant_admin_moves_and_each_address_gets_one_email`, `test_admin_moves_to_self_gets_one_email`, `test_disabled_old_owner_is_still_emailed`, `test_retry_returns_repeat_and_sends_nothing`, `test_mail_off_or_failing_keeps_the_move_and_says_not_sent`, `test_bad_timezone_does_not_block`, `test_refusal_codes_map_to_409`, `test_platform_admin_is_named_fourstacks_admin`, `test_no_address_in_logs`.
- [ ] **Step 2: Run them.** Expected: FAIL, 404 or missing names.
- [ ] **Step 3: Implement,** regenerate OpenAPI, and document both routes in `docs/CONSOLE_API.md`.
- [ ] **Step 4: Run** both test files and the contract snapshot test. Expected: PASS.
- [ ] **Step 5: Commit** `feat(console): Team routes read and move the HR owner, with emails after commit`.

### Task 3: Recent changes, and the owner's powers end to end

**Files:**
- Modify: `db/sql/hr/access_event_page.sql` (add `'owner.move'` to the IN list), `csa/hr/access_contracts.py`, `csa/hr/access_page.py`, `web/lib/hr-access-api.ts`, `web/lib/hr-access-copy.ts`, `web/tests/hr-access-copy.test.mjs` (or the existing test for `eventText`), `tests/test_hr_owner_move.py`

**Interfaces:**
- `AccessEvent.source` gains `"owner_move"`; a new field `previous_owner_name: str | None`.
- `_actor()` returns `UUID(details["actor_id"])` for `owner.move`; `details["user_id"]` is already the new owner. The switch-diff baselines (`baselines`, `access_event_previous.sql`) ignore `owner.move`.
- `eventText`: "{actor} made {target} the HR owner, replacing {previous}"; with no previous owner: "{actor} made {target} the HR owner".

- [ ] **Step 1: Write the failing tests** in `tests/test_hr_owner_move.py`:

```python
async def test_new_owner_with_no_switches_can_work_at_once(world):
    ...
    # Through HrService as the new owner: context() says client_owner; training_page lists and
    # opens a request; deciding it succeeds; the People page and access page read; candidates
    # are visible; payroll reads are refused.


async def test_old_owner_is_cut_off_in_the_next_request(world):
    ...
    # access page refused; training decide as owner refused; own-request rows still work.


async def test_card_made_before_the_move_is_refused_at_confirm(world):
    ...
    # The old owner makes an hr_person_leave card for a colleague with HR access, then the owner
    # moves, then Confirm: leaver_has_access, no change.


async def test_new_owner_saving_own_switches_keeps_owner_rows(world):
    ...
    # access.save for themselves, turning a switch on: every client_owner row is still there.


async def test_recent_changes_show_the_move_for_a_platform_actor(world): ...
```

  Add `eventText` tests for both sentences.
- [ ] **Step 2: Run them.** Expected: the event tests FAIL; the power tests show what Task 1 got wrong, if anything.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_hr_owner_move.py`, the access page test file and `npm test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(hr): Recent changes shows a moved HR owner`.

### Task 4: The Team page panel

**Files:**
- Create: `web/lib/hr-owner.ts`, `web/components/console/HrOwnerPanel.tsx`, `web/tests/hr-owner.test.mjs`
- Modify: `web/components/console/TeamManagement.tsx` (one prop, one rendered line), `web/app/portal/team/page.tsx` (pass `currentUserId`), `web/scripts/check-isolated.mjs` (list the new lib and test)

**Interfaces:**
- `lib/hr-owner.ts`:
  - `type HrOwner = {user_id: string; display_name: string; usable: boolean}`
  - `hrOwnerClient(clientId).read()` and `.move(newOwnerId, expectedOwnerId, key)`
  - `eligibleOwners(members, current: HrOwner | null)`: enabled, not `read_only`, not the current owner, sorted by name
  - `ownerRefusalText(cause)`: the table above; unknown codes give "We couldn't change the HR owner. Please try again."
  - `confirmText({oldOwnerName, isSelf})`, `moveDoneText(mail)`
- `<HrOwnerPanel clientId members currentUserId />`: renders nothing when `available` is false.

Copy, verbatim:
- Panel: `HR owner: {name}`, `No HR owner yet`; warning `This HR owner can't sign in or act. Choose a new one.`; buttons **Change** and **Set HR owner**.
- Confirm: `They get full control of who has HR access and can see sensitive employee details. {Old owner} loses HR owner powers straight away. Payroll stays off until they turn it on. Everyone affected gets an email.` With no old owner, drop the second sentence. If the chosen person is `currentUserId`, add `You are making yourself the HR owner.`
- Done with `mail="not_sent"`: `HR owner changed, but we couldn't send the emails. Please tell the people affected.`

The key is one `crypto.randomUUID()` per open of the confirmation, reused on retry, new after a refusal that changes the choice. Use the kit `Modal`. English only, like the rest of the page.

- [ ] **Step 1: Write the failing tests** in `web/tests/hr-owner.test.mjs`: `eligibleOwners` excludes disabled, read-only and the current owner; every refusal sentence verbatim; an unknown code's fallback; `confirmText` with no old owner, with one and for self; `moveDoneText` for `sent`, `repeat`, `not_sent`.
- [ ] **Step 2: Run** `node --test tests/hr-owner.test.mjs`. Expected: FAIL, module missing.
- [ ] **Step 3: Implement** and wire the one line into `TeamManagement.tsx`. Find every browser suite that opens a Team page (`git grep -ln "portal/team\|settings/team" -- tests`, at least `tests/test_invite_access_browser.py`) and add a stub for `team/hr-owner`, or it records an unexpected call.
- [ ] **Step 4: Run** the four web checks and those browser suites. Expected: PASS.
- [ ] **Step 5: Commit** `feat(console): the Team page names the HR owner and can move it`.

### Task 5: The refusal sentence, browser scenario and docs

**Files:**
- Modify: `web/lib/hr-records.ts:32`, `web/lib/hr-person-card.ts:12`, `web/tests/hr-records.test.mjs`, `web/tests/hr-person-card.test.mjs`, `tests/test_invite_access_browser.py`, `docs/hr/SETUP.md`, `docs/hr/PROGRESS.md`, `docs/PROGRESS.md`, `docs/CONSOLE_API.md:339`, `docs/ASSISTANT_RETEST.md`

**Interfaces:**
- English: `The HR owner can't be marked as leaving. Ask your tenant admin to move HR ownership to someone else first.`
- Malay: `Pemilik HR tidak boleh ditandakan sebagai akan berhenti. Minta pentadbir syarikat anda memindahkan pemilikan HR kepada orang lain dahulu.`
- Browser scenario `test_tenant_admin_moves_hr_owner` (stubbed API): the panel names the owner; Change, choose a member, the confirmation shows, Confirm; the POST carries `Idempotency-Key` and the expected owner; the panel shows the new owner. A second run with a 409 `owner_changed` shows its sentence and a run with `mail="not_sent"` shows the warning.
- `docs/hr/SETUP.md`: replace "the console has no way to change it yet"; describe the Team page; say the bootstrap script stays for isolated environments and seeding. `docs/hr/PROGRESS.md:209`: the rule becomes "client ownership is recorded by the move itself, made by the company's tenant admin or a platform admin".
- `docs/ASSISTANT_RETEST.md`, manual rows: set the first owner; move to a colleague; move to yourself; a read-only member refused; emails received by each person; the old owner loses the access page at once; the new owner approves a training request and sees candidates; payroll stays off; the refusal sentence on marking the owner as leaving, in both languages.
- `docs/PROGRESS.md`: a top entry with the rollout note: one scripted rollout, migration 0522 with all images.

- [ ] **Step 1: Update the pinned sentences in the tests** and write the browser scenario.
- [ ] **Step 2: Run** `node --test tests/hr-records.test.mjs tests/hr-person-card.test.mjs`. Expected: FAIL on the old sentence.
- [ ] **Step 3: Change the two sentences** and write the docs.
- [ ] **Step 4: Run** the four web checks, `tests/test_invite_access_browser.py -k moves_hr_owner` and `make lint`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(hr): the HR owner refusal points to the tenant admin; browser scenario and docs`.
