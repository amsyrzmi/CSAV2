# Developer B: bounded HR-0 foundation (2026-10-21)

Current continuation: the user authorized HR implementation and agents and
removed Developer A's review/integration as a gate. The active branch is now at
`/home/amsyrzmi/CSAV2-hr`. Read [current HR progress](../../hr/PROGRESS.md) for code,
validation and remaining work. This note preserves historical HR-0 evidence;
its freeze/review-first gates and temporary worktree paths are superseded.
A's worktree and the original dirty main are preserved.

## Start and ownership

Worktree: `/tmp/csav2-hr-foundation`; branch: `feat/hr-foundation`.
Inspected base: `0264c0ade3739f1285ab1a06e577573b71984d44`.
No commit, push, PR, merge or deployment was performed.

Read [repository rules](../../../AGENTS.md), [orientation](../../PROJECT_OVERVIEW.md),
[LG02](../../langgraph-rebuild/LG02_contracts_state_registry.md),
[LG05](../../langgraph-rebuild/LG05_spokes_and_tools.md),
[LG07](../../langgraph-rebuild/LG07_egress_outbox_hitl.md),
[LG13](../../langgraph-rebuild/LG13_agent_catalog_and_console.md), and
[LG15](../../langgraph-rebuild/LG15_console_turnover.md).
The supplied uncommitted `2026-10-21-workspace-agents-roadmap.md`, current
progress and active release handoff were read in the original worktree;
those newer planning edits are absent from this branch's base. Obtain their
current versions from Developer A before integration; do not copy evolving
shared code into this branch.

The original `/home/amsyrzmi/CSAV2` worktree was dirty on `main`, including
staging proposal/test/docs changes and two deleted review documents. It was
preserved. Developer A has a separate `feature/workspace-a0-contract` worktree
at `/home/amsyrzmi/CSAV2-workspace-a0`. No A-owned file was edited or imported.
The original HR-0 slice introduced these eight files; HR-1 additions are recorded
in the feature-specific progress document linked above:

- [Package boundary](../../../csa/hr/__init__.py)
- [Models](../../../csa/hr/models.py)
- [Field and relationship policy](../../../csa/hr/policy.py)
- [Training proposals and projection](../../../csa/hr/training.py)
- [Contract tests](../../../tests/test_hr_contracts.py)
- [Policy tests](../../../tests/test_hr_policy.py)
- [Training tests](../../../tests/test_hr_training.py)
- This handoff, `docs/handoffs/2026-10-21-developer-b-hr0-foundation.md`

## Implemented pure behavior

Employee references always contain both tenant and employee UUIDs. Login
accounts and employees are separate; the optional console-account link is
never inferred from email and is omitted from directory output. There is no
customer contact, `AgentContext`, console role, graph or persistence dependency.

External input models forbid extra fields and bound strings/enums/versions:
`DirectoryLookupInput` selects a local employee ID; `TrainingDraftInput` contains
only title/objective; `TrainingCommandInput` contains action, expected version
and an optional bounded decision note. Rejection requires a note. Control
characters are rejected. Neither tenant, actor, employee linkage, manager,
client ownership nor grants can be injected into draft/command authority.

`DirectoryRecord` and `TrustedAccess` are **trusted adapter requirements, not
authentication**. Only a future verified server adapter may construct them.
Pydantic validation does not establish that an assignment or grant is true.
The access object defaults to no grants and no client ownership. Unknown actions,
scopes, fields and unsupported action/scope combinations are rejected.

`project_directory` returns only allowlisted, explicitly granted field values.
The allowlist is display name, job title, department, work email and assigned
manager reference. Hidden values are replaced with absent/default-null fields;
serialize with `exclude_none=True` when an omitted-field wire shape is wanted.
An empty field grant reveals only the scoped employee reference. Matching grants
union their fields; an unrelated manager grant contributes nothing.
There are no salary, bank, medical or CV value/grant contracts. No HR record,
free text or decision note is written to generic result facts, logs or model calls.

`propose_draft` produces content plus scoped employee reference, without a
request ID, persistence flag or approval claim. There is no create/save function.
`TrainingSnapshot` models hypothetical or future adapter-loaded state and rejects
inconsistent decisions. `propose_transition` checks resource binding, tenant,
permission, expected version, allowed transition and injected aware clock.
It returns `before` and candidate `after`, increments the version and leaves the
original untouched. Clock rollback and version exhaustion fail explicitly.
It writes nothing: evaluating the same original snapshot twice produces two
equal proposals, not an idempotent committed outcome. Only future storage can
prevent concurrent duplicate commits. `project_training` requires scoped read
permission and omits internal decision subject/note.

