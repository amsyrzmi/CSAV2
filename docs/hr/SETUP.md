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
3. In **People & HR → More HR tools → People & access**, create manager and employee
   records, link actual tenant members and assign managers explicitly. An employee
   can exist without a console account. Complete region/entity/employment category
   together when preparing that employee for payroll.
4. Select the action profiles the client approves. They replace the target member’s
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

The bounded directory/training browser scenario is maintained in
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
