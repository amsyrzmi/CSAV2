> Historical HR-1 record. Its scope gates and paths are superseded by [current progress](./PROGRESS.md).

# HR feature implementation progress

Date: 2026-10-21. Status: HR-1 backend implemented and tested; final A review and
shared runtime integration outstanding. Branch: `feat/hr-foundation`.
Worktree: `/tmp/csav2-hr-foundation`. Base: `0264c0a`.

## Current scope

The user authorized implementation and deferred Developer A's contract review
until the end. This supersedes the earlier code freeze and review-before-build
gate in the [HR-0 handoff](../handoffs/2026-10-21-developer-b-hr0-foundation.md).
This feature-local document records implementation progress without changing
central project progress or Developer A's files.

Implement directory lookup and training draft, submit, withdraw, approve/reject
through one verified service and authenticated router. Persist requests, audit
and idempotency receipts atomically. Use synthetic disposable PostgreSQL tests.
The approved self/direct-manager/explicit-HR directory field set is display name,
job title, department, work email and assigned manager. Client ownership is a
separate per-tenant assignment. Client may decide their own request; an assigned
manager is required. Ordinary managers cannot decide their own requests.

## Work checklist

- [x] HR-0 contracts and pure policy: 123 tests passed; independently reviewed by A.
- [x] Inspect active concurrent development: A owns gateway changes and untracked
  migration `0370_workspace_assistant.sql`; preserve those files.
- [x] HR-specific schema, tenant/resource policies and session locking guard.
- [x] psycopg async service, persisted training transitions, audit and receipts.
- [x] Authenticated manual API with CSRF, safe errors and public projections.
- [x] Focused disposable database/API, concurrency and rollback tests.
- [x] Repository lint and final feature summary for A.
- [ ] A final review, migration-number allocation, gateway/readiness/OpenAPI mount.
- [ ] Coordinated full suite and disposable reset acceptance.

## Integration boundaries

Keep new implementation in `csa/hr/`, `db/sql/hr/` and `tests/test_hr_*.py`.
The executable schema in `db/sql/hr/schema.sql` is an unnumbered installation
candidate, exercised only in disposable tests until A allocates an ordered
migration after reviewing fresh integration HEAD. No guessed migration number,
shared readiness edit or gateway mount belongs to this concurrent branch slice.
The real router will use existing console sessions; no fabricated HTTP actor.

No workspace HR action union, chat/voice activation, UI, directory administration,
listing/export, recruitment, attendance, payroll, notifications or providers.
No commit, push, PR, merge, deployment, persistent migration or service restart.
No `.env.local`, real employee data or production credentials are used.
Full/load suites remain uncoordinated and are not authorized to overlap A's runs.

## Validation evidence

First expanded check: **123 passed, 1 setup error in 11.98 s**. The disposable
fixture initially detected PostgreSQL's Unix-socket-only bootstrap server before
database creation. Corrected it to wait for the final TCP server; the failed
attempt's exact task container was removed.

Next focused run: **156 passed in 70.76 s**, no skips or failures. The next
expanded run stopped at **159 passed, 1 failed in 209.06 s**: a test-only metadata
query passed an empty parameter tuple while its literal contained `%`. The helper
now uses no binding arguments for parameterless statements. This failure was
not a domain test pass and is retained here.

Initial targeted Ruff reported three long lines, corrected by formatting.
The first repository lint reached Pyright and failed with 11 typing findings
covering the router coroutine override and Docker/psycopg fixture typing.
Corrected all findings without suppressing rules. A later repository lint passed.

After corrections, the targeted privilege/contention/audit selection passed
**18 tests, 49 deselected in 28.00 s**. Those deselections were intentional for
that selection; they were included in the subsequent complete focused runs.
The expanded focused run passed **194 tests in 73.58 s**. A final readiness
regression was then added, requiring the safe app role, all seven HR tables with
staged RLS, and the required HR functions.

Final commands from the HR worktree:

```sh
NO_LOCAL_ENV=1 ENV=ci UV_OFFLINE=1 uv run pytest tests/test_hr_contracts.py tests/test_hr_policy.py tests/test_hr_training.py tests/test_hr_persistence.py -q --capture=tee-sys
NO_LOCAL_ENV=1 ENV=ci UV_OFFLINE=1 make lint
git diff --check
```

- Final focused suite: **195 passed in 58.48 s**, no failures, skips,
  deselections or reported warnings. Includes 123 HR-0 cases and 72 new
  persistence/API cases. It covers the final functional implementation.
