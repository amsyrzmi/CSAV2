# A0 workspace authority design and continuation

Date: 2026-10-21. Status: user-confirmed A0 design with additive pure contracts
and deterministic tests implemented. No persistence, authenticated adapter,
runtime wiring or live acceptance implemented.

The user approved only pure contracts/tests, explicitly deferred HR owner/
policy with HR denied, and reported no coordinated full-validation window.
Focused pure tests and lint are authorized; full-suite/reset evidence remains
missing. No further product-scope choices were reopened.

## Isolation and source evidence

- Verified original `main` and new worktree HEAD:
  `0264c0ade3739f1285ab1a06e577573b71984d44`.
- A owns branch `feature/workspace-a0-contract` in
  `/home/amsyrzmi/CSAV2-workspace-a0`. Original
  `/home/amsyrzmi/CSAV2` remains on `main`; its dirty staging/release/progress
  changes, untracked planning files and two review deletions are untouched.
- A concurrent B worktree was subsequently observed at
  `/tmp/csav2-hr-foundation`, branch `feat/hr-foundation`. No B files were read
  or edited. Its presence is not proof of agreement or a reserved test window.
- Read the supplied uncommitted roadmap and B prompt in the original worktree;
  they are not present automatically in this branch. Do not copy evolving shared
  planning files into this branch or attribute them to A0.
- Read [repository guidance](../../../AGENTS.md), [README](../../../README.md),
  [overview](../../PROJECT_OVERVIEW.md), current original-worktree progress,
  [API contract](../../CONSOLE_API.md), LG02/LG05 and relevant LG07/LG13/LG15 and
  console migration-plan sections. Original-worktree release handoff was read
  only for preserved environment/staging gates.

Source findings:

