> Historical record before the remaining local HR implementation. See [current progress](./PROGRESS.md).

# HR feature implementation progress

Updated: 2026-10-21. Branch: `feat/hr-foundation`, base `0264c0a`.
Active worktree: `/home/amsyrzmi/CSAV2-hr`.
Status: staff HR backend, manual console and deterministic agent implementation;
focused backend validation passed; final frontend validation unconfirmed after interruption.
This is not a completed statutory payroll system
or a deployed HR rollout.

## Current authorization and business decisions

The user explicitly removed Developer A’s final review/integration as a gate and
authorized HR features and agents. Earlier freeze, shared-path and review-first
gates in the [HR-0 handoff](../handoffs/2026-10-21-developer-b-hr0-foundation.md)
are historical. Changes to gateway, migrations, OpenAPI and navigation belong to
this isolated branch; A’s working directory and dirty original main are untouched.

- Five safe directory fields are approved for self, direct managers and explicitly
  granted HR readers: display name, job title, department, work email, manager.
- Client ownership is a separately recorded, client-approved per-tenant assignment.
  Console platform/tenant admin roles confer no HR authority.
- An active assigned manager is required for training and employee-workflow
  decisions. The verified client owner may decide their own training request;
  an ordinary manager cannot decide their own request.
- Malaysia / MYR; explicit region supports Peninsular Malaysia/Labuan, Sabah and
  Sarawak. Region selection is not a claim that statutory rules are implemented.
- Nonfinancial actions without required human approval may be automated. Manager
  decisions and financial actions remain human. Payroll needs explicit financial
  grants and a distinct reviewer. Pay cycle, legal entity and reviewed rule set
  are required inputs, with no inferred business defaults in the backend.
- Focused tests and lint only until a full-suite/shared-host window is coordinated.
  No full/load suite or reset window has been assumed.

## Worktree location

The original temporary worktree isolated B from A’s concurrent dirty work. It has
been relocated using `git worktree repair` to `/home/amsyrzmi/CSAV2-hr`, retaining
the same branch and all source. `/tmp/csav2-hr-foundation` is a backup, not the
active Git worktree; its old `.git` pointer was renamed `.git.relocated-backup`.
The active worktree has its own offline Python environment. The frontend uses the
existing pinned dependency directory through an ignored node_modules symlink.
No environment file was read or copied.

## Implemented code

| Area | Source and behavior |
|---|---|
| HR domain | [models](../../csa/hr/models.py), [policy](../../csa/hr/policy.py), [training](../../csa/hr/training.py): constrained contracts, explicit field projection, tenant/resource and transition rules. |
| Verified service | [service](../../csa/hr/service.py), [records](../../csa/hr/records.py): live session and CSRF proof, locked membership/authority, directory/training persistence, bounded keyset pages, atomic receipts/audit/CAS. |
| Staff agents | [entry point](../../csa/agents/hr.py), [dispatcher](../../csa/hr/agents.py): directory, training, recruitment guides, attendance/leave preparation, payroll status. Typed action schemas; read/draft/commit risk; 10-second timeout; no automatic retry. Observe loops can only read. |
| Human decisions | Agent training approve/reject returns an immutable, session/user/tenant-bound confirmation. Explicit human confirmation rechecks current grants, switch, version and expiry and atomically consumes once. Other workflow decisions use the manual API only. |
| HR workflows | [contracts](../../csa/hr/workflows.py), [service](../../csa/hr/cases.py): recruitment interview guides, attendance/corrections, leave, local payroll preparation requests. Explicit domain grants, immutable content, current permission checks, CAS and atomic immutable events/idempotency. |
| Attendance | Aware Malaysia timestamps, bounded intervals, no future completed attendance, active manager for decisions, employee lock for concurrent approvals, overlap check, approved same-employee corrections and prevention of a second replacement of one original. |
| Recruitment | Job competency interview guides. No ranking, hiring/rejection automation or candidate data ingestion. Caller-provided consent UUIDs do not establish verified consent; candidate-linked preparation is denied. |
| Payroll | Manual review-request metadata only: region, legal entity, MYR, explicit cycle/period/rule reference. Financial operator/reviewer grants are separate from general HR. Maker cannot approve their own run. Approval records a local workflow decision, not calculation, payment or filing. Pure private Decimal amount validation exists but is not wired to payroll persistence. |
| HTTP | [router](../../csa/hr/api.py): authenticated human routes, origin/CSRF, safe errors, no-store, bounded bodies and inputs; no caller-supplied authority. Mounted by [gateway](../../apps/gateway/main.py) when real identity is configured. |
| Console | [HR workspace](../../CSA_WebConsole/csa-console/web/components/console/HrWorkspace.tsx), [transport](../../CSA_WebConsole/csa-console/web/lib/hr-api.ts), protected `/hr` and `/portal/hr`: directory, training, workflow forms, review and confirmation, owner-managed agent switches. Exact retries retain keys and bodies in memory; no HR localStorage. |
| Schema | Ordered [0380](../../db/migrations/0380_hr_foundation.sql), [0390](../../db/migrations/0390_hr_staff_agents.sql), [0400](../../db/migrations/0400_hr_workflows.sql); tenant/composite FKs, staged RLS, no FORCE, narrow session/authority helpers. Application cannot provision employee/account/owner/grant authority. All runtime SQL is under [db/sql/hr](../../db/sql/hr/). |
| Runtime contract | Gateway and migration test expect `0400`; HR readiness verifies all 12 tables and required helpers. [OpenAPI exporter](../../scripts/export_console_openapi.py) includes HR. [Published snapshot](../console_openapi.json) regenerated without connections. |

