# Workspace assistant, HR, customer service and voice roadmap

Date: 2026-10-21. Status: proposed product/engineering plan, not implemented
functionality or deployment approval. This records the user's priority change
before further staging work. The first four areas are high priority: business
web chat, HR, refined customer service, and interactive voice. Teach-a-task is
last. The duplicated wallboard request is one workstream.

The user confirmed internal staff workspace chat first, directory plus training
requests as the first HR workflow, and authenticated staff-browser voice first.
These are product-scope decisions, not grants to read real employee data.

## Start and authority

Read [repository guidance](../../AGENTS.md), [project overview](../PROJECT_OVERVIEW.md),
[progress](../PROGRESS.md), [console API](../CONSOLE_API.md), then only the relevant
[LG02](../langgraph-rebuild/LG02_contracts_state_registry.md),
[LG05](../langgraph-rebuild/LG05_spokes_and_tools.md),
[LG07](../langgraph-rebuild/LG07_egress_outbox_hitl.md),
[LG13](../langgraph-rebuild/LG13_agent_catalog_and_console.md) and
[LG15](../langgraph-rebuild/LG15_console_turnover.md) sections.

For the next two implementation sessions use:

- [Developer A: workspace-assistant foundation](./2026-10-21-developer-a-workspace-prompt.md).
- [Developer B: independent HR foundation](./2026-10-21-developer-b-hr-prompt.md).

The [release handoff](./2026-10-19-domain-release-next-session.md) and
[release contract](../../deploy/RELEASE.md) retain staging gates. This roadmap
does not waive them. Public staging is not a prerequisite for pure contracts
and disposable development, but public acceptance and deployment remain gated.

## 1. What actually exists

Source inspected at local `main`
`0264c0ade3739f1285ab1a06e577573b71984d44`; recheck before implementation.

