# Cross-tab workspace assistant design — 2026-10-26

Status: design in review with the owner; the second of three related projects after
service packages (merged in PR #32/#33) and before HR completeness.

## Intent

One assistant, present on every console tab, that acts strictly as the signed-in user.
It answers questions with the context of the tab the user is on, carries out that tab's
actions through the same confirmation and the same server path as the tab's own buttons,
moves the user to another tab and continues the workflow there, resolves people by name
or relationship, and drafts internal email. Nothing it does exceeds what the user could
do by clicking.

## Goals

- A floating panel on every tab, with the conversation kept while navigating.
- Answers that use the current tab and selected record, fetched server-side under the
  user's authorization from identifiers only.
- Three end-to-end actions in the first version: compose and export an email draft,
  submit a training request, decide (approve or reject) a held customer reply.
- People resolution through a basic company directory (display name, job title, work
  email) visible to every staff member of the tenant; "my boss" from the user's own
  employee record; everything else about people stays behind HR grants.
- One assistant: the HR tab's planner endpoint delegates to it.
- Server-issued confirmations for every state change, shared by the panel and the tabs.

## Non-goals

- Sending email (drafts only), payroll or approval actions beyond the three above, any
  customer-facing surface, voice (project 1 roadmap), billing.
- Replacing the sandbox assistant or the customer coordinator.
- Org-chart browsing beyond the user's own manager and what HR grants already allow.

## Anti-goals

- The assistant deciding permissions. Every read and write goes through the existing HR,
  email, inbox and business services, which enforce roles, grants, packages and CSRF.
- Executing from chat text. Confirmation is a server-issued opaque proposal reference,
  consumed once, bound to the user's session; never a typed or spoken "yes".
- Page text, DOM content or browser-supplied identity reaching the server as authority.
- Directory listings reaching the language model. The model receives the phrase the user
  typed; the server resolves it and the user picks among candidates.
- A second business engine, a per-screen agent, or LangGraph outside `csa/graph/`.

## Constraints

`AGENTS.md` conventions: psycopg 3 async; Pydantic contracts; SQL under `db/sql/`
parameterized; additive migrations with staged RLS (never FORCE); exact pins; surface
errors; `make lint test` and the disposable reset test before done. Next migration
number `0460`; gateway readiness moves to `0460`.

Existing rules this design inherits (archived handoffs `2026-10-21-workspace-a0-authority-design.md`
and `2026-10-21-workspace-a1-implementation.md`, roadmap `2026-10-21-workspace-agents-roadmap.md`):
the assistant orchestrates and never owns authorization; the same service path as the
manual UI; mutations bind payload hash, expected version, expiry and session and are
confirmed through a CSRF-protected server reference; staff never enter the customer
`AgentContext`; a manager is never inferred from role or email; navigation and tenant
switching cancel stale work and keep pending work bound to its original context.

What exists today (surveyed 2026-10-26): `csa/console/workspace.py` with threads, turns,
32 KB bodies, fingerprinted request keys and audit, but a deterministic planner with two
read-only proposals; `csa/hr/assistant.py` (Groq planner producing one typed action or a
clarification) and `csa/hr/agents.py` (dispatcher with risk classes); HR `prepare_confirmation`
rows for approve/reject (session-bound, fingerprint, expected version, 15 min TTL, consumed
once by `POST /hr/confirmations/{id}/confirm`); email drafting that is reply-only with a
customer persona; inbox decisions with `expected_version` and audit; the directory field
allowlist `display_name, job_title, department, work_email, assigned_manager` and
`hr_employee_accounts(client_id, user_id, employee_id)`; the `AppShell` client component
receiving `me` and `packages`; `lib/navigation.ts` keys; package gates in
`csa/console/services.py`.

## Approaches considered

- **A. Grow the existing workspace assistant into the floating one with a shared
  proposal registry** (chosen). Keeps threads and turns, adds a planner, a registry table
  and a confirm endpoint, migrates the three tab actions onto the registry, delegates the
  HR planner endpoint.
- **B. Smart navigator with deep-linked prefill**, no registry. Rejected: keeps
  client-side confirmations the trust design forbids; no cross-tab continuation; rebuilt later.
- **C. Run inside the customer coordinator graph via the queue.** Rejected: staff must
  not enter the customer agent context; staff actions are synchronous and session-bound.

## Decisions made with the owner

1. Actions execute only through a typed proposal rendered as the tab's own confirmation
   card and confirmed through the same server endpoint and audit trail as the button.
2. The assistant can switch tabs and continue a workflow there.
3. People: a basic company directory (name, job title, work email) for every staff member
   of the tenant; HR grants govern everything else; "my boss" comes from the user's own
   record; several matches produce a clarification; no HR package means the assistant says
   people lookup is not part of the subscribed packages.
4. Email: drafts only, exported to the user's own Gmail; recipients resolved by the
   server; the full draft is shown before export.
5. First actions: email compose and export, training request submit, held-reply decision.
   Everything else is answer-and-navigate.
6. Approach A.

## Design

### Tab context (browser → server, identifiers only)

A `PageContextProvider` in the shell exposes `usePublishContext()`; each workspace page
publishes `{tab, section?, record?: {kind, id, version?}}` for its current state (for
example `{tab: "inbox", record: {kind: "conversation", id, version}}` when a conversation
is open; `{tab: "hr", section: "training", record: {kind: "training_request", id}}`). The
panel sends that object with each turn as `page_context`; the server treats it as an
untrusted hint, validates kinds against a closed set, and re-reads any record through
the owning service under the user's authorization before using it. Pages that publish no
context yield `{tab}` only, derived server-side from the route whitelist. Pages must not
publish text, names or amounts.

### Conversation store

Reuse `workspace_threads`/`workspace_turns` (`csa/console/workspace.py`). One thread per
user per tenant is the default ("current conversation"); the panel can start a new one.
Turns keep request-key idempotency (fingerprint over message plus `page_context`), 32 KB
bodies, redaction before storage, and the existing audit rows. The thread does not change
when the user navigates; `page_context` is per turn.

### Planner

`csa/workspace/planner.py` replaces the deterministic planner. It calls the existing
`GroqClient` (settings `GROQ_API_KEY`, `GROQ_MODEL`) with a system prompt that describes
the user's role, active packages, the current `page_context` kinds, and the closed set of
proposal kinds, and returns exactly one proposal or a clarification question. The prompt
receives no directory listings, no record bodies beyond a server-built redacted summary
of the current record, and never identifiers the user did not already have. Supported
languages: English and Bahasa Melayu, matching the sandbox. Planner output is validated
against the `WorkspaceProposal` union; anything else is a visible planner error.

### Proposal union (`csa/workspace/contracts.py`)

- `AnswerProposal`: markdown answer grounded on server-fetched facts for the current tab;
  the planner may request up to three typed reads (`read_current_record`, `my_profile`,
  `my_packages`) which the server executes before answering.
- `NavigateProposal`: `{tab, section?, record?}` restricted to tabs the user's role and the
  tenant's active packages allow (`lib/navigation.ts` rule mirrored server-side).
- `DirectoryLookupProposal`: `{query}`; the server resolves and returns candidates.
- `EmailComposeProposal`: `{recipients: [{employee_id} | {address}], subject, instructions,
  language}` → a draft created through a new compose endpoint; export is a separate
  confirmed step.
- `TrainingSubmitProposal`: `{training_request_id}` or `{employee_id, title, ...}` mapped
  onto `HrAgentInput.training.prepare`/`transition(submit)`.
- `InboxDecisionProposal`: `{approval_id, decision: approve|reject, expected_version}`.
- `ClarificationProposal`: a question with up to five typed options (used for ambiguous
  people and missing fields).

### Proposal registry and confirmation

Migration `0460_console_proposals.sql` creates `console_proposals(id uuid pk, client_id,
user_id, login_session_id, thread_id null, kind text, payload jsonb, payload_sha256 text,
expected_version text null, status requested|confirmed|executed|expired|cancelled,
created_at, expires_at (≤ 15 minutes), executed_at null, result jsonb null)` with tenant
RLS (not forced), `csa_app` grants, and an index on `(client_id, user_id, status)`. HR's
existing `prepare_confirmation` rows are not migrated; the registry wraps them for the
approve/reject kind, which keeps HR's own policy checks intact.

Endpoints under `/console/v1/workspace`:
- `POST /conversations/{id}/turns` (existing) now returns the planner's proposal; mutating
  kinds create a `requested` registry row and return `{proposal_id, card}` where `card` is
  the exact view model the tab's own confirmation renders.
- `POST /proposals/{id}/confirm` (CSRF, same login session, not expired, status requested,
  payload hash and expected version re-checked): executes through the owning service
  (`HrService`, `EmailService`, inbox decision), marks `executed` with the service's
  receipt, writes `console/audit.sql` `workspace.proposal.executed` with the service's own
  audit untouched. Executed once; a replay returns the stored result.
- `POST /proposals/{id}/cancel`.
- Navigation, tenant switch and logout cancel `requested` proposals of that thread
  (server-side on the next turn or on an explicit cancel call from the shell).

The three tab actions move onto the registry: HR training submit creates a proposal and
confirms it from the tab's review panel; email export creates a proposal and confirms
from the draft view; inbox approve/reject creates a proposal and confirms from the
decision card (replacing `window.confirm`). The card component is shared.

### Company directory

Migration `0460` also creates a view `company_directory(client_id, employee_id, user_id,
display_name, job_title, work_email)` over `hr_employees` joined to
`hr_employee_accounts`, readable by `csa_app` under the tenant GUC for any human session of
that tenant with the `hr` package active. New SQL `hr/directory_search.sql` does a
case-insensitive match on display name tokens and work email with a limit of 10.
`GET /console/v1/workspace/people?q=` returns candidates with the three fields; `GET
/console/v1/workspace/people/me` returns the caller's employee id and manager candidate
(`hr_employees.manager_id` projected through the same three fields) or `null` with a
reason code (`no_employee_link`, `no_manager`, `package_inactive`). No other HR field
leaves this endpoint. Rate limited per session (60 per minute) through the existing
identity throttle pattern.

### Email compose

`POST /console/v1/email/compose` creates a draft with `thread_id` NULL, `recipients` as a
list of resolved work emails (server-side from `employee_id`) plus validated free
addresses, `subject`, `instructions`, `language`, using an internal staff persona prompt
that names the recipients only by display name. Export remains
`POST /drafts/{id}/export` and becomes a proposal confirmation. The `staff_email` package
gate applies; recipients resolved from the directory require the `hr` package.

### HR planner delegation

`POST /console/v1/hr/assistant` keeps its contract but delegates to the workspace planner
with `page_context = {tab: "hr", section}`; its clarification and action shapes map onto
`ClarificationProposal` and `TrainingSubmitProposal`. Payroll and approve/reject remain
refused by the planner prompt and by the registry's allowed kinds.

### Frontend

- `components/console/AssistantDock.tsx` mounted in `AppShell` beside `{children}`:
  collapsed button on every tab for roles with a tenant membership; expanded panel with
  the conversation, the current tab chip, a text box, the proposal card, candidate pickers
  and a "new conversation" action. Read-only users get answer-and-navigate only.
- `components/console/ProposalCard.tsx`: one component for all kinds, with the tab's
  existing copy; `Confirm` posts to `/proposals/{id}/confirm` with CSRF; shows the
  service's receipt or `ApiError.message`.
- `lib/page-context.tsx` provider and hook; Inbox, Business, Knowledge base, HR and Email
  workspaces publish their state.
- Navigation proposals use the Next.js router; the panel stays mounted (it lives in the
  shell), so the conversation persists.
- `lib/workspace-api.ts` gains proposals, people and compose calls; all registered in
  `scripts/check-isolated.mjs`.

### Gating and roles

The dock appears for any role with an active tenant; per-kind availability follows the
package gates (`customer_support` or suspended for inbox decisions, `hr` for training and
people, `staff_email` for email) and the role rules (`read_only` never gets mutating kinds).
Platform admins use the dock only with a selected tenant.

### Audit and observability

Audit actions: `workspace.proposal.created`, `workspace.proposal.confirmed`,
`workspace.proposal.executed`, `workspace.proposal.cancelled`, `workspace.people.lookup`
(count only, no query text). Planner calls carry the thread id and turn id in telemetry;
prompts and completions are not logged.

### Tests

Disposable Postgres, synthetic Groq HTTP, `NO_LOCAL_ENV=1 ENV=ci`:
- Planner contract tests with recorded synthetic completions for each proposal kind,
  invalid output, and both languages.
- Registry: expiry, single execution, session binding (another user or session cannot
  confirm), payload hash and version mismatch, cancellation on navigation.
- Each action end to end over HTTP: email compose → export proposal → Gmail draft stub;
  training submit through `HrService` with grants; inbox decision with `expected_version`.
- Directory: visibility to all tenant staff, three fields only, cross-tenant invisibility,
  `my boss` cases (`no_employee_link`, `no_manager`, package inactive), multiple matches.
- Frontend isolated tests for page-context publishing and the API client; a Playwright
  acceptance that opens the dock on the Inbox, asks for a person, picks a candidate,
  composes an email, confirms export through the card, navigates to HR and submits a
  training request through the same card.
- Contract snapshot and migration tests updated; readiness `0460`.

### Documentation

`docs/CONSOLE_API.md` (new "Workspace assistant" section replacing A1), `docs/ARCHITECTURE.md`,
`docs/PROJECT_OVERVIEW.md`, `docs/PROGRESS.md`; the archived A0/A1 handoffs stay as history.

## Left to the builder

Prompt wording; exact card copy per kind; the directory search ranking; the
page-context shape for Business filters; SQL file names; implementation order (suggested:
registry and confirm endpoint with HR approve wrapping, directory and people endpoints,
planner and answer/navigate, email compose, the three tab migrations, dock and cards,
browser acceptance, docs).