- Final repository lint: **passed** Ruff, formatting (323 files), Pyright
  (0 errors, 0 warnings) and import-linter (1 kept, 0 broken).
- Final documentation/scope checks passed: two Markdown notes, 21 local link
  targets, balanced fences, whitespace in all 26 HR-owned files, no tracked/shared
  edits or staged files, and clean `git diff --check`. The final Docker label
  check found no task-owned HR fixture containers remaining.
- Full `make lint test`, load/performance suite and
  `tests/test_migrate.py::test_make_reset_only_disposable_container`: **not run**.
  The earlier shared-host coordination restriction remains; no window was claimed.
- Ordered HR migration application/reapplication, shared gateway/OpenAPI readiness,
  browser/UI, chat/voice and live/persistent acceptance: **not run/not implemented**
  in this slice. The candidate schema was installed against real baseline plus
  existing ordered migrations only in task-owned disposable databases.

The final task container was
`ca81ec5ef36da8eb75e880435849187c5b5d948dfd2d42adba7f2ddba46c2796`;
creation and exact removal were both recorded. The preceding 194-test run used
`e84ffbdf16871c916876ef0e9be54dc9e9d646a6a6dd138c70e96fa1c2e97bbc`;
the targeted run used
`9b39fe31f1290fbfc8fa65b1f6c6197b42105223e939c971610fc1b551992382`.
Both were removed. Earlier failed runs also removed their exact containers,
including `912dee196ef399de4ecb4b6c314d316e832720cc8a1cc41944fd364910de4463`
and `4adda136ac27f513aeaa62538418e155ba461be2bb1061d6aaea26ecfe81c34c`.
Every fixture records its task ID before start and removes only that object and
its task volumes in `finally`. No image build/pull or global pruning was used.
The six pre-existing retained containers/volumes and persistent services were
not touched. No secret/environment file was read or copied. Dependency manifests
and lockfile remain unchanged; validation used the worktree's offline environment.

## Implemented backend and integration contract

Sources: [records](../../csa/hr/records.py), [service](../../csa/hr/service.py),
[router](../../csa/hr/api.py), [schema candidate](../../db/migrations/0380_hr_foundation.sql),
[database/API tests](../../tests/test_hr_persistence.py). The service reuses
the existing HR-0 policy/transition functions without modifying their contracts.

| Manual API operation | Endpoint under `/console/v1/hr` |
|---|---|
| Authorized employee projection | `GET /employees/{employee_id}` |
| Authorized training projection | `GET /training-requests/{request_id}` |
| Save own training draft | `POST /training-requests` |
| Submit, withdraw, approve or reject | `POST /training-requests/{request_id}/transitions` |

Writes require a UUID `Idempotency-Key` header. A platform administrator must
select `client_id` explicitly and still needs current tenant membership and
explicit HR grants; role alone grants nothing. A tenant member cannot select
another tenant. The router rejects machine Bearer authentication, forged
authority headers/query/body fields, missing/invalid CSRF and wrong origins.
Read-only memberships cannot mutate, even if an erroneous HR grant exists.
Body bytes are bounded before parsing; validation errors omit submitted content
and all handled responses use `no-store`. Missing and forbidden HR resources
share a safe `resource_unavailable` response. Database failures return explicit
unavailability; lock/deadlock/serialization conflicts request a fresh retry.

On final integration, construct `HrService(app_pool, session_idle_seconds=...)`
using the exact current console identity configuration, run `check_ready`, set
`app.state.hr_service`, then mount `build_hr_router()`. It also requires the
existing real `app.state.identity_service`; no fake actor dependency is supplied.
These shared gateway/readiness/OpenAPI edits are reserved for A's final review.
Until then this router exists and is exercised in-process in tests, but is not
mounted in the production/development gateway. Central OpenAPI is unchanged.

### Transaction and revocation ordering

1. Existing identity resolution and CSRF run at the HTTP boundary. The HR
   transaction then independently validates the exact session/user/token proof,
   password-change gate, idle/absolute expiry, current membership and CSRF proof.
2. A narrow `csa_app`-only security-definer guard acquires shared row locks on
   user, session and membership. No identity table/secret read grant is added
   to the domain role. The identity role retains no HR table/function access.
3. Narrow helpers lock the actual account link, owner assignment and existing
   grant rows. The resolved inputs come from those locked server records,
   never request/model claims.
