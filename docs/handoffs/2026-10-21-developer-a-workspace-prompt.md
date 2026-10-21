# Developer A continuation prompt: workspace assistant foundation

Copy the following into a new session. Supply the linked roadmap too if it is
not yet in the chosen branch; these planning files are initially uncommitted.

```text
Work in /home/amsyrzmi/CSAV2, but isolate implementation on a dedicated branch
and separate worktree after inspecting current paths/branches. Do not change
branches in a directory another developer is using. Do not reset, stash, clean,
restore or stage the existing dirty worktree. Verify HEAD/status first.
Last observed main was 0264c0ade3739f1285ab1a06e577573b71984d44; verify it.
Do not commit/push/create a PR without explicit approval.

User direction:
Prioritize (1) authenticated business web chat and system guidance,
(2) HR, (3) refined existing customer service, (4) interactive LiveKit voice.
Wallboard/reporting/team coordination follow. Teach-a-task is LAST.
You own shared workspace/authority integration, not the HR domain implementation.
Another developer is working concurrently on a pure HR foundation.
The user confirmed internal staff workspace chat, HR directory/training first,
and authenticated staff-browser voice first. Do not re-ask those choices unless
new evidence materially conflicts. HR grants/field policy and voice consent/
languages/retention remain unresolved.
Do not deploy or resume staging connector work.

Read first:
- AGENTS.md and relevant nested instructions.
- README.md, docs/PROJECT_OVERVIEW.md, newest docs/PROGRESS.md sections.
- docs/handoffs/2026-10-21-workspace-agents-roadmap.md (proposed plan).
- docs/CONSOLE_API.md private sandbox, identity, business and inbox contracts.
- LG02, LG05, relevant LG07/LG13/LG15 sections.
- csa/agents/base.py, csa/agents/runner.py, csa/turn/deps.py.
- csa/console/contracts.py, access.py, chat.py, business.py, relevant tests.
- The current release handoff only for preserved staging/environment gates.
Load csa-console-feature, csa-add-spoke, csa-verify-change and, for handoff,
csa-phase-handoff. Load csa-db-change only if later approved schema work occurs.
Do not read every historical document.

Hard source boundaries:
Current chat is an owner-private, read-only sandbox. Only KB query and product
search/detail execute there. Customer records, business writes, callbacks,
voice and arbitrary attachments are refused. Preserve these restrictions.
AgentContext is contact-oriented, not a verified staff identity contract.
Console roles are platform_admin / tenant_admin / tenant_staff / read_only;
these do not establish HR/payroll/manager permissions.
Existing Telegram staff inbox/reply approvals do not automatically authorize
order, HR or financial commits. Model statements and typed/spoken yes are not
commit credentials. A user UUID is not a contact/employee UUID.
Live marketplace adapters and interactive CSAV2 voice are not established.
Do not reuse legacy voice containers or the shared Cloudflare connector.

Bounded first task (slice 0 / A0):
1. Inspect existing authority/service patterns and produce a concise permission
   matrix and concrete contract design for a separate INTERNAL workspace
   assistant. Keep sandbox and customer-channel identities distinct.
2. Propose strict Pydantic DTOs for bounded page/entity hints and typed
   action proposals/results. Externally supplied page/resource identifiers
   never establish access; forbid actor/role/grant claims in browser/model DTOs.
   Proposed principal types must distinguish verified human identity from
   untrusted hints and separately resolved resource/action authority.
3. Specify how persisted queued work binds to current session/membership,
   tenant and original resource, and how execution/confirmation rechecks
   revocation, capability switches, resource versions and proposal expiry.
   Do not serialize bearer/cookie/CSRF secrets or unrestricted HR payloads.
4. Compare reusing the existing runner/graph primitives versus an additive
   verified staff boundary; identify exact integration changes needed.
   Do not bypass sandbox checks using a new channel string and no policy.
5. Define a dependency-free HR adapter seam with Developer B. B initially owns
   only csa/hr/, tests/test_hr_*.py and an HR-local design note. B must not be
   blocked on importing your unreleased shared modules. Record mapping and
   ownership; do not edit B's files while B is working.
6. Present the design and use ask_user only for material remaining policy
   decisions needed for your slice, including HR access owner/policy and first
   business mutation when relevant. Do not treat the confirmed product scope
   as permission for broad runtime expansion.

If internal-surface/contract choices are confirmed, implement ONLY additive,
pure typed contracts and deterministic policy-validation tests in an agreed
workspace-specific module and test file. Choose names after repository search.
Do not mount routes, alter current chat, migrate, register agents, add a global
chat widget, introduce dependencies or start any voice service in A0.
An in-memory policy fixture is not authenticated/persisted end-to-end evidence.
Stop once the contract, tests and shared ownership/next slice are reviewable.

Later A1 (not included in A0):
One separately scoped vertical slice: authenticated page-aware guidance plus
one local order/business read through the existing authorized business service.
Require explicit tenant selection for platform admins, no role/header spoofing,
current execution authorization, owner-bound workspace threads, idempotency,
safe errors/source/as-of, and no tenant-switch state bleed.
Preserve sandbox/Telegram behavior. No business commits or payroll access.
Add draft/approval/commit only after that authority path is accepted.
Start fake finalized-utterance voice integration soon after the seam is stable;
real LiveKit sessions/providers require a separate reviewed task.

Parallel ownership:
You coordinate changes to shared identity/access, graph/runner, runtime wiring,
gateway mounts, onboarding/registry, migration numbering/schema readiness,
OpenAPI generation, shared fixtures, dependencies and global navigation.
Do not widen these just for the pure-contract slice.
B requests shared changes, rather than editing those files simultaneously.
Serialize migrations at integration after rechecking HEAD; do not reserve
numbers in two branches. Merge/review shared contract before dependent wiring.
Do not run full suites/load tests concurrently with B on the shared host.

Validation:
Pure contracts: targeted unit tests first with NO_LOCAL_ENV=1 ENV=ci.
Code changes require NO_LOCAL_ENV=1 ENV=ci make lint test per repository guidance.
Full tests create disposable containers: coordinate host usage and retain task
container IDs; do not prune or reuse persistent dev/production databases.
Reset verification is tests/test_migrate.py::test_make_reset_only_disposable_container,
never bare make db-reset. If not authorized/possible, report missing evidence,
do not claim full validation. For docs-only design validate links/fences/diff.
If future UI changes occur, read frontend AGENTS and run check:isolated.

Preserve environment/worktree:
Existing uncommitted staging proposal/test/release/progress/handoff changes
must not be mixed into this feature. Two pre-existing docs/reviews deletions
must not be restored/staged/attributed. ui-guide/ stays untouched.
No .env.local or secret inspection, production DB/bot/n8n/provider calls,
persistent migrations, image pull/publication, service start/restart,
network/tunnel/DNS, merge or deployment without separate explicit approval.
Six testcontainers were stopped with approval; containers/volumes were retained.
Do not remove them or touch csa_db, csa_dev_db, shared cloudflared or legacy voice.

Finish with exact files/behavior, test pass/fail/skipped/not-run evidence,
unresolved authority decisions and one next bounded slice. Separate proposals,
pure contracts, persistence, runtime wiring and live acceptance. Report whether
anything was committed/pushed. Keep the shared roadmap intact; record task-local
handoff evidence and coordinate central progress updates through this lane.
```
