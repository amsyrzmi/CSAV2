# Console API contract (v1)

Current release state: see [PROGRESS.md](./PROGRESS.md) and
[deploy/RELEASE.md](../deploy/RELEASE.md).
M6 remains incomplete; this contract is not public deployment approval. M6A local
records are not a live provider feed, and the new M6B verifier has no supported
console callback/runtime registration. Mounted legacy marketplace OAuth routes are
separate and must not be exposed as the M6B flow.

The packaged Next.js and gateway artifacts have passed recorded disposable runtime
acceptance; public browser/ingress and staging have not. See
[PROGRESS.md](./PROGRESS.md) for exact evidence and current gates.

M0–M5 plus offline M6A implementation (M4 functionally closed with accepted dev performance exception, 2026-10-16): the retained Next.js presentation calls one CSAV2
gateway through same-origin `/console/v1/*`. The copied Python API is reference only.
See the [frontend adoption instructions](../CSA_WebConsole/csa-console/web/README.md).
Offline M6B TikTok order-page validation is internal adapter groundwork only: it adds no
console routes or live connection claims. Complete provider response-contract verification,
tenant/store-bound credential resolution and safe browser projections remain required;
the normalized internal ecommerce order is not a console response model.
Official v202309 request paths, pagination placement and detail envelope have since been
verified/corrected; line-item quantity/price normalization still blocks provider readiness.
The verified scope notes lived in the original migration plan, since removed from the repository (see git history).

## Staff HR contract (source implementation, 2026-10-21)

The `/console/v1/hr` router and `/hr` / `/portal/hr` pages implement staff-only
directory, training, recruitment, attendance, leave and local payroll-review
workflows. See the [HR implementation and validation](./hr/PROGRESS.md) and
[setup and operating boundaries](./hr/SETUP.md) before enabling anything.
Ordered migrations 0380-0410 follow workspace migration 0370; 0430 adds the staff email assistant. This is not
persistent migration, deployment, live-provider or payroll-compliance acceptance.

- Only verified human sessions are accepted. Platform admins select a tenant
  explicitly and still need its active membership and explicit HR authority.
  Console roles alone grant no HR business access. Mutations require CSRF and
  reject read-only membership; service transactions recheck session/grants.
- `/context`, `/employees`, `/training-requests` and `/workflows` expose bounded,
  scoped projections. Training and workflow mutations use `Idempotency-Key`,
  current versions and coupled receipts/audit. Supervisor decisions remain human;
  payroll review requires a different reviewer and the exact private snapshot digest.
- `GET /guidance` returns only `{kind:"hr_navigation", actions, source:"current_hr_grants",
  as_of}`. Actions summarize current explicit directory/training grants, not permission
  to a particular employee/request. It reuses HR's transaction-bound session and
  authority checks, accepts only optional `client_id` (required for platform admins),
  and returns an empty action list when a verified tenant member has no such grants.
  No employee references, private areas, records or model input are returned.
  The staff workspace renders fixed guidance and same-origin links to the existing
  HR pages; this is separate from persisted workspace turns and their action union.
  HR navigation accepts only `section=directory|training` (since project 1a the HR pages
  redirect these to `/people` and `/training`); platform links retain the
  explicitly selected `client_id`. URL hints never establish authority. Opening a
  page rechecks resource eligibility, and read-only sessions still cannot mutate.
- `/operations` handles explicitly granted administration and private operations;
  `/records/{area}` and `/private/{area}/{resource_id}` return typed, separately
  authorized records. Candidate text/notes and payroll amounts are absent from
  generic operation receipts.
- HR agents use a separate staff catalog and session context, not customer
  contacts, the customer capability registry or the workspace action union.
  `/agents`, `/agents/contracts`, `/agents/execute` and session-bound
  `/confirmations/{confirmation_id}/confirm` preserve that distinction.
- `/assistant` is disabled by default through `HR_ASSISTANT_ENABLED` and a
  per-tenant switch. When explicitly configured, submitted messages use the
  existing model provider; private HR stores are not model context. Model plans
  cannot grant authority, approve/reject or prepare payroll. Browser voice is
  local dictation/status playback, not LiveKit or a conversational voice ingress.

All HR responses are no-store; validation errors do not echo submitted HR data.
External calendar/email delivery, hiring/ranking, payments, filings and automatic
PCB calculation are not implemented by these local receipts.

### HR access page

The "Who can do what" page (People & HR) reads and changes each member's HR access as
nine switches. The server works out the switches, the "Custom access" rows, the
warnings and the version; the page shows what it returns. Switch ids:
`directory`, `own_requests`, `approves_team`, `hr_team`, `manage_people`, `recruitment`,
`attendance`, `payroll_prepare`, `payroll_review`. `manage_people` ("Manage people", migration
0520) is the grant row `people.manage` on the `hr_operator` scope with no fields; the
`hr_team` preset turns it on and no other preset does. Neither it nor its grant can change
access.

- **Owner only.** All three routes need the HR client owner. Anyone else, including
  another tenant's owner, gets 404 `resource_unavailable`, as for
  `/records/members`.
- `GET /access/people?cursor=&q=&team=&has=&reports_to=` returns
  `AccessPeoplePage`: up to 50 people (`items`), an opaque `next_cursor`, the
  `version` stamp, `counts` per switch and the current `warnings`. `q` matches part
  of the name (any case), `team` the department, `has` a switch that is on or
  included, and `reports_to` the manager's employee id. `counts`, `warnings` and
  `version` always cover the whole client, whatever the filters.
- Each person carries `on` (every switch whose rows are all present),
  `included` (a switch whose rows sit inside another switch that is on, which
  cannot be turned off on its own), `custom` (the rows no switch describes, in
  plain words; for the HR owner, without the standard set a move of the title gives,
  which the page sums up in one line), `team_size` and `is_owner`. Every save keeps
  the rows no switch describes, so the owner's own decision rows on `client_owner`
  are never dropped here.
- `POST /access/changes` takes `AccessChanges`: `batch_id`, the `version` the
  page showed, `dry_run` and 1–50 `changes`, each `{user_id, on, base}`: the
  member's full desired switch set (`on`) and the `on` set the page loaded them
  with (`base`), both at most 16 unique switch ids (9 exist; the headroom keeps the next switch from needing
  another function rewrite); user ids are unique. The body
  cap is 65536 bytes.
  - A `version` that no longer matches gives 409 `access_changed` and writes
    nothing (a dry run too). The page reloads, keeps its own edits over each
    member's latest switches, and lets the owner save again.
  - A person whose current switches differ from their `base` is `refused` with
    `changed_since_load` and nothing is written for them (a dry run too). This
    covers a member who was not on the reloaded page.
  - Each person is saved through `/operations`' own path (`access.save`, then
    `private_access.save` per changed area), with idempotency keys derived from
    `batch_id` and `user_id`, so each gets the usual receipt and authority event.
    The version is checked once per batch and each **operation** is its own
    transaction: a batch is not atomic, and neither is one person's save. A save
    that stops after one of the person's operations landed is `refused` with
    `partly_saved`.
  - **Batch ids:** every new submit needs a new `batch_id`. Reuse one only to
    retry the same body after a lost response (the page's dry run and its
    confirm share one).
  - **Replay:** if any person of the batch already has a receipt for that
    `batch_id`, the batch is a retry and the version stamp is not checked. The
    per-person guards still apply: a person with an `access.save` receipt is
    compared with it (the same rows finish any missing area changes and report
    `saved`; different rows are `refused` with `access_changed` and write
    nothing); a person with only area receipts finishes the missing area
    changes; and a person with no receipt is checked against `base` and saved as
    usual.
  - `dry_run: true` writes nothing and returns the outcomes and the `warnings`
    for the proposed state.
  - `AccessChangeResult.results` has one entry per person: `saved`, `unchanged`
    (nothing to write) or `refused` with a `reason` (`not_a_member`,
    `too_many_rows`, `changed_since_load`, `partly_saved`, `access_changed` on a
    replay, or an HR policy code). Other people in the batch still save. It also
    returns the new `version` and the `warnings`.
  - A `cursor` that is not one this API returned gives 409 `invalid_cursor` (404
    `resource_unavailable` for a non-owner), on `/access/people` and
    `/access/recent`.
- Warnings (`AccessWarningOut`, names resolved by the server): `no_approver`,
  `no_manager`, `no_payroll_reviewer`, `manager_without_team` and
  `prepares_and_reviews`. They warn; they never block a save.
- `GET /access/recent?cursor=` returns `AccessEventPage`: up to 50 `access.save`,
  `private_access.save` and `owner.move` events, newest first, with an opaque cursor. Each
  `access.save` lists `turned_on` and `turned_off` against that person's previous
  `access.save`; a private-area event gives `area` and `area_enabled`. A member who
  is no longer enabled has `actor_name` or `target_name` `null` ("Former member").
  Each event has a `source`: `page` for a save on this page, `invitation` for access
  set on an invitation when it was accepted, or `owner_move`. An `invitation` event carries the `preset`
  (otherwise `null`); its actor is whoever chose the preset (a platform admin shows as
  "FourStacks admin"), and `turned_on` is only what the preset added to what the person
  already had. An `owner_move` event is a change of HR owner: its actor is whoever moved
  it (a platform admin shows as "FourStacks admin"), its target is the new owner,
  `replaced_owner` is true when there was an owner before, and `previous_owner_name` is that
  owner (`null` for a first owner or once they are no longer an enabled member, when the page
  says "replacing Former member"); it changes no switches.
- **Access chosen on an invitation** (`InviteIn.hr_preset`, see the invitation table
  below) is applied when the invitation is accepted. If it is not, it waits for the HR
  owner: a preset other than Staff whose chooser is no longer the HR owner (or no longer an
  enabled user with an enabled membership in the tenant), or any preset, Staff included,
  whose apply failed (for example the tenant's HR lock was held for more than 5 s). HR owner
  only, tenant query as for the other `/access` routes:
  - `GET /access/invitations` returns `PendingInvitationPage`: `items` of
    `{token_id, user_id, display_name, preset, chosen_by, chooser_name, accepted_at}`.
    `chooser_name` is "FourStacks admin" for a platform admin and `null` once the chooser
    is no longer an enabled member.
  - `POST /access/invitations/{token_id}/apply` applies the chosen preset now, on top of
    what the person already has (it never turns a switch off), and returns an
    `AccessPersonResult` with `outcome` `saved`. It checks again that the caller is the HR
    owner. Refusals are errors, never a `refused` outcome: 409
    `invitation_already_handled` when it was already applied or dismissed; 404
    `resource_unavailable` for an unknown token.
  - `POST /access/invitations/{token_id}/dismiss` leaves the person's access as it is; 204.
    The same 409 applies.
- The company features ("What the HR assistant can help with") stay on
  `PUT /agents`. They control only the HR assistant and agents; they never limit
  what staff can do in the console, and no switch or warning depends on them.

### People, departments and Manage people (project 1a, migration 0520)

The People & HR pages are `/portal/hr/…` for staff and `/hr/…` for a platform admin (who keeps
`client_id` on every link): Home (`/portal/hr`), `people`, `people/org-chart`,
`people/departments`, `people/new`, `people/<employee_id>`, `recruitment`, `leave`, `payroll`,
`training`, `requests` and `settings`. An old `?section=` link redirects to its tab
(`directory` and `people` to People, `attendance` and `leave` to Leave & attendance, `workflows`
and `requests` to Requests; `training`, `recruitment`, `payroll` and `settings` to their own).
The new routes below are GET only, return no-store, and take only `client_id` and the query keys
named here (`/people` is the only one with any; another key is 400 `invalid_hr_query`). A person
the caller may not see, and any caller the route does not serve, gets 404 `resource_unavailable`.

**Who sees what.** The server decides the viewer: `hr` (the HR owner or a holder of Manage
people), `directory` (the complete directory switch: `directory.read` on `hr_operator` with all
five basic fields) or `self` (any other HR session of the tenant). A hand-made partial directory
grant is `self`, not `directory`. A `self` viewer can open only their own linked person's profile;
the list, org chart and departments are 404 for them. A `directory` viewer sees active and starting
people with basic fields only. The HR-only fields (`employment_profile`, `version`,
`account_linked`, and Left people) are present for `hr` and `null` or absent for everyone else.

- `GET /people?q=&department=&manager=&status=&cursor=` returns `PeoplePage`: up to 50 `items`
  (`HrPersonOut`: `employee_id`, `display_name`, `job_title`, `department_id`, `department`,
  `manager_id`, `manager_name`, `work_email`, `work_phone`, `start_date`, `employment_type`,
  `status`, and the HR-only three), an opaque `next_cursor` and the `total` that matches.
  `q` (at most 120 characters) uses the directory's word-by-word rule (the first 5 words, each in
  the name or work email or starting a word of the job title). `department` is a department id,
  `none` or `all`; `manager` an employee id; `status` is `active`, `starting` or `left`. With no
  `status` the list shows Active and Starting; `left` is for `hr` only and is 404 for the others.
  `starting` means the start date is after today in Asia/Kuala_Lumpur. `cursor` is a number the
  API returned. A `department` that is neither `none` nor an id is 409 `invalid_department_filter`.
- `GET /people/{employee_id}` returns `PersonProfile`: the `person`, the `sections` the viewer may
  open (`overview` for everyone; `access` and `history` for `hr`) and `can_edit`.
- `GET /people/{employee_id}/history` returns `PersonHistory` (`hr` only): up to 200 changes,
  newest first, each `{changed_at, actor_name, fields:[{field, old, new}]}`, and `truncated`.
  A database trigger writes one row per change from any path, so changes made through
  `employee.save` appear too.
- `GET /people/{employee_id}/access` returns `PersonAccessOut` (`hr` only): the linked
  `account` (`{user_id, display_name}` or `null`), the `on` switches, the `custom` rows and
  `is_owner`.
- `GET /org-chart` returns `OrgChart`: `nodes` (up to 5000) and `looped`, the ids that sit in a
  manager loop (shown flat). `directory` viewers see active and starting people.
- `GET /departments` returns `DepartmentList`: each `{department_id, name, archived, version,
  people}`. `hr` and `directory` viewers only.
- `GET /home` returns `HrHome` (`training_waiting`, `requests_waiting`): counts of what waits for
  the caller, from the caller's own grants.

**Departments** are rows (`hr_departments`). The old free-text `department` was folded into rows by
the migration (names that differ only in case or spacing become one department), and the text
column stays as a mirror of the linked department's name, kept by triggers. So `employee.save`, the
directory and every existing reader keep working. A department name stays taken while archived.

**New operations** on `POST /operations` (CSRF and `Idempotency-Key`, as for the others):

- `person.add` `{employee_id, values, employment_profile?}`: a new active person. `employee_id`
  is chosen by the client, so a retry is safe. `values` is `{display_name, job_title, start_date,
  manager_id?, department_id? | new_department?, work_email?, work_phone?, employment_type?}`
  (`employment_type` is `full_time`, `part_time`, `contract` or `intern`; `job_title` and
  `start_date` are required here; giving both `department_id` and `new_department` is 422).
- `person.update` `{employee_id, expected_version, values, employment_profile?}`: every editable
  field at once. It never changes whether the person is active (leaving and rehire are 1b), and
  a `manager_id` equal to `employee_id` is 422. A stale `expected_version` is 409 `person_changed`.
  The `employment_profile` (`region`, `legal_entity_reference`, `employment_category`) is
  all-three-or-none.
- `department.save` `{department_id, expected_version?, name, archived}`: create (no
  `expected_version`), rename, archive or restore. A stale version is 409 `department_changed`.
- `account.link` `{employee_id, account_id | null}`: link a person to a tenant member's console
  account, or unlink. HR owner only.

`person.add`, `person.update` and `department.save` are for the HR owner or a Manage people
holder; the database checks again. They write a receipt but no authority event, and cannot change
access, account links or whether a person is active. `employee.save` stays in the API for
the HR owner (it is the only way to change `is_active` until 1b; the console no longer has a form
for it).