| Area | Source-grounded status | Consequence |
|---|---|---|
| Identity | Human sessions, membership, selected tenant, CSRF and four broad console roles exist in [console contracts](../../csa/console/contracts.py) and [access checks](../../csa/console/access.py). | Broad tenant staff/admin roles are not a payroll or employee-record permission model. |
| Web chat | [Chat routes](../../csa/console/chat.py), durable ingress/history and private local delivery exist. [API scope](../CONSOLE_API.md#private-sandbox-assistant-m4) is explicitly owner-private and read-only for business operations. | Do not widen the sandbox or reuse its histories as a staff/customer inbox. |
| Spokes | [Runtime wiring](../../csa/turn/deps.py) includes orders, tickets, escalation, KB, invoice reads and staged booking/subscription. [Runner](../../csa/agents/runner.py) enforces the web sandbox allowlist. | Reuse capabilities; do not build a second order/ticket implementation inside chat. |
| Execution context | [AgentContext](../../csa/agents/base.py) carries tenant/contact/conversation, not a general verified staff principal. | Staff authority needs an explicit reviewed contract; a user UUID is not a customer contact or employee UUID. |
| Customer service | Coordinator, grounding, KB, tickets, escalation, order callbacks, durable outbox and Telegram staff inbox/approval paths exist. | Refinement is an evaluated bundle of these capabilities, not a new competing coordinator. |
| Commerce | Local business reads and offline marketplace foundations exist; adapter registration does not establish configured provider access. Inventory authority remains staged. | Report source/freshness truthfully; do not promise live stock, refunds or seller operations. |
| Voice | Voice fields/failure handling and historical voice-note plans exist. Separate legacy LiveKit containers were observed. | Neither proves an authenticated interactive CSAV2 voice workflow. Do not attach or modify legacy services. |
| Other proposed domains | HR/payroll/interview, wallboard and reporting are not registered capabilities in the inspected runtime wiring. Email/calendar preparation is not provider execution. | Plan/build them explicitly; a copied UI/reference page is not implementation. |

Source expects schema 0360; persistent-dev 0300 is only a prior recorded approval.
Do not probe or migrate persistent databases to reconcile that record.

## 2. Organize by responsibility, not by how many chatbots appear

Use one modular Python application/domain layer, the existing coordinator,
gateway/worker/delivery responsibilities, and a separate media worker when voice
requires it. Do not create a microservice or independently reasoning agent per
screen/card. Extract services later only for measured scaling, isolation or
ownership needs. No wholesale directory/agent rename is needed.

| Group | Ownership and proposed capabilities |
|---|---|
| Workspace assistant | Authenticated employee-facing guide, page-aware navigation, authorized business questions and typed workflow proposals. It orchestrates; it does not own business truth or authorization. |
| Customer-service experience | Customer channels and staff inbox, existing KB/orders/tickets/escalation, response grounding, takeover, approvals and service metrics. Customers never inherit workspace/HR tools. |
| Commerce/operations | Orders, products, invoices, inventory and marketplace connections. Existing services retain ownership of domain transitions and provider bindings. |
| People/HR | Employee directory, reporting relationships, recruitment, attendance, training and later payroll. Implement bounded modules under an HR domain; expose only justified actions as spokes. |
| Recruitment/interview | A subdomain of HR, not an unrelated top-level workforce store. Job requisitions, consented CV intake, interview guides, scheduling and human-reviewed notes. |
| Team coordination | A workspace view over authorized HR directory/manager relationships plus shared messaging/calendar services. No second employee database. |
| Insights | Reporting owns metric definitions and reproducible queries/jobs. Wallboard owns saved presentation specifications, layout, sharing and refresh, not business mutations. |
| Channel/integration layer | Web, Telegram and future WhatsApp/email ingress/egress; LiveKit media adapter; calendar/email/provider adapters with scoped credentials. Reused by domains. |
| Teach-a-task | LG14 desktop/record-and-replay automation, last, disabled until sandboxing, approval and audit controls are separately accepted. |

Most spokes should be deterministic async tools with typed facts. Reasoning
belongs where interpretation is needed, not inside every SQL operation. Only
`csa/graph/` imports LangGraph. Preserve the existing facts/verification/approval
architecture rather than building a second agent framework.

## 3. Shared workflow and authorization foundation

Recommended flow:

```text
authenticated workspace text or bound voice session
  -> resolve current human principal + tenant + allowed surface
  -> validate bounded page/entity context, redact/minimize text
  -> persist work and scoped conversation identity
  -> bounded coordinator proposes typed capability/action/arguments
  -> service rechecks current action/resource permission and registry switches
  -> read facts OR save a durable proposal
  -> explicit bound confirmation and any required independent approval
  -> revalidate permission, resource version and proposal expiry
  -> atomic domain transition + audit + outbox intent
  -> delivery/refresh with honest status and recovery
```

The transport can change without changing the permission or workflow outcome.
Chat and voice must invoke the same service path as manual UI actions.

### Trust boundaries that must be designed before business chat writes

1. **Three separate surfaces:** existing sandbox, internal workspace assistant,
   and future customer web chat/widget. Propose a separate workspace route and
   thread namespace, subject to API review. Never bypass sandbox restrictions
   by changing a channel string or model-supplied flag.
2. **Verified staff execution context:** identity/tenant/session are resolved on
   the server and durably bound to queued work. Employee/manager relationships
   and action/resource scopes are separately resolved. Recheck membership,
   grants, capability enablement and resource authority at execution and commit,
   including after logout/revocation, retries and delayed approval. Do not put
   live session secrets into envelopes, checkpoints or model prompts.
3. **Resource and field permissions:** define capabilities such as business-read,
   ticket-note-draft, HR-directory-read, HR-training-request and payroll-read as
   proposed domain permissions, not assumed existing grants. Scope self/team/HR
   and specific fields; a platform operator is not automatically a payroll reader.
   Exceptional support access needs a separate audited policy.
4. **Page context is a hint:** allowlisted page key plus optional entity ID; server
   resolves authorized data. Do not upload the DOM, arbitrary page contents,
   secrets or an entire table. Cross-tenant/forbidden IDs must be denied without
   leaking existence. On navigation/tenant switch cancel stale requests, clear
   scoped caches and bind pending work to its original context.
5. **Data minimization:** retrieve only authorized fields before planning,
   composition, caching or persistence. Common-pattern PII redaction is not
   sufficient for salary, bank, medical or CV data. Define model-provider consent,
   trace scrubbing and retention; never put restricted facts in a general KB.
6. **Mutation lifecycle:** separate read, draft/proposal, confirm, approve and
   commit. The current reply-approval queue is not automatically a business/HR
   approval engine. Reuse primitives only after proving equivalent authority,
   expiry, payload/version binding, idempotency and atomic audit/outbox behavior.
   Model text, typed "yes" or spoken "yes" alone is not a commit credential.
7. **Long workflows:** use persisted job/state transitions for recruitment,
   reporting and payroll, not one indefinitely running graph turn. Show queued,
   running, awaiting input/approval, uncertain, completed and failed states.
   Multi-domain requests are bounded steps, not a fictitious cross-provider
   transaction; define partial completion and compensation.
8. **No arbitrary code/SQL/HTTP tools:** use allowlisted parameterized queries,
   approved recipient resolution and narrow adapters. Instructions in pages,
   CVs, documents or transcripts are untrusted content, not new tool authority.

## 4. Priority sequence for two developers

These are exit-gated slices, not fixed delivery dates. With two developers,
four high-priority areas cannot all be production-ready simultaneously. Start
chat and HR in parallel; begin a synthetic voice adapter early after the common
authority seam, while customer-service refinement reuses the same foundations.

| Slice | Developer A | Developer B | Exit/dependency |
|---|---|---|---|
| 0: contract and ownership freeze | Workspace surface, principal/page-context contract, permission matrix and regression plan. | HR data/permission contract, domain boundaries and synthetic cases. | Independent contract drafts reconcile once; no broad runtime changes. |
| 1: safe vertical foundations | Separate workspace assistant, authenticated page-aware navigation and one existing authorized business-read workflow. | HR domain foundation, then persisted employee directory and training requests after policy/schema review. | Anonymous/cross-tenant/stale-context/revoked-role denial, truthful results and unchanged sandbox. |
| 2: CS refinement + voice skeleton | Golden-case CS audit/fixes, then one low-risk draft-to-confirmed workflow using shared services. Add fake-media finalized-utterance adapter when authority seam is stable. | HR API/manual tab vertical slice, grants and audit; expose typed HR adapter only after shared integration. | Same permissions through manual UI/chat; deterministic fake voice round trip without real provider/token. |
| 3: interactive voice + HR breadth | Separately gated LiveKit interactive loop: STT, interruption, TTS, reconnect and human fallback. | Recruitment/interview assistance, attendance corrections/approvals; payroll discovery and reviewed calculations later. | Synthetic end-to-end first; live media requires separate consent/provider/startup acceptance. |
| 4: reusable insights | Initial reporting read models, explicit metric filters and manual report page. | Shared wallboard cards and authorized HR metric projections as capacity allows. | Versioned metric/card specs, scoped refresh, reproducible data and no permission leakage. |
| 5: outbound coordination | Explicitly approved email/calendar/WhatsApp adapters, recipient policy, report jobs and team meeting workflows. | HR-specific report/recruitment scheduling consumers and payroll review flows. | Adapter-specific live gates; drafted is not sent, invited, accepted or paid. |
| Last | Teach-a-task LG14 only after higher-priority acceptance and a separate design review. | No parallel desktop automation shortcut. | No dependency on it for ordinary domain operations. |

Suggested planning checkpoints are small PRs, each with one observable result,
not a single "all agents" branch. Estimate dates only after slice 0 establishes
the initial HR policy, voice languages/provider and available developer capacity.
Keep staging/infrastructure as a separately approved lane, not a third implicit
full-time workload.

### First workspace vertical slice

Recommended default: internal console users only; existing sandbox unchanged.
Start with page guidance and a bounded local order/business read for an actor
who could already read it through the manual business API. Reference existing
authorization/services and expose source/as-of/limitations. No customer account
impersonation, order commit, payroll, arbitrary search, external sends or live
marketplace operations.

Acceptance: anonymous/machine denied; explicit platform tenant selection;
current actor/resource checks; forged page/entity/tenant rejected; exact
same-key replay and changed-payload conflict; safe error/freshness states;
tenant switch does not mix conversations; sandbox and Telegram tests remain
green. Add a deterministic planner fixture so permissions do not depend on a
real model response.

### HR progression and non-negotiable distinctions

- **HR-0:** pure typed employee/directory/training-request contracts, explicit
  self/team/HR permissions and deterministic validation/transition tests.
- **HR-1:** additive persisted employee directory and training-request workflow,
  authenticated API and manual UI; staged registry/chat wiring only after
  integration review. An employee need not have a login; an account may belong
  to multiple tenants; a customer contact must not become an employee by guess.
- **HR-2:** requisitions/candidate consent/CV retention, structured interview
  guides and human-reviewed notes. Support scheduling but do not autonomously
  rank/reject/hire candidates or infer protected attributes.
- **HR-3:** manual/imported attendance, timezone/shift rules, corrections,
  manager review and duplicate/replay controls. No implicit biometric tracking,
  location surveillance or automatic wage deduction.
- **HR-4:** payroll only after country/legal entity, currency, pay cycle, tax,
  statutory contributions, overtime/leave rules and qualified owner are decided.
  Use Decimal, versioned effective-dated rules, reviewable calculation snapshots,
  maker/checker approval and period locking/reversal. No autonomous bank transfer
  or statutory filing. Salary and bank data are separate restricted projections.

Recruitment/attendance can be reordered by business need after HR-1. HR-0 does
not require a live payroll provider or resolution of every future payroll rule.
Leave/absence, holidays and employment lifecycle are missing from the original
list but necessary before attendance can safely drive payroll.

### Customer-service refinement

Build an experience bundle over existing components: tenant KB/policy answers,
customer-bound order status, ticket create/follow-up, consented handoff and
staff takeover/release. Explicitly stage refunds/returns, omnichannel identity,
SLA escalation and live marketplace data until their domain contracts exist.
Measure correct routing, fact grounding, wrong-customer denial, duplicate
delivery/retry, handoff outcomes and language quality using synthetic goldens.
Do not claim "a human will follow up" without the durable handoff fact.

### Voice: ingress plus conversational rendering, not a privileged agent

Use a separate, explicitly bound LiveKit media session and the same workspace
or customer authority profile. Do not reuse the running legacy voice containers
or expose old callbacks. STT finalized utterances carry stable session/utterance
IDs; interim words never commit. Summaries preserve intent but are not the sole
source for names, amounts, dates or authorization. Clarify uncertainty and show
an inspectable text/action preview. TTS speaks authorized, grounded outcomes.

Interactive acceptance must include interruption/barge-in, cancellation versus
an already-committed action, end-of-turn detection, silence/timeouts, duplicate
final transcripts, overlapping utterances, reconnect/replay, logout/revocation,
language/noise tests, transcript retention/recording consent, latency budgets,
cost limits and human/text fallback. Sensitive details should default to
on-screen display rather than audible playback. Telephony is a separate scope
from browser voice; existing voice-note STT is not live conversation acceptance.

### Reporting, wallboard and presentation

Reporting defines allowlisted metric IDs, tenant/resource/field filters,
time window/timezone, currency, source/as-of and metric version. Use approved
read models, not LLM-generated SQL. Aggregate views still require HR/finance
authorization and small-group disclosure review. Missing data is not zero.

Wallboard stores a versioned card/query/presentation specification with owner,
sharing policy and refresh schedule. Provide text, stat/table and bounded chart
renderers; do not accept generated JavaScript/HTML as executable widgets. Chat,
voice and manual UI create the same validated card draft/preview. Reauthorize
the data on every refresh/view/export; sharing a card cannot grant new data
access. Treat saved snapshots as sensitive retained data with expiry/deletion,
not as a way around a revoked grant. Begin with private cards; public displays
need separately reviewed non-sensitive projections.

Email/Telegram/WhatsApp report delivery needs explicit destinations, recipient
authority, preview/approval, schedule timezone, idempotent run keys and durable
delivery attempts. Verify authorization when the job executes, not only when
scheduled. Calendar invitations require conflict/timezone handling and an
approved organizer; sent does not mean accepted. External sends are generally
not exactly-once. Team manager composes these services over HR directory data.
Start presentation with reusable frontend components/export jobs, not another
microservice.

## 5. Parallel-development rules

Use separate branches AND separate worktree directories. Two branches in one
working directory are not concurrent isolation. Before creating a worktree,
inspect existing branches/paths; never reset/stash/clean the current dirty tree.
Suggested new branches are `feat/workspace-assistant-foundation` for A and
`feat/hr-foundation` for B; verify availability before creating either.
The plan/prompts are initially uncommitted: explicitly supply these files to
the second developer or publish a separately approved planning commit first.
Do not assume a fresh worktree contains them.

| Ownership | Initial paths / restriction |
|---|---|
| A: shared integration owner | Principal/workspace contracts, assistant transport/UI, future gateway mount, graph/runner seams, permission integration, onboarding/registry wiring, schema readiness, OpenAPI export and shared test fixtures. |
| B: HR-only owner | Proposed `csa/hr/` domain modules, `tests/test_hr_*.py`, HR-specific design note. After review: HR SQL/service/adapter and manual HR UI. |
| Reserved shared paths | Existing identity/access, `AgentContext`, runtime deps, registry/onboarding, gateway wiring, `tests/conftest.py`, dependencies/lockfile, migrations, OpenAPI snapshot, global navigation and central progress/handoff. B requests changes rather than independently editing them. |

The initial B package must not depend on a not-yet-written A module. Define
HR-local typed policy inputs and pure domain operations; do not authorize them
from an HTTP request or claim a synthetic permission object is authenticated.
A later reviewed adapter supplies trusted data from shared authorization.
Both developers review the cross-domain mapping once, then reuse it.

Migrations are serialized: A allocates the next ordered number only at integration
after rechecking repository head. B can draft a schema proposal but must not
reserve a guessed number or modify schema readiness independently. Do not merge
pure logic as though persistence/API/UI already exists.

Merge order: bounded contract foundation -> HR pure package -> domain-specific
persistence/integration PRs -> per-feature UI/chat adapters. Share a small agreed
contract commit before a dependent PR. Do not copy evolving shared files between
branches. Each PR reports migration/provider/runtime status, commands/results and
remaining gates.

On the shared host use separate Compose projects/fixtures/ports, never shared
persistent dev for branch acceptance. Schedule full suites/load timing serially
to avoid contention; use focused pure tests in parallel. Track task-owned
container IDs and cleanup failures; never prune globally. Six labeled test
containers were stopped with user approval on 2026-10-21; their containers and
volumes remain preserved. Production/dev databases and shared Cloudflare were
left running. Cleanup failure cause is unproven.

## 6. Confirmed scope and remaining decisions

Confirmed through the planning-session scope form:

1. Internal workspace assistant first, not a public customer widget. Preserve
   the sandbox and use a separate explicit workspace surface.
2. HR-0 then directory/training-request HR-1 first; recruitment/attendance
   follow, payroll later.
3. Authenticated staff-browser voice first, not public customer voice or
   telephony.

Still required before the corresponding behavior:

- Identify HR owner, self/team/HR field access, manager relationships and support
  access policy before persisted HR data. Country/legal entity/payroll rules
  are gates for payroll, not blockers for pure HR-0 contracts.
- Decide supported voice languages, recording/retention and consent policy
  before a real media pilot.
- Select one first business mutation after reads; recommendation is a reviewed
  ticket-note draft, not payroll/order/payment execution.

The copy-ready prompts deliberately bound initial implementation to
proposal/pure-contract work. They stop before API/authority expansion or
unresolved sensitive-data policy decisions.

## 7. Validation and continuation

This planning slice changes documentation only. Check local Markdown links/fences
and `git diff --check`; no databases/services are needed. It does not establish
new runtime coverage. Existing staging proposal/test/docs changes and unrelated
review deletions remain separate and uncommitted; no commit/push was requested.
The maintained Markdown check passed for 48 files and 444 local targets with
zero link/fence errors; `git diff --check` passed. No runtime tests were run
for this documentation-only planning slice.

For later code use [verification guidance](../../.agents/skills/csa-verify-change/SKILL.md):
focused checks then `NO_LOCAL_ENV=1 ENV=ci make lint test`; migration reset
acceptance only via the disposable-container test, never bare persistent reset.
Frontend changes require the adopted isolated check and scoped authenticated
browser evidence. Live media/provider acceptance is separately authorized.

Immediate next work is slice 0: agree the authority/ownership contracts under
the confirmed scope, then execute the two independent prompts. Do not
resume connector provisioning as an implicit consequence of this product plan.