The customer/contact-oriented AgentContext, customer capability registry, sandbox
chat and A0’s workspace union are unchanged. HR has a separate staff catalog and
HTTP dispatcher. These are deterministic tools, not a free-text HR chat or voice
implementation. No A-owned unmerged module is imported.

## Validation

Previous HR-1 slice: **195 passed**, lint passed. That evidence predates the
current expansion; preserved in [HR-1 history](./HR1_VALIDATION_HISTORY.md).

Current expansion:

- Initial collection failed on a Pydantic discriminated-union/before-validator
  incompatibility. Moved shared control-character validation to after parsing;
  the original HR-0 cases subsequently passed.
- Initial expanded database run: **159 passed, 1 failed** on an obsolete HR table
  count after adding tables. Updated the intentional schema/API expectations.
- Expanded focused run: **226 passed in 60.04 s**, no skips or failures. Exact
  disposable container `786c90fc87bb30766baa7e45aba578af10993cdf358481fbac0d125648a1aca5`
  was removed. The preceding failed run removed its exact container
  `0e32717a22b3c671fb508d94ad690ca5d5e61ef4e236f5ca20d53526fa46f3a6`.
- Repository lint passed before the last agent/contract refinements. A later lint
  found one import-order issue; corrected. Final repository lint subsequently passed:
  Ruff, formatting (329 files), Pyright (0 errors/warnings) and import-linter
  (1 kept, 0 broken).
- First isolated frontend check passed transport tests, lint, generated types,
  TypeScript, production build and secret-free build traces. This predates the
  additional HR transport tests. A subsequent isolated check including all three HR
  transport tests, lint, types, production build and trace checks passed. After the
  final saved-versus-refresh UI correction, another isolated run started; its final
  result could not be recovered after the interruption, so final-source frontend
  acceptance is unconfirmed.
- Final focused suite: **239 passed in 73.20 s**, no failures, skips,
  deselections or reported warnings. Includes all five HR test files plus
  `tests/test_console_contract_snapshot.py`. Final task container
  `cde37b325389a3e3f052ec8a69b321db69f85d2fe49ef1b3ce3b9f7584cc52bd`
  was removed. Intermediate runs passed 228 and 230 tests, respectively.
- OpenAPI structural comparison found 13 added paths and 30 added schemas;
  no existing path or schema was removed or changed. Snapshot tests passed.
- Three HR notes passed Markdown checks: balanced fences, 43 existing local
  links and no trailing whitespace. `git diff --check` passed.
- Full/load suites, disposable reset and authenticated browser/provider acceptance
  remain not run under the existing restrictions.

Commands (no environment-file loading):

```sh
NO_LOCAL_ENV=1 ENV=ci UV_OFFLINE=1 uv run pytest tests/test_hr_contracts.py tests/test_hr_policy.py tests/test_hr_training.py tests/test_hr_workflows.py tests/test_hr_persistence.py tests/test_console_contract_snapshot.py -q --capture=tee-sys
NO_LOCAL_ENV=1 ENV=ci UV_OFFLINE=1 make lint
NO_LOCAL_ENV=1 ENV=ci UV_OFFLINE=1 uv run python -m scripts.export_console_openapi
PATH=/tmp/fourstacks-toolbin:/usr/local/bin:/usr/bin:/bin npm --prefix CSA_WebConsole/csa-console/web run check:isolated
```

Fixtures use synthetic users/employees and a cached image ID in uniquely labeled,
loopback-only disposable Postgres containers. They run real ordered migrations,
real identity and application roles and clean up only their exact container.
No image pull, persistent database, production connection or live provider is used.

## Remaining implementation and rollout work

- [ ] Consented candidate/CV intake, private notes, retention/deletion and actual
  interview scheduling. Candidate-linked workflows currently fail closed.
- [ ] Attendance import, shift/holiday configuration, leave balances and reviewed
  jurisdiction/employment-specific entitlements. Current leave is a request and
  decision workflow; approval does not calculate or deduct a balance or wage.
- [ ] Qualified, effective-dated statutory payroll rules for every supported region,
  employment category and cycle; verified rule records; persisted private amounts,
  reviewed snapshots, period locks/reversals and statutory fixtures. A supplied
  rule-set UUID currently identifies proposed configuration; it is not verified.
- [ ] Real staff natural-language chat and authenticated browser voice adapters.
  They must call the same service/dispatcher and keep human confirmation outside
  model actions. No external model/provider requests have been made.
- [ ] Notifications/reminders/outbox and provider configuration; no sends or
  calendar invitations are implemented or claimed.
- [ ] Employee/account/grant provisioning and client-approved owner onboarding.
  No default permissions or business-owner assignment are seeded from roles.
- [ ] Coordinated full suite, disposable reset acceptance and authenticated
  browser/runtime acceptance. Focused/build results do not establish deployment.
- [ ] Reconcile branch changes with concurrent A source before any later merge;
  A’s review is not a user-required gate. Migration `0370` belongs to A’s separate
  branch; HR `0380`–`0400` depend only on the base through `0360`.

No commit, push, merge, PR, deployment, persistent migration, service start/restart,
external send, image pull or environment-file access was performed.
