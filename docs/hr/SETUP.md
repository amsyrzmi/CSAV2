# HR setup and operating boundaries

Start from the current merged checkout or its explicitly selected implementation
worktree, not Developer B's preserved pre-integration worktree. Current progress
and validation are maintained in [PROGRESS.md](./PROGRESS.md). Source and migrations
are implemented; no persistent rollout or service activation was performed.

## Tenant setup

1. Apply ordered migrations through `0410` in the separately authorized isolated
   environment using the normal migration process. Never use a production URL or
   a bare persistent reset to validate changes.
2. Record the first client-approved HR owner. In a real company the tenant admin sets it on the
   **Team** page (see “Moving the HR owner” below). The bootstrap tool stays for isolated
   environments and seeding, and requires
   explicit dev/ci environment, an isolated loopback owner connection in
   `HR_BOOTSTRAP_DATABASE_URL`, an active member who has changed their password,
   and a nonempty business approval reference. It cannot replace an existing owner.
   Database ownership is the bootstrap authority; console roles are not ownership.
3. In **People & HR → People → Add person**, create the manager and employee records and
   choose each manager explicitly. Then link actual tenant members from the person’s
   **Access** section (HR owner only). An employee can exist without a console account.
   Complete region/entity/employment category together (the “Employment details for
   payroll” group of the person form) when preparing that employee for payroll. See
   “People, departments and Manage people” below.
4. In **People & HR → Settings**, use **People & access** (“Who can do what”) to give each
   member the switches the client approves, and the raw **Advanced → Access profile** form only
   when a switch cannot say it. The action profiles there replace the target member’s
   current HR action grants; multiple profiles can be selected. Separately grant
   private recruitment, attendance or payroll access. Payroll reviewers also need
   the financial review action; private access alone does not grant it.
5. Configure reviewed calendars, holidays and leave allocations before approving
   leave. A cross-year request needs configuration and sufficient allocation in
   every affected year. These are client-reviewed policies, not statutory defaults.

The bootstrap entry point is [bootstrap_hr_owner.py](../../scripts/bootstrap_hr_owner.py).
It reads its DSN from the process environment only; do not put credentials in argv,
source, handoff notes or copied command output.

```sh
NO_LOCAL_ENV=1 ENV=dev uv run python -m scripts.bootstrap_hr_owner \
  --client-id "$HR_CLIENT_ID" --owner-id "$HR_OWNER_ID" \
  --approval-reference "$HR_OWNER_APPROVAL_REFERENCE" --client-approved-assignment
```

## Moving the HR owner

Migration `0522` lets the company move its own HR owner, so an owner who leaves is no longer a
dead end. Apply it through `scripts/staging_rollout.py` with all images, since the console and the
gateway both depend on it.

- **Where.** On the **Team** page a panel names the HR owner (“HR owner: Name”, or “No HR owner
  yet”) and offers **Change** (or **Set HR owner** when there is none). A platform admin sees the
  same panel on the admin Team page for any company, so a customer can still ask FourStacks. The
  panel is hidden when the company has no HR package.
- **Who can do it.** A tenant admin of the company or a platform admin. Holding the tenant admin
  role gives no HR data: they learn who the owner is and nothing else, until they choose themselves.
