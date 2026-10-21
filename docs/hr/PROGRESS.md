# HR feature implementation progress

Updated: 2026-10-21. Branch `feat/hr-foundation`, base `0264c0a`.
Active worktree: `/home/amsyrzmi/CSAV2-hr`.

## Current status

The remaining local HR workflows, console controls, staff chat and local browser
voice adapters are implemented. Source expects ordered migrations through `0410`.
This is source/disposable-test implementation, not an activated deployment or
certification of Malaysian payroll compliance. Follow [setup and boundaries](./SETUP.md).

The user removed Developer A’s review/integration as a gate. Earlier review-first
and code-freeze instructions in [HR-0 history](../handoffs/2026-10-21-developer-b-hr0-foundation.md)
are superseded. A’s worktree and the dirty original main remain untouched.
The old `/tmp/csav2-hr-foundation` directory is a backup; the active worktree is the
stable path above. No environment file was read or copied.

## Business policy preserved

- Employee, console account and customer contact remain distinct. Account linking
  uses explicit tenant membership references, never inferred email matches.
- Directory reads expose only the five approved fields through explicit self,
  direct-manager or HR grants. Console roles create no HR permissions.
- Client ownership requires a separately recorded, client-approved tenant assignment.
- Training and employee-workflow decisions require an active assigned manager.
  The verified client owner may decide their own training request; ordinary
  managers cannot decide their own requests.
- Nonfinancial preparation and permitted reads may run automatically. Supervisor
  decisions and financial actions remain human. Payroll has distinct maker/checker
  rules, explicit private access, and separately granted financial actions.
- Malaysia / MYR supports Peninsular Malaysia/Labuan, Sabah and Sarawak explicitly.
  Calendars, employment categories, contribution tables, entity and cycle are
  reviewed configuration, not inferred legal defaults.

## Implemented feature matrix

| Feature | Implementation and limits |
|---|---|
| Directory and training | Existing verified service, field projections, requests, human decisions, atomic audit/receipts, replay and version checks. Agent decisions require a bound human confirmation. |
| Onboarding and access | Recorded owners create/update/deactivate employees, link actual tenant members and replace explicit action grants. Private recruitment, attendance and payroll grants are separate. An explicit first-owner bootstrap refuses to replace an existing assignment. |
| Recruitment | Consented CV text intake, explicit retention deadline, private CV/notes views, human notes, erasure, local interviews, overlap checks and cancellation. Expired CVs disappear from application reads. Erasure removes CV text, consent evidence and notes and cancels local interviews. No autonomous ranking, hiring or rejection. |
| Interview automation | Enabled recruitment agents can schedule a local interview for an authorized operator, using an active consent record and an actual tenant member. Results contain only a receipt, never CV text. Interviewers can view their own appointments without gaining CV access. |
| Attendance | Atomic bounded CSV import; imported rows remain pending review. Existing timezone, interval, future-time, overlap and single-correction checks remain enforced. Shifts can be scheduled and cancelled. No biometric/location collection or automatic wage calculation. |
| Calendars and leave | Explicit region/year work calendars and holidays, reviewed allocations and current balances. Approval consumes working days atomically, supports year boundaries and prevents overlapping leave or exceeding allocations. |
| Payroll | Immutable effective-dated contribution-band rules, reviewed employment-profile matching, exact Decimal calculations and private persisted snapshots. PCB is a separately reviewed input with a source reference. A different reviewer must confirm the exact snapshot digest. Approved overlapping periods are locked; reversal preserves the original decision and snapshot history. No bank payment or filing. |
| Staff chat | Optional configured planner selects one typed HR action. It cannot approve/reject, provision authority, read private CVs or create payroll runs. Submitted messages and the caller’s employee reference are the planner input; private HR stores are never supplied. Persisted session-bound plans deduplicate retries; business actions revalidate current authority. |
| Browser voice | Explicit dictation into a reviewable text box, followed by normal authenticated chat submission. Recognition requires local processing; speech output requires a local voice. Unsupported browsers keep typed input available. No microphone audio reaches CSA and no cloud speech fallback is selected. |
| Reminders and retention | Durable local notifications, daily reminder deduplication, mark-read and personal interview views. Page-open maintenance is available; an owner-opted-in dedicated worker also supports background retention/reminders. Its role has maintenance-function access and cannot read HR tables, approve or operate payroll. |
| Authority audit | Immutable owner-visible grant, linkage/status and reviewed configuration events. CV text, candidate notes and payroll amounts are excluded from generic operation receipts and access-change audit. |
| Runtime wiring | Authenticated HR router, gateway startup/readiness, schema expectations, API export, protected console navigation and isolated-build allowlist are updated in this branch. Staff HR stays separate from customer/contact agents and A0’s workspace union. |

## Source map

- [Contracts](../../csa/hr/automation_models.py), [private projections](../../csa/hr/operation_records.py),
  [operations](../../csa/hr/operations.py), [workflow service](../../csa/hr/cases.py).
- [Agent entry point](../../csa/agents/hr.py), [dispatcher/catalog](../../csa/hr/agents.py),
  [staff planner](../../csa/hr/assistant.py), [HTTP router](../../csa/hr/api.py).
- [Core verified service](../../csa/hr/service.py), [ordered operations migration](../../db/migrations/0410_hr_operations.sql),
  [parameterized SQL](../../db/sql/hr/), [OpenAPI](../console_openapi.json).