| Existing boundary | Evidence and implication |
| --- | --- |
| Human identity | [ConsoleActor](../../../csa/console/contracts.py) includes session UUID and broad role; [identity resolution](../../../csa/console/identity.py) checks active user/session/membership and forced password change. It also carries a CSRF hash: never serialize the entire actor to queued work. |
| Tenant selection | [Access](../../../csa/console/access.py) requires explicit existing tenant selection for platform admins, rejects tenant-member cross-selection and separates machine access. No proxy/actor headers establish human authority. |
| Sandbox | [Chat](../../../csa/console/chat.py) has owner-bound threads and separate sandbox contacts. [Runner](../../../csa/agents/runner.py) allows only KB query and product search/detail for web. [Turn dependencies](../../../csa/turn/deps.py) separately refuse callbacks, restricted composition and invalid outbound bindings. Preserve all layers. |
| Business reads | [Business](../../../csa/console/business.py) permits human platform/tenant admins and staff, denies readers/machines, projects local summaries only. Order queries currently live in router closures, not a reusable authorized service. A1 must extract one, preserving current behavior. |
| Agent identity | [AgentContext](../../../csa/agents/base.py) requires a customer contact, pool and pending-action store. It is not a verified human, HR employee or manager contract. |
| Reply approvals | [Inbox contract](../../CONSOLE_API.md#telegram-customer-inbox-m5-guarded-dev) governs customer-message takeover/reply decisions, not order, HR or finance authorization. |
| Execution switches | Runner freshly checks tenant capability switches on attempts/retries. Switches are necessary but do not establish human/resource/field authority. |

Relevant existing tests inspected, not executed here:
[business](../../../tests/test_console_business.py),
[sessions](../../../tests/test_console_sessions.py),
[chat](../../../tests/test_console_chat.py) and
[runner](../../../tests/test_agents_runner.py).
These are regression targets, not evidence for a new workspace surface.

## 1. Separate INTERNAL surface and permission matrix

Propose future `/console/v1/workspace` and an owner-private workspace-thread
namespace. Route mounting, storage and navigation are NOT A0 changes.
Do not adopt sandbox contacts/history, customer conversations or Telegram
staff-target identity. Platform admin tenant selection is request context,
not a field in the model/browser action payload.

This is the approved initial contract matrix, not new grants in the runtime:

| Principal/scope | Page guidance | Local order summary | HR directory/training | Business commits |
| --- | --- | --- | --- | --- |
| Anonymous or machine | Deny | Deny | Deny | Deny |
| Customer-channel/contact identity | Deny workspace | Existing customer policy only, never staff reads | Deny | Existing customer callback policy only |
| Human `read_only` | Deny initial workspace, including thread content | Deny, matching manual business API | No inferred grant | Deny |
| Human `tenant_staff` / `tenant_admin` with current tenant membership | Allow bounded supported-page guidance | Allow only through current authorized business service and tenant/resource lookup | Deny until separately resolved HR action, relationship and field policy | Deny A0/A1 |
| Human `platform_admin` | Explicit existing tenant required; own threads only | Existing manual business permission in explicitly selected tenant | No HR/support/payroll privilege inferred | Deny A0/A1 |
| Future explicitly authorized HR operator/self/manager | Base workspace access still required | No extra business grant | Exact action/resource/field scope only after HR-owner review | No inferred permission |

Thread access always requires exact `(tenant_id, owner_user_id, thread_id)`,
even for another platform admin. A second user cannot confirm the owner's
proposal merely because both have a broad admin role. A future independent
approver has a separate reviewed approval path and identity.
Static guidance contains only allowlisted application instructions, not page
contents, customer facts or employee facts.

## 2. Implemented pure contracts

Repository search found no existing workspace module to extend. Approved A0
files:
[package marker](../../../csa/workspace/__init__.py),
[contracts and deterministic policy](../../../csa/workspace/contracts.py),
[tests](../../../tests/test_workspace_contracts.py). Validation and policy checks
stay in the contracts module; no framework or plugin abstraction was added.

Use Pydantic v2 with `extra="forbid"`, `strict=True`, `frozen=True`; reject
unknown fields at every nesting level. JSON parsing accepts canonical UUID/time
representations; Python constructors require actual typed values. No arbitrary
`dict[str, Any]`, capability names, action strings, SQL, URLs, DOM, attachments,
employee records, credentials or grant maps in external DTOs.
Runtime request byte limits and safe validation responses remain A1 work.

### Untrusted browser/model inputs

| Proposed DTO | Fields and invariants |
| --- | --- |
| `PageHint` | Discriminated union: `page="workspace_home"` or `"business_orders"` has no entity; `page="business_order"` requires one `OrderHint`. Allowlist keys are semantic, not arbitrary browser paths. |
| `OrderHint` | `kind="local_order"`, `order_id`: opaque nonblank string, 1..100 characters, no control characters, no normalization. Existing local order IDs are strings, not necessarily UUIDs. A syntactically valid ID never proves existence/access. |
| `WorkspaceTurnInput` | `request_key: UUID`, nonblank `body` 1..4000 characters, optional `page_hint`. No tenant, owner, session, actor, role, grant, contact, employee, channel or voice fields. |
| `GuidanceProposal` | `action="page_guidance"`, supported guidance page key only. No model-written instructions become executable navigation or authority. |
| `OrderReadProposal` | `action="local_order_read"`, one `OrderHint`. Target is untrusted even when copied from page context. No list/export wildcard. |
| `WorkspaceActionProposal` | Discriminated union of the preceding two read-only actions; action determines risk `"read"` in code. No draft/commit action accepted in this slice. |

External text is always untrusted, including instructions embedded in text.
Pydantic cannot establish authentication or scrub all sensitive free text.
Before runtime persistence/provider use, define minimization, safe traces and
retention; common-pattern redaction alone is insufficient for HR data.

### Server-only identity, binding and authority

These types may be constructed only by trusted adapters, never accepted as
request bodies or model output. Their names/types do not authenticate them.

- `VerifiedWorkspacePrincipal`: `user_id`, opaque non-secret `session_id`,
  selected `tenant_id`, current console `role`, `verified_at`. No bearer,
  cookie, CSRF value/hash, email, contact or employee UUID. It denotes a current
  identity lookup, not a permanently trusted serialized actor.
- `WorkspaceWorkBinding`: `work_id`, `thread_id`, `owner_user_id`, `session_id`,
  `tenant_id`, `request_key`, `created_at`, `expires_at`, typed original action.
  Bind original resource inside that action. Use aware UTC instants with
  `created_at < expires_at`; maximum TTL is 15 minutes for A0 contracts.
  Do not persist a grant or role snapshot as permission.
- `ResolvedWorkspaceAuthority`: exact `work_id`, `thread_id`, `tenant_id`,
  `user_id`, `session_id`, typed action and resource, `checked_at`,
  `capability_enabled`, and action/resource permission outcome. Supplied only
  by a fresh resolver immediately before an attempt. For an order, include
  server-read `updated_at` as its local version token. Guidance has no entity
  version. No browser boolean may instantiate this authority.
- `WorkspacePolicyDecision`: discriminated allowed/denied outcome with fixed
  safe codes, not a silent false or fabricated successful read. Suggested
  codes: `principal_unavailable`, `binding_mismatch`, `action_forbidden`,
  `capability_unavailable`, `resource_unavailable`, `stale_resource`,
  `proposal_expired`. Forbidden and missing IDs share `resource_unavailable`
  externally; restricted existence does not leak.

Pure validation compares the exact principal/binding/authority and action,
requires a current permitted role, current switches/resource permission,
checks aware time ordering and expiry, and checks the original version token
when present. Reject a future-dated check and any mismatched nested resource.
Never infer authorization from an input hint. In A0 fresh lookup is an injected
synthetic fact, not a real session/membership/SQL query or dispatch fence.
`MAX_EVIDENCE_AGE` bounds injected snapshots to 30 seconds, inclusive, and both
checks must be at or after work creation. This fixture bound is not permission
to reuse cached authorization at runtime: A1 still needs fresh execution
lookups and a reviewed revocation/dispatch ordering. Missing authority returns
the additional explicit code `authority_unavailable`. All timestamps require
awareness; equivalent timezone offsets compare as instants.

The resolver must re-read membership by existing `(user_id, tenant_id)` key;
do not invent a membership UUID or claim the current identity tables provide
membership/grant revisions. Inactive/revoked identities produce an explicit
denial, not a fake enabled principal.

### Typed public results

Propose a discriminated union; no unrestricted result payload:

- `GuidanceResult`: `status="completed"`, `action="page_guidance"`, supported
  page key, fixed instruction key, `source="application_guide"`, aware `as_of`.
  UI resolves instruction text from reviewed application guidance.
- `OrderReadResult`: `status="completed"`, `action="local_order_read"`,
  safe existing `BusinessOrder`-equivalent projection, `source="local_records"`,
  `marketplace_sync="unavailable"`, aware `as_of` (local observation time).
  Preserve decimal amounts and local record timestamps. No ship-to/contact,
  arbitrary items, provider payload or credentials. In pure A0 use an explicit
  bounded projection, not an import of the FastAPI router module.
- `WorkspaceDeniedResult`: `status="denied"`, safe fixed code, no entity data.
- `WorkspaceUnavailableResult`: `status="unavailable"`, fixed safe dependency
  code and retryability. A failed dependency is not an empty successful read.

Queued/processing/uncertain/expired receipts belong to later persisted work
contracts, not a claim that constructing `OrderReadResult` executed a query.
Read completion is not delivery, acknowledgement, approval or a commit.
No unrestricted HR payload can inhabit these A0 result types.

## 3. Persisted work and confirmation design (not implemented)

1. On future request acceptance, resolve cookie session through identity
   service, enforce Origin/CSRF, resolve explicit platform tenant or current
   membership, supported surface and exact thread owner. Resolve allowed
   resource and minimize fields before model use. Store immutable binding,
   minimized input and canonical semantic payload fingerprint atomically with
   work receipt/audit. Scope replay to tenant/owner/thread/request key;
   same key + same payload returns original receipt, changed payload conflicts.
2. Store session UUID, not a session token/hash or CSRF secret. A new identity
   repository operation must look up the existing session by UUID and recheck
   active user, revoked/absolute/idle expiry, password-change gate and current
   membership. Current `resolve` takes a raw cookie token; do not fake a cookie
   or resurrect old work using a new session. Queued work does not touch idle
   time to keep itself authorized.
3. Bind work permanently to original tenant/thread/owner/session/action/resource.
   Tenant navigation cannot retarget it. Revoked session or lost membership
   terminates access/execution; new login must not silently transfer work.
   Owner can recover history under a later verified session if current access
   policy allows, but unexecuted old work requires a fresh request/key.
4. Before each attempt/retry and publication, recheck identity, membership,
   current action/field/resource permission, capability switches, expiry and
   resource version. Do not reuse admission-time or planner-time permission.
   Local order `updated_at` is only an available optimistic token, not an
   established lock/version protocol. A1 must prove transaction ordering and
   define re-plan/conflict handling; pure checks do not close revocation races.
5. Future mutation proposals additionally bind immutable typed payload hash,
   risk/action, original entity/version, proposer, expiry and approval policy
   version. Do not add generic mutation dictionaries now. Confirm via a
   CSRF-protected, server-issued opaque proposal reference bound to the same
   human/session/tenant/thread and exact payload, never text/spoken "yes".
   Revalidate current grants and any independent approver at confirm AND commit.
6. Future atomic commit must lock/recheck the proposal/resource and current
   authority, use one idempotent domain transition with audit/outbox, and consume
   the bound confirmation once. Define a dispatch/commit linearization fence
   for concurrent revocation; a prior successful pure check is insufficient.
   An uncertain remote outcome is reconciled, not blindly replayed.
7. HR work should store opaque authorized domain references and safe decisions,
   not salary/bank/medical/CV data or full employee records in checkpoints,
   queue payloads, generic facts, model prompts or logs. A separate reviewed
   HR retention/projection policy is required before persisted HR work.

No migration, table, revision column, queue or secret resolver is created here.

## 4. Runner/graph integration comparison

| Option | Assessment |
| --- | --- |
| Feed staff into existing `AgentContext` using `channel="workspace"` | Reject: bypasses the web allowlist without establishing human/resource authority; falsely maps human user to customer contact and inherits customer callbacks/composition/delivery. |
| Add optional staff fields to customer envelope/context and reuse everything | Defer: broad invalid combinations and every queue/checkpoint/callback/recorder/outbound check must understand them. Not justified for A0/A1. |
| Add verified staff service boundary, reuse compatible orchestration primitives | Recommended: separate typed principal/work/action authority; call existing authorized domain services, not customer contact-scoped order methods. Reuse risk taxonomy, bounded planning, facts grounding, timeout/breaker and current switch semantics only after adapters preserve authority. |

Exact later integration changes, owned by A:

- Extract narrow local order-read service from `business.py`; both existing
  router and workspace adapter invoke the same human/tenant/resource policy,
  safe projection and parameterized SQL. Do not call the router through HTTP
  with forged headers or duplicate order SQL in a chat agent.
- Add session-UUID execution revalidation to the identity repository/service
  with narrowly reviewed privileges; no raw credential in the worker.
- Add owner-bound workspace persistence/queue/receipts and an explicit
  surface-aware dispatcher. Unknown/unsupported surfaces fail closed.
  Keep current sandbox runner, callback, composition and delivery checks.
- Add workspace dependencies/adapter with separately resolved resource
  authority, rechecking on retries. Share runner mechanics by extraction only
  when useful; never weaken `SpokeRunner.execute` or use a new channel to skip it.
- Keep LangGraph imports in `csa/graph/`. Reuse orchestration structure only
  through workspace-specific typed state/dependencies; do not synthesize
  customer contacts to satisfy current `TurnState`/`Envelope`.
- Later gateway mounts/OpenAPI/navigation and tenant-switch cancellation/cache
  clearing are coordinated changes, not work B can independently wire.
  No new registry capability or migration number is reserved by A0.

## 5. Dependency-free HR seam and ownership

B owns only `csa/hr/`, `tests/test_hr_*.py` and one HR-local note initially.
A owns shared identity/access/workspace/runner/graph/runtime, global fixtures,
dependencies, schema readiness, OpenAPI and navigation. B requests shared
changes. A does not edit B's files during concurrent work.

Proposed mapping for joint review; not an agreed B API or code dependency:

| A's trusted adapter supplies later | B's independent HR-local contract |
| --- | --- |
| Verified tenant and user, explicitly resolved account-to-employee mapping | Tenant-scoped employee reference; employee may have no login; no contact conversion |
| Fresh action/resource grant decision from reviewed HR owner policy | Local resolved permission input for exact directory/training action |
| Current self/assigned-manager/HR-operator relationship and team scope | Local relationship facts; never infer manager from tenant role or email |
| Authorized field allowlist before retrieval/composition | Safe directory projection; deny unknown fields; exclude salary/bank/medical/CV |
| Exact training draft/request reference, expected version, approved policy and clock | Pure training validation/transition; valid proposal is not persisted/submitted/approved |
| Secret-free typed outcome and source/as-of | Map only approved projection into workspace result/facts; no unrestricted HR blob |

Neither side imports the other's unreleased modules. B may define HR-local
types and pure functions against synthetic resolved inputs now. After both
contracts are reviewed, A implements one adapter importing HR-local contracts;
manual HR UI, chat and fake finalized-utterance voice reuse that service path.
No HR action is accepted by the A0 workspace external union. Unknown/future
actions fail closed; HR adapter activation waits for reviewed policy and B's
actual API, not a guessed signature. This note records an interface proposal,
not evidence that B has accepted it.

## 6. Decisions and stop gates

Confirmed product choices remain internal staff chat, HR directory/training
first, authenticated staff-browser voice first; do not ask these again.

A0 contract confirmation received: separate future workspace surface,
above initial matrix, named files, read-only action union and pure validation
only. That implementation is complete; stop before authenticated integration.

HR access owner and self/team/operator field/approval/support policy remain
unresolved. They do not block A0 if HR stays denied; ask only to name an owner
or explicitly defer. Broad console roles do not fill the gap.
First mutation is deliberately deferred beyond A1; later obtain a separately
scoped choice (recommended ticket-note draft). Voice consent/languages/
retention remain gated until the fake seam is stable and real media is scoped.
Do not resolve these by expanding A0.

## 7. Final evidence and one next slice

Exact additions: this design/handoff note, the workspace package marker,
pure contracts/policy module and its deterministic test file linked above.
No existing runtime files, dependencies, database objects, public API,
B-owned files or central roadmap/progress were modified.
No commit, push, PR, merge, deployment, service, image, provider, tunnel, DNS,
environment/secret inspection or persistent database action was performed.
No container was started/stopped/removed; retained containers/volumes remain.

Documentation validation: a stdlib Python 3 check passed the initial 17 local link
targets (including the referenced heading) and balanced code fences; final
validation also checks the three newly linked implementation files.
Initial `python` invocation was unavailable; rerunning with installed `python3`
passed without installing anything. `git diff --no-index --check /dev/null`
against this untracked note emitted no whitespace errors (exit 1 denotes
the added file). Final tracked `git diff --check` is also required.
Python validation used the existing original-worktree dependency environment
read-only, without synchronizing/installing dependencies. Every command used
`NO_LOCAL_ENV=1 ENV=ci UV_NO_SYNC=1
UV_PROJECT_ENVIRONMENT=/home/amsyrzmi/CSAV2/.venv` from the A0 worktree.
No shared environment/manifest/lockfile was changed.

| Check | Result |
| --- | --- |
| `uv run python -m pytest tests/test_workspace_contracts.py -q` | Initial 84 passed in 0.45 s, before the final additional boundary cases. |
| Initial targeted Ruff check | Failed: one import ordering issue and two long lines. Corrected with Ruff import sorting/formatting; no rule disabled. |
| `uv run python -m pytest tests/test_workspace_contracts.py tests/test_agents_runner.py tests/test_contracts.py -q` | Final 128 passed in 9.09 s; none failed, skipped or deselected. Includes existing sandbox runner allowlist/denial and capability-switch/retry regressions. |
| `make lint` | Final pass: Ruff, formatting (314 files), Pyright (zero errors/warnings), import-linter (one contract kept). |
| Full `make lint test` / full test suite | NOT RUN: user confirmed no coordinated B window. Passing lint alone does not satisfy this requirement. |
| Disposable reset acceptance | NOT RUN, not skipped: requires the disposable migration test in a coordinated window. No bare reset. |
| Authenticated console/DB/Telegram delivery/browser/UI/provider/voice acceptance | NOT RUN and not established by A0. No frontend changed. |

Tests exercise extra-field/actor injection rejection, bounded hints/actions,
identity/binding/action/resource mismatches, role denial, missing identity/
authority, switch changes, denied resource, stale versions, future/stale/
pre-admission checks, exact freshness/expiry/TTL boundaries, immutable strict
DTOs, aware times, safe result discriminators and Decimal serialization.
They construct trusted snapshots in memory; they do not verify logout,
membership revocation or concurrent writes against real persisted state.
Full suite/reset remain outstanding before integration; coordinate B's window,
retain task container IDs, and never prune or reuse persistent databases.

One next bounded slice, after contract review, is A1: authenticated page-aware
guidance plus one local order read through the extracted authorized business
service, owner-private persisted workspace threads, current execution checks,
idempotency, safe errors/source/as-of and no tenant-switch bleed. Preserve
sandbox/Telegram. No commits, payroll, HR activation or LiveKit service.

Continuation prompt:

```text
Start in /home/amsyrzmi/CSAV2-workspace-a0 on
feature/workspace-a0-contract; recheck HEAD/status/worktrees. Read this A0
design, repository guidance and supplied current original-worktree roadmap.
The original dirty main and B's HR worktree are not ours to modify.
A0 pure contracts/tests are approved and implemented; HR owner/policy is
explicitly deferred with HR disabled and independent. Final focused tests
128 passed; repository lint passed. Full suite/reset NOT RUN because B's
host window is uncoordinated. Review the contract, coordinate missing checks,
then obtain a separately scoped A1 task for authenticated guidance plus one
authorized local order read. Do not broaden A0 into routes, schema, registry,
UI, voice or staging. Preserve sandbox/Telegram and use no persistent reset.
Keep central roadmap/progress intact; hand off exact evidence through A.
No commit/push/PR/merge/deployment without explicit approval.
```