| Current state | Intent | Candidate state |
|---|---|---|
| draft | submit | submitted |
| draft or submitted | withdraw | withdrawn |
| submitted | approve | approved |
| submitted | reject with reason | rejected |
| approved, rejected or withdrawn | any | denied |

No implicit edit, reopen, expiry, cancellation of approved training, fallback
approver, notification or automatic approval is implemented. HR business approval
is separate from the existing held customer reply queue.

## Permission matrix and decisions

Every allowed entry below still requires an explicit resolved action grant;
there are no seeded/default grants and no deployed policy. Field selection
within directory reads is always explicit.

| Relationship / grant scope | Directory fields | Training view | Draft / submit / withdraw | Decide |
|---|---|---|---|---|
| Self | explicit safe fields | own | own | denied through self grant |
| Assigned manager | explicit safe fields for direct assignment | assigned employee | denied | assigned employee |
| HR operator | explicit safe fields in tenant | tenant-scoped | denied | denied unless separately eligible through manager/client scope |
| Verified client owner | no implicit read | no implicit read | no implicit write | explicit decision grant; assigned manager required |
| Console platform/tenant admin, tenant_staff, unspecified case | no implicit access | no implicit access | no implicit access | no implicit access |

The user identified the client themselves as the HR policy reviewer/assigned
manager, and confirmed client approval including their own request while blocking
approval when no manager is assigned. The client-owner decision grant therefore
allows approval/rejection when **any explicitly assigned manager** is present,
including the client's own request; it is an owner override within that tenant.
An ordinary manager cannot approve their own request through a self-manager
relationship. A client self-manager relationship can pass with verified owner
authority and the separate manager decision grant. No-manager decisions return
`permission_denied`, including for the client. Drafting/submission without a
manager remains a proposal; it does not bypass the approval gate.

Follow-up user decisions (2026-10-21): self, assigned managers for their direct
reports, and explicitly granted HR operators may see all five safe directory
fields: display name, job title, department, work email and assigned manager.
Everyone else remains denied. This is the approved HR-1 field matrix, not a
change to HR-0's generic explicit-field contracts or a deployed grant.
The user also approved a separately recorded, client-approved client-owner
assignment per tenant; no console role implies ownership.

The individual verified principal representing each client owner is still to be
recorded at HR-1. Tenant-admin/platform-admin status alone must never supply
`client_owner=True`. HR operators still need explicit action grants; actual
account links and reporting relationships must come from verified server state.
Broader team hierarchy, delegates, inactive employees/managers, support access,
retention and reassignment effects on previously submitted requests remain gates.
This implementation checks the adapter's current direct assignment, so an old
manager's grant stops matching after that assignment changes.

## Proposed HR-1 schema constraints (no DDL)

Developer A allocates migration numbers only after rechecking integration HEAD.
Load the database/console skills at that later approved slice.

- Employees: composite `(tenant_id, employee_id)` key; bounded safe columns;
  optional account link through tenant membership, never email deduplication.
  Confirm whether one active account link per employee/account/tenant is required.
- Manager assignment: composite tenant/employee foreign keys to both employee
  and manager, with current/effective assignment authority. Do not globally forbid
  self-manager references while the approved client-owner exception exists.
- Grants: tenant, verified subject, action, resource relationship and safe field
  allowlist, reviewed owner/policy revision and revocation state. No console-role
  expansion and no sensitive-field wildcard. Keep ownership binding distinct
  from HR-operator and manager grants.
- Training requests: composite tenant/request key; composite employee FK;
  bounded validated content, state CHECK and bounded nonnegative version;
  immutable submitted content unless a separately reviewed edit protocol exists.
- Decisions: tenant/request/subject binding, approved/rejected action and bounded
  private note with rejection reason; decision/state coherence and a single
  terminal decision. Audit the effective manager/owner/policy revision used.
- Mutation transaction: reauthorize from current server state, load/lock current
  resource/assignment, compare-and-swap expected version, write state plus
  immutable audit and scoped idempotency receipt atomically. A proposal alone
  is never the receipt. Bind idempotency to tenant, verified subject, action and
  key; reject changed-payload replays. Outbox intents, if later approved, belong
  in that same transaction, with separate delivery attempts.
- Staged RLS/least privilege must enforce tenant and resource policy at storage
  boundaries. Do not enable FORCE RLS while legacy n8n uses the owner role.
  No HR truth belongs to reporting/wallboard snapshots or the customer queue.