- [HR workspace](../../CSA_WebConsole/csa-console/web/components/console/HrWorkspace.tsx),
  [remaining tools](../../CSA_WebConsole/csa-console/web/components/console/HrAutomation.tsx),
  [assistant UI](../../CSA_WebConsole/csa-console/web/components/console/HrAssistant.tsx),
  [local voice](../../CSA_WebConsole/csa-console/web/lib/hr-voice.ts).
- [Owner bootstrap](../../scripts/bootstrap_hr_owner.py),
  [maintenance worker](../../scripts/run_hr_maintenance.py).
- [Operations acceptance](../../tests/test_hr_operations.py),
  [persistence/security tests](../../tests/test_hr_persistence.py),
  [gateway lifecycle tests](../../tests/test_console_identity_gateway.py),
  [browser transport/speech tests](../../CSA_WebConsole/csa-console/web/tests/hr-transport.test.mjs).

All new HR tables use staged RLS, never FORCE. Private actors and resources use
tenant-composite references. Narrow helpers hold current authority locks through
commit. Model/browser arguments cannot supply execution authority. The new worker
is a distinct, explicitly provisioned system principal for retention and local
reminders, not a fabricated human session or customer contact.

## Validation evidence

Final backend checks:

```sh
NO_LOCAL_ENV=1 ENV=ci UV_OFFLINE=1 make lint
NO_LOCAL_ENV=1 ENV=ci UV_OFFLINE=1 uv run pytest tests/test_hr_contracts.py tests/test_hr_policy.py tests/test_hr_training.py tests/test_hr_workflows.py tests/test_hr_persistence.py tests/test_hr_operations.py tests/test_console_contract_snapshot.py tests/test_console_identity_gateway.py -q --capture=tee-sys --maxfail=1
```

- **262 passed in 89.64 s**, no failures, skips, deselections or reported warnings.
  Includes real-role disposable database/API tests, original HR-0 cases, model
  injection/replay tests using an in-process synthetic provider, dedicated worker
  role restrictions, migration reapplication, and gateway lifecycle/readiness.
- Repository lint passed: Ruff, formatting (336 files), Pyright (0 errors and
  warnings), import-linter (1 kept / 0 broken).
- Final task containers `b946d5340e03fe232ffd64a3ebe7fca4173b1f098170d9e86d52f8848b000be9`
  and `61ebc277bcec8f355e27a477c11c39bcccb4ca7d9e8137155c010b8414dfd841`
  were both removed by their fixtures. Only task-created containers were removed.
- An earlier complete focused run passed 251 cases in 81.43 s. Additional retry
  canonicalization and affected gateway tests were then included in the final run.
- Initial operations expansion stopped at 36 passed / 1 failed because the previous
  table-count assertion still expected 12 after adding tables. The intentional
  schema expectation was updated. Initial targeted lint found formatting/import/
  line-length issues, all corrected; an added Docker marker briefly lacked its
  import and was corrected. These were not reported as green runs.
- Several narrower operations selections passed (5, 10 and 12 cases). Their
  synthetic containers were removed. No shared image build or pull was used.
- Earlier isolated frontend runs passed transport tests, lint, generated types,
  TypeScript, production build and trace exclusion checks. The final run adds
  local-only speech tests. All 25 transport/state/speech tests passed; ESLint then
  rejected a this-alias in the new speech mock. The mock was corrected without
  suppressing the rule. Final isolated rerun **passed all 25 tests**, ESLint,
  generated route types, TypeScript, production build and trace checks. The build
  compiled in 29.2 s and its TypeScript phase completed in 13.2 s. Build traces
  excluded environment files, nested Git, backups and the legacy API.

Final frontend command:

```sh
PATH=/tmp/fourstacks-toolbin:/usr/local/bin:/usr/bin:/bin npm --prefix CSA_WebConsole/csa-console/web run check:isolated
```

Full/load suites and disposable-reset acceptance remain **not run** because the
shared-host validation window is still uncoordinated. No image-build fixture or
load test was substituted. Authenticated live browser, real microphone, live
provider and persistent runtime acceptance remain **not run**.

Earlier evidence and failures remain in [HR-1 history](./HR1_VALIDATION_HISTORY.md)
and [prior expansion history](./HR2_VALIDATION_HISTORY.md).

Final structural checks passed: five HR notes, 73 local link targets, balanced
fences and no trailing whitespace; all new source files have final newlines.
The published contract adds 17 HR paths without changing or removing any existing
non-HR path or schema. `git diff --check` passed. The final label-specific Docker
check found no HR disposable fixture containers remaining.

Concurrent work advanced independently: original main was at `0e6a7b0` and A’s
worktree at `c860bcd` when checked. A’s migration filenames ended at `0370`; no
collision with HR `0380`–`0410` was observed. No files in either worktree were
modified by this HR task. Shared gateway/navigation/OpenAPI changes still need
normal conflict reconciliation before a later merge.

## Configuration and acceptance still required

Code does not create real client-owner assignments, real staff, policy grants,
calendars, statutory rules, credentials or worker services automatically. Complete
[setup](./SETUP.md) with approved client data in an authorized isolated environment.
Qualified reviewers must validate payroll tables, categories, source versions and
PCB inputs; a certified automatic PCB engine is not implemented. Candidate intake
is bounded text intake, not a PDF parser or background document-analysis service.
Interview scheduling and notifications are local; no external calendar/email
connection, bank transfer or statutory filing adapter is activated.

This HR task performed no commit, push, PR, merge, deployment, persistent migration, service start/restart,
image pull or live model/provider request was performed. No environment file,
production connection, real CV or real payroll record was used. Branch changes
must be reconciled with concurrent source before a later merge; Developer A’s
review is not a user-required gate.
