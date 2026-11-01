# HR setup and operating boundaries

Start from the current merged checkout or its explicitly selected implementation
worktree, not Developer B's preserved pre-integration worktree. Current progress
and validation are maintained in [PROGRESS.md](./PROGRESS.md). Source and migrations
are implemented; no persistent rollout or service activation was performed.

## Tenant setup

1. Apply ordered migrations through `0410` in the separately authorized isolated
   environment using the normal migration process. Never use a production URL or
   a bare persistent reset to validate changes.
2. Record the first client-approved HR owner. The new bootstrap tool requires
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
  holders choose **Left** in the status filter to see them. Leaving and rehire themselves come in
  project 1b; until then there is no screen that makes someone inactive. The API call
  `employee.save` (HR owner) still can.
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

Set `HR_ASSISTANT_ENABLED=true` in the authorized gateway configuration, using the
existing configured Groq client. A client owner must also enable `hr_assistant` and
any actual HR tool it will use. Submitted messages and the caller’s employee
reference go to the configured model provider; persisted private CV/payroll stores
are never supplied. The UI discloses the provider use before submission.

The planner may request one typed permitted action or return a fixed clarification.
Its schema rejects approval/rejection and payroll preparation. All executions use
the same freshly verified services as manual forms. Session-bound persisted plans
and operation receipts handle explicit retries after uncertain responses. Changing
an input with the same key conflicts; an acknowledged operation can begin a new
request. Permission/calendar set order is canonicalized across app processes.

Dictation fills an editable text box; it does not submit automatically. Recognition
requires [on-device processing support](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition/processLocally)
and an installed English Malaysia language pack. Speech playback selects only a
[local voice](https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesisVoice/localService).
No cloud speech fallback or automatic language-pack download is attempted. Browser
support and actual microphone acceptance must still be verified on the client’s
browser; typed chat remains available.

## Acceptance before rollout

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