## One adapter seam for Developer A

1. Agree the account-to-employee and client-owner mappings. Verify session,
   active tenant membership, CSRF for mutations, and current domain grants using
   server state; reject caller authority headers/body/model claims.
2. Resolve path/selector IDs inside that tenant. Load `DirectoryRecord` including
   current manager assignment and optional account link, plus request snapshot.
   Produce `TrustedAccess` from verified identity/owner/grants; never deserialize
   it, manager facts or `ResolvedGrant` from HTTP/model payloads.
3. Map `directory.read` to employee resource plus explicit fields;
   `training.read` to request/employee; `training.draft`, `training.submit` and
   `training.withdraw` to self; `training.decide` to direct manager or separately
   verified client owner, always requiring an assigned manager. Reauthorize
   every operation/view/export rather than caching authority in a chat turn.
4. Reuse these domain operations through one service/adapter for manual UI,
   workspace chat and authenticated staff-browser voice. Keep untrusted intent
   separate from verified identity and commit confirmation. Preserve sandbox
   restrictions and never fabricate a customer contact.
5. Persist via the reviewed transaction above; map content-free domain codes to
   typed errors. Do not log Pydantic `.errors()` inputs or HR free text. Limit
   public output to authorized projections, with safe fact builders reviewed
   before any model result integration.
6. Add real authenticated HTTP spoofing, revoked-grant/manager-change, CSRF,
   tenant RLS, replay/concurrency, atomic audit rollback and API snapshot tests
   during HR-1. HR-0 injection tests cover the external **model boundary only**;
   no dummy endpoint or HTTP authentication acceptance is claimed.
7. Coordinate migrations/readiness/OpenAPI, full-suite timing and per-tenant
   disabled-by-default registration with A. No HR capability is registered here.

## Follow-up verification and HR-1 contract review

The user relayed Developer A's independent verdict: **ready for integration
review, not runtime integration**, with no blocking defects. A reported fresh
**123 focused tests passed**, repository lint passed, and handoff links/fences
and whitespace passed. Full/load suites and disposable reset acceptance were
not run. These are A's reported results, distinct from B's commands below.
The referenced detailed verification response was not supplied in this thread;
do not invent further findings, timing or an accepted integration contract.

The user explicitly requested leaving HR code unchanged. This continuation
changes only this HR-local note. A's local untracked
`csa/workspace/contracts.py` and
`docs/handoffs/2026-10-21-workspace-a0-authority-design.md` were inspected
read-only at base `0264c0a`; no A-owned source was imported or edited.
The latter note still describes earlier HR-policy deferral. The new user
decisions above apply to this HR proposal; A owns reconciling their note.

### Proposed mapping for A to accept

This table proposes the future server adapter; it is neither implemented nor
jointly accepted yet. A's current workspace action/result union accepts only
page guidance and a local order read. It must continue rejecting HR actions.

| Verified server source / A contract | HR-local destination | Required check |
|---|---|---|
| Current session lookup; `VerifiedWorkspacePrincipal.user_id` | `TrustedAccess.subject_id` | Same verified human throughout; current session and applicable membership, never raw caller UUID authority |
| Verified selected tenant; `VerifiedWorkspacePrincipal.tenant_id` | `TrustedAccess.tenant_id` | Resource, request, account link, ownership and grants all bind to this tenant |
| Current `(tenant, user)` employee-account link | `TrustedAccess.employee` | Optional; never email inference, customer contact conversion or browser-supplied employee linkage |
| Client-approved, current per-tenant owner assignment | `TrustedAccess.client_owner` | Separate recorded authority, checked fresh; no derivation from role or ownership of a workspace thread |
| Current reviewed HR action/resource policy | `ResolvedGrant` tuple | Only explicit action/scope grants; A0 `action_allowed`/`resource_allowed` booleans are not HR grants |
| Approved self/direct-manager/HR read field matrix | Directory-read grant fields | Exactly the five approved fields in eligible resource scope; no grant for everyone else |
| Tenant-scoped employee and current manager rows | `DirectoryRecord` | Composite reference integrity, current assignment; record may have no console account |
| Tenant/request row plus employee binding | `TrainingSnapshot` | Load by verified tenant and requested opaque ID; compare exact employee reference and integer version |
| Validated DTO content/intent only | `TrainingDraftInput`, `TrainingCommandInput` | Reject authority extras; selector IDs never grant access |
| Injected server clock | `propose_transition(now=...)` | Aware timestamp and current trusted facts; this clock check is not a revocation fence |
| HR-local public projection | Future explicitly reviewed HR response type | Never cast into A0 `OrderReadResult`, generic facts or arbitrary result dictionaries |