- **Who can be chosen.** The picker lists enabled members who are not read only and are not already
  the owner. The server then also requires that the person has changed their first password and is
  not marked as leaving. Someone still on their first password or marked as leaving can be picked
  but is refused at Confirm, with a sentence that says what is wrong (“This person can't be the HR
  owner yet. They need to sign in and change their password first.” or “This person is leaving, so
  they can't be the HR owner. Choose someone else.”).
- **Choosing yourself.** A tenant admin may make themselves the HR owner. The confirmation says so,
  and everyone affected is emailed.
- **What happens.** It is immediate and needs no accept step from the new owner. The old owner
  drops to their ordinary HR switches straight away, even if their account is disabled or read
  only, and their open page or assistant card is refused on the next action. The new owner gets the
  owner-only access and the access page. **A new owner gets no payroll automatically**: no payroll
  workflow rows and no payroll private area. They (or anyone with the right) turn payroll on for
  themselves on the access page, and that change shows in Recent changes.
- **Emails.** One email goes to the old owner, the new owner and each enabled tenant admin, once
  per address. It names the company, who made the change, the old and new owner and the time in the
  company's timezone. A platform admin appears as “FourStacks admin”. There is no link and no HR
  data. If mail is off or a send fails, the move stands and the page says: “HR owner changed, but
  we couldn't send the emails. Please tell the people affected.”
- **Retries.** A lost answer is safe to retry: the page keeps one key per exact request, so a
  repeat changes nothing, names whoever holds the title now (a later move names the later owner),
  and sends no second email (the page then says “If you're not sure the emails went out, please
  tell the people affected.”). If someone else changed the owner first, the move is refused with
  “The HR owner changed since you opened this page. Reload to see who it is now.” In the rare case
  of a deadlock with another HR action the answer is `try_again`: “Someone else changed HR records
  at the same moment. Please try again.” Nothing changed, and the same request can be sent again.
- **Record.** Recent changes on the access page lists each move (“A made B the HR owner,
  replacing C”), and the move writes an audit row.
- **Bootstrap.** `scripts/bootstrap_hr_owner.py` stays for isolated environments and seeding. It
  still cannot replace an existing owner.

## People, departments and Manage people

Migration `0520` (project 1a) adds departments, a change history per person and the Manage people
switch. Apply it through `scripts/staging_rollout.py`, which stops the gateway, worker, delivery
and frontend for the migrate step.

- **The switch.** `manage_people` (“Manage people”) lets someone add and edit people and
  departments, and see Left people and each person’s History. It does not let them change access:
  that stays with the HR owner. The `hr_team` preset turns it on; no other preset does, and the
  HR owner can turn it on for one member on the access page. A holder cannot newly make their own
  person anyone’s manager, and cannot change their own manager (only the HR owner can).
- **Who sees the People list.** The HR owner and Manage people holders see everyone, including
  Left people, with every field. A member with the complete directory switch (“See everyone’s basic
  details”, part of the Staff preset) sees the active and starting people with name, job
  title, department, manager, work email, work phone, start date and employment type, and the org
  chart. A member whose directory access was set by hand to only some of those fields is not
  “directory”: they get no People tab. Anyone else with HR access sees only their own profile,
  under Home.
- **The Left filter.** People who have left are hidden by default. The HR owner and Manage people
  holders choose **Left** in the status filter to see them. People who are leaving stay in the
  default list, marked Leaving. Marking someone as leaving and rehiring them are on the profile
  (project 1b, see “Leaving and rehire” below). The API call `employee.save` (HR owner) can still
  make someone inactive directly.
- **Departments.** Each department is a row, and a person points at one. The free-text department
  already on each person was turned into departments by the migration (names that differ only in
  case or spacing became one). The old text column is kept as a mirror of the department’s name so
  that older readers keep working. A department name stays taken after it is archived; restoring
  it brings it back. In **People → Departments** the HR owner and Manage people holders create,
  rename, archive and restore departments, and see how many people each has. The Add person
  form can also create one with **New department…**.
- **History.** A person’s **History** section (HR owner and Manage people holders) lists each
  change with who made it and when, field by field, old to new, up to the latest 200. It is
  written by the database for every change, including the `employee.save` call. Saving the person
  form uses the version it loaded: if someone else saved first, nothing is saved and the form says
  “Someone changed this person since you opened it. Reload to see their change.”
- **Linking a console account.** In a person’s **Access** section the HR owner picks a tenant
  member to link, or unlinks the current one. A member can be linked to one person only.
  Changing what the member can do stays on the access page (**Settings → People & access**).

## Personal details and Sensitive details

Migration `0521` (project 1b) adds a **Personal** section to a person's profile: IC or passport
number, date of birth, home address, personal phone, emergency contact (name, relationship and
phone), bank, bank account number, income tax number, EPF number and SOCSO number.

- **The switch.** `sensitive_details` (“Sensitive details”) lets a member see and edit every
  person's personal details. No preset turns it on; the HR owner turns it on for one member on the
  access page. It does not let them change access. Manage people alone does not show personal
  details.
- **Who sees the section.** The HR owner and Sensitive details holders see and edit it for anyone.
  The person sees their own and edits only their home address, personal phone and emergency
  contact; HR keeps the rest. Managers and colleagues never see it, and it never appears in the
  People list, the directory, search or the assistant.
- **Give Sensitive details with the directory switch.** A holder of Sensitive details who does not
  also have the directory switch (“See everyone's basic details”, part of the Staff preset) or
  Manage people cannot open other people's profiles, so cannot reach their Personal section. The
  staff defaults include the directory switch, so this only happens when it was turned off by hand.
- **Numbers.** The IC or passport, bank account, tax, EPF and SOCSO numbers are encrypted in the
  database with the gateway's `CREDENTIALS_KEY`, so keep that key with the backups. They are shown
  masked, as `•••• ` and the last 4 characters (`••••` for 4 characters or fewer). **Show** reveals
  one number until **Hide**, Edit or a reload, and every Show is recorded (who, whose, which
  number, when). In the edit form a number box starts empty: leave it blank to keep the stored
  number, type to replace it, or tick **Clear** to remove it.
- **History.** A change is listed in the person's History by field name only (“Changed Bank
  account number”), never with its value.

## Employee documents

Migration `0521` (project 1b) adds documents on a person's profile: a contract, an identity file, a
certificate or another file. The HR owner and Manage people holders add, see and remove them, for
any person including those who have left. The person sees their own contract, identity and
certificate files, not the ones filed as Other, and cannot add or remove anything. A manager or a
colleague sees none. Removing a document hides it and deletes its file; the record and its
history entry are kept.

- **Files.** PDF, JPG, PNG or DOCX, at most 10 MB each, one per upload. The file's content must
  match its extension; a renamed file is refused. Names are kept as given (up to 255 characters).
- **Storage.** Files live under `HR_UPLOAD_ROOT/<client id>/` with a generated name, on a volume
  that only the gateway mounts (`staging-hr-uploads` in `deploy/compose.staging-pipeline.yml`,
  mounted at `/var/lib/csa/hr-uploads`). Leave `HR_UPLOAD_ROOT` unset and the document routes answer
  503 `hr_documents_unavailable`. It must not be, contain or sit inside `KB_UPLOAD_ROOT`; settings
  refuse that at startup, since each side's cleanup would delete the other's files. Include the
  volume in backups: the database holds only the record.
- **Not covered.** Files are **not scanned for viruses** and are **not encrypted on disk**; the
  volume and its backups need the access control and disk encryption of the host. The document
  list, download and History show the file name, so names should not carry identity numbers.

## Leaving and rehire

Migration `0521` (project 1b) adds leaving to the profile. The HR owner and Manage people holders
use **Mark as leaving** on a person's Overview.

- **Mark as leaving.** Choose the last day (today or later, Kuala Lumpur time) and the reason:
  Resigned, Contract ended, Dismissed, Retired or Other (Other needs a short reason, at most 200
  characters). When the person has reports, choose who they move to; the default is the leaver's
  own manager. The person shows as **Leaving** until the last day has passed. The reason is for
  HR viewers only; the person and their direct manager see the last day.
- **Who can be marked.** The HR owner cannot be marked as leaving while they are the owner: “The HR
  owner can't be marked as leaving. Ask your admin to move HR ownership to someone else
  first.” (a company has one HR owner; the tenant admin moves it on the Team page, see “Moving the
  HR owner”).
  Only the HR owner can mark someone who holds HR access beyond the staff switches, or who is a
  tenant admin of the company, as leaving (“Only the HR owner can mark someone with HR access as
  leaving.”), because finishing them removes that access and disables their membership. A Manage
  people holder can still mark themself.
- **Undo leaving** is on the profile until the end of the last day. If the reports have already
  moved, they stay with their new manager.
- **On the last day** the person keeps their access all day, and their reports move to the chosen
  manager (or to no manager, when that person has since left).
- **The day after the last day** the person is finished: they become Left, their HR access in this
  company (switches, workflow rows, private areas and the HR owner row) is deleted, their console
  membership of the company is disabled and their sessions there are revoked. The link between
  the person and the console account is kept. History shows these changes by “System”, and one
  `hr.leaver_finished` row goes to the console audit log. The main worker (`apps/worker`) runs
  this every 15 minutes (`hr_leavers_round`, 900 seconds), and People & HR Home runs it too for
  that company while the HR owner has it open (its maintenance, every five minutes), so a stopped
  worker only delays it. Running it twice finishes a person once.
- **Rehire** is on the profile of someone who has left: choose a new start date. They come back as
  an active person with no manager if their old manager has left. No access comes back: invite
  them again from **Settings** to give console access.

## Private recruitment and scheduling

Intake records the authorized operator’s consent evidence and a retention deadline
within 366 days. CV text is bounded to 12,000 characters; line breaks normalize to
spaces. Human notes are private and bounded. No candidate data is sent to a model
by the recruitment tools, and no hiring/ranking/rejection tool is exposed.

Candidate-linked guides verify the stored consent reference. Local interviews
check current candidate availability, active tenant membership and schedule
conflicts. Interviewers can view their own local appointments without CV access.
Erasure removes CV text, consent evidence and notes and cancels local interviews;
opaque operational references remain for audit. Expired content is unavailable
through application reads. Actual background erasure requires the configured
maintenance worker; page-open maintenance also performs local cleanup.

## Payroll preparation and human review

Every run selects MYR, region, legal entity, cycle, period and an immutable reviewed
rule reference. The employee’s recorded region/entity/category must match that
rule. Rules include explicit effective dates, source/version and reviewer evidence.
Contribution CSV columns are `scheme,above,through,employee,employer`, where scheme
is `epf`, `socso` or `eis`. Wage bands must be contiguous; an uncovered wage fails
rather than extrapolating a rate.

Payroll line CSV columns are `employee_id,gross,epf_wage,socso_wage,eis_wage,reviewed_pcb,reviewed_other_deductions,tax_review_reference`.
Use plain decimal amounts without thousands separators. The server uses Decimal,
validates deduction bounds and calculates net pay from the reviewed data. PCB is
an externally reviewed amount, not a calculation certified by this application.

A preparer saves an immutable snapshot. A different, currently authorized reviewer
must open the private amounts and confirm that exact digest. Approved overlapping
entity/region periods are locked. Reversal retains the original decision, snapshot
and review history and permits a separately prepared correction. No payment or
filing is executed.

Qualified review must use the rules applicable to the actual period and employee
category. Primary references include [KWSP mandatory contributions](https://www.kwsp.gov.my/en/employer/responsibilities/mandatory-contribution),
[PERKESO contribution schedules](https://www.perkeso.gov.my/en/rate-of-contribution.html)
and [HASiL PCB specifications](https://www.hasil.gov.my/majikan/jadual-pcb-dan-spesifikasi-data/).
A source reference or region selection is not evidence of statutory compliance.

## Background maintenance

The migration creates `csa_hr_maintenance` as NOLOGIN with only the maintenance
function privileges. An operator must deliberately provision its separate isolated
credential and configure `HR_MAINTENANCE_DATABASE_URL`; never use an owner/app DSN
for this worker. It verifies its current role and rejects privileged substitutes.

A recorded client owner must enable background maintenance and choose the reminder
threshold in the console. The worker discovers only opted-in tenants, removes
expired candidate content, cancels affected local interviews and creates deduplicated
review/interview reminders. It never approves, calculates payroll, sends mail or
contacts a calendar provider. Its audit contains counts and references, not CV text.

```sh
NO_LOCAL_ENV=1 uv run python -m scripts.run_hr_maintenance --once
NO_LOCAL_ENV=1 uv run python -m scripts.run_hr_maintenance --poll-seconds 300
```

These commands describe explicit activation after configuration; no worker was
started during implementation. The page-open timer does not replace a deployed
background worker when users close their browsers.

## Staff chat and browser voice

> **Moved (project 1b).** The separate HR chat on People & HR Home is retired; HR actions are
> now asked of the workspace assistant dock, which is the only assistant. The notes below
> describe the deprecated `POST /console/v1/hr/assistant` route, which stays for one release
> and is then removed in a cleanup PR. `lib/hr-voice.ts` stays for the sandbox workspace.

Everything in this section up to the dictation paragraph applies only to the deprecated
`POST /console/v1/hr/assistant` route. No console screen calls it any more, so there is no
chat UI and no provider notice of its own.

Set `HR_ASSISTANT_ENABLED=true` in the authorized gateway configuration, using the
existing configured Groq client. A client owner must also enable `hr_assistant` and
any actual HR tool it will use. Messages submitted to the route and the caller’s employee
reference go to the configured model provider; persisted private CV/payroll stores
are never supplied.

The route's planner may request one typed permitted action or return a fixed clarification.
Its schema rejects approval/rejection and payroll preparation. All executions use
the same freshly verified services as manual forms. Session-bound persisted plans
and operation receipts handle explicit retries after uncertain responses. Changing
an input with the same key conflicts; an acknowledged operation can begin a new
request. Permission/calendar set order is canonicalized across app processes.

Dictation (`lib/hr-voice.ts`, now used only by the Sandbox assistant; the workspace
assistant dock has none) fills an editable text box; it does not submit automatically. Recognition
requires [on-device processing support](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition/processLocally)
and an installed English Malaysia language pack. Speech playback selects only a
[local voice](https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesisVoice/localService).
No cloud speech fallback or automatic language-pack download is attempted. Browser
support and actual microphone acceptance must still be verified on the client’s
browser; typed input remains available.

## Acceptance before rollout

The access page and the People profile have browser scenarios in
[`test_hr_access_browser.py`](../../tests/test_hr_access_browser.py) against a synthetic API:
the owner changing two members' switches, and the HR owner using a profile's Personal (masked
number and Show) and Documents (upload, list, download) sections, marking the person as leaving
and undoing it. CI runs them.

The bounded People/training browser scenario is maintained in
[`test_packaged_hr_browser_acceptance`](../../tests/test_packaged_console.py).
Run it with synthetic packaged services and disposable PostgreSQL only:

```sh
NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_packaged_console.py::test_packaged_hr_browser_acceptance -q
```

It exercises real JavaScript-enabled login, field grants, tenant/role/CSRF
denial, training submission, manager confirmation, grant revocation and an
explicit same-key retry after a committed request loses its acknowledgement.
The workspace-guidance expansion also checks current-grant links, platform tenant
retention, section deep links, failed-refresh cleanup and no-grant/revocation behavior.
It verifies durable request, receipt, audit and confirmation rows. Browser
egress outside the synthetic origin is blocked and asserted absent. This
scenario neither uses nor validates microphones, external models, recruitment,
attendance, payroll, public ingress or real client configuration.

Coordinate the full suite and disposable-only reset test, validate approved
configuration with synthetic data, then exercise actual session-based browser
flows and the intended browser’s microphone support. Persistent migrations,
service activation, external providers and deployment remain separate actions.
Do not enable money movement or external sends from the local workflow receipts.