Refusals are 409 with the code as `detail`, except `permission_denied` and `resource_unavailable`
(404 `resource_unavailable`): `employee_exists`, `person_changed`, `manager_unavailable`,
`manager_loop` (the new manager reports, directly or not, to this person), `manager_is_you` (a
non-owner cannot newly make their own linked person anyone's manager), `own_manager_owner_only` (a
non-owner cannot change their own linked person's manager), `department_unavailable`,
`department_archived`, `department_changed`, `department_name_taken`, `account_taken` (the
account is linked to another person) and `member_unavailable` (not an active member of the tenant).
Validation failures are 422 `invalid_hr_input` with no field named.

**`GET /records/interviewers`** (a new `records` area) returns `{user_id, display_name}` for every
enabled console member of the tenant, for the HR owner or anyone with private recruitment
access (everyone else gets 404). The Recruitment tab uses it to choose an interviewer.

**`GET /context` (`HrContext`) gained three fields:** `switches` (the access switches that are on
for the caller, in switch order), `workflows` (the caller's `domain.action` workflow grants, sorted
and unique) and `hr_duties` (true for the HR owner and for anyone holding a switch beyond
directory and own requests). The console uses them to hide tabs the caller cannot use; they
never grant anything.

### Personal details, documents and leaving (project 1b, migration 0521)

Every route below takes only `client_id` (the upload also takes `kind`) and answers no-store. A
caller the route does not serve gets 404 `resource_unavailable`, never 403. Writes need CSRF; the
upload, the removal and the operations also need an `Idempotency-Key`.

**The Sensitive details switch.** `sensitive_details` is a new access switch (`people.sensitive`
on the `hr_operator` scope, in no preset). It appears in `SwitchId`, in `HrContext.switches` and on
the access page like the others, counts as an HR duty, and changes no access: only the HR owner
changes access.

**People and profiles.** `HrPersonOut.status` gains `leaving` (active with a last day that has not
passed in Asia/Kuala_Lumpur), and `HrPersonOut` gains `last_day` (HR viewers, the person and their
direct manager; `null` for everyone else) and `leaving` (`{reason, note}`, HR viewers only).
`GET /people?status=leaving` lists them; with no `status` the list shows Active, Starting and
Leaving people. `PersonProfile.sections` is now, in this order, `overview`; `personal` (the HR
owner, Sensitive details holders and the person themself); `documents` (the HR owner, Manage people
holders and the person themself); then `access` and `history` for `hr` viewers. History fields gain
`last_day`, `leaving_reason`, the twelve personal field names (with `old` and `new` always `null`)
and `document` (`old` or `new` reads like `Contract: offer.pdf`). A history row with no actor (the
leaver finish) has `actor_name` `System`.

**Personal details.**

- `GET /people/{employee_id}/personal` returns `PersonalOut`: `ic_or_passport`, `date_of_birth`,
  `home_address`, `personal_phone`, `emergency_name`, `emergency_relationship`, `emergency_phone`,
  `bank_name`, `bank_account`, `tax_number`, `epf_number`, `socso_number`, `version` (`null` until
  the first save) and `can_edit` (`all` for the HR owner and Sensitive details holders, `contact`
  for the person). The five numbers are masked: `•••• ` and the last 4 characters, or `••••` for 4
  characters or fewer.
- `POST /people/{employee_id}/personal/reveal` `{field}` (one of `ic_or_passport`, `bank_account`,
  `tax_number`, `epf_number`, `socso_number`) returns `{value}` in full, or `null` when none is
  stored. Every call is recorded in `hr_personal_reveals` (who, whose, which, when).
- `personal.save` on `POST /operations` `{employee_id, expected_version, values, encrypted}`.
  `values` holds the plain fields and `encrypted` the numbers; a key that is absent keeps the
  stored value and `null` clears it. The person may send only `home_address`, `personal_phone` and
  the three emergency fields (anything else is 409 `personal_field_refused`). A stale version is
  409 `person_changed`. A number equal to the stored one is not a change. The receipt's
  `version` is 0 when nothing was saved and no record exists. A same-key retry returns the first
  receipt. The numbers are encrypted with `CREDENTIALS_KEY`, and the request fingerprint is an
  HMAC keyed from it, so it never holds a plain hash of a number. A 422 is `invalid_hr_input` and
  never repeats a submitted value.

**Documents.** The gateway needs `HR_UPLOAD_ROOT`; without it these routes answer 503
`hr_documents_unavailable`. With `STORAGE_BACKEND=s3` the files live in a bucket instead; see
[OBJECT_STORAGE.md](OBJECT_STORAGE.md).

- `GET /people/{employee_id}/documents` returns `DocumentList`: `items` of `DocumentOut`
  (`document_id`, `kind`, `file_name`, `media_type`, `byte_size`, `uploaded_at`,
  `uploaded_by_name`), newest first. The HR owner and Manage people holders see every document;
  the person sees their own `contract`, `identity` and `certificate` documents, not `other`. A
  person outside the tenant is 404.
- `POST /people/{employee_id}/documents?kind=` (`contract`, `identity`, `certificate` or `other`)
  takes one multipart field `file`: PDF, JPG, PNG or DOCX, at most 10 MB (413 `file exceeds 10
  MB`; more than one file is 422 `upload one file`). It returns 201 `DocumentOut`; a retry with the
  same `Idempotency-Key` returns the first document and stores one file. The content must match
  the extension (409 `hr_document_type_unsupported`); an empty file is 409 `hr_document_empty` and
  an unusable name 409 `hr_document_name_invalid`. HR owner and Manage people holders only.
- `GET /documents/{document_id}/download` streams the file with a safe `Content-Disposition`
  name. A file missing from the volume is 503 `hr_document_file_unavailable`.
- `POST /documents/{document_id}/remove` (`{}`) answers 204. Removal is soft: the record and its
  History entry stay and the file is deleted. Removing twice is harmless. HR owner and Manage
  people holders only.

**Leaving and rehire** on `POST /operations` (the HR owner or a Manage people holder):

- `person.leave` `{employee_id, expected_version, last_day, reason, note?, reports_new_manager}`:
  `reason` is `resigned`, `contract_ended`, `dismissed`, `retired` or `other` (`other` needs a
  `note`, at most 200 characters); `reports_new_manager` is an employee id or `null` (no manager).
  Marking again replaces the plan.
- `person.leave_undo` `{employee_id, expected_version}`: until the end of the last day.
- `person.rehire` `{employee_id, expected_version, start_date}`: someone who has left becomes
  active again. A manager who has left is dropped. No access comes back.

New refusals (409 with the code as `detail`): `last_owner_leaving` (the HR owner cannot be marked
as leaving; a company has one HR owner, so ownership must move first, which a tenant admin does
on the Team page), `leaver_has_access` (only
the HR owner marks someone whose account holds HR duties beyond the staff switches or an enabled
`tenant_admin` membership of the company; marking yourself is allowed), `last_day_past`,
`leaving_final` (already left, or nothing to undo),
`not_left` (rehire of someone who has not left), `person_not_active`, and, as for `person.update`,
`person_changed`, `manager_unavailable` and `manager_loop` (the new manager is the leaver or sits
under them).

**The finish.** The worker's `hr_leavers_round` (every 900 seconds) and HR Home's `automation.run`
call `hr_finish_leaver` for each due person. On the last day it moves the direct reports to
`reports_new_manager` (or to no manager when that person has left or now reports to the leaver).
The day after, the person becomes inactive, the linked account's HR grants, workflow rows, private
areas and HR owner row for the tenant are deleted, its membership is disabled and its sessions
revoked, and one `console_audit` row `hr.leaver_finished` is written. The account link stays.
Each step happens once, whoever runs it.

## Authentication, tenancy and settings

Development reset safety: `make db-reset` is destructive. Its default target is loopback
port **5434**, never production port 5433. A host `psql` client is required by default;
there is no automatic Docker fallback. An explicit `PSQL` override must address the same
database as `DEV_OWNER_DSN`: preflight compares database/system identifiers before any
schema drop, failing closed on missing clients, probe failure or mismatch. Use a named
disposable test container for reset validation; do not reset persistent dev merely to
validate application changes. Keep credentials in the process environment, not command
arguments or copied logs. See [current dev recovery status](./PROGRESS.md).

Human routes are mounted independently of `CONSOLE_API_TOKEN`. Configure
`IDENTITY_DATABASE_URL` using **csa_identity**, `CONSOLE_ALLOWED_ORIGIN` using the exact
web origin, and the frontend server-only `CSA_CONSOLE_API_ORIGIN` using the gateway
origin. `CONSOLE_COOKIE_SECURE=true` is the default; false is accepted only on loopback
dev/CI origins. HTTPS is required elsewhere. No proxy/forwarded headers establish identity.
`CONSOLE_SESSION_IDLE_SECONDS=1800` and `CONSOLE_SESSION_ABSOLUTE_SECONDS=43200` are
bounded and configurable. Missing identity configuration returns an explicit unavailable
response; configured pool failures fail startup/readiness. Both pools close at shutdown.

Configured logging uses a background output writer with a bounded 1,024-event waiting
queue (plus one active write). It does not perform output I/O on the request event loop.
`/healthz` remains process-only; `/readyz` also returns **503** when queued output is
stalled for more than five seconds, overflows, or its writer fails. The readiness reason
contains a secret-free failure code and `dropped_events` count. Overflow/writer errors
are latched until logging is reconfigured or the process restarts; restoring a merely
stalled sink allows readiness to recover if no events were lost. Operators must monitor
readiness and keep the output collector draining; this is not lossless logging under
permanent sink failure. Shutdown draining has a bounded deadline and raises on failure.

An upload's HTTP 500/network error does not prove its transaction rolled back. The
frontend retains the selected files and idempotency key for an **explicit retry**; do
not reselect/re-upload with a new key for an uncertain result. Retrying the same key and
same files returns the committed job, or reports a conflict if contents differ.
Proxy socket-reset and real database lost-acknowledgement cases are regression-tested.

Migration **0270** creates users, sessions, memberships, tokens, throttle buckets and an
append-only audit ledger. The identity role has no application-domain access or role
memberships. Session/token tables allow global identity lookups only to that role;
membership and tenant audit operations set transaction-local user/tenant context. RLS is
enabled without FORCE. Domain mutations and their audit entries share a tenant transaction.

Provision the new login role by explicitly passing `CSA_IDENTITY_PASSWORD` (≥12 characters)
to `scripts.migrate` against an isolated database. It is optional: without it the new role
remains NOLOGIN. Re-running the migration runner with this variable deliberately provisions
or rotates the role password without altering the migration ledger. Keep values out of argv,
source and logs. Bootstrap the first admin deliberately:

```sh
NO_LOCAL_ENV=1 uv run python -m scripts.bootstrap_console_admin \
  --create-first-platform-admin --email admin@example.test --display-name 'Synthetic Admin'
```

The CLI reads only its explicit `IDENTITY_DATABASE_URL` environment value, prompts with
`getpass`, closes bootstrap after the first platform admin, and requires a password change.
No copied accounts, passwords or sessions are imported. Synthetic test identities are not
installed into an existing dev database.

Add a further platform administrator only through the operator command; invites
create tenant members, never platform admins:

```sh
NO_LOCAL_ENV=1 uv run python -m scripts.add_platform_admin \
  --approver-email existing-admin@example.test \
  --email new-admin@example.test --display-name "New Admin" --add-platform-admin
```

The approver re-enters their own password (same limits as login, on a separate counter) and must be an
enabled platform administrator who has changed their initial password. The new
account must change its password at first login. Audit: `identity.platform_admin_added`
(approver as actor) or `identity.platform_admin_add_refused`.

Passwords support Unicode and use salted scrypt (N=131072, r=8, p=1), offloaded from the
event loop with bounded concurrency. Session secrets are 256-bit random opaque values;
only SHA-256 hashes persist.
The `csa_session` cookie is HttpOnly, host scoped, SameSite=Lax and Secure by default.
User disablement, session revocation, idle/absolute expiry and membership changes invalidate
access. Password changes revoke other sessions and rotate the current session and CSRF token.
Login throttling persists per normalized email and direct peer address; forwarding headers are
ignored. Invalid credentials use a generic response.

Every cookie mutation, including login, invite inspection/acceptance and reset requests,
requires an exact configured `Origin` plus double-submit `csa_csrf`/`X-CSRF-Token`; authenticated
CSRF is additionally bound to the session hash. Start with `GET /auth/csrf`. Responses are
`Cache-Control: no-store`; console validation errors omit raw input values. Forced password
change permits only identity inspection, CSRF, logout and password change before domain access.

Actors and roles come from verified sessions. Browser `X-Client-Id`, `X-Actor-Id`,
`X-Actor-Role`, `X-User-Id` and `X-Role` are rejected. An initial session selects its first
active membership; membership switching is deferred. Tenant users cannot select a different
`client_id`. Platform admins must explicitly select an existing tenant using `?client_id=UUID`
for tenant reads/mutations; they cannot invent an actor or grant platform status via JSON.

Machine access is separate: optional `CONSOLE_API_TOKEN` (≥32 chars), bearer authentication,
`X-Client-Id`, and `CONSOLE_MACHINE_CLIENT_IDS` (JSON UUID list). It permits existing domain
**reads only**, for allowlisted tenants. It cannot administer humans, onboard tenants, or
mutate capabilities/KB. Mixed bearer/cookie domain requests are rejected. Never expose the
machine token to browsers. Empty allowlist grants no tenant access.

## Private sandbox assistant (M4)

The private sandbox assistant lives on **Overview** (`/` for platform admins, `/portal` for tenant
members); `/chat` and `/portal/chat` only redirect there, keeping `client_id`. It is not a customer
inbox. It uses the verified session and tenant selector above.
All chat reads additionally require a human session and exact conversation ownership;
even another staff member or platform administrator cannot read someone else's sandbox.
Machine principals are denied. Read-only members can read their existing history but
cannot create, send or archive. Mutations use the existing role/Origin/CSRF checks.

Migration **0290** adds participants, threads and turns with staged tenant RLS, not FORCE.
Each `(client_id, user_id)` has its own newly created sandbox contact; the session user
UUID is never treated as a contact UUID and existing customer contacts are not adopted.
The existing queue requires endpoint routing metadata: the participant has a **disabled,
internal-web-only** endpoint and a `channel='web'` metadata record with no tokens.
No Telegram endpoint, credential, external webhook or infrastructure setting is changed.
There is no external send in the web-delivery branch.

All paths below use `/console/v1/chat` and the existing tenant-selection query:

| Method/path | Body/query | Result |
|---|---|---|
| `POST /conversations` | `{request_key: UUID}` | 201 thread; same actor/tenant/key returns the same thread |
| `GET /conversations` | `before=<next_cursor>` optional | Active owned threads, newest creation first, 20/page |
| `POST /conversations/{id}/turns` | `{request_key: UUID, body: string}` | Atomic ingress/turn/audit commit, 202 `{id,trace_id,duplicate}` |
| `GET /conversations/{id}/turns` | `before=<next_cursor>` optional | Owned turn history, newest ID first, 50/page |
| `POST /conversations/{id}/archive` | `{}` | Idempotent 204, one `chat.archived` audit; no deletion or cancellation |

Both list responses are `{items,next_cursor}`. Cursors are positive signed-bigint
IDs; pass the returned cursor unchanged for older pages. Turn IDs, trace IDs and
request keys remain stable across polling/retry. The browser refreshes loaded history
pages every 2.5 seconds and merges no content from another tenant/conversation.
Archived conversations leave the list but remain readable by their owner if the ID is
known. Accepted work continues; new sends to an archived conversation return 409.
An already accepted same-key replay still returns its original turn after archive.

Text is nonblank, maximum **4000 input characters** and **32 KiB JSON request**.
The server creates Envelope 3.0/`Channel.WEB`, all authority fields and a namespaced
ingress key. Extra fields (including contact, channel, callback, voice and attachments)
are rejected. Deterministic PII redaction happens before storing text in the envelope,
turn or message; placeholders may expand to at most 8000 persisted characters.
This is common-pattern redaction, not a promise to remove all personal information.
The fingerprint distinguishes different original text even if redaction would match.
Same-key changed text returns 409; no client-provided identity becomes authority.

Only `kb_agent.query`, `order_agent.search_products`, `order_agent.product_availability`
(our own catalogue only: no TikTok Shop read on the web) and `order_agent.product_detail`
can execute for web turns. The shared spoke runner denies everything else **before
agent execution**, including order status/customer history, KB administration,
invoice/payment/marketplace, booking/subscription/ticket, escalation and business writes.
Normal capability switches and grounding checks still apply to allowed queries.
Fact-free account-domain plans return the fixed sandbox-scope refusal instead of
asking for customer invoice/order/booking details, even when no agent task was planned.
Business confirmations are refused too. General policy answers grounded in allowed
KB facts remain available; this restriction does not change Telegram composition.
Runtime metadata, audit, KB gap telemetry and sandbox-local message persistence are
still written; “read-only” refers to business authority, not a read-only SQL transaction.
Callbacks are rejected before pending actions are consumed. No business confirmation
controls are exposed: this is the explicitly selected read-only narrowing of the M4 plan,
not an unimplemented mutation shortcut.

Turn statuses:

- `queued` / `processing`: accepted, not delivered; retries preserve the turn.
- `awaiting_delivery`: an outbox reply exists but is not yet locally available.
- `held`: tenant policy held the draft. The private held draft/reason are retained,
  not returned as a reply, not sent to staff, and not inserted into the expiring
  customer-approval queue. Release controls require a separately designed later slice.
- `available`: local web delivery and its attempt committed atomically. History returns
  only delivered replies, keyed by message ID; polling does not consume or duplicate them.
  This means private local availability, not a Telegram receipt or proof it was read.
- `failed`: terminal ingress/delivery failure or runtime fail-safe. A delivered local
  fail-safe may appear as text, but the turn remains failed; no staff follow-up is promised.
- `no_reply`: the runtime intentionally finished without an outbound reply.

Transient worker/delivery failures retain existing lease/retry/dead-letter behavior.
Web delivery validates the contact/conversation/endpoint/trace/message binding and never
decrypts Telegram credentials or calls an external transport. Telegram delivery remains
at least once; the web local-availability transaction does not change that guarantee.
Archive is audited hide, not retention-approved purge.

The browser distinguishes a **15-second HTTP timeout** (potentially accepted; retain the
same in-memory request key/text and explicitly retry) from a definite 4xx rejection and
from the **90-second turn deadline** (show delayed status, never invent delivery/failure).
Polling can reconcile an uncertain send by its request key. Check for new messages only refreshes
history; it never resends. If the page/tenant is left or reloaded, check persisted history
before submitting again; the retained draft/key are not stored in browser storage.
Voice, arbitrary attachments and confirmation actions remain unavailable. KB upload is
a distinct action.

The user explicitly approved guarded dev rollout despite the disclosed full-suite TCP
latency failure. Forward-only **0290** and guarded gateway/worker/delivery/frontend
replacement are complete on isolated dev; persistent dev was not reset. Health/readiness,
login and anonymous-access gates pass. The scope-fix full rerun has **674 passed /
2 latency failures**: ASGI p95 **508.717 ms**, TCP **298.288 ms**, unchanged `<200 ms`.
Lint and disposable-only reset pass. After disclosure of both failures, the user
explicitly approved worker-only replacement; the fix is running.
User-operated refusal, sandbox KB and fresh Telegram checks are metadata-corroborated.
Audited archive, history reload and browser-tab offline/online recovery also pass.
With separate explicit approval, the user privately registered one synthetic read-only
fixture through the normal invite flow. Agent-operated `/portal/chat` checks pass:
creation disabled, send/archive controls absent, owned list 200/empty, another owner's
active and archived histories 404, cross-tenant selection 403. No credentials, invitation
tokens or cookies were inspected. Reading the reader's own pre-existing history remains
test-proven rather than live-replayed because this new account has no history.
The latest uninstrumented focused reader/latency run is **2 passed / 1 failed**:
reader backend denial and ASGI pass (p95 **93.234 ms**); TCP fails (**261.451 ms**).
Instrumented standalone passes do not supersede either this failure or the full run.
The user subsequently accepted **functional M4 closeout with a laptop-dev performance
exception**, carrying latency work forward. Laptop hardware is reported context, not a
proven cause. Benchmarks and `<200 ms` remain unchanged; release performance acceptance
is still required. M5 may begin as a separate staff surface, not expanded sandbox
authority. The closeout and M5 prompt were removed from the repository (see git history).
See [current evidence](./PROGRESS.md). Offline tests/build are not live-browser evidence.
Future replacements still require scoped approval. No public rollout is implied.

## Telegram customer inbox (M5; guarded dev)

The user confirmed Telegram customer conversations only for `platform_admin` (explicit
selected tenant), `tenant_admin` and `tenant_staff`. Readers, machine principals and
anonymous requests are denied, including content reads. Customer-web and stock-changing
order decisions are deferred. Private sandbox histories/contacts and held turn fields
are excluded, including archived or mislabeled sandbox rows. `/inbox` and `/portal/inbox`
are distinct staff surfaces; `/chat` remains owner-private.

All paths use `/console/v1/inbox` with the existing tenant-selection query.

| Method/path | Request | Result |
|---|---|---|
| `GET /conversations` | `before=UUID`, `limit=1..50` (20 default), `channel`, `needs_review=true`, `sender=known\|new`, `q` (2..64 characters) | `{items,next_cursor,needs_review_count}` |
| `GET /conversations/{id}` | — | Ownership, mute and version metadata, plus `contact` with the full `phone` |
| `GET /conversations/{id}/messages` | `before=UUID`, `limit=1..100` (50 default) | Bounded redacted text, direction, author and actual delivery status |
| `GET /approvals` | `before=bigint`, `conversation_id=UUID`, `include_handled=false`, `limit=1..50` | Pending reply approvals; optionally settled history |
| `POST /conversations/{id}/takeover` | `{request_key: UUID, expected_version: int}` | Audited indefinite human ownership |
| `POST /conversations/{id}/release` | Same | Audited AI ownership; old turns stay suppressed |
| `POST /conversations/{id}/reply` | Same plus `body` (1–2000 chars) | 202 structured receipt with `message_id`, `queued:true` |
| `POST /approvals/{id}/decide` | `{request_key: UUID, decision: approve/reject/edit, text?: string}` | Structured applied/already_handled/expired result, status, queue flag and warnings |

Mutations enforce Origin/CSRF and reject extra fields; JSON is capped at 32 KiB.
Only edits accept/require `text`, with shared 2,000-character/control-character validation.
Takeover is required before a human reply. Actions serialize per conversation; stale
versions or changed content under a used key return 409. Mutation receipts, audit,
message and outbox share the transaction. Explicit same-key retry recovers committed
results after lost acknowledgement. Reads use stable time/ID keyset ordering. No raw
envelopes, routing credentials, attachment payloads or arbitrary source URLs are returned.

Muted new turns skip model/planner/spoke/composer work. Already-running computation may
finish, but escalation, emit, hold, fail-safe and delivery recheck ownership. Taking
over a temporary escalation clears its expiry and establishes indefinite ownership;
the UI offers **Take over indefinitely** for a temporary pause. A late escalation
cannot replace that ownership or re-mute an explicitly released stale turn.
Takeover and release
record a suppression boundary so older turns cannot start a stale send after release.
Takeover skips pending automatic sends; explicit human/reviewed replies remain authorized.
A code-selected handoff acknowledgement requires a matching escalation and is still
subject to the takeover boundary. Rejection/expiry keeps its normal fallback unless
ownership suppresses automatic replies; the decision/audit still commits.

The shared approval transition locks conversation then approval, closes Telegram staff
actions, and writes domain audit/outbox. Console actors use the verified user UUID;
Telegram callback/edit binding stays in the Telegram adapter. No second SQL approval
implementation or generic order-status mutation is introduced. Queued is not delivered.
Existing Telegram order callbacks retain metadata-only completed ingress provenance
for their customer acknowledgement, atomically with that message/outbox, so the stricter
delivery endpoint/trace binding does not reject those replies. This does not add console
order-decision authority or fabricate a Telegram staff identity.

Delivery serializes by conversation, including retries. It holds the conversation lock
through Telegram send and local acknowledgement; takeover waits for a send already in
progress. It cannot recall an accepted remote request. Remote acceptance followed by a
crash before local commit can still duplicate; delivery remains at least once.

Additive **0300** has staged RLS without FORCE. After disposable validation and explicit
user approval it was applied forward-only on isolated dev, without reset, with coordinated
service replacement. Platform-admin browser and synthetic Telegram acceptance are
verified; live reader UI/direct read/CSRF-valid mutation denial replay passes (403).
Representative tenant-admin/staff browser replay also passes: scoped reads, cross-tenant/
sandbox denial, takeover/human reply/release. Fresh decision role matrix retains automated evidence.
Future migration/replacement/live-send scopes require separate approval. See [M5 scope and evidence](
./handoffs/2026-10-17-m5-telegram-inbox.md). M4 latency failures remain recorded under the
accepted laptop-dev exception; unchanged release thresholds apply at M7/M8.
Post-review offline full validation passes **692 tests** plus lint; ASGI/TCP ack p95
**108.284 / 174.623 ms** meet the unchanged `<200 ms` threshold in that run.
Neither offline nor guarded-dev evidence is a guarantee under all laptop loads or
approval for public deployment.

## Telegram bot (one per company)

Migration `0494`. The platform team sets up a separate bot per company: they paste the company's
BotFather token and its staff chat. Routes under `/console/v1/channels/telegram` with the usual
tenant selection (a platform admin passes `client_id`). Human sessions only and behind the
`customer_support` package gate. Reading is open to any member of the company; every write needs a
platform admin, otherwise 403 `platform_admin_only`.

| Method/path | Result |
|---|---|
| `GET /telegram` | `{connected, bot_id, bot_username, webhook_registered, connected_at, staff_chat: {address, verified} or null, hooks_ready}`; database only, never calls Telegram |
| `PUT /telegram` | Body `{bot_token, staff_chat_id?, replace_webhook?}`. Connects the company's bot, or replaces the token of the bot it already has. With `staff_chat_id` it then saves the staff chat; if that fails the bot stays connected and the error is returned |
| `PUT /telegram/staff-chat` | Body `{chat_id}` (an integer string, negative for groups). The bot sends a test message first; only a delivered message saves the chat and marks it verified. A new chat retires the old one (kept for history) |
| `POST /telegram/check` | Live `getWebhookInfo`: `{webhook_url_matches, pending_update_count, last_error}` |
| `DELETE /telegram` | 204; deletes the webhook (best effort), erases the token, disables the endpoint and the staff chat. The bot is then free to connect to any company |

Connect proves the token with Telegram (`getMe`) before storing anything, then stores it and a
fresh webhook secret sealed with `CREDENTIALS_KEY`, registers the webhook at
`<hooks origin>/hooks/<public id>` (`HOOKS_PUBLIC_BASE_URL`, else `WHATSAPP_CALLBACK_BASE_URL`,
never the console origin) for messages and button taps, and only then shows the company as
connected. The token and secret are never returned, logged or audited. Audit actions:
`channels.telegram.connected`, `.token_replaced`, `.staff_chat_set`, `.disconnected` (target is the
credential id).

Refusals (`detail` is the code): 422 `telegram_token_malformed`, 422 `telegram_token_rejected`, 502
`telegram_unreachable`, 503 `hooks_origin_not_configured`, 409 `telegram_bot_in_use` (another
company holds the bot), 409 `telegram_bot_replace` (the company has a different bot: disconnect
first), 409 `telegram_webhook_in_use` (the bot's webhook points elsewhere; send
`replace_webhook: true` to take it over), 409 `telegram_connect_in_progress`, 502
`telegram_webhook_failed` (stored but not registered; connect again with the same token), 409
`telegram_not_connected`, 422 `telegram_chat_invalid`, 422 `telegram_chat_unreachable` (the bot is
not in that chat, or the id is wrong), 422 `telegram_chat_forbidden` (the bot was removed or
blocked), 503 `telegram_credential_unavailable`.

## WhatsApp channel

Lane A1 (migration `0470`). One Meta app per deployment; each tenant connects its
own WABA number. Setup steps are in
[STAGING_PIPELINE.md section 9](../deploy/STAGING_PIPELINE.md#9-connect-whatsapp-owner-and-client).

Console routes, under `/console/v1/channels` with the usual tenant selection.
Human sessions only (the machine token is refused) and behind the
`customer_support` package gate:

| Method/path | Who | Result |
|---|---|---|
| `GET /whatsapp` | any tenant member, platform admin | `{connected, display_phone, phone_number_id_last4, waba_id, enabled, callback_url, connected_at}` |
| `PUT /whatsapp` | `tenant_admin`, platform admin | Body `{phone_number_id, waba_id, access_token}`. Before storing, the gateway calls Graph `GET {WHATSAPP_GRAPH_BASE_URL}/{phone_number_id}?fields=display_phone_number,verified_name` with that token and stores Graph's `display_phone_number`: 422 `whatsapp_token_rejected` when Graph refuses (auth, permission or not found), 502 `whatsapp_unreachable` on a network error, 429 or 5xx. Nothing is stored or audited on either. The token is sealed with `CREDENTIALS_KEY`, never returned and never logged; audited `channels.whatsapp.connected`. 409 `whatsapp_number_in_use` when another tenant holds the number |
| `DELETE /whatsapp` | `tenant_admin`, platform admin | 204; disables the endpoint, erases the token and releases the number; audited `channels.whatsapp.disconnected` |

`callback_url` is null while the deployment's WhatsApp secrets are unset; otherwise
it is `WHATSAPP_CALLBACK_BASE_URL` (or `PUBLIC_BASE_URL`) plus `/channels/whatsapp`.
The web card shows it to platform admins only.

Webhook (not a console route; 404 while `WHATSAPP_APP_SECRET` and
`WHATSAPP_VERIFY_TOKEN` are unset or `disabled`):

- `GET /channels/whatsapp` answers Meta's handshake (`hub.mode=subscribe`, matching
  verify token, echoes `hub.challenge`); otherwise 403.
- `POST /channels/whatsapp` requires `application/json` (415) and a valid
  `X-Hub-Signature-256` over the raw body (403, no side effects). A broken envelope
  (not a JSON object, no `entry` list, no `metadata.phone_number_id`) is 422,
  oversized bodies 413, an endpoint over its rate limit 429. Inside a signed batch, a
  single message or status the adapter does not understand (for example an unknown
  status such as `deleted`) is skipped with a `whatsapp_item_skipped` warning (no body
  or token) and the rest is stored. Otherwise 200 `{ok, accepted, duplicates}`.
  Messages for an unknown or disconnected number are dropped with a warning; a retried
  message id is a duplicate, never a second turn.
- Every customer message type other than text reaches the coordinator as a live turn
  with an empty body and an attachment stub: media keep their kind, mime type and
  media id (none when Meta sends none); location, contacts, interactive, button and
  other types carry `kind="other"` and `mime_type="application/vnd.whatsapp.<type>"`.
  Reactions, `system`, `unsupported`, `unknown` and `request_welcome` events are
  stored as completed, inert rows.
- The planner is told that WhatsApp cannot take orders or show buttons, so it answers
  in words.

Delivery and statuses:

- Replies are free-form text and are sent only within 24 hours of
  `conversations.last_inbound_at`; later sends fail with `outside_service_window`.
- Terminal codes (no retry, dead-lettered, staff notified): `outside_service_window`,
  `recipient_not_on_whatsapp`, `token_invalid`, `provider_rejected`. `rate_limited`
  retries after Meta's delay. `channel_not_configured` while the secrets are unset.
- Inbox messages expose the code as `delivery_error`; the web inbox shows the
  service-window copy from the [writing guide](WRITING_GUIDE.md#errors).
- Meta's status callbacks set `outbound_deliveries.provider_status` to `sent`,
  `delivered`, `read` or `failed`, matched by `provider_message_id`; `failed` is never
  overwritten by a later status. A `failed` callback also stores Meta's error code in
  `outbound_deliveries.provider_error` (for example `131047`, the closed 24-hour
  window; `131026`, undeliverable). Most per-recipient failures arrive this way, after
  the send returned 200.
- Inbox messages carry `provider_status` and `provider_error`. The web inbox labels a
  failed status "not delivered" and shows a plain line: the service-window sentence for
  `131047`, otherwise "Not delivered." with the reason. Dead deliveries with
  `recipient_not_on_whatsapp`, `token_invalid`, `whatsapp_endpoint_unavailable` and
  `channel_not_configured` have their own plain-words lines; codes never reach the
  screen.
- HTTP 429 and Graph codes 130429, 131056, 80007 and 4 retry as `rate_limited`.
- Inbox conversations carry `channel` (`telegram` or `whatsapp`) and the list accepts
  `?channel=telegram|whatsapp`.

### Inbox read fields, filters, search and order

Each conversation also carries these additive fields (no behaviour change for existing
clients):

- `contact`: `display_name` (the stored name, or `null` when it is blank or only the
  contact's own number, as WhatsApp stores when the customer has no profile name),
  `telegram_username` (the customer's Telegram @username without the `@`, from migration `0500`; `null` when they have none, for WhatsApp and for any value that is not 4 to 32 letters, digits or underscores), `phone_masked`
  (last four digits, for example `••••0148`; `null` when no number is stored), `phone`,
  `kind` (`customer`) and `known`. `phone` is `null` in list rows; only `GET /conversations/{id}` returns the full number.
  Takeover, release and reply results carry no contact data, preview or approval count
  (`contact` and `last_message` are `null`), and neither do their stored receipts.
- `known` is true when the contact has at least one order, at least one subscription,
  or a non-blank email or address. Otherwise the sender is new.
- `last_message`: `preview` (first 120 characters of the redacted body, whitespace
  collapsed; the same text the thread shows) and `at`; `null` when there are no messages.
- `pending_approvals`: count of the conversation's approvals that are `pending` and not
  past `expires_at`.
- `handoff_note`: `{ text, at }`, the assistant's staff note from its newest escalation of
  this conversation that has one (migration `0560`; `at` is that escalation's time), or
  `null`. Only `GET /conversations/{id}` fills it. List rows, takeover, release and reply
  results, and their stored receipts never carry it (`null`). The text is model output
  shaped by the customer's message, at most 500 characters: clients show it as plain text
  and never as markup or a link, and it is never sent to the customer.

The page also returns `needs_review_count`: the number of conversations in the whole
tenant with at least one such approval. It ignores `channel`, `needs_review`, `sender`
and `q`, so a filtered list shows the same chip count.

List filters, all applied on the server across every page and combined with `channel`:

- `needs_review=true`: only conversations with a pending approval that has not expired.
- `sender=known` or `sender=new`: split by `known` above. Any other value is 422.
- `q`: search over the contact's name, phone and Telegram @username (so `isyah`,
  `aisyah_r` and `@AISYAH_R` all find `aisyah_r`). The text is trimmed, one leading `@` is ignored, and fewer than two characters
  is 422 `search needs at least two characters`; more than 64 characters is 422. Matching
  is case-insensitive and substring; `%`, `_` and `\` match only themselves. Phone numbers
  match on digits only when the text holds two or more digits, so `012 555`,
  `0125550148` and `+60125550148` agree. Message bodies are never searched. The text is
  personal data: it is not written to the audit log, and the gateway's access log drops
  query strings on `/console/` paths.

Order is last activity, newest first: the time of the latest message, or `opened_at`
when there are no messages, then `id` descending. `before` stays the id of the last
conversation on the previous page; the server derives its position from that
conversation's current activity time.

## Workspace assistant

The floating assistant dock (on every console tab) and the tab actions share one
session-authenticated, owner-private surface under `/console/v1/workspace`. It does not
reuse customer sandbox conversations, customer contacts, `AgentContext`, Telegram staff
actions or callback authority. The Staff workspace tab is removed and its routes
redirect. Platform administrators must select a tenant explicitly (`?client_id=`);
tenant administrators and staff are constrained to their active membership. `read_only`
members, machine principals and anonymous callers are denied by the workspace router,
and the dock is hidden for `read_only` and for platform admins without `?client_id=`.
`GET /auth/me` (and the other `MeOut` responses) carries `workspace_assistant`, which
mirrors `WORKSPACE_ASSISTANT_ENABLED`; while it is `false` (the default) the dock is not
rendered for anyone.
Browser identity, role and tenant claims are never accepted, and cookie-authenticated
writes keep the global Origin/CSRF checks. Responses are `Cache-Control: no-store`.

Migration **0370** added owner-private threads and turns (tenant RLS, session-bound
provenance, same-owner idempotency). Migration **0460** adds `console_proposals`
(including the stored `card`, its `origin`, `claim_token` and `claimed_at`), relaxes the turn CHECKs for
the new actions, and creates the `company_directory` and `company_directory_me` views.
Turn text is redacted before persistence. No bearer, cookie, CSRF token, unrestricted HR
payload or employee/contact identity mapping is stored.

| Method/path | Body/query | Result |
|---|---|---|
| `POST /workspace/conversations` | `{request_key: UUID}`, tenant query | Creates an owner-private thread; same owner/tenant/key returns the same thread (200) |
| `GET /workspace/conversations` | optional positive `before` sequence, tenant query | Active owner's threads, newest first, 20/page |
| `POST /workspace/conversations/{id}/turns` | `{request_key, body (1..4000), page_context \| page_hint}`, tenant query | 201 turn with the planned result; same-key replay is 200. `page_context` and `page_hint` together are 422 |
| `GET /workspace/conversations/{id}/turns` | optional positive `before` turn ID, tenant query | Owner-only turn history, newest ID first, 50/page |
| `POST /workspace/proposals` | `{kind, payload, thread_id?}`, tenant query | 201 `ProposalOut` for a tab-initiated action; the kind's executor gates the package and validates the payload before any row exists |
| `GET /workspace/proposals/{id}` | tenant query | The owner's proposal with its stored card and, once executed, its result |
| `POST /workspace/proposals/{id}/confirm` | none, tenant query | 200 `ProposalOut` (see below) |
| `POST /workspace/proposals/{id}/cancel` | none, tenant query | 200 `ProposalOut`; cancelling a requested proposal sets `cancelled` |
| `POST /workspace/proposals/{id}/retry` | none, tenant query | 201 `ProposalOut`: a new `plan` from the failed and not-run steps of an executed plan (see Plans) |
| `GET /workspace/people` | `q` (1..80), tenant query | Up to 10 people (see People) |
| `GET /workspace/people/me` | tenant query | The caller's employee link and manager |

`page_context` is `{tab, section?, record?}` where `tab` is one of `overview`,
`services`, `inbox`, `knowledge_base`, `business`, `hr`, `email`, `subscriptions`, `activity`, `chat` and
`record` is `{kind, id, version?}`; `kind` is one of `conversation`, `approval`,
`training_request`, `draft`, `order`, `employee` or `product` (a TikTok Shop listing of ours,
its local UUID, published by the Products & stock page while a TikTok listing card is open;
the Orders page publishes an open order as `order`). The legacy `page_hint` path (fixed page
guidance and a single local order read with `source:"local_records"`, `marketplace_sync`
`"connected"` or `"not_connected"` (whether the tenant has a connected TikTok Shop now; turns
stored before 4 Nov 2026 replay `"unavailable"`) and an `as_of` timestamp) is otherwise
unchanged; reusing a request key with different input, or
replaying an order result after its resource version changes, returns 409. A
missing or foreign order returns only `resource_unavailable`.

### Language

Every planned turn has one language, `"en"` or `"ms"`, returned as `WorkspaceTurnOut.language`
(default `"en"`) on the 201 response, the 200 replay and `GET .../turns`, and saved in the stored
turn result (no migration; a turn stored without it reads as English). The server guesses it from
what the user typed (`detect_language` in `csa/workspace/planner.py`, which ignores email
addresses, URLs and words both languages share) and decides it once per turn: a clear guess wins,
and when the message gives no clear guess (a typed address, a name, "ok", a clicked option) the
turn carries the previous turn's language, or English for the first turn of a thread. The turn's
answers, questions, locked-tab text and cards are written in that language; a locked tab is named
as the console's sidebar shows it (for example "People & HR"). The console panel (dock title,
buttons, card status and error lines, the email preview's labels) follows `language`, from the
latest turn for panel-wide text and from the turn or card for its own text; text the console
shows by name (tab names, addresses, subjects, bodies) is never translated. Limits: before any
turn exists the panel is English, so a first message that fails gets an English error; the HR
assistant chat and tab-origin cards stay English.

### Planner

A turn with `page_context` is planned by one model call that returns exactly one typed
proposal: `answer`, `navigate`, `directory_lookup`, `email_compose`, `email_reply`,
`email_team`, `inbox_summary`, `training_submit`, `inbox_decision`, `clarification`, the
people actions or `product_stock_read`. The planner only proposes; role, package, switch and
record-version gates are enforced afterwards by the turn service and the proposal
registry, so a wrong plan can at worst produce a refused turn or a card the user
declines. It runs only when `WORKSPACE_ASSISTANT_ENABLED=true` (default false; the
gateway then builds the planner from `GROQ_API_KEY` and `GROQ_MODEL`). Without a
configured planner a `page_context` turn returns 503 `planner_unavailable`.

- The prompt receives a server-built summary only: role, package states, the page, a
  redacted record summary (ids, statuses, versions, redacted titles/subjects; an open
  customer conversation also lists its pending held-reply approval ids and statuses), and
  the thread's last 6 stored turns. Each stored turn contributes its (already redacted)
  body, its action and result kind, and for a directory result or clarification the
  employee ids or option values it offered and which one the next message picked.
  Directory listings, names, addresses and record bodies are never sent.
- When the message mentions the user's boss or manager, the server resolves the caller's
  own manager (as `GET /workspace/people/me` does) before planning and passes only the
  manager's `employee_id`, or a reason (`no_employee_link`, `no_manager`,
  `package_inactive`).
- When the message is about training (English or Malay words such as "training",
  "course", "latihan", "kursus"), the server passes `my_employee`: `{linked,
  can_request_training, reason}`, with `reason` one of `hr_inactive`, `no_hr_access`,
  `no_employee_link`, `assistant_off` (the tenant's HR assistant switch is off) or null.
  When either flag is false the planner answers with the reason instead of proposing
  `training_submit`.
- The dock repeats the originating request in follow-up turns: picking a directory
  candidate sends `Use employee <id> for: <original message>`, and a clarification answer
  sends the option value with its question and the original message.
- A `directory_lookup` without the `hr` package is answered ("not part of the subscribed
  services") rather than refused. An `email_compose` to a directory employee
  (`recipient_employee_id`) requires the `hr` package, on this path and on
  `POST /email/compose`, and is refused with 403 `package_inactive` before any draft is
  written.
- The user's own message is sent to the planner unredacted; the stored turn body is
  redacted before it is persisted.
- A `navigate` to a tab that the console would not offer (the package and role rule of
  `navigationFor`, in `csa/workspace/rules.py` `tab_available`) is turned into an `answer`:
  the package is not active, or the tab is not available to the role.
- Planner failures return 503 `planner_unavailable` (provider timeout, transport or HTTP
  error; retryable) or 502 `planner_invalid_output` (output that is not JSON, or a refusal
  the server cannot name). A plan refused for a named rule is re-planned once (one
  correction per turn, shared with the content correction) and, if refused again, answered
  with a no-option question for the address on an email turn, otherwise a plain answer (see
  `docs/EMAIL_ASSISTANT.md`).
- TikTok Shop is read-only for the assistant (spec 6.8): no planned action or confirm card can
  change anything on TikTok Shop. An open order's summary adds, for a synced TikTok order,
  `TikTok · synced <age>` and TikTok's status code (states only: never the ship-to, buyer,
  phone or items). An open product's summary (from our copy) names the product, its variant
  count, the stock in our copy and the sync age. Both need the Business tab's package
  (`orders_products` or `marketplaces`); otherwise the summary says the service is not active.
- `product_stock_read` (`{query?, listing_id?}`) answers "how many do we have" with a
  `ProductStockResult`: `{action:"product_stock_read", source:"tiktok_shop", product, variants:
  [{variant, seller_sku, stock}], total, checked_live, on_sale, as_of, note}`. The server finds
  the listing in our copy (the picked listing, the words, or the open product record), then
  reads its product live through the fenced TikTok reader (the customer assistant's
  `csa.marketplace.reads.live_stock`), which refreshes our copy of the listing; it never
  writes to TikTok or to the tenant's own products. `note` is in the turn's language: "Checked
  live just now."; "Synced from TikTok just now." when our copy is under 60 seconds old, so no
  live call is made (`checked_live:false` is the normal answer inside that window); or "As of
  2 hours ago (live check failed)." from an older copy. Staff get every SKU of the product with
  its exact count whatever its TikTok status, from the copy as well as live; `on_sale:false`
  marks a product TikTok does not sell (the card says "Not on sale"). Several matching
  products give a `clarification` with up to 5 products (option value: a listing id); no
  connected TikTok Shop, no match, or no package gives a plain `answer`. The turn row stores
  the action as `answer` (the action column's check list is set by migration 0521); the stored
  result keeps `product_stock_read`.
- A failure of the email-drafting model (invalid output, provider error or timeout) on an
  `email_compose`, `email_reply` or `email_team` turn returns 503
  `email_model_unavailable`; nothing is drafted or stored.

### Proposals and confirmation

Every mutation is a proposal the user confirms on a card. The kinds are:

- `email_export`: freeze a draft and save it to the user's own Gmail drafts;
- `email_send`: send a draft from the user's own Gmail (see
  [Email management](#email-management));
- `training_submit`: submit a training request. When the card is made (and again at
  confirm) the caller must have their own employee record (409 `no_employee_link`) and the
  `training.submit` grant, plus `training.draft` for a new request (403
  `hr_training_not_permitted`), so a refused submit never leaves a draft behind;
- `inbox_decision`: approve or reject a held customer reply;
- `subscription_action`: a [subscription state change](#subscriptions), requested from
  the Subscriptions tab.

`ProposalOut` is `{id, kind, status, card, result, created_at, expires_at}`; the card
holds `proposal_id`, `kind`, `title`, `summary`, label/value `fields`, `expires_at`, `expected_version`
and `language` (`"en"` or `"ms"`, default `"en"`): the language the card's title, summary and field
labels are written in. It is stored with the card, so confirm and cancel responses and a reloaded
card keep it, and it never enters the proposal payload or its hash. A card made from a tab
(`POST /workspace/proposals`) and a card stored before the field existed read `"en"`. The email
cards' hidden `Subject` and `Draft` sentinel fields keep their English labels in both languages.

Statuses are `requested`, then `confirmed`, then `executed`, or `cancelled` / `expired`.
A proposal lives 15 minutes and is visible only to its owner.

Confirm succeeds only when all of these hold: the caller is the login session that
created the proposal (and the request carries its CSRF token); the status is
`requested`; it has not expired; the stored payload still matches its SHA-256 hash; and,
when `expected_version` is set, the executor's re-read of the record still returns that
version. The package gate is re-run at confirm.

- Confirm takes an exclusive 2-minute claim lease (`claim_token`, `claimed_at`, measured
  on the database clock). A second confirm during the lease gets 409 `proposal_consumed`.
  A stale claim, left by a confirm that died, may be retaken after re-checking expiry and
  the package gate; the executor then runs again and replays its receipt.
- A repeat confirm of an executed proposal returns 200 with the stored result (replay).
- Executors use idempotency keys derived from the proposal id, so the underlying action
  never runs twice. If an executor fails, the caller's own claim is released.
- Navigating to a different tab (a planned turn whose tab differs from the thread's last
  turn) cancels the thread's `requested` proposals on the server. In the browser the dock
  also cancels its requested cards whenever the route's tab changes (sidebar links, back
  button or a `navigate` result), when the tenant changes, and when "New conversation"
  drops them. Logout does not cancel proposals: it ends the login session, and because
  confirm is bound to the session that created a proposal, its requested proposals can no
  longer be confirmed and simply expire.

| Code | Status | Meaning |
|---|---|---|
| `package_inactive` | 403 | The required package is not active (a tenant switch that is off surfaces as 409 `agent_disabled` instead) |
| `proposal_session_mismatch` | 403 | Confirmed from a different login session than the one that created it |
| `proposal_not_found` | 404 | Unknown, foreign or not owned by the caller |
| `proposal_expired` | 409 | Past its 15-minute lifetime; the status is persisted as `expired` |
| `proposal_consumed` | 409 | Already confirmed or cancelled, or another confirm holds the lease |
| `proposal_stale` | 409 | Payload hash or `expected_version` no longer matches |
| `planner_unavailable` | 503 | No planner configured, or the model provider failed |
| `planner_invalid_output` | 502 | Non-JSON output or an unnamed refusal; named schema failures are corrected once |
| `email_model_unavailable` | 503 | The email-drafting model failed on a compose, reply or team turn; try again |
| `mailbox_not_connected` | 409 | An `email_export` or `email_send` card was asked for while the user's mailbox is not connected; checked when the card is made, not only at confirm |
| `no_employee_link` | 409 | A `training_submit` card was asked for, or a training draft saved, while the caller's login is not linked to an employee record |
| `hr_training_not_permitted` | 403 | A `training_submit` card was asked for without the training grants it needs (`training.submit`, plus `training.draft` for a new request) |
| `reply_approval_not_found` | 404 | An `inbox_decision` card named a held reply that is gone or belongs to another conversation |

Audit actions carry the actor, tenant, proposal id and `{"kind": ...}` only:
`workspace.proposal.created`, `workspace.proposal.confirmed`,
`workspace.proposal.executed` and `workspace.proposal.cancelled`. A people search writes
`workspace.people.lookup` with details `{"candidates": n}` only, never the query or a name.

### Plans

A `plan` proposal puts several allowed steps (`email_send`, `email_export`, `booking_action`,
`inbox_decision`, `training_submit`, `hr_person`; at most 20, at most 10 of them not email)
behind one card and one confirm. Only the assistant makes one (origin `assistant`):
`POST /workspace/proposals` rejects the kind with 422 `plan_not_creatable`. The card holds a
fixed title per step (an email step also carries its draft id as the row value) and counts, never
an address, name, subject or body. Confirm runs each step on its own, up to four at a time, and
returns `result.steps`: one entry per step with `index`, `kind`, `status` (`done`, `failed`,
`skipped` for a step that changed since it was reviewed, or `not_run`), a `code` when it did not
complete and the step's `receipt` when it did.

`POST /workspace/proposals/{id}/retry` makes a new plan, stored as the assistant's and bound to
the caller's login session (another session gets 403 `proposal_session_mismatch`; another user
sees 404 `proposal_not_found`), from the executed plan's `failed` and `not_run` steps only. A
`done` or `skipped` step is never retried. Each step's version is read again from its own
card, so a step whose card now refuses (a draft already sent, a closed slot) is left out and
counted on the new card's summary, never named. The answer is 201 with `Cache-Control:
no-store`; the new plan is confirmed like any other. Refusals, all 409: `plan_not_executed` (the
plan has not run), `plan_nothing_to_retry` (no failed or not-run step is left to propose),
`proposal_not_retryable` (the proposal is not a plan).

### Gates

| Action | Requires |
|---|---|
| Inbox decision | `customer_support` package `active` or `suspended` |
| Training submit | `hr` package active; training proposals created by an assistant (the workspace planner, or the deprecated `POST /console/v1/hr/assistant` route) also require the tenant's `hr_assistant` switch; tab-originated proposals do not |
| People search and `people/me` | `hr` package active (`people/me` reports `package_inactive` as a `reason`, not an error) |
| Email export, compose and send | `staff_email` package active and `email_settings.enabled` |
| Email my team | The above, the `hr` package, and the HR access switch "approves team" |

### People

`GET /console/v1/workspace/people?q=` returns up to 10 of `employee_id`, `display_name`,
`job_title` and `work_email` from the `company_directory` view, for every tenant staff
member whose tenant has the `hr` package active. Nothing else about a person is exposed.
A best-effort, in-memory throttle allows 60 lookups per minute per login session (429
`people_lookup_rate_limited`); it resets on restart and is not shared across workers.

`GET /console/v1/workspace/people/me` returns `{employee_id, manager, reason}` for the
caller from the caller-only `company_directory_me` view, which is keyed on the caller's
identity and never on an HR grant. `reason` is `no_employee_link`, `no_manager` or
`package_inactive`. The organisation chart is not exposed to colleagues.

### Email compose

`POST /console/v1/email/compose` (201, with the usual email idempotency key) takes exactly
one of `recipient_employee_id` (resolved through the directory; 422
`recipient_unresolved` when the person has no work email) or `recipient_address`, plus a
subject, instructions and an optional language (default `auto`). It returns a model-written draft with
`thread_id` null and `source="model"`, exported through the existing export flow (and so
through an `email_export` proposal). Nothing is sent.

### Email management

All routes sit under `/console/v1/email`, take the tenant `client_id` query, need the
`staff_email` package and the tenant switch, and (except the reads) take an
`Idempotency-Key`. Sending uses the staff member's own Gmail; no scope beyond
`gmail.readonly` and `gmail.compose` is needed. The behaviour and limits are in
[EMAIL_ASSISTANT.md](EMAIL_ASSISTANT.md#sending).

| Route | Body | Result |
|---|---|---|
| `GET /drafts/{draft_id}` | none | `DraftOut` for the owner; 404 otherwise. The console uses it to show recipients and body on a card |
| `PUT /drafts/{draft_id}` | `{to?, cc?, bcc?, subject, body}` | Edited `DraftOut` (`recipient` is the old single-To field) |
| `POST /drafts/{draft_id}/send` | `{version}` | `SendResult` `{outcome, sent_at, gmail_message_id, remaining_this_hour}` |
| `POST /drafts/{draft_id}/release` | none | `DraftOut` back in `draft`, owner only |
| `POST /threads/{thread_id}/reply` | `{instructions?, language? (auto\|en\|ms), cc?, bcc?}` | 201 `DraftOut` addressed by the reply rule; 409 `reply_target_unavailable` when the only target is the owner |
| `POST /sync/more` | none | `Receipt` for the next page of 20 threads; `{"threads": 0, "done": true}` once the mailbox holds 200 threads or cleanup removed the whole page |
| `GET /attention` | none | `AttentionPage` `{as_of, items, skipped}` |

`DraftOut` carries `to`, `cc`, `bcc`, `recipients` (each address with an `outside` flag),
`status` (`draft`, `sending`, `sent` or `exported`), `sent_at`, `needs_staff_input`,
`reply_to_message_id` and `version`. The server computes `version` from the thread,
recipients, subject and body; a client sends it back on `send` and never computes it.
`EmailContext` gains `last_synced_at` and `more_available`.

`GET /oauth/callback` is where Google sends the browser back after Connect Gmail (`code`, `state`,
`iss`, optional `error`; the session must be the one that started it). It is always a 303
(no-store) to `/portal/connections`, or to `/connections?client_id=<company>` when the callback
carried `client_id`, with `?connected=1` or `?error=<code>` (`oauth_state_invalid`,
`provider_denied`, `oauth_issuer_invalid`, or a Gmail or policy code). The Connections tab says the
result once and removes those parameters; `client_id` stays.

Email errors:

| Code | Status | Meaning |
|---|---|---|
| `draft_changed` | 409 | The draft changed since the version was read; read it again |
| `draft_already_sending` | 409 | Another send of this draft is in flight |
| `draft_already_sent` | 409 | The draft was sent |
| `draft_already_exported` | 409 | The draft was saved to Gmail drafts and is frozen |
| `draft_needs_staff_input` | 409 | A `[STAFF:` placeholder is still in the draft |
| `send_limit_reached` | 429 | 20 sends in the last 60 minutes; the body has `retry_after` in seconds |
| `send_refused` | 409 | Gmail refused the message with another 4xx; the draft is back in `draft` |
| `mailbox_reauthorization_required` | 409 | Gmail answered 401, or 403 for a reason other than a quota limit; reconnect the mailbox |
| `provider_rate_limited` | 409 | Gmail answered 429, or 403 with the reason `userRateLimitExceeded`, `rateLimitExceeded` or `dailyLimitExceeded`; the draft is back in `draft` |
| `invalid_message` | 409 | Gmail could not accept the message; the draft is back in `draft` |
| `recipients_invalid` | 422 | Not 1 to 20 valid addresses in To, CC and BCC together |
| `send_outcome_unknown` | 502 | Timeout, 5xx, 408, 409, 499 or a 200 without an id. The draft stays `sending`; the body has `message` "Check your Gmail Sent folder before trying again." |
| `draft_not_releasable` | 409 | Release needs the owner, status `sending`, no Gmail id and a claim at least 120 seconds old |
| `send_released` | 409 | A replay of a send key whose outcome was unknown, after the owner released the draft (it is back in `draft`). Gmail is not called; send again with a new key |
| `reply_target_unavailable` | 409 | The only reply target is the mailbox owner |

A send makes at most one Gmail call, with a 25-second deadline and no retry, after
claiming the draft under a per-mailbox lock. Each claim carries a `send_claim_id`, so a
late outcome from a released attempt never changes the row.

`GET /attention` reads stored data only and runs at most 5 analyses. It leaves out
`newsletter_or_promotion`, `spam_or_phishing` and threads whose latest message the owner
sent, and returns up to 10 items. It skips threads it cannot analyse and counts them in
`skipped`. It is refused for `read_only` members. `GET /threads` lists every cached
thread, up to 200. A thread's time is Gmail's `internalDate` for its latest message (the
`Date` header only when that is missing), and a message labelled `SENT` (send-as aliases
included) counts as the owner's.

`email_send` proposal: the card follows the user's own words, never a planner question or
a clicked option, nor text equal to an option any earlier card offered. The first match wins:
a draft word (draft, drafts, draf, save, simpan) or a negated send (don't send, do not send,
never send, jangan hantar) gives an `email_export` card; a send word (send, hantar, kirim)
gives a send card unless a write word (write, tulis, karang) is there and the planner does not
ask to send; an email verb (email, e-mail, e-mel) gives a send card when the planner also asks
to send and the user wrote no write word; anything else is an `email_export` card (see
[EMAIL_ASSISTANT.md](EMAIL_ASSISTANT.md#workspace-assistant)). In the console only the newest
assistant card's options and candidates can be clicked; older cards are history.
Its stored payload is `{draft_id, version}`; the card's title, summary
and fields hold counts only, and so does the `email_export` card now. `expected_version` is
the draft version, so an edit after the card was made makes the confirm return 409
`draft_changed` and nothing is sent. The `email_export` card's `expected_version` is the
same `draft_version`, bound to To, CC, BCC, subject and body, so any edit makes it
`proposal_stale`. The executor uses an idempotency key derived from the
proposal id. A refused confirm returns the send error body above (for example
`send_limit_reached` with `retry_after`). The executed result is `{draft_id, outcome,
sent_at, gmail_message_id, remaining_this_hour}`, with no content.

Planner actions: `email_reply` takes a `thread_id` from the mailbox facts (its To is never
chosen by the model), `email_team` drafts one email to the caller's active direct reports
(refusals `package_inactive`, `not_team_approver`, `no_employee_link`, `no_reports`,
`no_work_email` and `team_too_large` come back as plain answers), and `inbox_summary`
answers from `GET /attention`; its failures are also plain answers, and the stored turn
says only "Inbox summary: N items." Any address the model proposes must appear in the
user's own message; an address seen only in the mailbox can never become a recipient. An
employee id in To, CC or BCC must be in the message, among the ids an earlier directory
turn offered, or `my_manager`; any other id is refused, corrected once, then met with a
no-option question asking the user to type the address.

### HR assistant delegation

**Deprecated.** `POST /console/v1/hr/assistant` is kept for one release, removed in a cleanup
PR. The console no longer calls it: the separate HR chat left People & HR Home in project 1b,
and the workspace assistant dock is the only assistant.

`POST /console/v1/hr/assistant` keeps its HR planner and plan replay (`hr_chat_plans` is
still written). A training submit no longer executes: it returns a `ProposalOut` with 201
(a replay of the same request key returns 200 with the same proposal id), to be confirmed
through the workspace proposals API. With the tenant `hr_assistant` switch off the call
returns 409 `agent_disabled`: training proposals created by the assistant also require the
tenant's `hr_assistant` switch (rechecked at confirm); tab-originated proposals
(`POST /console/v1/workspace/proposals`) do not, because the human confirming the card is
the safety boundary. Each stored proposal records its `origin` (`tab` or `assistant`).

## Subscriptions

Lane B1 (migration `0480`). The Subscriptions tab manages a tenant's plans and its
customers' subscriptions. Every state change is a confirm card (the workspace proposal
kind `subscription_action`); the tab never changes a subscription directly. Nothing is
charged: the module records state and dates only.

Routes, under `/console/v1/subscriptions` with the usual tenant selection (the collection
has no trailing slash). Human sessions only (the machine token is refused) and behind the
`subscriptions` package gate (active status only):

| Method/path | Who | Result |
|---|---|---|
| `GET /settings` | any tenant member | `{timezone, pending_expiry_days, version}`: the tenant's IANA zone (`clients.timezone`), so the tab shows dates on the tenant's calendar, and the days a pending subscription waits before it expires (default 14 until the company saves a value; `0` means never; `version` is 0 until the first save) |
| `PUT /settings` | writers | Body `{pending_expiry_days (0-90), version}`; returns `SettingsOut`. The first save makes the row (send `version: 0`); a save from an older read is 409 `version_conflict`; out-of-range values are 422; audited `subscriptions.settings.updated` |
| `GET /plans?include_archived=false` | any tenant member | `PlanOut[]`: `{code, name, price, currency, billing_interval, features, sort_order, is_active, signup_mode, is_signup_open}` |
| `POST /plans` | writers | Body `{code, name, price, currency?, billing_interval?, features?, sort_order?, signup_mode?, is_signup_open?}`; 201. `code` is `[a-z0-9_-]{1,64}`, `price` is a non-negative amount with two decimals, `billing_interval` is `month` or `year`, `signup_mode` is `staff` (default: staff decide a sign-up) or `auto` (a sign-up is approved at once), `is_signup_open` (default true) says whether customers can sign up for the plan in the chat. 409 `plan_exists`; audited `subscriptions.plan.created` |
| `PATCH /plans/{code}` | writers | Any of `name`, `price`, `billing_interval`, `features`, `sort_order`, `is_active` (`false` archives), `signup_mode`, `is_signup_open`. 404 `plan_not_found`; 409 `plan_in_use` for a changed `billing_interval` while an active, paused or pending subscription is on the plan or has it as `pending_plan` (subscriptions keep their price but renew on the plan's interval; price and other edits stay allowed); audited `subscriptions.plan.updated` |
| `GET` (the collection) | any tenant member | `{items, next_cursor}` of `SubscriptionOut`. Filters `q` (name, email or, when the term reads like a phone number, digits), `status`, `plan`; keyset `before` and `limit` (1-100, default 50) |
| `GET /customers?q=&limit=20` | any tenant member | `CustomerRef[]` (`{id, full_name, phone, email}`): the tenant's contacts to put on a plan, by name or email (`%` and `_` are literal) or, when the term reads like a phone number with 3 or more digits, by phone digits; no `q` lists everyone by name. `limit` 1-50 |
| `GET /{id}` | any tenant member | `{subscription, events}`, events being the timeline. 404 `subscription_not_found` |
| `POST` (the collection) | writers | Body `{contact_id, plan_code, started_at?, renews_at?}`; 201 `SubscriptionOut`, active at the plan's current price. An explicit `renews_at` sets the cycle's day; a defaulted one keeps `started_at`'s day. 422 `plan_unavailable` (missing or archived plan), 422 `renews_at_before_start`, 404 `contact_not_found`; audited `subscriptions.created` |
| `POST /{id}/actions` | writers | Body `{action, reason?, ...}`; 201 `ProposalOut` (the card), `Cache-Control: no-store`. Nothing changes until the same login session confirms the card at `POST /console/v1/workspace/proposals/{id}/confirm` |
| `GET /signups?status=pending\|decided&before&limit` | any tenant member | `{items, next_cursor}` of `SignupOut` (newest first, keyset `before`, `limit` 1-100; `pending` is the default, `decided` is approved, declined and expired) |
| `POST /signups/{id}/approve` | writers | No body; returns the approved `SignupOut`. Creates one `pending` subscription at the plan's price now. 404 `signup_not_found`; 409 `signup_not_pending`; 409 `signup_has_plan` (the customer already has an active, paused or pending subscription on the plan, for example added by hand; the request stays pending for staff to decline); 409 `plan_not_open` (the plan was archived or closed to sign-ups; the request stays pending); audited `subscriptions.signup.approved` |
| `POST /signups/{id}/decline` | writers | Body `{reason?}` (at most 300 characters; optional body); returns the declined `SignupOut`. 404 `signup_not_found`; 409 `signup_not_pending`; audited `subscriptions.signup.declined` |
| `GET /links?contact_id=&limit=` | any tenant member | `LinkOut[]` (newest first, `limit` 1-200, default 100): `{id, survivor, loser, reason (verified_phone\|staff), created_at, unlinked_at, can_unlink}`, `survivor` and `loser` being `{id, full_name, phone, channels}`. With `contact_id` only links where that contact is survivor or loser (404 `contact_not_found` for a contact of another company). `channels` is the distinct `contact_identities.channel` of the contact now; for the loser of a link not yet undone it also lists the channels its identities had before the link moved them to the survivor. `can_unlink` is true while the link is not undone, is under 30 days old and no later active link involves either contact: the same rule `unlink` enforces. Never returns what a link moved, email or a Telegram account |
| `POST /links` | writers | Body `{contact_a, contact_b}`; 201 `LinkOut` with reason `staff`. The survivor is the contact holding a subscription, else the older. 404 `contact_not_found` (unknown contact or another company's); 422 `same_contact`; 409 `contact_merged` (either is already a merged shell); 409 `link_refused:<code>` with the refusal after the colon (`telegram_conflict`, `same_channel_identity`, `table_conflict`; nothing changed); audited `contacts.link.created` |
| `POST /links/{id}/unlink` | writers | No body; 200 `{skipped}`, the contact columns the unlink left alone as `role.column` (for example `survivor.telegram_id`: changed since the link, or taken by another contact), `[]` for a plain unlink. 404 `link_not_found`; 409 `link_not_active` (already undone), `unlink_window_closed` (older than 30 days), `later_link_active` (undo the newer link first), `survivor_merged` (the survivor was linked into another contact since); audited `contacts.link.removed` |
| `GET /link-reviews?status=open\|resolved&limit=` | any tenant member | `ReviewOut[]` (newest first, `limit` 1-200, `open` is the default): `{id, contact_a, contact_b, reason (several_matches\|telegram_conflict\|same_channel_identity\|table_conflict\|previously_unlinked), status, resolution (linked\|dismissed\|null), created_at}`, the contacts being `{id, full_name, phone, channels}` as in `LinkOut` |
| `POST /link-reviews/{id}/resolve` | writers | Body `{resolution: dismissed\|linked}`; returns the settled `ReviewOut`. `dismissed` closes the review and links nothing; `linked` links the pair as staff (reason `staff`; 409 `link_refused:<code>` leaves the review open, and 409 `contact_merged` when one of the pair is already a merged shell: dismiss it). 404 `review_not_found`; 409 `review_not_open`; audited `contacts.review.dismissed` or `contacts.review.linked` |
| `GET /requests?status=open\|decided` | any tenant member | `{items, next_cursor}` of `RequestOut`, the customer requests staged by the subscription agent, with the decision once one exists |
| `POST /requests/{id}/decide` | writers | Body `{decision: approve\|decline, note?, resume_at?}`; returns the decided `RequestOut` |

"Writers" are `tenant_admin` and `tenant_staff` in their own tenant, and platform admins
who select a tenant with `?client_id=`. Every member of the tenant may read, including
`read_only`; a `read_only` writer attempt and any other tenant's id are 403. The static
paths (`/settings`, `/plans`, `/signups`, `/links`, `/link-reviews`, `/requests`, `/customers`) are registered before `/{id}`. The Customers tab's
"Add subscription" form uses `GET /customers` to find the customer, then `POST` (the
collection) with an active plan and an optional start date.

### Statuses and flags

`status` is `pending`, `active`, `paused` or `cancelled` (a database check since `0480`, `pending` since
`0580`; legacy values become `cancelled` in the migration). A `pending` subscription is an approved
customer sign-up that staff have not started: no `renews_at` and no renewal anchor, so it cannot be
paused or renewed, only started (`activate`) or cancelled. `version` increases on every change and binds a card.
Flags: `cancel_at_period_end` (cancel when `renews_at` passes), `pending_plan` with
`pending_effective_at` (a scheduled plan change) and `resume_at` (a date on which a paused
subscription resumes).

### Actions

`ActionIn` is `{action, reason?, resume_at?, plan_code?, effective_at?, renews_at?, price_override?}`.
A field outside the action's own set is refused (422), so a card never shows less than the
payload carries.

| Action | From | Fields | Effect |
|---|---|---|---|
| `activate` | pending | none | "Start the subscription": active; `started_at` and `activated_at` are now; `renews_at` and the renewal anchor are one billing period on the tenant's calendar from now |
| `pause` | active | optional `resume_at` (date, after today on the tenant's calendar) | paused; `resume_at` set when given |
| `resume` | paused | none | active; `resume_at` cleared; a lapsed or missing (`NULL`) `renews_at` becomes one billing interval from now |
| `change_plan` | active, paused | `plan_code` (required), `effective_at`, `price_override` | absent or past `effective_at` changes now at the new plan's current price; a future one sets `pending_plan` and `pending_effective_at` |
| `cancel_at_period_end` | active, not already flagged, with a `renews_at` | none | sets `cancel_at_period_end` |
| `cancel_now` | active, paused, pending | none | cancelled immediately; `cancelled_at` set; pending plan, resume date and period-end flag cleared |
| `extend_renewal` | active | `renews_at` (required) | moves `renews_at`; must be after the current one |

Each executed action records one timeline event keyed by the proposal id (`activated`,
`paused`, `resumed`, `plan_changed`, `plan_change_scheduled`, `cancel_scheduled`, `cancelled`,
`renewal_extended`), so a confirm replayed after commit returns the same receipt rather
than acting twice. The card runs the same checks at create and again at confirm; a
subscription that changed in between fails the confirm with 409 `proposal_stale`. An
immediate `change_plan` card also binds the target plan's price, currency and billing
interval (`expected_version` reads `<version>:<price>:<currency>:<interval>`, for example
`1:59.90:MYR:month`); the executor reads them again under the row lock, so a plan edit
between the card and its execution is 409 `proposal_stale` and nothing is applied. A
scheduled change binds the version only, and its card says the price will be the plan's
price on that day (with today's price for reference).

Refusals (422 unless noted), raised before any proposal row is written and again at confirm:

| Code | Meaning |
|---|---|
| `action_not_allowed` | The current status does not allow the action, the period-end flag is already set, or a period-end cancel has no `renews_at` (the period would never end; extend the renewal or cancel now instead). An approval of `request_cancel` for such a subscription is refused the same way and records no decision |
| `plan_unavailable` | The target plan is missing or archived |
| `plan_unchanged` | `change_plan` to the plan already held, with no override |
| `price_override_requires_immediate_change` | An override on a change scheduled for later |
| `renews_at_not_after_current` | `extend_renewal` to a date that is not later |
| `renews_at_before_start` | A new subscription whose `renews_at` precedes `started_at` |
| `request_has_no_subscription` | A request whose agent payload named no subscription (it can be declined, not approved) |
| `request_subscription_mismatch` | The request's subscription is missing or belongs to another customer |
| `request_has_no_plan` | A change request that names no plan |
| `request_action_mismatch` | The proposal is not the action the request asked for |
| `resume_at_applies_to_pause_only` | `resume_at` on the approval of anything but a pause request |
| `resume_at_not_after_today` | A pause (or a pause approval) whose `resume_at` is today or earlier on the tenant's calendar |
| `request_decided` (409) | The request already has a decision |
| `proposal_stale` (409) | The subscription changed after the card was made, or (immediate `change_plan`) the target plan's price, currency or interval did |

### Price rule

`change_plan` takes the new plan's current price. Editing a plan's price never changes an
existing subscription; the price a subscription pays moves only through a plan change. A
`price_override` needs a non-empty `reason` and applies only to an immediate change; the
timeline records `price_override: true` with the old and new price. A scheduled change is
applied by the due job at the plan's price on the day, not the price quoted when it was
scheduled.

### Sign-ups

A customer signs up for a plan in the chat; the bot files one `signup_requests` row (migration
`0580`) and the request waits for staff, or for the plan: a plan with `signup_mode: "auto"`
approves it in the same transaction as the filing. `SignupOut` is `{id, contact: {id, full_name,
phone, email}, plan_code, plan_name, full_name, phone, phone_verified, status, decided_via,
decline_reason, subscription_id, created_at, decided_at, source_channel}`. `contact` is the
contact record as it stands. `full_name` and `phone` are what the customer typed (the phone
normalised); the typed phone stays on the request and is never copied to the contact, and no
route edits a contact's phone: when the customer already has another contact, staff link the two
contacts by hand (`POST /links`). `phone_verified` is true only when the
contact's phone was verified by its channel and equals the typed number. `source_channel` is the
channel of the contact's newest identity (null when it has none). `status` is `pending`,
`approved`, `declined` or `expired`; `decided_via` is `staff` or `auto`.

- One open request per customer and plan: a repeat returns the open one, and a live (active,
  paused or pending) subscription on the plan files nothing.
- Approve (and the automatic approval) locks the request, refuses unless it is pending (409
  `signup_not_pending`), refuses (409 `signup_has_plan`) a customer who already holds the plan,
  reads the plan again (409 `plan_not_open` unless active and open for sign-ups) and creates the
  `pending` subscription at the plan's price and currency then, with a `signup_approved` timeline
  event (actor the approving user, or `system:signup_auto`). Two
  approvals at once, or a staff approval racing an automatic one, give one subscription and one 409;
  filing and approving take a per-customer-and-plan lock first, so two filings at once never leave
  two live subscriptions.
- Decline stores the optional reason for staff only; it is never sent to the customer.
- Staff start a pending subscription with the `activate` action; otherwise it expires after the
  company's `pending_expiry_days`.

### Request queue

The subscription agent stages customer requests (`request_pause`, `request_cancel`,
`request_change`) without acting on them. `GET /requests?status=open` lists the undecided
ones, `decided` the rest (newest first, keyset `before`, `limit` 1-100).

- Decline writes a decision row (`declined`, optional note) and audits
  `subscriptions.request.declined`.
- Approve makes the server create and confirm the card itself, as the approving user:
  `request_pause` runs `pause` (the approval may add `resume_at`), `request_cancel` runs
  `cancel_at_period_end` and `request_change` runs an immediate `change_plan` to the
  requested plan at its current price. The executor checks that the proposal is exactly
  that action, on the subscription the request names, and that the subscription belongs to
  the requesting customer, so a card made through the workspace proposals route cannot
  approve a request with a different action or for another customer's subscription.
- The approved decision row is written in the same transaction as the subscription
  change (and audited `subscriptions.request.approved` there). A request has one
  decision, so approve, decline and a second approve are mutually exclusive: the first to
  commit wins and the others change nothing and answer 409 `request_decided`. A failed
  approval leaves the request open for any staff member to retry.

### Due job

`subscriptions_due` runs every 5 minutes for each active tenant, with actor
`system:subscriptions_due` as the timeline actor. It changes nothing (zero counts) for a
tenant whose `subscriptions` package is not `active`: a requested or suspended package, or
none, gets no automatic changes, using the same package check as the route gate. Four rules run in this order, each claiming up
to the job's batch size (100) of rows with `FOR UPDATE SKIP LOCKED`:

1. Cancel at period end: an active, flagged subscription whose `renews_at` has passed
   becomes `cancelled` (`cancelled_at` is that date). A scheduled plan change is dropped
   with it, as `cancel_now` does; the event payload is `{cleared_pending_plan}` (`null` when
   there was none). It runs first so a cancelling subscription is never renewed in the same
   pass.
2. Pending plan: a due pending change applies at the plan's current price and clears the
   pending fields (`plan_changed`). An archived or missing pending plan, or a cancelled
   subscription, only clears them (`pending_change_skipped`).
3. Resume: a paused subscription whose `resume_at` is today or earlier resumes, with a lapsed
   or missing `renews_at` set as in a manual resume.
4. Renew: an active, unflagged subscription whose `renews_at` has passed advances to the
   next step of its renewal cycle (`renewed`).

A row moves at most one step per rule per run, so a long-lapsed renewal catches up one
interval per run. Nothing is charged. Renewal and resume arithmetic and the resume date
use the tenant's calendar (`clients.timezone`, default `Asia/Kuala_Lumpur`).

Renewals keep the cycle's day of the month. Each subscription counts its renewals from a
stored anchor (`subscriptions.renewal_anchor`, not exposed by the API) in whole billing
intervals of its plan, so a renewal clamps to the last day only in a shorter month and
returns to the day afterwards (31 Jan, 28 Feb, 31 Mar). A subscription created with an explicit `renews_at` takes that date's day as the cycle's day; a defaulted one keeps `started_at`'s day. An extension moves the cycle to
the chosen date. A resume with a lapsed or missing `renews_at` (manual or by the job)
restarts the cycle from the resume time. A plan change between monthly and yearly, applied
now or by the job, counts the new interval from the end of the current period.

Audit actions: `subscriptions.plan.created`, `subscriptions.plan.updated`,
`subscriptions.created`, `subscriptions.request.approved`, `subscriptions.request.declined`,
plus the `workspace.proposal.*` rows the registry writes for every card.

## Business management (M6A/B; offline local reads and store records)

Only verified human platform administrators (explicit selected tenant), tenant
administrators and tenant staff may read `/console/v1/business`. Readers, machines
and anonymous principals are denied. No marketplace client is called. Store metadata
queries select only local identity, expiry, update and sync-progress columns, never token columns or `meta`;
no credential content is read, decrypted or inspected.
The only write routes are the TikTok Shop connect and disconnect routes below (Admin
only); normal session/tenant controls remain authoritative.

| Method/path | Query | Response |
|---|---|---|
| `GET /business/orders` | `before=<order id>`, `limit=1..50` (20 default), optional `channel` and `shop_id`, optional `from`, `to`, `status`, `payment_status`, tenant query | `{source:"local_records",marketplace_sync:"connected"\|"not_connected",items,next_cursor}`; each row also carries `provider_status`, `synced_at` and `buyer_cleared` (null, null, false for local orders), never ship-to or lines |
| `GET /business/orders/counts` | optional `from`, `to`, `payment_status`, `channel`, `shop_id`, tenant query (a `status` parameter is ignored) | `{total,by_status:{<status>:int}}` with every order status present, zero included |
| `GET /business/orders/{id}` | optional `channel` and `shop_id`, tenant query | Order summary (tracking/carrier, channel/store/order IDs, timestamps, `provider_status`, `synced_at`, `buyer_cleared`) plus `lines:[{sku,name,qty,unit_price}]`, `subtotal`, `shipping_fee`, `discount` and `ship_to:{recipient_name,phone,address_text,region}` **only for a synced TikTok order** (channel `tiktok_shop` with a provider order ID). For any other order those five fields are `null` and its stored ship-to is never returned. Once the buyer details were cleared (90 days after delivery or cancellation) `ship_to` is `null` and `buyer_cleared` is `true` |
| `GET /business/listings` | optional `store_id`, `q` (1..100 characters, substring of product name, variant or SKU, wildcards literal), `before=<listing UUID>`, `limit=1..50` (20 default), tenant query | `{items,next_cursor}`; each item has `id`, `product_name`, `variant_name`, `seller_sku`, `price`, `currency`, `stock_qty`, `product_status`, `image_url` (only an `https://` URL, else `null`), `synced_at` and `same_sku_product` (name of the tenant's local product with an equal SKU, looked up at read time, `null` without a SKU). Sorted by name then ID; a foreign or missing cursor is 404. Admin or staff, same package gate as the other business routes |
| `GET /business/products` | `before=<UUID>`, same bounds/tenant query | `{source:"local_catalogue",marketplace_sync:"connected"\|"not_connected",items,next_cursor}` |
| `GET /business/products/{id}` | tenant query | Local product summary, decimal price and local stock/reserved/available quantities |
| `GET /business/stores` | optional `platform=tiktok_shop\|shopee\|lazada`, `before=<record UUID>`, `limit=1..50` (20 default), tenant query | `{source:"local_credential_records",marketplace_sync:"connected"\|"not_connected",items,next_cursor}` |
| `GET /business/stores/{record_id}` | optional `platform`, optional `shop_id` (requires platform), tenant query | Local store identity/expiry and stored lifecycle status; exact tenant/record/platform/store match when bound |
| `POST /business/stores/tiktok/connect` | body `{store_id: UUID\|null}`, tenant query; Admin or platform admin, `marketplaces` package | `{authorization_url}` (no-store). No `store_id`: first connect on the tenant's pending store (reused, or created). With it: reconnect of that store. 503 `tiktok_unavailable` while TikTok is off; 404 `store_not_found` |
| `GET /business/stores/tiktok/callback` | `code`, `state`, optional `error` (sent by TikTok); session required | Always 303 (no-store) to `/portal/connections` or `/connections?client_id=…` with `tiktok=connected`, or `tiktok=failed&reason=` one of `tiktok_unavailable`, `provider_denied`, `state_invalid`, `multiple_shops`, `no_authorized_shop`, `shop_in_use`, `insufficient_scope`, `connect_failed`. Staff get 403 |
| `POST /business/stores/{store_id}/disconnect` | tenant query; Admin or platform admin, `marketplaces` package | 204 (a repeat is also 204); 404 `store_not_found` for another tenant's, a missing or a pending store |

Order/product keysets sort `(created_at,id)` descending; foreign/missing resource or cursor404, bad
UUID/limit422. Monetary values serialize as decimal strings. Neither customer addresses/
contacts (except the TikTok order detail above) nor arbitrary items/attributes/credentials/provider payloads are projected.
Local update timestamps are not marketplace-sync evidence. Product local active status
is not a published listing; stock is not synchronized and is not cross-store authority.
These pages do not claim live tracking, connection health or import completeness.
Tickets, invoices/PDFs, preparation and provider-backed operations remain staged in the
expanded M6 plan (removed from the repository; see git history).
Implementation is offline; the last approved M5 services have not been replaced.

Order filters match persisted `channel` (`telegram`, `web`, `tiktok_shop`, `shopee`,
`manual`) and `external_shop_id` exactly; they are not provider or connection discovery.
`shop_id` requires a `channel` (400 without it), is 1..100 characters, and stays opaque
(no trimming, wildcard matching or case folding). Invalid channel/empty or oversized store
IDs return422. Same store IDs on different platforms or tenants do not combine.
Filtered detail/cursor reads return404 if the order does not belong to the selected
tenant/channel/store, including a cursor moved to a different store. A missing store
returns an empty local page, not a connection-health claim. Omitting filters preserves
all tenant-local orders, including missing store IDs. Products remain tenant-wide local
catalogue/stock; no store-specific listing or inventory mapping is inferred.

Order list window and status filters (all optional, combine with each other, with `channel`,
`shop_id` and `before`, and stay tenant-scoped): `from` and `to` are ISO 8601 timestamps with an
offset (for example `2026-03-10T00:00:00+08:00`) on `created_at`, the column the list sorts and
pages on; `from` is inclusive and `to` exclusive, so a row stamped exactly at `to` belongs to the
next window. `status` is one of `draft`, `pending_confirmation`, `confirmed`, `processing`,
`shipped`, `delivered`, `cancelled`, `refunded`; `payment_status` is one of `unpaid`,
`cod_pending`, `paid`, `refunded`, `failed`. An unknown value is rejected with 422
`invalid_status` or `invalid_payment_status`, `from` at or after `to` with 422 `invalid_range`,
and a timestamp without an offset with the standard 422 validation error. With none of them the
response and the `(created_at, id)` descending order are unchanged. Orders with no `placed_at`
are listed and counted like any other (the window is on `created_at`). The order list objects
carry no customer or line-item fields; only the single-order read does, and only for TikTok orders.

`GET /business/orders/counts` returns `{total, by_status}` for the same `from`, `to`,
`payment_status`, `channel` and `shop_id` filters (same validation, same tenant, package and
role gating as the list). It deliberately does not read `status`, so a status chip bar can show
the number behind every chip while one status is selected. `by_status` lists all eight order
statuses in the order above; a status with no matching order is `0`, and `total` is their sum.

The UI applies order filters explicitly and remounts order paging on changed applied
scope, aborting obsolete requests and starting from newest. Tenant changes reset filter
drafts and all record state. Draft filter edits do not change applied results until Apply.
The applied scope remains visible; inventory is explicitly labelled unaffected.

The order date range, the status chips and the payment-status select act at once (only the
source and store ID wait for Apply). The page builds every query with `URLSearchParams` and
sends the chosen days as UTC instants from `toISOString()`, never a local offset, and leaves
empty parameters out. The counts read starts beside the list and is aborted when its filters
change; a status chip changes only the list. With no dates chosen the page asks for the counts
over the last 30 days (the unwindowed read scans the whole tenant and can be slow); "Count all
time" is an explicit choice that sends no window. A failed or malformed counts read removes the
numbers from the chips and never shows them as zero. "Older orders" adds the next page to the
rows on screen.

Store records contain `id`, `platform`, `shop_id`, recorded `expires_at`/`updated_at`,
derived `expiry` (`unknown`, `expired`, `not_expired`), and three summary fields that follow
`connection.status`: `authorization` is `verified` for `authorization_recorded`, else
`unverified`; `permissions` is `granted` for `authorization_recorded`, `missing` for
`permissions_missing`, else `unknown`; `provider_reads` is `available` for
`authorization_recorded`, else `unavailable`. They also contain
`connection:{status,live_integration}` (`live_integration` follows `provider_reads`),
`sync` (`null` except for TikTok stores: `{orders_synced_at,products_synced_at,problem}`, where
`problem` is `"sync_failing"` while a failure notice is open) and `can_manage` (true for
platform admins and the tenant's Admin, false for staff). `marketplace_sync` on the order,
product and store pages is `connected` when the tenant has a TikTok store that can sync now
(active grant, bound shop, not disconnected, live tokens), else `not_connected`. This is a
metadata-only stored lifecycle observation, not a lease, a provider request or proof
of current external authorization. List and detail use the same tenant-scoped
projection; one bounded batch query enriches each page, not one lookup per store.

`connection.status` is one of:

| Status | Local meaning |
|---|---|
| `unsupported` | Platform is not TikTok; this seller lifecycle is not implemented |
| `missing_shop_id` | Exact shop ID is absent |
| `revoked`, `disconnected` | Latest exact-store grant is terminal |
| `expired` | Credential or selected grant expiry timestamp has passed |
| `unverified` | No exact-store grant, future verification time or unknown credential expiry |
| `app_unavailable` | Referenced app is missing, disabled or on a different revision |
| `binding_missing` | No stored shop-binding evidence exists for the selected grant |
| `permissions_missing` | Required order/shop scopes are absent from stored grant metadata |
| `authorization_recorded` | Local app revision/generation/expiry/scope/binding metadata matches; **not verified live** |

The latest exact-tenant/record/platform/shop grant is selected by credential generation,
verification timestamp then grant ID; retained older terminal history cannot mask a
newer authorization. Terminal state precedes expiry; credential-generation mismatch
is disconnected. App revision/state checks precede grant expiry and stored binding/scope
checks. This advisory status may change immediately after reading and cannot authorize
provider dispatch. No token presence/validity, ciphertext integrity, external app registry
availability or fresh scopes are tested.

Connect/Reconnect controls are disabled with no handlers. No Business authorization
mutation or callback route is added, and no synthetic verifier/legacy OAuth path is invoked.
Live authorization requires eligible app/seller access and separate integration/approval;
Partner account registration alone does not prove eligibility or a Sandbox exemption.
Expiry is evaluated against an aware UTC clock with equality treated as expired.
`not_expired` means only a future local timestamp, not valid tokens, granted scopes,
seller authorization or working integration. Null/missing store IDs remain visible as
incomplete local records and cannot satisfy an exact store binding. Arbitrary `meta`,
tokens, app secrets, cipher values, scope values, attempt state/code and credential-presence
checks are excluded. Stored scopes are tested only as booleans inside SQL; values are
never returned. The shop-binding query tests row existence without selecting cipher data.

Store pages use immutable record UUID descending keysets, not update/connection recency.
Only marketplace-channel records are included; Telegram/other channel records are not.
Foreign/missing/platform-mismatched cursor or record404, invalid platform/UUID/limit422.
Detail `shop_id` without platform400; empty/oversized store IDs422. An exact binding
combines server-verified tenant, record UUID, platform and opaque store ID; it resolves
metadata only. This does not implement a credential decryptor or authorize provider calls.
Unknown permissions/verification, revocation and token validity cannot be inferred from
the existing table. No connect/revoke/token-refresh/write endpoints are added.
The UI resets store paging on platform/tenant changes and explicitly separates these
records from verified connections. No live or browser acceptance is implied by build/tests.

## Implemented M1 lifecycle and onboarding

All paths below have prefix `/console/v1`. Models reject extra fields. Login/me/accept/change
return `{user:{id,email,display_name,is_platform_admin,must_change_password}, memberships:
[{client_id,role}], active_client_id, role, csrf_token}`.

| Method/path | Body or query | Permission/result |
|---|---|---|
| GET `/auth/csrf` | — | Obtain/refresh CSRF; recover from stale session cookies |
| POST `/auth/login` | `{email,password}` | Verified session; generic invalid-credential error |
| GET `/auth/me` | — | Verified identity, current roles/memberships, `workspace_assistant` flag (dock shown only when true) |
| POST `/auth/logout` | — | Revoke current session, clear cookies; 204 |
| POST `/auth/password/change` | `{current_password,new_password}` | Change own password (12–1024 chars), rotate session |
| POST `/auth/password/reset` | `{email}` | Generic 202 directing user to admin; no account disclosure |
| POST `/invites/inspect` | `{token}` | Valid invite/reset metadata; never a token-bearing API URL |
| POST `/invites/accept` | `{token,password,display_name?}` | Single-use acceptance, membership/session creation |
| GET/POST `/admin/clients` | POST `{slug,name,timezone?}` | Platform admin; list (up to 200) / audited creation |
| GET `/profile` | Platform selection if applicable | Verified tenant profile |
| POST `/admin/invites` | `{email,display_name,client_id,role,hr_preset?}` | Platform admin; issue an invitation, emailed when mail is on |
| POST `/team/invites` | Same | Selected tenant admin/platform; no platform role grants |
| GET `/admin/invites` | `client_id=UUID` | Authorized tenant admin/platform; pending/used/revoked/expiry metadata, and `hr_preset` (`null` for none) |
| POST `/admin/invites/{id}/renew` | — | Revoke old pending token, issue replacement (keeps `hr_preset` and who chose it); same scoped authorization |
| DELETE `/admin/invites/{id}` | — | Revoke invitation; reset-token revocation requires platform admin |
| GET `/team` | `client_id=UUID` | Selected tenant admin/platform; member list |
| GET `/team/hr-owner` | `client_id=UUID` | Selected tenant admin/platform; `{available,owner}`, where `owner` is `{user_id,display_name,usable}` or `null`; package off gives 200 with `available=false` and `owner=null` |
| POST `/team/hr-owner` | `Idempotency-Key`; `{client_id,new_owner_id,expected_owner_id}` | Selected tenant admin/platform; moves the HR owner at once and emails the people affected; `{owner,mail}`. See [Moving the HR owner](#moving-the-hr-owner) |
| PATCH `/team/{user_id}` | query `client_id`; `{role?,is_enabled?}` | Scoped member change; revoke affected tenant sessions; protect last admin |
| GET `/admin/users` | bounded `limit` | Platform admin; safe user metadata |
| PATCH `/admin/users/{id}` | `{is_enabled}` | Platform admin; disable revokes sessions/tokens; protect last admins |
| POST `/admin/users/{id}/reset` | — | Platform admin; revoke sessions/tokens, force change, issue reset |
| POST `/admin/users/{id}/revoke-sessions` | — | Platform admin; audited revocation |
| GET `/admin/audit` | optional `client_id`, bounded `limit` | Platform admin; global events or selected tenant events |

Roles are `platform_admin`, `tenant_admin`, `tenant_staff`, `read_only`. Tenant admins can
manage their tenant memberships and client switches, staff can perform authorized tenant
writes, and read-only users cannot mutate. Membership changes and disablement protect the
last active tenant administrator; platform disablement also protects the last platform admin.

Invitations/reset tokens are hashed, expire after 48 hours, and are consumed under locks.
The UI uses `/invite#token=...` and immediately clears the fragment before submitting the
token in JSON.

**Delivery (B4).** Invite, renew and admin-issued reset return `IssuedTokenOut`:
`{id, token, expires_at, delivery, delivery_note}`. With invitation mail on
(`INVITE_MAIL_ENABLED` and both `SMTP_USERNAME` and `SMTP_PASSWORD` secrets, sent through
the Google Workspace relay `smtp-relay.gmail.com:587` with STARTTLS, 10 s timeout),
the link is emailed after the token commits: `delivery` is `email`, `delivery_note` is
`null` and **`token` is `null`**, so the raw token never leaves the server. Otherwise
`delivery` is `manual` and the raw token is returned once to the authorized issuer, with
`delivery_note` saying why: `mail_off` (mail is not configured, which is normal) or
`send_failed` (the relay refused, timed out or failed; the user and token still exist
once, with no retry). Failures are logged by category only (`smtp_disabled`,
`smtp_auth_failed`, `smtp_refused`, `smtp_timeout`, `smtp_error`), never with the address,
link or body; a message that fails to build is `smtp_error` too. The gateway names itself
in EHLO as `SMTP_EHLO_HOSTNAME` (default `fourstacks.my`). With `INVITE_MAIL_ENABLED` on but
a relay secret missing or empty, startup logs one warning, `console_mail_inactive` with
category `secrets_missing`, and mail stays off. Reset links expire in `Asia/Kuala_Lumpur`
time; invitation links in the tenant's `clients.timezone`, which is checked before the
invitation is written only while mail is on (with mail off an invalid zone does not block
inviting). Renewing an invitation that has no tenant is 404 `pending invitation not found`.

**HR access on an invitation.** `InviteIn.hr_preset` is one of `staff`, `manager`,
`hr_team`, `payroll_preparer`, `payroll_reviewer`, or omitted for no HR access. Any
inviting admin may choose none or `staff`; the other presets only the tenant's HR owner
(403 `hr_preset_requires_hr_owner`, checked again at apply time). A preset on a tenant
without the `hr` package is 422 `hr_not_available`. The preset and who chose it are stored
on the token (migration 0483) and applied when the invitation is accepted; see
`/access/invitations` under "HR access page" for one that must wait for the HR owner.
Existing credentialed accounts cannot be overwritten
through invitations; cross-tenant account attachment and membership switching remain deferred.
Admin-issued password resets require an account that has already set its password.
A pending invitation returns 409 without revoking its token; renew or accept that invitation
instead. This prevents reset acceptance from bypassing the membership-creation step.

Tenant creation atomically writes the tenant, nine disabled registry rows with current
Pydantic input schemas, and an actor-attributed audit event. It starts no channel, provider,
worker or external operation. Deliberate subscription/tenant enablement is separate.

### Moving the HR owner

A tenant admin of the company or a platform admin, who needs no membership, can read and move the
HR owner. A tenant admin gains no HR data by holding that role: they learn who the owner is and
nothing else. `usable` is false when the owner's account or membership is disabled or their role is
`read_only`.

`POST /team/hr-owner` runs `hr_owner_move` (migration 0522) on the app pool. `expected_owner_id` is
required, and `null` means "no owner yet". A new owner gets no payroll access.

| Answer | Detail | When |
|---|---|---|
| 403 | `tenant administrator required` | The caller isn't a tenant admin of this company or a platform admin |
| 422 | `hr_not_available` | The company's HR package isn't active |
| 409 | `owner_read_only` | The person chosen is a read-only member |
| 409 | `owner_leaving` | The person chosen is marked as leaving, or their linked person isn't active |
| 409 | `owner_unavailable` | The person chosen isn't an enabled member, or is still on their first password |
| 409 | `owner_unchanged` | The person chosen is already the owner |
| 409 | `owner_changed` | `expected_owner_id` is not the current owner |
| 409 | `idempotency_conflict` | The key was already used for something else: another person, another expected owner or another operation |
| 409 | `try_again` | A rare deadlock with another HR action; nothing changed, so the same request can be sent again |

A 200 means the move has committed. `owner` is who holds the title now, so a retry after a later
move names the later owner. `mail` is one of:

- `sent`: every email went out.
- `not_sent`: mail is off, or at least one email failed. The move stands, and the page asks the
  admin to tell the people affected. Logs carry a failure category only.
- `repeat`: this key already ran with this request. Nothing changed and no email went out.

One email goes to the old owner (even if they're disabled), the new owner and each enabled tenant
admin, once per address. It names the company, who made the change, the old and new owner and the
time in the company's timezone, or the console's default zone if that timezone is invalid. A
platform admin appears as "FourStacks admin". There is no link and no HR data.

## Endpoints
### `GET /console/v1/traces?limit=50&before=<ISO time>`
One row per customer turn, newest first. Page by passing the last `started_at` as `before`.
```json
{"trace_id":"…","conversation_id":"…","started_at":"…","finished_at":"…","steps":14,"had_error":false,
 "route":"plan_args","intent":"order","gate_decision":"send","agents":["order_agent"]}
```
`gate_decision`: `send` (sent), `approve` (held for staff), `reject` (shadow mode, nothing sent).

### `GET /console/v1/events?trace_id=<uuid>&after_id=0&limit=100`
Ordered timeline steps. Poll for live updates by passing the largest `id` you have as `after_id`
(every 1–2 s while a trace is open). `trace_id` is optional (omit for a tenant-wide live feed).
```json
{"id":812,"trace_id":"…","conversation_id":"…","node":"run_spoke","agent":"order_agent",
 "status":"ok","duration_ms":41,"summary":{"task_id":"t1","ok":true,"agent_status":"ok"},"created_at":"…"}
```

### Nodes and summary keys
| node | meaning | summary keys |
|---|---|---|
| load_ctx, guard | context loaded, safety check | – |
| signals | configured classifier judged the message | `intent`, `provider` (`llm`, `jev` or callback `policy`) |
| route | policy chose a path | `route` |
| fast_plan / plan_args | plan made | `intent`, `strategy`, `confidence`, `tasks` |
| run_spoke | an agent ran (`agent` set) | `task_id`, `ok`, `agent_status`, `error_code`, `requires_human` |
| collect | results merged into facts | – |
| observe | agent loop looked at the results and chose read-only follow-ups | `step`, `next_tasks`, `loop_stop` |
| handoff | escalation decision | `handoff_required`, `handoff_reason` |
| compose / verify | reply written / checked | `language`, `fact_count`, `verified`, `verify_issue_count` |
| gate | policy verdict | `decision`, `reason`, `risk` |
| hold / emit | reply held for staff / queued to send | `messages` |
| finalize / fail_safe | turn closed / safe fallback used | `error` |

The classifier is the LLM unless the company's policy stores a `decide_mode`, or it stores none and the
worker's `CUSTOMER_DECIDE_MODE_DEFAULT` is `cascade` with a Jev key present; the `provider` key then
says which answered. The staff assistant's own Jev decisions (batch email, routing, the inbound
injection check) are not turns and add no timeline nodes; they are `decision_log` rows with a node
named `assistant_email`, `assistant_routing` or `assistant_inbound_injection` (docs/RUNBOOK.md).

**Agent loop.** After the first agents run, `observe` may start another round of read-only lookups
(for example product details after a search). Follow-up `run_spoke` events have task ids `s<step>-<n>-<agent>`,
so the console can draw rounds: `plan → run_spoke × n → observe (step 1) → run_spoke → observe (step 2)`.
`loop_stop` says why the loop ended: `done`, `rejected:not_read_only`, `rejected:duplicate`,
`rejected:over_budget` or `observe_failed:<error>`. Turns that are not LLM-planned have no `observe` event.

`status` is `error` when the step failed (`summary.error` has the code). Summaries never contain customer text
or secrets; they are codes, counts and names, so they are safe to render.
Semantic rejection details stay in turn state for regeneration; only their count appears on the timeline.
Classifier comparisons are persisted separately in `decision_log`; a console endpoint for those rows
is not implemented yet.

## Bookings

Stage 1, PR 1 (migrations `0510`, `0511`). The Bookings package sells on its own: a company
does not need Customer support, and activating it moves an `onboarding` company to `pilot`
(one `tenant.status_changed` audit row), as Customer support does. This section covers the
setup routes, then the booking and calendar routes.

Routes, under `/console/v1/bookings` with the usual tenant selection. Human sessions only
(the machine token is refused) and behind the `bookings` package gate (active status only).
Reads need membership of the company (a platform admin chooses it with `?client_id=`).
Writes need an Admin of the company or a platform admin who chose it; staff and read-only
members get 403. A `PATCH` carries the `version` read, and a stale one gets 409
`version_conflict`. Every change writes a `bookings.*` audit row. Names, addresses, notes,
links and email addresses must be one short line of plain text (they end up in mail headers and
calendar files); an address is one plain `name@host` with no spaces, commas or angle brackets.
The schemas are published in `docs/console_openapi.json` as `Booking*` (`BookingPersonOut` and so on,
so they never clash with HR's `PersonOut`).

| Method/path | Result |
|---|---|
| `GET /people` | `BookingPersonOut[]`: `{id, display_name, user_id, employee_id, email, linked_to_hr, is_active, version}`. Only an Admin or platform admin is sent `email` and `employee_id`; everyone else gets `null` for both. `linked_to_hr` is sent to everyone and is true when the person has a stored `employee_id` or their account is linked to an HR person (the link is read live, so one made after the person was added counts). The write routes answer an Admin only, so they carry the address and the HR id |
| `POST /people` | Body `{display_name, user_id?, employee_id?, email?}`; 201. `user_id` must be an enabled member of this company and `employee_id` an HR person of this company, else 422 `member_not_found` / `employee_not_found`; a member already listed is 409 `person_exists` |
| `PATCH /people/{id}` | `{version, ...}` and any of the same fields plus `is_active`; a field sent as `null` is cleared. 404 `person_not_found` |
| `POST /people/from-team` | Adds each enabled console member not yet listed, linking the account and the HR person (if any); returns the people added, `[]` the second time. Works for a company without HR |
| `GET, POST /resources`, `PATCH /resources/{id}` | Rooms and shared things: `{id, name, capacity, is_active, version}`. 404 `resource_not_found` |
| `GET, POST /types`, `PATCH /types/{id}` | Booking types: name, `duration_minutes`, `buffer_minutes`, `audience` (`customers`, `staff`, `both`), `delivery` (`named`, `any`, `several`), `resource_rule` (`none`, `specific`, `any`), `location_kind` (`in_person`, `phone`, `online`), `address`, `meeting_link`, `needs_confirmation`, `min_notice_minutes`, `max_advance_days`, `change_cutoff_minutes`, `person_ids`, `resource_ids`. 422 `type_needs_people` (`named`/`several` with nobody), `type_needs_resources` (`specific`/`any` with no room), `type_needs_address`, `type_needs_link` (an `https://` link); `person_not_found`, `resource_not_found` for ids outside the company. The address or link not used by `location_kind` is dropped |
| `POST /types/starters` | Creates "Appointment" (30 minutes) and "Consultation" (60 minutes), for customers, by any free person, by phone, only when the company has no type: 201 with the new types, then 200 with the existing ones and nothing created |
| `GET /hours?subject_kind=&subject_id=` | `{subject_kind, subject_id, is_default, periods: [{weekday, opens, closes}]}`; `weekday` 0 is Monday, times are `HH:MM` in the company's time zone. With no subject: the company's hours, the default week (Monday to Friday, 09:00 to 18:00, `is_default: true`) until set. For a `person` or `resource`: its own hours, `[]` when it follows the company's |
| `PUT /hours` | Body `{subject_kind?, subject_id?, periods}` replaces those hours as a whole. Each period must open before it closes (latest closing 23:59; nothing may run past midnight) and none may overlap on a day: 422 `hours_invalid` / `hours_overlap`. An empty list clears them (a company goes back to the default week). A `subject_id` outside the company: 422 `subject_not_found` |
| `GET, POST /closed-days`, `DELETE /closed-days/{date}` | `{day, note}`; `GET` takes `from` and `to` dates. Closing a closed day again changes its note. `DELETE` is 204, 404 `closed_day_not_found` |
| `GET /blocks?from=&to=` | Blocks ending after `from` (default now) and starting before `to` |
| `POST /blocks` | Body `{subject_kind: person\|resource, subject_id, starts_at, ends_at, note?}`; 201. Writes the block's hold in the same transaction. 409 `slot_taken` when the person or room is already held for any of that time (a booking or another block); 422 `block_times_invalid`, `subject_not_found`, and 422 for a time outside the years 2000 to 2100 (the same range every booking time has) |
| `DELETE /blocks/{id}` | 204, and the hold goes with it. 404 `block_not_found` |
| `GET /settings` | `BookingSettingsOut` `{reminder_minutes, pending_hours, customer_limit, version, replies_off}`. Every member may read. Until an Admin saves, the defaults (`1440`, `24`, `5`) with `version: 0`; reading makes no row. `replies_off` is true while customers get nothing automatic: Customer support active with its mode Off (`shadow`), or Customer support suspended, whatever its mode. A company with no Customer support (or one still requested), or active with replies on, gets `false` |
| `PUT /settings` | Admin or platform admin who chose the company. Body `BookingSettingsIn` `{reminder_minutes, pending_hours, customer_limit, version}`, all required, replacing the settings as a whole; `version` is the one read (`0` before the first save). Returns `BookingSettingsOut`. 409 `version_conflict` for a stale `version`. 422 outside `reminder_minutes` 15 to 10080 (minutes before the start), `pending_hours` 1 to 168 (how long a pending booking waits for staff) and `customer_limit` 1 to 50 (the most bookings the bot makes for one customer). The worker reads them: `reminder_minutes` sets how long before a confirmed booking the customer, the people on it and its guests are reminded (the customer on Telegram when they are there, otherwise by email; staff and guests by email; nothing for a pending or cancelled booking, a private meeting never reminds its customer, and no customer reminder goes while replies are Off or Customer support is suspended), and the bot reads `pending_hours` and `customer_limit`. This route only stores them. Writes a `bookings.settings.updated` audit row |

### Booking, moving and the calendar

Same prefix, gate and tenant selection. Reads need membership; `POST /bookings` and the changes
below need an Admin (or a platform admin who chose the company) or staff. Read-only members get 403
on every change. Staff change only bookings they created or attend (403 `own_bookings_only`
otherwise); an Admin changes any booking. A booking of another company is 404 `booking_not_found`.
Times are UTC on the wire; hours, closed days and leave are read in `clients.timezone`. Every
time a client sends (`starts_at`, `from`, `to`) must carry an offset and fall in the years 2000 to
2100, in its own offset and in UTC: a time without an offset or outside that range is 422.

| Method/path | Result |
|---|---|
| `GET /availability?type_id=&from=&to=` | `BookingSlotOut[]`: `{starts_at, ends_at, people, resources}`, at most 31 days (422 `window_too_large`, `window_invalid`). Respects the type's length, buffer, notice and `max_advance_days`. `limit` (default 200, at most 500) stops the scan after that many slots, earliest first, and the scan runs off the event loop. Or `people=&resources=&minutes=` (repeat `people` / `resources`), which are then all required (422 `minutes_required` with no type, 422 `people_or_resources_required` with neither). With a type and no `people`: "several" needs all its people, "any" and "named" list who is free. A type with nobody active left to deliver it has no slots, and neither has a type that picks "any" room when every room it names is switched off. Nothing in the past is ever offered: the window starts no earlier than now, with or without a type (writing a booking in the past is still allowed) |
| `GET /calendar?from=&to=&person=&resource=` | `BookingCalendarOut` `{items: BookingOut[], busy: BookingBusy[]}`: bookings that are not cancelled, at most 62 days (422 `window_too_large`). `person` / `resource` keep the bookings that hold that person or room. `busy` is `{starts_at, ends_at, label: "Busy"}` and is described below |
| `GET /customers?q=&limit=` | The Subscriptions customer search (`CustomerRef[]`). 403 for read-only members, as the Inbox |
| `POST /bookings` | 201 `BookingOut`. Body: `type_id`, or a meeting's `title` and `minutes` (not both); `starts_at`; `people`, `resources`; `contact_id` or `walk_in {name, phone?, email?}` (one of phone or email); `guests [{name?, email}]`; `private` (meetings only); `location_kind`, `address`, `meeting_link` (a meeting with no type only: 422 with a `type_id`, because the type decides; `address` and `meeting_link` are single-line and the link must begin with `https://`, as on types); `acknowledged` (findings). A type's `named` delivery needs `people` (422 `people_required`); "any" picks the free person with the fewest bookings that day; "several" takes all its people; a specific room is held and an "any" room is picked. Status is `confirmed`, or `pending` when the type needs confirmation (a pending booking holds the time). The type's location, address and link are copied onto the booking. 422: `type_not_found`, `type_inactive`, `person_not_found`, `person_inactive`, `resource_not_found`, `resource_inactive`, `contact_not_found`, `nobody_to_book` (a type with nobody active to deliver it, or nothing and no one named for a meeting), `no_room_available` (a type that picks "any" room whose rooms are all switched off; nothing is booked), `private_meetings_only`. A read-only member gets 403 `bookings changes require staff or an administrator` |
| `PATCH /bookings/{id}` | Move it: `{version, starts_at, acknowledged}`. Keeps the id, length, people and rooms, bumps `version`, replaces the holds in one transaction. A refusal changes nothing. 409 `stale` for an old `version`; 409 `wrong_status` unless pending or confirmed; 422 `nobody_to_book` for a booking of a type that holds no person |
| `POST /bookings/{id}/cancel`, `/decline`, `/confirm`, `/no-show` | Optional body `{version}`. Cancel (pending or confirmed) and decline (pending) delete the holds; confirm turns pending into confirmed; no-show (confirmed and already started, else 409 `not_started`) keeps the record and its holds. 409 `wrong_status` otherwise. Each bumps `version`. Cancelling, declining or moving a booking also withdraws a pending request to move it, in the same transaction (see below) |

Refusals carry `detail` (the short code) and, for these, more at the top level of the body:

| Status | `detail` | Body |
|---|---|---|
| 409 | `slot_taken` | `message`: "That time was just taken." (a person or room is already held, or two requests raced and one lost) |
| 409 | `needs_acknowledging` | `findings` (`outside_hours`, `closed_day`, `unavailable`) and a `message` of their sentences ("Outside working hours.", "This is a closed day.", "Not available that day."). Send them back in `acknowledged` |
| 409 | `refused` | `findings` and `message`: "Not available that day." (staff and a person who is `unavailable`) |
| 409 | `stale` | `message` |

`unavailable` is the one code for a person who is away. It never says why, and it is the same name in
`findings` and in `acknowledged`; the server keeps a different name for it inside.

The owner's table: outside hours or a closed day, staff and Admin acknowledge then are allowed; shorter
notice than the type allows, staff and Admin are allowed; a person on approved leave, staff are refused
and an Admin acknowledges. The message never says why a person is away, and leave is never stored,
returned or shown beyond the dates the server reads. A double booking is refused for everyone.

`BookingOut` is the only shape a booking is shown in, and one function redacts it. A private meeting
the viewer did not create and does not attend is `redacted: true` with `title: "Busy"` and only the
times, `status` and `version`, for everyone including Admins and platform admins, who can still move
or cancel it (the answer stays redacted).
Read-only members get `customer: null` and no `guests`. `can_change` is true when this viewer may
move, cancel or otherwise change the booking, by the same rule the change routes enforce (one
function, `service.may_change`, next to `_for_change`): an Admin or platform admin, always, a redacted
private meeting included; staff when they created it or a person linked to their account is on it
(the creator is never put on the wire); read-only members never. It says nothing about the status, so
a client also checks `pending` or `confirmed` before offering a move. `conflict` is `"unavailable"` when a person on
a pending or confirmed booking now has approved leave over it, computed on every read. A person's leave is looked up live, through the HR person their account is linked to (and any stored `employee_id`), so a link made after the person was added counts, and a company without the HR package has none. `people[].is_active` and `resources[].is_active` say whether someone or somewhere was switched off after the booking was made; the booking stays, and a client shows it flagged (not on a redacted meeting, which carries no people or rooms).
Every change writes `bookings.booking.created|moved|cancelled|confirmed|declined|expired|no_show` with
the booking id as target and no customer details.

**Bookings the customer's own bot made.** `BookingOut.source` is `"staff"`, `"assistant"` or `"bot"`
(null on a private meeting the viewer is not part of), and `replaces_booking_id` is set on a pending
request to move another booking (the customer asked to move a confirmed booking and staff have not
answered). A request holds its new time and is `pending`, so a client shows it beside the booking it
would move. The booking and its request can be on different weeks, so each says so on its own: a
request carries `replaces_starts_at` and `replaces_ends_at` (when the booking it would move is, null
on anything else), and a booking carries `pending_change_id` (the id of the live request to move it,
null when none waits or once it is answered or withdrawn). All three are null on a private meeting
the viewer is not part of. Moving or cancelling a booking withdraws its pending request in the same
transaction, so a client says so before it does.

- Confirming a request moves the booking it replaces, which keeps its `id`: the answer is that
  booking, `confirmed`, at the new time with a higher `version` (so the same calendar event is
  updated with a higher `SEQUENCE`). The request is closed (`cancelled`, never shown again) and its
  holds are freed in the same transaction. Nothing is checked again: the request already holds its time.
- Declining a request leaves the booking it replaces as it was. Cancelling or declining a booking, or
  moving it, withdraws its pending request in the same transaction.
- A bot booking that is `pending` lapses on its own (a job runs every five minutes): when its expiry
  has passed, or when it has already started (a booking moved earlier keeps its old expiry). Staff's
  own pending bookings never lapse.
- Cancel on a pending booking the bot made, or on a request to move one, is a decline for the
  customer: the same message and `customer_told`. A decline mails a customer who is not on Telegram a
  plain "wasn't confirmed" message with no calendar file (they were never sent an invitation); for a
  request to move a booking it says the request wasn't accepted and the booking stays on its time.
  Only the customer is mailed about a request.
- Moving or cancelling a confirmed booking, whoever made it, writes a chat message too when its
  customer has a Telegram chat (mentioning a withdrawn request), and the email with the calendar update
  still goes if they have an address. A private meeting never tells a customer anything.
- A chat message, and the declined-booking mail, invite a reply or another request only while replies are
  on and Customer support is active.
- Cancelling or declining a booking whose end has already passed tells nobody: no chat message and no
  mail, to the customer or the people on it (`email` and `customer_told` are null).
- Confirming and declining tell the customer, even with replies Off, because a person acted: a plain
  Telegram message written in the same transaction, or else an email (a customer with both gets the
  message, and the confirmation email too because it carries the calendar file). A booking staff made
  tells the customer nothing until it is confirmed. A lapse sends a Telegram message only while replies
  are on and Customer support is active.

**Email with a calendar file.** After a staff action commits, the server emails the people on the
booking, each with an `invite.ics` that adds, updates or removes it in their calendar. It uses the
mailer the console builds at startup (the one invitation mail uses), never sends inside the
transaction, and a failed email never changes the booking or the status code. `BookingOut.email` says
how it went and is set only on the answer to a staff action:

| Action | Mail |
|---|---|
| `POST /bookings`, `/confirm` | An invitation (`METHOD:REQUEST`). Confirming a request to move a booking is an update of that booking instead: the same `UID`, a higher `SEQUENCE`, the new time. A booking still `pending` goes to its people as `TENTATIVE` and not to the customer, so the confirm is the customer's first mail |
| `PATCH /bookings/{id}` | The same `UID` with a higher `SEQUENCE` (the booking's `version`) and the new time |
| `/cancel`, `/decline` | `METHOD:CANCEL`, `STATUS:CANCELLED`, same `UID`, higher `SEQUENCE`. A booking that was never confirmed (cancelled or declined while `pending`) is cancelled for its people and guests only: the customer was never told about it, so they are not told it is gone. A booking that was confirmed first still tells the customer. A decline of a booking the customer's own bot made is the exception: they asked, so it tells them (a chat message, else the cancellation). A request to move a booking sends no mail at all |
| `/no-show` | Nothing; `email` is null |

`customer_told` (true, false or null) says whether the customer got a message about this change: true if they were mailed or sent a chat message, false if the booking has a customer who was not (no address and no chat, mail off, the send failed, or a private meeting), and null if there is no customer, on every read, on a no-show, on a redacted meeting, and while a booking is still waiting for confirmation (the customer is told nothing then, by design; a customer who was never told a booking is gone is null too, except a booking the customer's own bot made, which they asked for: declining it answers true or false). A client says "Customer wasn't told." when it is false, whatever `email` says.

`email` reports on whoever was to be mailed. It is `"none"` when nobody was (no address, or only a
customer who is not to be told), else `"off"` when mail is switched off, else `"failed"` when any send
failed (the others still went), else `"sent"`. It is null on every read, on a no-show, and on a
redacted private meeting, so an Admin who is not part of one learns nothing about it.

The server waits at most 15 seconds for the mail, so the answer is never held up by a slow relay.
Past that, sends not yet started are dropped and `email` is `"failed"`; a send already in flight
cannot be taken back and may still arrive. So `"failed"` means some mail may not have gone, not that
none did. Any error after the commit (reading the company's details, building a message) is also
`"failed"`, logged by category only, and never changes the status code or the booking.

Who gets mail, once each by address: the customer (if their contact has an email), each person on the
booking with an address (their own, or their account's), and each outside guest. The person who made
the change gets one only if they are on the booking. A private meeting goes to its attendees and
guests only, never to the customer or to an Admin who merely moved or cancelled it. Replies being Off
makes no difference: this is a staff action. Each recipient gets their own file with only themselves
as `ATTENDEE`, and `ORGANIZER` is the mailer's sender address, so nobody learns another address.
Times in the file are UTC with a `Z`; the message gives the local time in the company's zone, the
company's brand name (or its name), the address or link, and Maps and Waze links for in-person
bookings. The calendar read carries no `email`.

A read filtered by `person=` or `resource=` works differently for a private meeting the viewer is
not part of. Its real id would let anyone ask about each person in turn and rebuild who is in it,
so it is not an item at all: it is a `BookingBusy` in `busy`, with only the times and the label
"Busy", and no id, version, status, people or rooms. The unfiltered read keeps the redacted item
with its id (people and rooms empty, address and link null), so an Admin can still move or cancel
it, and `busy` is empty there. The creator and attendees get the full item either way. What this
does not hide: overlapping `busy` times from two filters can still hint that they are one meeting.
That is accepted, because a person's calendar has to show them as busy.

`booking_team_members(client)` (migration `0511`) is a `SECURITY DEFINER` read, granted only to
`csa_app`, that lists the company's enabled members with the HR person linked to each account;
`csa_app` can read neither `console_memberships` nor, without an HR grant, `hr_employee_accounts`.

### Google Calendar

Stage 2 (migration `0513`, off unless `BOOKINGS_GOOGLE_CALENDAR_ENABLED`). A member linked to an
active bookable person connects their own Google account once, and their confirmed bookings appear
on a "Bookings" calendar the app creates in it. The one scope is `calendar.app.created`: the app can
touch that calendar and nothing else in the account. Same prefix, gate and human-session rule as the
rest of Bookings, under `/console/v1/bookings/calendar`. No answer ever holds a token; tokens are
stored encrypted and only the sync code reads them.

| Route | What it does |
| --- | --- |
| `GET /status` | `CalendarStatusOut` `{available, can_connect, status, people}`. `available` is false when the feature is off or the Google client is not set up (everything else is then `false` or `null`). `can_connect` is true for an Admin or staff member linked to an active bookable person, and `status` is theirs: `not_connected`, `connected` or `needs_reconnect` (`null` when they cannot connect). `people` is for an Admin or platform admin: `[{person_id, name, status}]` for each active person with an account, as a status only |
| `POST /connect` | `{url}`: the Google address to send this member to (PKCE, a one-use state that expires after 10 minutes). Only for themselves. 409 `person_required` when their account is not linked to an active bookable person, 503 `calendar_unavailable`. A read-only member gets 403 |
| `GET /callback` | Where Google sends the browser back. Always a 303 (no-store) to `/portal/connections` with `?calendar=connected` or `?calendar_error=<code>`: `oauth_state_invalid` (unknown, used, expired or another member's state), `provider_denied`, `oauth_issuer_invalid`, `scopes_not_granted`, `calendar_unavailable`. The calendar is made on the first connection and reused on a reconnect while it still exists |
| `POST /disconnect` | 204. Clears the stored grant at once, drops the person's open sync rows, then revokes at Google as far as it will; the calendar and its events stay in their account. 409 `not_connected` |

A refused refresh token (`invalid_grant`) makes the connection `needs_reconnect`; nothing more is
tried for that person until they connect again, and everyone else is unaffected.

#### Meet links (`BOOKINGS_GOOGLE_MEET_ENABLED`, needs the calendar switch)

A confirmed online booking with no meeting link gets a Google Meet link: the connected attendee who
made it (else the first connected attendee) asks Google when their event is made, and the link is
kept on `bookings.meeting_link` only if the field is still empty (a link staff typed always wins),
which moves `version`. The answer to the booking change says what the booking is after that, so it
can already hold the link. Everyone else's event carries the link in its location. If Google would
not make one the booking, its mail and the answer are as without Meet, and the job asks again
(up to five times at one version). No mail or customer message goes out when a link is added later.

- `BookingOut.meet_missing`: true for a confirmed online booking still to come with no link while
  Meet links are on; false everywhere else and with them off. A client shows "No Meet link yet"
  with a retry and the existing field to paste a link.
- `POST /console/v1/bookings/{id}/meet` (no body): ask Google again now, for someone who may change
  the booking (403 for a read-only member or `own_bookings_only`; an Admin may for any). Answers
  with the `BookingOut`, which still has `meet_missing` true if Google still would not make a link.
  409 `calendar_unavailable` (Meet links off), `wrong_status` (not a confirmed online booking still
  to come without a link), `not_connected` (no one on it has a calendar connected).

## Training library

A company's own training PDFs and videos: staff list them and open them, tenant admins and the company's HR
owner add, edit and remove them, and anyone may ask for a stored summary. The web client is
`TrainingWorkspace` under `/training` and `/portal/training`. Operations, settings and what is out
of scope are in [TRAINING_LIBRARY.md](./TRAINING_LIBRARY.md). Migrations `0540` (assistant page key)
and `0541` (`training_items`, `training_is_hr_owner()`). The schemas are published in
`docs/console_openapi.json` as `ItemOut`, `Processing`, `ItemList`, `ItemPatch`, `SummaryRequest`,
`SummaryOut`, `NoSummary`, `VideoStart`, `VideoUploadOut`, `PieceOut`, `PlaybackOut`,
`TranscriptLine` and `TranscriptOut`. Migrations `0600` and
`0601` add video to the same items and the tables behind it.

All routes are under `/console/v1`, behind the `training_library` package gate (`active` only, so
every role, a platform admin included, gets 403 `package_inactive` until it is activated) and for
human sessions only: a machine token is refused, as on the KB and HR routes (a machine `POST`,
`PATCH` or `DELETE` gets 403 `machine principal has read-only access` from the console's
authentication before the route runs; only a machine `GET` reaches the route's 403 `human session
required`). A platform admin chooses the company with `?client_id=` (400 without it); a member of
another company is refused (403 with `client_id`, 404 on an item id). An item of another company is
always 404 `training_not_found`, never 403. The file store is the `training` store
(`TRAINING_UPLOAD_ROOT` or the `training/` S3 prefix); when it is not configured every route answers
503 `training_storage_unavailable`.

Who may call:

- **Read** (list, file, read a summary, ask for a summary): any member of the company, every role
  including `read_only`, and a platform admin who chose the company.
- **Manage** (add, edit, remove, regenerate a summary, every video upload route): a platform admin who chose the company, a
  tenant admin, and the company's HR owner when that person is a `tenant_staff` member. The HR owner
  is asked again on every request, so a move takes effect on the next call (see
  [TRAINING_LIBRARY.md](./TRAINING_LIBRARY.md#the-hr-owner-dependency)). `can_manage` on the list
  says which a caller is. A plain staff member gets 403 `training_forbidden`.
- A `read_only` member is refused every `POST`, `PUT`, `PATCH` and `DELETE` by the console's write rule
  with 403 `write permission required` before the route runs, with one exception: `POST
  /training/items/{id}/summary`. That exemption (`reader_may_post` in `csa/console/access.py`) holds
  only when the matched route is that one and the decoded path is exactly that route with a
  canonical UUID. CSRF and origin checks still apply to it. `read_only` asking for `regenerate: true`
  gets 403 `training_forbidden`.

| Method/path | Who | Result |
|---|---|---|
| `GET /training/items` | read | `ItemList` `{items, categories, can_manage}`. `items` are newest first, each `ItemOut` `{id, kind, title, description, category, view_mode, page_count, byte_size, created_at, uploaded_by_name, has_summary, status, duration_ms, failure, processing, notice}`. `kind` is `pdf` or `video`. For a PDF, `view_mode` is `slides` or `document`, `page_count` is set, `status` is `ready` and `duration_ms`, `failure`, `processing` and `notice` are `null`. For a video, `view_mode` and `page_count` are `null`, `duration_ms` is set once it is known and `status` is `uploading`, `uploaded`, `transcribing`, `summarising`, `ready` or `failed`. Readers (every member who cannot manage) get PDFs and `ready` videos only, and `failure`, `processing` and `notice` are always `null` for them. Managers get every video in every state, with `failure` (a `training_` code, on a `failed` video), `processing` `{stage, chunks_done, chunks_total, waiting}` (while the video is `uploaded`, `transcribing` or `summarising`; `stage` is `transcribing` or `summarising`, `chunks_total` is `null` until known, `waiting` says the daily processing limit is used up) and `notice` (`hevc`: the video may not play on some computers). `has_summary` is true for a PDF with a stored summary and for a `ready` video with speech and a stored summary, never for a silent or failed video. `categories` lists each non-empty category once (compared case-insensitively), in alphabetical order. `uploaded_by_name` is the uploader's display name, `null` when a platform admin uploaded it or the name could not be read. The list never carries summary text |
| `POST /training/items` | manage | 201 `ItemOut`. `multipart/form-data` with one part named `file` and no other field, plus the `Idempotency-Key` header (a UUID). Title is the file name without `.pdf` (cut to 120 characters), `view_mode` is `slides` when the first page is landscape (its `/Rotate` counted), else `document`. Step one of the two-step upload, below |
| `PATCH /training/items/{id}` | manage | `ItemPatch`: any of `title`, `description`, `category`, `view_mode`; returns the `ItemOut`. Step two of the upload, below. On a video `title`, `description` and `category` work in every state; `view_mode` is 422 `training_not_pdf` and nothing in that request is applied |
| `DELETE /training/items/{id}` | manage | 204. Removes the row (a video's pieces, job, transcript and search windows go with it, in any state), then the file or the unfinished upload and the store's multipart upload. The video's processing-usage row stays, so deleting does not give its seconds back |
| `GET /training/items/{id}/file` | read | The PDF, streamed. Headers below. A video is 404 `training_not_found` here |
| `GET /training/items/{id}/playback` | read | `PlaybackOut` `{url, expires_at, media_type}` for a video: where to play it from for the next 15 minutes (ask again after `expires_at`). On S3 `url` is a presigned inline GET under the company's own prefix (`private, no-store`, never log or store it); on the local store it is the gateway's `/console/v1/training/items/{id}/video` (with `?client_id=` for a platform admin). `media_type` is `video/mp4` (a `.mov` plays as that) or `video/webm`. A PDF, an item of another company, and for a reader any video that is not `ready` are 404 `training_not_found`; so is a manager's video with no file to play (still `uploading`, or failed for a reason in the file itself, whose file the worker deleted). The answer is `Cache-Control: private, no-store`. See [The video route](#the-video-route) |
| `GET /training/items/{id}/video` | read | The video itself, local store only (404 `training_not_found` on S3, where `playback` gives the URL), answering HTTP Range. See [The video route](#the-video-route) |
| `GET /training/items/{id}/transcript` | read | `TranscriptOut` `{language, speech, segments}`; `segments` are `{start_ms, end_ms, text}` in time order. `speech` is `speech`, `no_speech` or `no_audio` and `language` the transcript's language, both `null` until transcribing has decided them. A reader gets it for a `ready` video only (404 otherwise); a manager gets it in every state, the lines made so far while the video is transcribing. A silent video has no segments. A PDF is 404. `Cache-Control: private, no-store` |
| `GET /training/items/{id}/summary` | read | `SummaryOut` when one is stored, else `{"summary": null}` (`NoSummary`). Never calls the model. Works for a video as for a PDF; a video that is not `ready` is 404 for a reader, and a silent or failed video answers `{"summary": null}` to a manager |
| `POST /training/videos` | manage | 201 `VideoUploadOut`. Starts a video upload; see [Video uploads](#video-uploads) |
| `PUT /training/videos/{id}/pieces/{n}` | manage | 200 `PieceOut` `{number, received}`. One piece, raw `application/octet-stream` with `X-Piece-SHA256` |
| `GET /training/videos/{id}/upload` | manage | `VideoUploadOut`: the plan and the piece numbers already stored, for resuming |
| `POST /training/videos/{id}/complete` | manage | 200 `ItemOut` with `status: uploaded`. No body. Idempotent |
| `POST /training/items/{id}/retry` | manage | 200 `ItemOut`. No body. Only for a `failed` video whose `failure` is one worth trying again (`training_transcription_unavailable`, `training_summary_unavailable`, `training_storage_unavailable`): its processing goes back in the queue at the stage that failed, with its attempts reset, and the item's `status` becomes that stage (`transcribing` or `summarising`) with `failure` `null`. Any other item is 409 `training_retry_not_allowed` (a video that is not failed, or failed for a reason in the file itself, which needs a new upload). Works while `TRAINING_VIDEO_ENABLED` is off; the training worker picks the job up once it runs. See [Video processing](#video-processing) |
| `POST /training/items/{id}/summary` | read; `regenerate: true` needs manage | Body `SummaryRequest` `{regenerate?: false}`. The body is required (an empty POST is 422); send `{}` for a plain read-or-make. 200 `SummaryOut` `{summary, key_points, truncated, model, summarised_at}`. Returns the stored summary; makes and stores one first when there is none. `regenerate: true` makes a new one and replaces the stored text. Details below. On a video it is 409 `training_summary_automatic` (a video's summary is made when it is processed), after the 403 for `regenerate: true` from a non-manager and the 404 for an item the caller cannot see |

### Limits

| What | Limit |
|---|---|
| One file | 25 MB (`25 * 1024 * 1024` bytes); the whole body is bounded at that plus 64 KB of multipart framing |
| Pages in one PDF | 300 (at least one) |
| Items in one company | 300 |
| Total size of one company's items | 500 MB (counts `byte_size` of the rows) |
| `title` | 1 to 120 characters after trimming; no control characters |
| `description` | 0 to 1000 characters after trimming; line breaks and tabs allowed, no other control characters |
| `category` | 0 to 60 characters after trimming; no control characters; free text |
| Text sent for a summary | the first 24,000 characters of the PDF's text |
| `summary` / `key_points` stored | summary 1 to 1500 characters; at most 6 points of 1 to 200 characters |

The four upload limits are constants in `csa/training/limits.py`. The count and byte limits are
checked under the company's lock, so two uploads racing for the last slot give one 201 and one 409.

### Two-step upload

The shared upload parser takes one file part and no other fields, so details are a second call.

1. `POST /training/items` with the file and a fresh `Idempotency-Key` UUID. The server checks the
   file (see below), stores it under a generated name (`<uuid>.pdf`; the uploaded name is kept as
   data and never used as a path) and answers 201 with the item under its default title.
2. `PATCH /training/items/{id}` with the title, description, category and `view_mode` the person
   chose. If this call fails the item exists under its default title, and the page says so.

A retry with the same `Idempotency-Key`, the same file name and the same content answers 201 with
the first item and keeps one file and one audit row (also when the library has since filled up).
The same key with other content, or the same content under another name, is 409
`training_key_conflict`. Send a new key for each file the person picks.

`PATCH` leaves a field out (or `null`) to keep it; send `description: ""` or `category: ""` to
clear it. A body with no field to change, an unknown field, or a value outside the limits is 422
(FastAPI validation, not a `training_` code). An id that is not in the company is 404
`training_not_found`.

### The checks on an upload

In this order. A refusal stores nothing and writes no row.

1. The caller may manage (403 `training_forbidden`), before the body is read.
2. The multipart body is one part named `file`, within 25 MB and its header size.
3. The file name is one safe segment: not blank, at most 255 characters, not `.` or `..`, no `/` or
   `\`, no control or separator characters (including line and paragraph separators and bidi
   controls). Ordinary Unicode with spaces is fine.
4. The extension is `.pdf` (any case) and the content starts with `%PDF-`. An empty file is
   `training_not_pdf`.
5. The PDF opens and is checked in a limited child process: not encrypted (an empty-password
   encryption counts as protected), readable, 1 to 300 pages, and no document-level JavaScript or
   launch action (catalog `/Names/JavaScript`, an `/OpenAction` of type JavaScript or Launch or
   carrying `/JS`, a non-empty `/AA` on the catalog or on any page).
6. Under the company lock: the idempotency key, then the item and byte limits, then the write and
   the row.

### Video uploads

A video is sent in pieces, each its own request, so a dropped connection costs one piece and an
upload can resume. While `TRAINING_VIDEO_ENABLED` is off (the default) the four video routes answer
503 `training_video_unavailable`; without a training store they answer 503
`training_storage_unavailable` as the other routes do. Abort an upload with `DELETE
/training/items/{id}` (it works in every state).

1. `POST /training/videos` with `VideoStart` `{file_name, byte_size, media_type}` (name 1 to 255
   characters, `byte_size` at least 1, `media_type` 1 to 100 characters) and a fresh
   `Idempotency-Key` UUID. In this order: the name passes the name rule (422
   `training_name_invalid`), the extension is `.mp4`, `.m4v`, `.mov` or `.webm` in any case (422
   `training_not_video`), `byte_size` is at most 500 MiB (413 `training_file_too_large`); then under
   the company lock: manage (403), the key (the same key with another name, size or media type is
   409 `training_key_conflict`; the same values answer 201 with the same upload and what it has
   received), at most 50 videos and 5 GiB of video for the company, counting the declared size of
   every upload still open (409 `training_library_full`), and at most 2 uploads open at once (409
   `training_uploads_in_progress`). The answer is `VideoUploadOut` `{item, piece_size,
   piece_count, received}`: the item is `uploading` with the file name without its extension as its
   title, `piece_size` is 8 MiB and `piece_count` is `byte_size` divided by it, rounded up.
   `received` is `[]`. The file is stored under a generated name (`<uuid>.<ext>`); the uploaded name
   is data only, and `media_type` counts for the key only (the stored type follows the extension).
2. `PUT /training/videos/{id}/pieces/{n}` for each piece, in any order and up to 4 at once, with
   the raw bytes as an `application/octet-stream` body and `X-Piece-SHA256` set to the piece's
   SHA-256 in hex. Checked in this order: manage (403), the item is a video of the company (404) and
   still uploading (409 `training_upload_closed`); `n` is in `1..piece_count`, the hash header is
   64 hex characters and `Content-Length` is exactly the piece's size, `piece_size` for every piece
   but the last and the remainder for the last (422 `training_piece_invalid`, before any byte of the
   body is read); a free slot (503 `training_busy` with `Retry-After: 2` when the gateway already
   holds 4 pieces; send it again then); the body is exactly that size (422
   `training_piece_invalid`), its SHA-256 matches the header (422 `training_piece_corrupt`, nothing
   written) and piece 1 starts as its extension says: an ISO-BMFF `ftyp` box (bytes 4 to 8) for
   `.mp4`, `.m4v` and `.mov`, the EBML header `1A 45 DF A3` for `.webm` (422 `training_not_video`).
   The piece is written at the offset its number gives and recorded. The same piece with the same
   hash again answers 200 without writing; a different hash replaces it. A piece for an upload that
   is removed or completed while it is sent is 409 `training_upload_closed` and leaves no file; once
   the removal has committed the item is gone and a piece is 404 `training_not_found`. The answer is
   `PieceOut` `{number, received}`, `received` being how many pieces are stored now.
3. `GET /training/videos/{id}/upload` answers `VideoUploadOut` with the numbers stored so far, to
   resume after a reload: send the others. Past `uploading` every number is listed.
4. `POST /training/videos/{id}/complete` (no body) under the company lock: every piece is stored
   with its exact size and the sizes add up to `byte_size` (else 409 `training_piece_missing`). The
   store then checks every piece's bytes against its hash and publishes the file (the S3 upload is
   completed, the local file renamed). A piece whose stored bytes no longer match is forgotten and
   the answer is 409 `training_piece_missing`: ask `GET .../upload`, send the pieces it no longer
   lists, and complete again. On success the item becomes `uploaded` with its `byte_size` and a
   SHA-256 of the piece hashes (each piece's hex SHA-256 in order, joined by line breaks), the upload
   and piece records go, and the video is queued for processing (`processing.stage` is
   `transcribing`). Completing again answers the item. Completion refuses new pieces of that item
   (409 `training_upload_closed`) and waits for those already being written, so the published file
   is exactly the recorded pieces.

| What | Limit |
|---|---|
| One video | 500 MiB (`500 * 1024 * 1024` bytes) |
| Videos in one company | 50 |
| Total size of one company's videos | 5 GiB, counting uploads still open at their declared size (PDFs are counted apart) |
| Uploads open at once in one company | 2 |
| Piece size | 8 MiB, the last piece the remainder |
| Pieces one gateway holds at once | 4 |

The limits are constants in `csa/training/limits.py`; the count, byte and open-upload limits are
checked under the company's lock, so two starts racing for the last slot give one 201 and one 409.

### Video processing

The training worker (`python -m apps.training_worker.main`, one process per deployment) processes a
completed upload in two stages, transcribing then summarising, one video at a time and at most one
per company at once, oldest first. A manager sees the progress in `processing` and the outcome in
`status` and `failure`:

- A failure worth trying again (the speech service, the model or storage did not answer) waits
  60 seconds times the attempt number and tries again, up to 3 attempts per stage. After the third
  the video is `failed` with `training_transcription_unavailable`, `training_summary_unavailable` or
  `training_storage_unavailable`, and `POST /training/items/{id}/retry` starts that stage again. A
  worker stopped mid-video carries on where it stopped (finished chunks are not sent again) once its
  heartbeat is 10 minutes old.
- A refusal of the file itself (`training_video_unreadable`, `training_video_too_long`,
  `training_video_codec`) fails the video at once and deletes the stored file, so its bytes stop
  counting against the 5 GiB; the row stays so the manager sees why. It cannot be retried.
- At most 5 hours of video per company are processed in any 24 hours. The seconds are booked before
  the first chunk is sent, and deleting a video does not give them back. A video past the limit
  waits (`processing.waiting` is `true`) until enough booked time is more than 24 hours old.
- Every hour the worker removes uploads with no new piece for 24 hours (audit
  `training.upload_abandoned`, with no actor), aborts storage uploads that have no upload in
  progress, removes stored files no item keeps, and deletes usage records older than 2 days.

### Detail codes

Every refusal is `{"detail": "<code>"}`. The web client keeps the code on `ApiError.code` and shows
a plain sentence (see below).

| Status | `detail` | When |
|---|---|---|
| 403 | `package_inactive` | The company's `training_library` package is not `active`. Every route, every role |
| 403 | `human session required` | A machine token on a `GET` route |
| 403 | `machine principal has read-only access` | A machine token on `POST`, `PATCH` or `DELETE` (the console's authentication, before the route runs) |
| 403 | `write permission required` | A `read_only` member on `POST`, `PUT`, `PATCH` or `DELETE`, except the summary `POST` |
| 403 | `training_forbidden` | A caller who may not manage (plain staff who is not the HR owner) on add, edit, remove or retry; anyone but a manager sending `regenerate: true` |
| 404 | `training_not_found` | No item with that id in this company (another company's id too), also an item removed while its summary was being made |
| 409 | `training_key_conflict` | The `Idempotency-Key` was used for different content or a different name |
| 409 | `training_library_full` | The upload would pass 300 items or 500 MB for the company (PDFs only; videos are counted apart) |
| 409 | `training_summary_automatic` | `POST .../summary` on a video |
| 409 | `training_retry_not_allowed` | `POST .../retry` on an item that is not a `failed` video, or one that failed for a reason in the file itself |
| 409 | `training_uploads_in_progress` | Starting a third video upload while two are open in the company |
| 409 | `training_upload_closed` | A piece for a video that is no longer uploading (completed, or removed while the piece was sent), or sent while its completion runs |
| 409 | `training_piece_missing` | Completing before every piece is stored with its size, or a stored piece no longer matches its hash (that piece is forgotten; send it again) |
| 413 | `training_file_too_large` | The file is over 25 MB, or the body passes the bound; a video over 500 MiB |
| 413 | `training_upload_invalid` | The multipart headers pass 8 KB |
| 415 | `training_upload_invalid` | The body is not `multipart/form-data` |
| 416 | `training_range_not_satisfiable` | `GET .../video` with a range outside the file or several ranges; `Content-Range: bytes */size` |
| 422 | `training_upload_invalid` | Anything else wrong with the form: no file, two files, another field name, a malformed body |
| 422 | `training_name_invalid` | The file name fails the name rule above |
| 422 | `training_not_pdf` | Extension is not `.pdf`, the content does not start with `%PDF-`, or the file is empty; also `view_mode` in a `PATCH` of a video |
| 422 | `training_not_video` | A video's extension is not `.mp4`, `.m4v`, `.mov` or `.webm`, or its first piece does not start as that container does |
| 422 | `training_piece_invalid` | A piece number outside `1..piece_count`, an `X-Piece-SHA256` that is not 64 hex characters, or a `Content-Length` or body that is not exactly the piece's size |
| 422 | `training_piece_corrupt` | A piece's bytes do not match its `X-Piece-SHA256`; nothing is written |
| 422 | `training_pdf_damaged` | The PDF cannot be read, has no pages, or its page tree is broken or hostile |
| 422 | `training_pdf_protected` | The PDF is encrypted |
| 422 | `training_pdf_too_many_pages` | More than 300 pages (a page count that lies is caught after the walk) |
| 422 | `training_pdf_active_content` | The PDF carries document-level JavaScript or a launch action |
| 422 | `training_pdf_unreadable` | The limited child could not finish the check in its CPU, memory or 20 second wall-clock limit, or crashed or answered garbage. The file is refused; nothing is wrong with the service |
| 422 | `training_no_text` | Summary only: the PDF has no text to read (a scan), or the content budget ran out before any text. Nothing is stored |
| 503 | `training_storage_unavailable` | The store is not configured, the write failed after the client's retries, or the stored object is missing or unreadable (the log carries the item id only). Retry |
| 503 | `training_video_unavailable` | A video upload route while `TRAINING_VIDEO_ENABLED` is off |
| 503 | `training_busy` | The gateway already holds 4 pieces; `Retry-After: 2`. Send the piece again |
| 503 | `training_parser_unavailable` | The PDF checker could not run, or the 30 second wait for it (slot and parse) ran out. Infrastructure, not the file. Retry |
| 503 | `training_assistant_unavailable` | Summary only: the provider failed, timed out (20 seconds) or answered something that does not fit the summary shape, or another click on the same item was still making it after 60 seconds; also no model client and none is stored, which happens only when the gateway's console identity block did not run at startup (`GROQ_API_KEY` is a required setting, so the client is always built when it does). Nothing is stored |
| 422 | (FastAPI validation) | A missing or non-UUID `Idempotency-Key`, a non-UUID item id, an invalid or empty `PATCH` body |

A file refusal on the summary route can only be `training_pdf_damaged`, `training_pdf_protected`,
`training_pdf_too_many_pages`, `training_pdf_unreadable` or `training_no_text`, as the stored file
is read again for its text (that read does not run the active-content check). Other platform codes
(`400 platform admin must select client_id explicitly`, `404 tenant not found`, `403 tenant is not
authorized`, `403 active tenant membership required`) are the console's usual tenant ones.

### The file route

`GET /training/items/{id}/file` reads the row under the tenant context, opens the stored object and
streams it in 64 KB blocks. It takes no company lock and holds no database connection while it
streams, so a removal never breaks a download already open. Headers:

- `Content-Type: application/pdf`
- `Content-Length`: the stored size
- `Content-Disposition: inline; filename*=utf-8''<name>`: shown in the page, with the uploaded name
  percent-encoded (the name is never used as a path)
- `Cache-Control: private, no-store`
- `X-Content-Type-Options: nosniff`
- `Content-Security-Policy: sandbox`

A missing or unreadable object is 503 `training_storage_unavailable` (404 when the item was removed
in the meantime). A browser's own PDF viewer in an iframe is not an option (the console sends
`X-Frame-Options: DENY`): the page fetches this route and draws it.

### The video route

`GET /training/items/{id}/video` is served by the gateway for the local store only (staging). It
opens the stored object, seeks to the first byte asked for and streams it in 64 KB blocks, with no
company lock and no database connection held while it streams. It must work behind the console's
`/console/v1` rewrite and it answers the `Range` header of a browser's `<video>` element:

- No `Range`, or a header that is not one `bytes=<first>-<last>` range (another unit, `bytes=abc`,
  `bytes=5-2`), is ignored: 200 with the whole file.
- One range (`bytes=a-b`, `bytes=a-` or the last `n` bytes as `bytes=-n`; a last byte past the end is cut
  to the end): 206 with `Content-Range: bytes a-b/size` and the range's `Content-Length`.
- A range that starts at or past the end, `bytes=-0`, several ranges (any comma), or a header over 256
  characters: 416 `training_range_not_satisfiable` with `Content-Range: bytes */size`.

Every answer, 416 included, carries `Accept-Ranges: bytes`, `Content-Disposition: inline`,
`Cache-Control: private, no-store` and `X-Content-Type-Options: nosniff`; a 200 or 206 also
carries `Content-Type` from the stored container (`video/mp4` for `.mp4`, `.m4v` and `.mov`,
`video/webm` for `.webm`). A missing object is 503 `training_storage_unavailable` (404 when the item was
removed in the meantime).

### The summary

- `GET` reads; `POST` reads, or makes one first. The first make stores the summary and every later
  call, by anyone in the company, returns it with no model call. A stored summary is returned even
  when no model is configured.
- The server extracts the PDF's text (up to 24,000 characters) in the limited child and sends it to
  the language-model provider as one JSON completion, with a system prompt that says the text is data
  and never instructions, that the model has no tools, and to answer in the PDF's language. The
  answer is checked against `{summary, key_points}` before it is stored. `truncated` is true when
  the text was cut; the page says "Summarised from the first part of this PDF." `model` is the model
  name that wrote it and `summarised_at` the time it was stored.
- Concurrent first clicks in one gateway process wait on a per-item lock, so ten clicks make one call
  and one stored summary; across several gateway processes two calls are possible, but the first
  writer wins and one summary is stored. No connection or database lock is held during the parse or
  the model call.
- `regenerate: true` needs manage (403 `training_forbidden` for everyone else, `read_only` included),
  always calls the model, and replaces the stored text. Manage is checked again just before the write.
- Audit: `training.summary_generated` when none was stored before, `training.summary_regenerated`
  when one was replaced; asking for an existing summary writes nothing.
- The model's output is shown to every member of the company, so the web client escapes it and never
  renders it as HTML or Markdown.
- The two sentences the spec fixes for these refusals: for `training_no_text`, "This PDF has no text
  we can read, so it cannot be summarised."; for `training_assistant_unavailable`, "We could not
  reach the assistant. Wait a minute and try again."

### Error codes the web must map

Staff never see a code (see [WRITING_GUIDE.md](./WRITING_GUIDE.md): sentence case, plain words,
what happens next). Each of these maps to a sentence in the console copy, with the raw code kept on
`ApiError.code`: `training_not_pdf`, `training_pdf_damaged` and `training_pdf_unreadable` (damaged
file), `training_pdf_protected`, `training_pdf_active_content`, `training_file_too_large` (25 MB),
`training_pdf_too_many_pages` (300), `training_library_full` (items or 500 MB),
`training_name_invalid`, `training_upload_invalid`, `training_key_conflict`,
`training_storage_unavailable` and `training_parser_unavailable` (try again), `training_not_found`,
`training_not_video`, `training_uploads_in_progress`, `training_upload_closed`,
`training_piece_missing`, `training_piece_invalid`, `training_piece_corrupt`, `training_busy` (the
page retries on its own), `training_video_unavailable`, `training_retry_not_allowed`,
`training_no_text`, `training_assistant_unavailable`, `package_inactive`, `training_forbidden` and
`write permission required` ("only admins and the HR owner can change the library").

### Audit

One `console_audit` row per change, with the item id as target and empty details:
`training.video_upload_started` (a video upload was started; a retry with the same key writes none),
`training.item_added` (a PDF stored, or a video's upload completed; a retry that returns the first
item writes none), `training.item_edited`, `training.item_removed`,
`training.video_retried` (a manager queued a failed video again), `training.upload_abandoned` (the
training worker removed an upload with no new piece for 24 hours; `actor_id` is `null`),
`training.summary_generated`, `training.summary_regenerated`. Views, downloads and reads of a summary
are not logged. Log lines carry the item id, the company id, a byte count, the model name and an
outcome, never a title, file name, PDF text or summary text.

## Services and packages
Capabilities are grouped into service packages (catalogue in `csa/console/packages.py`; add-ons require `customer_support`; `bookings` is a separate service and requires nothing). Packages are the management surface; the capability switches below remain the runtime enforcement.

| Package | Kind | Capabilities | Tabs | Sellable |
|---|---|---|---|---|
| `customer_support` | base | `ticket_agent`, `escalation_agent`, `order_approval` | inbox | yes |
| `knowledge_base` | addon | `kb_agent` | knowledge_base | yes |
| `orders_products` | addon | `order_agent`, `invoice_agent` | business | yes |
| `subscriptions` | addon | `subscription_agent` | subscriptions | yes |
| `bookings` | standalone | `booking_agent` | bookings | yes |
| `training_library` | standalone | none | training_library | yes |
| `marketplaces` | addon | `ecommerce_agent` | ecommerce | yes |
| `e_invoicing` | addon | `lhdn_agent` | lhdn_myinvois | no |
| `hr` | standalone | none; covers `hr_agent_settings` modules `hr_directory`, `hr_training`, `hr_recruitment`, `hr_attendance`, `hr_payroll`, `hr_assistant` | hr | yes |
| `staff_email` | standalone | none; covers `email_settings` | email | yes |

State is in `console_service_packages` (migration `0450`): `status` is `requested`, `active` or `suspended`, with requester, activator, `reference`, `note` and timestamps. Tenant RLS applies; only `csa_app` has grants. The migration backfills a package as `active` with reference `backfill-0450` when all its member capabilities are `is_enabled` (`hr`: any enabled `hr_agent_settings` row; `staff_email`: `email_settings.enabled`).

- `GET /console/v1/services`: catalogue joined with the tenant's state, including `consistent` and `inconsistencies` (drift between the package record and member switches) per package. `staff_email` also reports `inner_switch`.
- `last_change` on each service and on each member agent's `capability` in `GET /console/v1/services` (and the service returned by request, activate and suspend): `null` when the audit trail has no recognisable change, otherwise `{action, at, actor}` from the newest matching `console_audit` row.
  - `action` for a service is `requested`, `activated` or `suspended` (from `service.requested`, `service.activated`, `service.suspended`). For a capability it is derived from the `capability.updated` details: `is_enabled` false to true is `subscribed`, true to false `unsubscribed`; `client_enabled` false to true is `tenant_switch_on`, true to false `tenant_switch_off`. When one row changed both switches the subscription wins. A `capability.updated` row whose `before` and `after` are equal (package activation writes one per member even when that member was already on) is skipped, so the newest real change is returned instead; `last_change` is `null` only when no row changed anything.
  - `at` is the audit time; rows with the same time are ordered by audit id. `actor` is the person's display name only when the caller is a platform admin (`"a platform administrator"` when the audit row has no actor). Every other role gets a label, never a name, user id or email: `"a platform administrator"` when the person who made the change is a platform admin, `"a member of your team"` for any other person, and `"an administrator"` when the audit row has no actor (system or legacy rows). The platform-admin check uses the person's current status, not their status when they made the change: someone who became a platform admin later is labelled as one on their older changes too, and the reverse.
  - Read through `public.console_last_change(client, target, actions)` (migration `0502`), a `SECURITY DEFINER` function granted only to `csa_app` that returns action, time, display name, details and `actor_is_platform_admin` (no user id), filters by tenant itself and returns nothing unless `client` equals the transaction's `app.client_id`. `csa_app` still cannot read `console_audit` or `console_users`. The same migration adds the index `console_audit_tenant_target_time (client_id, target_id, created_at DESC)` for the per-target lookup. Only `GET /console/v1/services` (and the service returned by request, activate and suspend) fills `last_change`: `GET /console/v1/capabilities` and `GET /console/v1/agents` always return `last_change: null`.
- `GET /console/v1/services/active`: `{"packages": [{key, status}]}` for packages that are `active` or `suspended`, used to build navigation; the console shows the Inbox for a suspended Customer Support package and every other gated tab only when its package is active. A platform admin without `client_id` receives `{"packages": []}`.
- `POST /console/v1/services/{key}/request`: tenant admin or platform admin.
- `POST /console/v1/services/{key}/activate` with body `{"reference": str, "note"?: str}`: platform admin. Flips member `is_enabled` and `client_enabled` through the same SQL as the capability toggle; audits `capability.updated` and `service.activated`.
- `POST /console/v1/services/{key}/suspend` with body `{"note"?: str}`: platform admin. Sets `is_enabled=false` only for members not in another active package, and never for `escalation_agent` or `order_approval`.

Errors: 409 on state conflicts (already active, requirements inactive, dependent add-on active); 422 for an unknown or unsellable key; 403 for the wrong role or `human session required` for machine tokens. Audits `service.requested`, `service.activated` and `service.suspended` carry details `{before, after, reference}`; `requested_by` and `activated_by` are user ids.

Gates: a route behind an inactive package returns 403 `package_inactive`, and gates run before role checks, so every role sees it.
- Inbox requires `customer_support` in status `active` **or `suspended`**, so staff can still resolve held replies and escalations while a client is suspended (the required safety capabilities keep running); Business requires `orders_products` or `marketplaces` active.
- All `/kb/*` routes (uploads, files and downloads for every role) require `knowledge_base`, which migration `0530` gave to each company with a `customer_support` row, with the same status; `/invoices*` require `orders_products`.
- The HR API requires `hr`; the Email API, including the OAuth callback, requires `staff_email`.
- All `/training/*` routes require `training_library` (see [Training library](#training-library)); it is not backfilled, so a company has it only once a platform admin activates it.
- Not gated: `/tabs/specialized`, `/agents`, `/capabilities`, runtime views, sandbox chat, audit, team and settings.

Activation does not flip the HR module switches or the email inner switch; the HR owner and the tenant admin keep control of those. The web navigation shows Inbox, Business knowledge, Business, People & HR and Email to tenant members only when the package is active (platform admins see all); the former Agents page is the Services tab at `/services` and `/portal/services`.


### Customer replies (Go live)

A new company starts with no replies to customers. Its tenant admin turns them on here, with no help from FourStacks.

- Behind the `customer_support` gate, which accepts `active` and `suspended`.
- `GET /console/v1/services/customer-replies`, for any member or platform admin, returns `{mode, brand_name, persona_name, brand_tone, intro_line, can_manage, warnings}`. The three persona fields are `null` while unset, and the customer bot then uses its default voice.
- `mode` is `off`, `approve` or `auto`, which are the deployment modes `shadow`, `suggest` and `auto`. It reads `config.policy.deployment_mode`, as the turn gate does, so it reports what the bot does.
- `warnings` holds any of: `replies_off`; `no_knowledge` (the `knowledge_base` add-on is active and no active document has chunks); `base_only` (no add-on is active, so most questions go to staff).
- `PUT /console/v1/services/customer-replies`, for a tenant admin or platform admin, with CSRF, takes `{mode, brand_name}` and, optionally, `persona_name` (at most 60 characters), `brand_tone` (200) and `intro_line` (300, the greeting, which cannot contain a digit, a link or `@`: the bot may only state what it can check against its facts, so a number in the greeting would fail that check on every hello). Each is trimmed and a single line with no control characters; `brand_name` is at most 120. Left out or `null`, the stored value stays; for the three persona fields an empty string clears the field back to the default. They set the bot's wording only: the default support guide and the facts-only rule always apply. The persona name and tone are also used when the Email assistant drafts a customer reply for staff (it signs the draft with the persona name).
- Refusals: 422 `brand_name_required` for `approve` or `auto` with no name, stored or sent; 409 `package_suspended` while Customer support is suspended; 403 `tenant administrator required`.
- Saving `approve` also sets `approval_ttl_minutes` to 1440.
- A real change writes one `console_audit` row, `tenant.customer_replies_set`, with before and after (the three persona fields appear only when one of them changed). A repeat writes none.
- A company still `onboarding` moves to `pilot` when Customer support is activated, or on a save, with one `tenant.status_changed` row. Background jobs visit only `pilot` and `active` companies, so its knowledge base files process before it goes live. Other statuses don't change.

## Capabilities and specialised tabs (LG13)
Packages (see Services and packages) are the management surface; the switches below remain the enforcement.
- `GET /console/v1/capabilities`: each agent has `name`, `is_enabled` (the admin subscription), `client_enabled` (the client's own switch), `active` (true only when both are on), `circuit_state`, `fail_count`, `capability_kind`, and `updated_at`. `last_change` is always `null` here; the services read fills it (see Services and packages).
- `PATCH /console/v1/capabilities/{name}` with body `{"is_enabled"?: bool, "client_enabled"?: bool}`. A human platform admin controls subscriptions; a human tenant admin/platform admin controls tenant switches. Actor fields such as `changed_by` are rejected. Required safety capabilities cannot be disabled. Changes and before/after audit share one locked transaction.
- `GET /console/v1/agents`: authoritative management catalogue. Each row has `name`, `title`,
  nullable `capability` (the existing registry contract), `implementation`, `readiness`,
  explanatory `detail`, `required_system`, `specialized`, `can_view_specialized`,
  `can_subscribe` and `can_toggle`. Unknown registry agents are explicitly `not_implemented`.
  A missing registry row is not a subscription. Permission flags are session-role-derived;
  machine principals receive no mutation permissions. Required safety switches may be
  enabled if initially off, but cannot be disabled.
- `implementation`: `local`, `prepared_only`, or `not_implemented`. Local invoice reads
  do not imply PDF delivery/issuance; booking/subscription preparation is not provider
  execution. MyInvois is not implemented. No e-commerce adapters are wired into the default
  turn runtime, even though adapter libraries exist.
- `readiness`: `available` for implemented local paths, `setup_required`,
  `configured_unverified`, or `reindex_required`. KB returns the selected embedding model
  and compatible/incompatible active chunk counts. Mixed indexes report both counts;
  only matching-model chunks are retrievable. Only incompatible chunks require reindexing.
  Dev hash retrieval is labelled lexical, not semantic readiness. A configured TEI endpoint
  is **not** a live health check. No URL/credential is exposed or provider contacted.
- `GET /console/v1/tabs/specialized`: the catalogue (`lhdn_myinvois`, `ecommerce`,
  `knowledge_base`, `invoices`, `bookings`, `subscriptions`) includes independent switch,
  implementation, readiness and `can_view`/`can_manage` metadata. `metrics` remains an
  empty compatibility object; hard-coded provider health/compliance metrics are removed.
  Specialised inspection navigation requires a subscription and an implemented surface,
  not a fabricated healthy state. Setup-required/prepared-only labels remain visible.
  `can_manage` governs capability toggling, not future feature-specific permissions.

### Switch enforcement

Production turn dependencies install a fresh tenant-scoped registry check in `SpokeRunner`
before **each** execution attempt, including callback and follow-up tasks. This authorization
does not use the 30-second discovery cache, so another worker's warm cache cannot retain
permission after a committed toggle. Missing/off subscriptions or tenant switches return
an explicitly recorded `capability_unavailable` result. Disabling during retry backoff
prevents another attempt and preserves the previous attempt's failure result.
The existing circuit breaker remains responsible for circuit/probe behavior.

Console KB text-ingest/delete/gap-resolve execution returns 409 when either KB switch is
off. Upload jobs are claimable only when both switches are on; disabled jobs remain queued
without consuming attempts. Work that has already started/been claimed is not cancelled.
This is not a transaction-wide cancellation lock or cross-process breaker reset.

## Runtime visibility (M2)

All endpoints use the same verified tenant selection as the catalogue. All human roles
may read their tenant's metadata; platform admins must explicitly select `client_id`.
The separate machine principal remains read-only and tenant-allowlisted.

- `GET /console/v1/overview`: recorded run counts (`runs`, `succeeded`, `failed`),
  `last_run_at` and `window_hours=24`. Counts describe recorded attempts, not provider
  health, revenue or complete historical workflow usage. Query failures propagate rather
  than being replaced by zero.
- `GET /console/v1/runs?limit=50&before_id=<id>&agent=<optional exact name>&from=<ts>&to=<ts>&status=<ok|error>`:
  `{items, next_before_id}`; limit 1–200, descending unique run ID keyset pagination.
  `from` and `to` are optional ISO 8601 timestamps with an offset (for example
  `2026-03-10T00:00:00+08:00`) on `created_at`: `from` is inclusive, `to` is exclusive.
  `status` is optional, `ok` or `error` (the stored run status; `partial` runs match
  neither). The filters combine with `agent` and `before_id` and stay tenant-scoped; with
  none of them the response and ordering are unchanged. `from` at or after `to` is
  rejected with 422 `invalid_range`, any other `status` with 422 `invalid_status`; a
  timestamp without an offset is rejected with the standard 422 validation error.
  Run IDs/cursors must be positive PostgreSQL bigint values (maximum `2^63 - 1`);
  out-of-range values are rejected with 422 before querying run data.
  Pass `next_before_id` unchanged for the next page; null means no older page.
  Each item contains `id`, `trace_id`, `conversation_id`, `agent`, `ok`, `status`,
  `error_code`, `error_category`, `latency_ms` and `created_at`.
- `GET /console/v1/runs/{id}`: the same metadata, or tenant-scoped 404.
  Raw run inputs/outputs, message text and model prompts are deliberately excluded.
- The frontend `/activity` and `/portal/activity` poll every five seconds and reuse
  `/events?trace_id=...&after_id=...` for incremental trace inspection. Errors are
  displayed; no success-shaped empty/zero fallback is rendered. Legacy trace listing
  remains compatible; the new runs screen uses ID keysets rather than its timestamp cursor.

## Knowledge base (LG13 / M3)
- `GET /console/v1/kb/documents?limit=50` lists documents with `version`, `is_active`, `file_name`, `chunk_count`, `kind`, and `embed_model`.
- `POST /console/v1/kb/documents` adds plain text. Body: `{title, content, category?, language?, source_uri?}`. Returns 503 when the embedding service is down.
- `POST /console/v1/kb/uploads`: authenticated multipart repeated `files` fields,
  **1–3 non-empty PDF/DOCX/CSV files, at most 10 MiB each**, UUID `Idempotency-Key`
  header. Returns **202 `{job_id, duplicate_request}`**, not a searchable/success result.
  Same tenant/key and ordered filename/content fingerprint returns the existing job;
  reuse with different files returns 409. Cookies/CSRF and verified tenant context
  are required; machine credentials cannot upload or read/download managed sources.
  Platform/tenant admins and tenant staff may write; readers may view/download.
  Upload, job retry and gap resolution require both KB switches on (409). Document and
  source deletion, the other job actions and reconciliation remain available with switches off.
- Parser bounds apply while streaming, before unbounded buffering: **31 MiB total**,
  **8 KiB headers per part**, at most 3 file parts and no non-file fields. Oversized
  input is 413; malformed/incomplete multipart, invalid filenames, empty files, unsupported
  types or invalid signatures/content are 422; non-multipart is 415. No client-selected
  server paths or base64 payloads. Existing extraction resource limits still apply
  (500 PDF pages, 5,000 CSV rows, 50 MiB DOCX expansion); image-only PDFs require OCR.
- Storage uses `KB_UPLOAD_ROOT/<client_id>/<server UUID>.<validated extension>`,
  private directory/file permissions, exclusive partial writes, fsync and atomic rename
  **before** committing queue/file/batch/audit rows. A failed/ambiguous commit reconciles
  against durable metadata rather than blindly unlinking possibly committed files.
  Cancellation finishes publication before releasing the shared tenant lock.
- `GET /console/v1/kb/files?limit=100`: public source metadata (`id,name,media_type,
  size_bytes,document_id,created_at`), never a storage path/hash.
  `GET /console/v1/kb/files/{id}/download` authorizes the tenant and human session again;
  deleted/foreign sources are 404, missing storage is an explicit 503. Responses are
  attachment downloads with no-store/nosniff. Downloads already authorized/open may finish
  during deletion; new downloads cannot.
- `DELETE /console/v1/kb/documents/{id}`: **200 `{storage_cleanup}`**, scoped/audited;
  missing is 404. Removes chunks/document, marks every linked source deleted and unlinks originals.
  `DELETE /console/v1/kb/files/{id}`: **200 `{storage_cleanup}`**, scoped/audited and repeatable
  for known sources; when linked it deletes the shared document and **all linked duplicate
  sources**. Deleting an unprocessed source invalidates it even if a worker already read its bytes.
  Superseded document versions remain visible until explicitly deleted. `storage_cleanup` is
  `done` when the storage sweep after the commit succeeded (or no store is configured) and
  `pending` when it failed: the removal still stands, the failure is logged and
  `POST /kb/storage/reconcile` finishes the sweep. Both routes close the jobs whose files are now
  all deleted (status `cancelled`, dismissed); a job with a path-only entry or a live file stays.
- Worker deduplication, final storage and deletion share a tenant transaction advisory
  lock. Hash tombstones fence jobs queued before a content deletion (including legacy
  jobs without file IDs); managed file validity is rechecked after embedding, so deletion
  wins over in-flight/retried work. A deliberate new upload queued after deletion is allowed.
  Locks do not span embedding/network calls. Deletion fences persist; no automatic purge.
- `POST /console/v1/kb/storage/reconcile`: authenticated writer/CSRF, audited;
  returns `{removed_files}`. Explicit recovery after crashes/disk failures: under the
  same tenant lock removes only UUID-pattern orphan/deleted/partial files, retaining
  active DB-backed sources and unknown operator/legacy filenames. No recursive purge,
  legacy import or scheduler is implied. Available via **Reconcile orphaned storage**
  on the source panel; I/O/DB failures propagate and are logged, not reported as success.
- `GET /console/v1/kb/ingest-jobs?limit=20` and `GET /console/v1/kb/ingest-jobs/{id}`:
  curated typed job status (`queued`, `processing`, `done`, `partial`, `failed`, `cancelled`),
  attempts/timestamps, public `{name,file_id}` files, per-file `meta.results`
  (`file,status,document_id,chunks,version,error`) and `original_stored`. Internal
  paths/requester/arbitrary metadata are omitted; errors containing path syntax are redacted.
  Legacy `upload:` document source locations are omitted too. This intentionally replaces raw
  job JSON. Dismissed jobs are left out of the list and are 404 by id (migration 0550).
  `original_stored` is false when a file of the job has been removed, or when the job (or one of
  its results) failed because its content was deleted after it was queued; such a job can only be
  dismissed. An entry with a path and no `file_id` (legacy) counts as stored.
- Job actions: human writer/CSRF, knowledge base add-on, scoped to the tenant and audited. A
  job that is absent, belongs to another tenant or is dismissed is 404.
  `POST /console/v1/kb/ingest-jobs/{id}/dismiss`: **200 `{storage_cleanup}`** (was 204); takes a
  finished job (`done`, `partial`, `failed`, `cancelled`) off the list and deletes the job's own
  stored uploads; `queued`/`processing` is 409 `job_still_running`. Audit `kb.job_dismissed`.
  A stored upload is deleted (record soft-deleted, bytes swept after the commit, as on the file
  delete route) only when the job names it in `files[].file_id`, it is live, no document uses it and no other job on
  the list names it; documents, chunks and hash tombstones are never touched. `storage_cleanup` is
  `done` or `pending` exactly as for `DELETE /kb/files/{id}`: `pending` means the sweep failed and
  `POST /kb/storage/reconcile` finishes it. A dismissed job is 404 for a later retry.
  `POST /console/v1/kb/ingest-jobs/{id}/retry`: **200 job**; queues a `failed` or `partial`
  upload job again with all its files (files read before answer `duplicate`), attempts 0, error
  cleared and a new queue time, so a deletion before the retry does not fence it. 409
  `original_removed` when `original_stored` is false; 409 `not_retryable` when the job is not
  `failed`/`partial` or not an upload job (two quick calls queue once); 409 when the KB switches are off. Audit
  `kb.job_retried`.
  `POST /console/v1/kb/ingest-jobs/{id}/cancel`: **200 job plus `storage_cleanup`** (`cancelled`,
  dismissed, `original_stored` false once its files are deleted); only a `queued` job, including one
  paused while the switches are off; anything else is 409 `not_cancellable`. It deletes the job's
  own stored uploads like dismiss. Audit `kb.job_cancelled`.
  `POST /console/v1/kb/ingest-jobs/dismiss-failed`: **200 `{dismissed, storage_cleanup}`**, the
  number of the tenant's `failed`, `partial` and `cancelled` jobs taken off the list, whose own
  stored uploads are deleted by the same rule. Audit `kb.jobs_dismissed` (target: the tenant).
- `GET /console/v1/kb/gaps` lists unanswered questions.
  `POST /console/v1/kb/gaps/{id}/resolve` atomically marks resolved and audits the action;
  it does not create an answer. Reader controls are absent and backend writes are denied.

Frontend `/knowledge-base` (platform tenant selection) and `/portal/faq` reuse same-origin
cookie/CSRF transport, multipart browser boundaries and stable retry keys. They poll jobs,
sources, versions/model metadata and gaps every five seconds, render explicit unavailable
errors, and confirm destructive actions. Next's proxy buffer is aligned at 31 MiB; the
gateway remains authoritative. No frontend DB connection/file-ingestion shortcut exists.

### Isolated dev rollout prerequisites

Migration **0280** adds upload batches/files/hash tombstones with staged tenant RLS
and application-role grants (no identity access). Gateway readiness requires 0280.
Forward-apply it to the verified isolated dev database **without a reset**, then deliberately
restart the gateway. Set the same writable `KB_UPLOAD_ROOT` for gateway and worker.
Enable `KB_INGEST_ENABLED=true` only on the intended ingestion worker, with both tenant
switches on. Dev/CI can use explicitly labelled lexical `hash-384`; TEI needs deliberate
configuration and compatible model indexing. Queued jobs do not prove a worker/TEI is live.
Do not use production connections/tokens or start the full provider worker just to exercise
uploads. Do not import copied legacy files without an approved inventory/re-index plan.
On 2026-10-15 the user explicitly authorized isolated-dev activation: 0280 was applied
without reset, gateway/frontend restarted, and platform-admin browser upload/download/
duplicate/delete/reconciliation checks passed through configured loopback TEI.
The configured upload root resolved to the repository root, so tasks override it to
`$HOME/.local/share/csav2-dev/kb-uploads` (private, pre-created). No environment-file
edit was made; do not assume generic launch commands inherit this override.

For this already-provisioned dev checkout, use **Tasks: Run Task** in VS Code:

1. **Console: isolated dev gateway**: loopback 18081; guards domain `csa_app` and
   identity `csa_identity` targets at `127.0.0.1:5434/csa`.
2. **Console: isolated dev frontend**: loopback 13000, upstream gateway 18081.
3. **Console: isolated dev KB ingestion**: enables ingestion only for synthetic
   tenant `7f797233-684a-47ab-8222-6e887b6480db`.

After the approved clean-dev recovery, gateway, frontend, full conversation/KB worker
and delivery tasks were restarted; the M1-only KB task was not. Do not start duplicates.
Forward **13000 privately** and open **http://localhost:13000**, then select
the intended tenant on Knowledge base. Recovery recreated both synthetic tenants with
all capabilities disabled and no KB documents; enable deliberately and upload fresh
fixtures. Old on-disk files were preserved, not adopted or reconciled. Stop only the corresponding
task terminal with Ctrl-C. Do not run `make run-dev` merely for this console smoke test.
The frontend task's existing `/tmp/fourstacks-toolbin` Node wrapper is host-local,
not a portable installation.

The ingestion task runs `python -m scripts.dev_kb_ingest --client-id UUID`, not the full
worker. Its guards require dev mode, the exact isolated database/application role,
an existing dedicated absolute root, and credential-free loopback HTTP TEI when configured.
It directly claims only that tenant, including onboarding state; the normal full worker's
active-tenant enumeration is not used. Other tenants' jobs are not serviced by this task.
No bots, delivery, other agent runtime, production connection or external provider startup
is implied. Missing/invalid configuration fails explicitly; a configured TEI failure does
not silently switch to hash embeddings.

An additional **Console: isolated dev conversation worker** task was added during
explicitly authorized Telegram acceptance. Unlike the KB-only task, it runs the
standard conversation worker, KB ingestion and scheduled jobs across eligible isolated-dev tenants;
do not launch it merely for console uploads or alongside another conversation worker.
It guards domain/checkpoint roles and targets at loopback 5434 and uses the same
dedicated upload root and credential-free loopback TEI. Do not run the separate KB-only
task alongside this task. It does not start delivery or a Telegram poller.
**Console: isolated dev delivery** separately guards the same domain/owner targets and
drains the outbox. Neither task changes the existing webhook. Fresh post-recovery KB and
Telegram acceptance subsequently passed for Local Telegram Development; see
[trace and recovery evidence](./PROGRESS.md). The recovered Telegram tenant is `pilot`,
with deliberately enabled KB and explicitly user-approved gate policy `auto`; M1 remains
onboarding/disabled. Normal KB polling processes only `pilot`/`active` tenants, and a
shadow gate intentionally produces no sends, even when KB ingestion succeeds.

## Invoices (LG13)
- `GET /console/v1/invoices?limit=50` lists invoices.
- `GET /console/v1/invoices/{id}/download-link` returns **501** until M6 supplies a real artifact-serving path. The placeholder signing-key fallback is removed. The adopted UI does not offer downloads.

## OpenAPI
The gateway serves `/openapi.json` and `/docs` only outside production; Redoc is disabled.
The safe static [console OpenAPI snapshot](./console_openapi.json) contains console contracts
only and is exported without settings, DB connections or providers:

```sh
NO_LOCAL_ENV=1 uv run python -m scripts.export_console_openapi
```

Pydantic is authoritative. The first slice has explicit typed TypeScript adapters in
`web/lib/identity-contracts.ts` and `identity-api.ts`, checked by TypeScript and transport
tests. Regenerate/review this snapshot with contract changes; automated TypeScript generation
from this versioned artifact is the follow-on strategy rather than fetching machine-protected
runtime OpenAPI during builds. Do not infer authentication from a successful schema build.

## Not built yet (planned)
Wave 2/3 preparation libraries are now available under `csa/agents/staged/` (2026-10-15).
Booking/subscription customer agents have local reads and unexecuted requests. Staff email,
web-research and invoice-draft preparation are **not HTTP endpoints**. A future authenticated
adapter must construct `StaffActor` from verified identity, never from user-supplied request
fields, and supply the tenant/contact context. No console source is needed for these libraries;
it is required to map existing UI calls and finish the actual turnover.

Server-sent live stream, customer-web inbox, order decision UI, policy and
agent settings editing, channel/integration connect, notification targets, DLQ/redrive,
usage/billing. The offline Telegram M5 slice above does not complete broader M5–M8 acceptance.
Authentication/onboarding, M2 agents/activity and M3 KB pages are adopted; all project/PM/terminal/Hermes/diagram/workroom/voice and
unmigrated business pages return 404. No legacy API/n8n rewrite remains active.

## Recorded sandbox voice

The authenticated `/console/v1/chat/voice` configuration endpoint and
`/console/v1/chat/conversations/{conversation_id}/voice/{request_key}` POST/GET
routes support recorded audio to a reviewable text draft. They do not submit a
chat turn automatically. Submission uses raw audio bytes and a supported audio
Content-Type, with the normal session, CSRF, writer-role and private ownership
checks. Jobs preserve exact-key replay and expose processing/completed/failed/
timeout/expired states. See [configuration, retention and recovery limits](./CONSOLE_VOICE.md).