Manual HTTP UI uses the same verified HR application service that later chat
and voice adapters will call. It need not construct a workspace turn or customer
context to invoke HR. Future queued HR work additionally needs A-owned, separately
reviewed binding of original tenant, session, owner, thread, action, resource,
expected version, payload fingerprint and expiry. Do not reinterpret A0's
order-only resource version or add an HR case to its union during this review.
Fresh authority is needed at attempt, retry and commit; a serialized principal,
grant or boolean snapshot cannot serve as current authorization.

### Bounded HR-1 proposal, before implementation authorization

Proposed first implementation task: persisted synthetic directory plus training
draft/request/decision storage, one verified HR service and authenticated manual
API. Keep chat/voice activation, generic employee administration, bulk imports,
notifications, agent registration and UI implementation as later explicit slices.
Directory administration/grant-assignment endpoints are excluded until their
maker/reviewer policy is agreed; synthetic fixture setup establishes test rows
only. This prevents an unreviewed directory-write permission from entering a
read/training task. No real employee onboarding or owner provisioning is implied.

Proposed service operations and acceptance contract:

| Operation | Domain reuse | Observable acceptance required |
|---|---|---|
| Read one employee | `project_directory` | Current identity, tenant, relationship and explicit fields; hidden/missing/forbidden resources have safe responses |
| Read one training request | `project_training` | Current scoped read; no internal decision subject or private note in public view |
| Save own training draft | `propose_draft` | Tenant-scoped stored request/employee, generated opaque ID, version and audit; response claims saved only after commit |
| Submit/withdraw own request | `propose_transition` | Expected-version CAS, exact/conflicting idempotency replay and atomic audit; terminal/stale transitions write nothing |
| Manager/client approve or reject | `propose_transition` | Fresh assignment and explicit grant; client self-approval permitted; ordinary self-approval and missing-manager decision denied |

Do not add directory listing/search/export in this first API task: HR-0 has an
individual lookup contract, and enumeration/pagination needs its own reviewed
projection and resource-scope tests. Do not accept caller-supplied target employee
for own-draft creation; resolve it through the current verified account link.
An employee without an account can still exist and be read under eligible grants.
Acting on behalf of such an employee remains outside this slice.

A owns shared identity lookup, session/membership revalidation, mounts/OpenAPI,
schema readiness, shared fixtures and migration allocation. B may implement
HR-specific service/storage/SQL/tests only after the expanded path ownership is
explicitly agreed. A allocates migration numbers at fresh integration HEAD;
this note reserves none. The existing pure HR-0 code remains unchanged unless
a separately reviewed implementation requires a stated domain-contract change.

The implementation plan must specify a transaction ordering/fence for concurrent
session, membership, owner/grant revocation and manager reassignment, followed
by version CAS and atomic request/decision/audit/idempotency writes. Prove it
with synchronized disposable-database tests, including no partial outcome after
injected failure. Two separately committed identity and domain transactions
without a reviewed ordering do not close that race. Keep owner assignment
separate from the tenant-wide console administrator role and staged RLS.

Before declaring the persisted slice complete, require authenticated spoofing,
CSRF, cross-tenant composite-FK/RLS, lost grant/ownership, manager reassignment,
client self-approval, missing-manager denial, conflicting replay, competing
decisions and rollback evidence. Coordinate the shared-host full suite and
`tests/test_migrate.py::test_make_reset_only_disposable_container` acceptance
with A. There is no reserved window yet; full/load/reset checks remain not run.
Disposable tests may not silently pull images or touch persistent databases.

### Copy-ready contract-review request to A

```text
Review the follow-up HR-1 mapping and bounded task in B's existing note:
/tmp/csav2-hr-foundation/docs/handoffs/2026-10-21-developer-b-hr0-foundation.md

B accepts your independent HR-0 verdict with no blocking findings. HR source
and tests remain unchanged. User now approved all five safe directory fields
for self/direct-manager/explicit-HR scopes, everyone else denied; owner authority
is a separately recorded client-approved assignment per tenant, never a role.
Keep confirmed approval rules and keep HR out of A0's action/result union.

Please accept or amend the proposed user/tenant/employee/owner/grant/resource
mapping; identify any missing contract and accept a shared revocation/commit
ordering proposal. Confirm HR-1 path ownership and review the first persisted
directory-lookup/training manual API slice, without implementing it yet.
No directory administration, listing/export, notifications, HR runtime/chat/voice
activation or registration belongs to that slice. Identify one coordinated
full-suite/disposable-reset window; do not assume one exists or start it now.
Return accepted decisions, concrete amendments and remaining gates. You own
reconciling your A-local design note; do not edit B's files or central documents.
No commit/push/merge/PR, service, image pull or persistent/provider action.
```

