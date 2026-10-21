# Developer B continuation prompt: independent HR foundation

Copy the following into the HR developer's session. Give them this file and the
roadmap if they have not been published; a fresh branch will not contain
uncommitted planning documents automatically.

```text
Implement a bounded HR FOUNDATION for CSAV2, concurrently with Developer A's
workspace-assistant foundation. Work in a separate clone or dedicated branch
AND separate worktree directory. Never share the active working directory.
Inspect branches/paths/status first. If the supplied repository path contains
someone else's dirty work, preserve it; do not reset/stash/clean/restore.
Last observed main: 0264c0ade3739f1285ab1a06e577573b71984d44, not an assumption.
Do not commit/push/create PRs without explicit approval.

Product context:
The user's first four priorities are authenticated business web chat, HR,
customer-service refinement and interactive voice. HR includes recruitment,
payroll, training requests and attendance. Interview is an HR recruitment
subdomain; team manager uses the same employee directory. Wallboard/reporting
are shared consumers, not owners of HR truth. Teach-a-task is LAST.
Do not implement everything in this session or wait for the whole assistant UI.
The user confirmed directory + training requests as the first HR workflow,
internal staff workspace chat first, and authenticated staff-browser voice.
Do not re-ask these scope choices. HR grants, field visibility and approval
policy still need a named business owner's review.

Read first:
- AGENTS.md, README.md, docs/PROJECT_OVERVIEW.md and applicable nested rules.
- Latest docs/PROGRESS.md and the supplied
  docs/handoffs/2026-10-21-workspace-agents-roadmap.md.
- LG02/LG05 agent contracts and relevant LG07/LG13/LG15 requirements.
- csa/agents/base.py, csa/agents/runner.py and csa/console/contracts.py/access.py
  for existing constraints, not as authorization you may bypass.
- csa/agents/staged/models.py and service.py for typed preparation prior art;
  do not assume its StaffActor object is authenticated by an HTTP endpoint.
Load csa-add-spoke and csa-verify-change; use csa-phase-handoff for your result.
Load csa-db-change / csa-console-feature only for later approved DB/API/UI work.

Current limitations:
Runtime AgentContext is contact-oriented. Console role names do not confer
HR/payroll or manager access. Employee identity, login account and customer
contact are different; never fabricate a contact to reuse a customer agent.
Existing chat is a PRIVATE READ-ONLY SANDBOX, not a staff HR transport.
No inspected runtime registration provides an HR agent. Payroll/provider
execution, calendar invitations and email sends must not be claimed.

Initial ownership (strict concurrent-development boundary):
- You may create csa/hr/ domain modules and tests/test_hr_*.py.
- You may add ONE HR-local design/handoff note at an agreed unique path under
  docs/handoffs/; include exact validation and remaining decisions there.
- Do not edit existing identity/access, AgentContext, shared contracts,
  graph/runner/runtime deps, onboarding/registry, gateway mounts,
  migrations/schema readiness, OpenAPI snapshot, central progress/handoff,
  global navigation, tests/conftest.py, dependency manifests or lockfiles.
Developer A is the shared integration owner. Propose required changes in your
handoff; do not silently implement them. No guessed migration numbers.
If scope needs these shared changes, stop at the boundary and coordinate.
Do not import an A-owned module that has not yet been agreed/merged.

Task HR-0: independent, pure contracts and policy/transition logic:
1. Inspect prior art, then define constrained Pydantic domain models for
   employee identity/reference, safe directory projection, explicit field
   visibility and a training-request draft/lifecycle proposal.
   Scope all HR references to a tenant; no shared cross-tenant employee IDs
   inferred from email. Employee records can exist without a console account.
2. Propose a clear permission matrix for self, assigned manager/team and HR
   operator. Deny unspecified cases; platform/tenant admin and tenant_staff
   must not automatically gain salary, bank, medical or CV data access.
   HR role/grant policy and reporting relationships need business-owner review.
3. Use ask_user before implementing unresolved access/approval behavior with
   multiple reasonable policies. The confirmed first persisted slice is
   employee directory + training requests; payroll country/legal rules are
   NOT blockers to pure HR-0 modelling.
4. Implement only pure validation, public projection and transition-policy
   functions that take typed domain facts/resolved permission inputs.
   Label those inputs as trusted adapter requirements, NOT authentication.
   Any browser/API adapter must later resolve them from verified server state.
   No HTTP caller/model may supply a manager/HR grant as its authority.
5. Define safe draft/request/decision semantics with explicit invalid-state
   errors and an injection point for a clock if dates/expiry are needed.
   Distinguish a model-valid proposal from a persisted request or approval.
   HR business approval is not the existing customer reply-approval queue.
   No fake successful persistence or provider execution.
6. Use synthetic fixtures only. Prove bounded input/enum validation, cross-tenant
   employee references denied, self/team/HR field filtering, unsupported grant
   denial, manager outside team denied, invalid/stale transitions, and input
   that tries to inject actor/grants rejected at the relevant external boundary.
   Do not add a dummy API solely to test spoofing; clearly mark future API tests.
7. Write your HR-local handoff with proposed schema constraints, required
   action/resource permissions and mapping points for Developer A's future
   verified workspace adapter. Keep salary/bank/medical data out of generic
   result facts, logs and model fixtures.

Stop after HR-0 package, focused tests and handoff. Do not register an agent,
add routes, migrations, persistent storage, UI, scheduled jobs or adapters yet.
Do not represent HR-0 as completed recruitment, attendance or payroll.
The pure package must work independently of Developer A's ongoing branch.
At integration, agree one adapter and reuse domain services for manual UI,
workspace chat and voice; do not duplicate business logic in each surface.

Proposed later milestones (separate tasks):
HR-1: reviewed directory/training-request schema, grants/RLS, audit,
idempotency, authenticated API/manual UI, then registered disabled-by-default
capabilities enabled deliberately per tenant. Coordinate additive ordered
migration numbers, roles and readiness with A after reviewing current HEAD.
HR-2: recruitment/interview guides, consented CV intake, retention/deletion,
human-reviewed notes and later scheduling; no autonomous hiring/ranking/
rejection or protected-attribute inference.
HR-3: attendance/manual import, timezone/shifts, leave/holidays/corrections,
manager approval and replay/conflict rules. No biometric/location surveillance.
HR-4: payroll only after country/legal entity, currency, pay cycle, employment
rules, statutory contributions/tax and qualified reviewer are agreed. Decimal,
effective-dated/versioned calculation rules, reviewed snapshots, maker/checker,
period locks/reversals. No automatic wage deduction, bank transfer or filing.
Do not expose payroll just because a user is tenant_admin/platform_admin.

Validation:
Start with NO_LOCAL_ENV=1 ENV=ci uv run pytest <your exact HR test files> -q.
Run repository lint with NO_LOCAL_ENV=1 ENV=ci.
Code changes require full NO_LOCAL_ENV=1 ENV=ci make lint test; coordinate its
time with A so shared-host full/load suites do not overlap. Full suite uses
disposable Testcontainers; never use persistent dev or production.
Reset acceptance is the disposable-only test in tests/test_migrate.py,
not a bare make db-reset. If full validation is unavailable/unapproved report
it explicitly, never claim the focused result covers persistence/runtime.
Documentation-only design needs links/fences and git diff --check, no service.
Track task-created container IDs; cleanup only your exact resources.

Safety and preserved work:
Never inspect .env.local/environment secret contents, production DB/tokens/
n8n, real employees/CVs/payroll, or make live provider/model requests.
No service start/restart, image pull/publication, persistent migration,
network/tunnel/DNS change, merge or deployment without separate approval.
Do not touch ui-guide/, existing shared Cloudflare or legacy voice services.
Do not restore/stage two existing docs/reviews deletions, nor absorb A's dirty
staging proposal/test/docs changes. Six old test containers were stopped with
user approval and retained with their volumes; do not prune/delete them.

Finish with your exact files, pure behaviors, validation results including
failures/skips/not-run checks, decisions still needed and proposed HR-1 seam.
Clearly state no persistence/API/agent registration/provider/live acceptance.
Give A a short integration checklist, not edits to their working files.
```