4. Serialize a tenant/subject/idempotency-key with an advisory transaction lock.
   A replay rechecks current authority before returning the original persisted
   result version/status. Immutable content need not be duplicated in receipts.
   Changed semantic input with the same key conflicts; another subject cannot
   replay that key as its receipt. Lock the target employee and assigned manager
   before checking the resource policy. An inactive employee is unavailable;
   an inactive manager cannot authorize a decision.
5. Lock the exact training row and run the HR-0 transition/version policy.
   Revalidate session time/CSRF immediately before the write. CAS, receipt and
   content-free append-only audit commit or roll back together. A deferred DB
   constraint requires the matching audit/receipt for every request version.
6. Existing identity/authority UPDATE or DELETE revokers conflict with these row
   locks. If the HR operation locks first it may commit, then revocation proceeds;
   if revocation commits first the operation must observe denial. Revocation
   is effective at its commit; this is not retroactive cancellation of a prior
   committed HR decision. Different legacy lock ordering may deadlock; that
   aborts explicitly and a retry must start with fresh authorization.

RLS is enabled, never forced, with tenant/resource visibility; exact action and
field filtering are also enforced by the service. `csa_ro` and `csa_identity`
receive no HR data access. The application cannot provision employees, links,
owners or grants, or rewrite audit/receipts. SQL and stored projections contain
no salary/bank/medical/CV contract; audit excludes training content and notes.
All new runtime SQL is parameterized under `db/sql/hr/`.

The schema allows an employee without an account, with at most one active account
link per employee and one employee per account within a tenant. There is one
explicit current client-owner assignment per tenant. Membership references in
request/receipt history require retaining membership rows; revocation disables
them rather than deleting historical principals. Provisioning, delegates and
real-data retention policy remain outside this slice and require final review.

## Remaining decisions and final review

A owns final acceptance of the adapter mapping and identity revocation ordering,
shared integration, migration allocation and the validation window. Per-tenant
employee/account links, HR grants and client-approved owner assignments require
deliberate provisioning; broad console roles never create them automatically.
Directory/grant administration policy and real-data rollout remain separate work.

### Final review packet for Developer A

All additions remain untracked/uncommitted in this isolated branch. HR-0 models,
policy and training functions retain their reviewed contracts; only the package
docstring was updated to describe the new modules. HR-1 adds:

- `csa/hr/api.py`, `csa/hr/records.py`, `csa/hr/service.py`.
- `db/sql/hr/schema.sql` plus parameterized session, authority, employee, request,
  key-lock, transition, receipt, audit and readiness queries under `db/sql/hr/`.
- `tests/test_hr_persistence.py` and this progress document.
- A current-continuation pointer in the existing HR-0 handoff.

No A-owned workspace, identity, gateway, central progress, migration, global test
fixture, dependency or global OpenAPI file was changed. No A module is imported.

1. Review the actual schema/functions and transaction protocol above against
   A's current identity/workspace changes. The 14 synchronized revocation cases
   prove both orderings for seven authority sources; they do not replace review
   of future new writers, delegates or shared identity lock ordering.
2. Allocate the ordered migration after rechecking integration HEAD. Move the
   candidate schema into that migration with appropriate tests, keeping one
   authoritative definition; update the HR fixture's installation path. No
   migration number is reserved here. Verify apply/reapply and failed-ledger
   behavior, schema readiness and owner/app/identity privileges at integration.
3. Configure/check the service, mount the router, update shared schema readiness
   and regenerate the central OpenAPI contract in A-owned paths. Preserve the
   sandbox and keep HR out of A0's action/result union. No chat/voice action is
   activated by mounting the manual HR API.
4. Review deliberate tenant/account/employee, grant/field and client-owner
   provisioning. Existing roles cannot populate HR tables. Real onboarding and
   directory/grant administration need a separately scoped policy and surface.
5. Coordinate and run the required full suite and disposable reset acceptance;
   keep performance/load timing separate. Never use bare persistent reset or
   silently pull an unavailable image. Record actual results and container IDs.
6. Review the resulting backend before the separate manual UI and workspace/
   staff-browser voice adapters. Those surfaces must call this service, not
   duplicate business policy or synthesize a customer contact. Persistent rollout,
   app restarts, external sends and deployment remain separately authorized acts.

No commit, push, PR, merge or deployment was performed. This is a tested backend
implementation awaiting final review/integration, not a live HR feature claim.