## Validation and environment

Follow-up continuation (documentation only): all seven HR source/test SHA-256
hashes matched the pre-edit baseline. The existing eight-file task scope remained
exact, with no tracked/staged changes. Validation passed for 14 local links,
balanced fences, note whitespace and `git diff --check`. No pytest/lint, full/load
suite or disposable reset was rerun for this documentation-only continuation.
Earlier B results and the separately user-reported A verification remain dated
evidence. No implementation, integration, service or infrastructure action occurred.

Commands ran from `/tmp/csav2-hr-foundation` with local environment loading disabled
and dependency resolution offline:

```sh
NO_LOCAL_ENV=1 ENV=ci UV_OFFLINE=1 uv run pytest tests/test_hr_contracts.py tests/test_hr_policy.py tests/test_hr_training.py -q
NO_LOCAL_ENV=1 ENV=ci UV_OFFLINE=1 make lint
git diff --check
```

- Initial focused tests: **112 passed in 0.58 s**, no failures/skips.
- Final focused tests after policy-edge tests and decision typing fix: **123
  passed in 1.07 s**, no failures, skips, deselections or test warnings. This
  covers the final code; subsequent changes are handoff documentation only.
- First lint run failed on three new-test import-order errors. After those were
  corrected, the second run failed on one Pyright decision-enum narrowing error.
  Both were fixed within HR-owned files; no acceptance thresholds changed.
- Final repository lint passed: Ruff, formatting (**317 files**), Pyright
  (**0 errors, 0 warnings**), and import-linter (**1 kept, 0 broken**).
- Handoff validation passed: **14 local link targets**, balanced code fences,
  no trailing whitespace/missing newlines in all **8 owned files**, exact
  ownership scope, no tracked/staged changes, and `git diff --check` clean.
  The original worktree's final status matched its initial dirty-file list.

The user explicitly instructed: full-suite timing is not coordinated; run focused
tests and lint only. Thus full `NO_LOCAL_ENV=1 ENV=ci make lint test`, load suite
and `tests/test_migrate.py::test_make_reset_only_disposable_container` were **not
run**, rather than skipped/passed. No bare `make db-reset` was run. Persistence,
runtime and phase-wide acceptance remain unverified by HR-0.

The shell sandbox failed to start with `bwrap: loopback: Failed RTM_NEWADDR`.
Approved commands ran outside it. A worktree-local `.venv` was created with
`UV_OFFLINE=1`, the existing pinned lockfile and cached dependencies; no manifest
or lockfile changed. The installer reported a harmless hardlink-to-copy fallback.
No `.env.local` or environment-secret contents were read, copied or loaded.
There were no service starts/restarts, container/image/network/provider/model
operations, persistent database connections, migrations or production access.
No task-created container IDs exist to clean up. The six retained old test
containers/volumes and shared Cloudflare/legacy voice services were untouched.

## Bounded continuation prompt

```text
Continue Developer B HR work in /tmp/csav2-hr-foundation on feat/hr-foundation.
Read this HR-0 handoff and the current roadmap/progress supplied by Developer A;
the uncommitted 2026-10-21 planning files are absent from base 0264c0a.
Preserve the original dirty main tree and A's separate workspace branch.
HR-0 is a pure proposal package, not an authenticated or persisted workflow.
Client may approve including own request, but a manager must be assigned;
client authority must be mapped from verified server state, never admin role.
The five safe directory fields and explicit per-tenant client-owner assignment
are approved. The user subsequently authorized implementation and deferred A's
contract review until the end. Read docs/hr/PROGRESS.md for the HR-1 backend,
fresh validation and the exact remaining shared integration steps. Keep the
original main and A's active workspace/migration/gateway work untouched.
Do not register, mount in the shared gateway, apply persistent migrations,
deploy, commit or push implicitly. Do not run the full/load suite
until shared-host timing is coordinated. Use synthetic/disposable fixtures only;
never inspect .env.local or use persistent/production databases or providers.
```
